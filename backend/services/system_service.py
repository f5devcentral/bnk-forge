"""
System Service — DB and business logic for system administration.

Extracted from routes/system.py to separate HTTP handling from domain logic.

Covers:
- System health checks (DB, Redis, Celery)
- Task queue metrics
- Performance metrics
- Recent errors
- Database stats, cleanup, vacuum
- System version check and upgrade
"""

import logging
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import UTC, datetime, timedelta
from typing import Any

import requests
from sqlalchemy import func, text
from sqlalchemy.orm import Session, joinedload

from celery_app import celery_app
from core.cache import cache
from core.errors import BadRequestError
from core.worker_heartbeat import heartbeat
from models import ApplicationSetting, AuditLog, DeploymentLog, ModuleLibrary, Project, ProjectModule, Task
from models.enums import TaskStatus
from services.bnk.consumption import aggregate_cluster_consumption, aggregate_fleet_summary
from services.bnk.fetch import fetch_all_bnk_data
from services.defaults_service import DEFAULT_REPO_URL
from services.dpf.fetch import detect_dpf
from services.kubernetes_service import KubernetesService

logger = logging.getLogger(__name__)


class SystemService:
    """Service layer for system monitoring and administration."""

    def __init__(self, db: Session):
        self.db = db

    # ================================================================
    # Health
    # ================================================================

    def get_health(self) -> dict[str, Any]:
        """Get comprehensive system health status (cached 30s)."""
        cached = cache.get("system:health")
        if cached:
            return cached

        health_data = {"services": {}, "timestamp": datetime.now(UTC).isoformat()}

        # Backend
        backend_start = time.time()
        health_data["services"]["backend"] = {
            "status": "healthy",
            "response_time_ms": round((time.time() - backend_start) * 1000, 2)
        }

        # Database
        try:
            db_start = time.time()
            self.db.execute(text("SELECT 1"))
            health_data["services"]["database"] = {
                "status": "healthy",
                "response_time_ms": round((time.time() - db_start) * 1000, 2)
            }
        except Exception as e:
            health_data["services"]["database"] = {"status": "offline", "error": str(e)}

        # Redis
        try:
            redis_start = time.time()
            celery_app.backend.client.ping()
            health_data["services"]["redis"] = {
                "status": "healthy",
                "response_time_ms": round((time.time() - redis_start) * 1000, 2)
            }
        except Exception as e:
            health_data["services"]["redis"] = {"status": "offline", "error": str(e)}

        # Celery Workers
        try:
            worker_status = heartbeat.get_worker_status()
            if worker_status.get("error"):
                health_data["services"]["celery"] = {
                    "status": "degraded", "workers": 0, "active_tasks": 0,
                    "error": worker_status["error"]
                }
            elif worker_status["total"] > 0:
                health_data["services"]["celery"] = {
                    "status": "healthy", "workers": worker_status["total"],
                    "active_tasks": worker_status["active_tasks"]
                }
            else:
                health_data["services"]["celery"] = {
                    "status": "degraded", "workers": 0, "active_tasks": 0,
                    "error": "No workers available"
                }
        except Exception as e:
            health_data["services"]["celery"] = {"status": "offline", "error": str(e)}

        cache.set("system:health", health_data, ttl_seconds=30)
        return health_data

    # ================================================================
    # BNK Resource Consumption
    # ================================================================

    _BNK_CONSUMPTION_CACHE_KEY = "system:bnk_consumption"
    _BNK_CONSUMPTION_TTL_SECONDS = 60

    def get_bnk_consumption(self) -> dict[str, Any]:
        """
        Aggregate BNK resource consumption across all clusters.

        Combines ``fetch_all_bnk_data`` with pod metrics to produce a
        fleet-wide summary plus a per-cluster breakdown split by control
        plane vs data plane. Results are cached in-memory for 20 seconds.
        """
        cached = cache.get(self._BNK_CONSUMPTION_CACHE_KEY)
        if cached is not None:
            return cached

        from models.kubernetes import KubernetesCluster

        clusters = self.db.query(KubernetesCluster).all()
        k8s_service = KubernetesService(self.db)

        def _collect_cluster(cluster) -> dict[str, Any]:
            bnk_data: dict[str, Any] | None = None
            pod_metrics_response: dict[str, Any] | None = None
            dpf_summary: dict[str, Any] | None = None
            reachable = True

            try:
                # This single call both checks reachability and returns BNK inventory.
                # include_nodes=True so we can fall back to node allocatable capacity
                # when cluster metrics-server is not installed.
                bnk_data = fetch_all_bnk_data(k8s_service, cluster.id, include_nodes=True)
            except Exception as exc:
                logger.warning(f"BNK consumption: cluster {cluster.name} (id={cluster.id}) unreachable: {exc}")
                reachable = False

            if reachable and bnk_data is not None:
                try:
                    pod_metrics_response = k8s_service.get_pod_metrics(cluster.id)
                except Exception as exc:
                    logger.warning(f"BNK consumption: pod metrics failed for cluster {cluster.id}: {exc}")
                    pod_metrics_response = {"available": False, "error": str(exc)}

                try:
                    dpf = detect_dpf(k8s_service, cluster.id)
                    dpf_summary = {
                        "detected": bool(dpf.get("detected")),
                        "dpu_count": int(dpf.get("devices", {}).get("total", 0)),
                    }
                except Exception as exc:
                    logger.warning(f"BNK consumption: DPF detection failed for cluster {cluster.id}: {exc}")
                    dpf_summary = {"detected": False, "dpu_count": 0}

            return aggregate_cluster_consumption(
                cluster_id=cluster.id,
                cluster_name=cluster.name,
                node_count=getattr(cluster, "node_count", None),
                status=cluster.status or "unknown",
                bnk_data=bnk_data,
                pod_metrics_response=pod_metrics_response,
                dpf_summary=dpf_summary,
                reachable=reachable,
                cloud_provider=getattr(cluster, "cloud_provider", None),
                region=getattr(cluster, "region", None),
            )

        # Collect per-cluster data in parallel with a per-cluster timeout.
        # Without timeouts a single unreachable cluster can stall the whole
        # fleet view for minutes (e.g. metrics-server not installed).
        cluster_results: list[dict[str, Any]] = []
        per_cluster_timeout = 30  # seconds
        with ThreadPoolExecutor(max_workers=min(len(clusters) or 1, 8)) as executor:
            futures = {executor.submit(_collect_cluster, c): c for c in clusters}
            for future in futures:
                cluster = futures[future]
                try:
                    cluster_results.append(future.result(timeout=per_cluster_timeout))
                except FuturesTimeoutError:
                    logger.warning(f"BNK consumption: cluster {cluster.name} (id={cluster.id}) timed out after {per_cluster_timeout}s")
                    cluster_results.append(aggregate_cluster_consumption(
                        cluster_id=cluster.id,
                        cluster_name=cluster.name,
                        node_count=getattr(cluster, "node_count", None),
                        status=cluster.status or "unknown",
                        bnk_data=None,
                        pod_metrics_response={"available": False, "error": "Timed out collecting cluster consumption"},
                        dpf_summary={"detected": False, "dpu_count": 0},
                        reachable=False,
                        cloud_provider=getattr(cluster, "cloud_provider", None),
                        region=getattr(cluster, "region", None),
                    ))
                except Exception as exc:
                    logger.warning(f"BNK consumption: cluster {cluster.name} (id={cluster.id}) failed: {exc}")
                    cluster_results.append(aggregate_cluster_consumption(
                        cluster_id=cluster.id,
                        cluster_name=cluster.name,
                        node_count=getattr(cluster, "node_count", None),
                        status=cluster.status or "unknown",
                        bnk_data=None,
                        pod_metrics_response={"available": False, "error": str(exc)},
                        dpf_summary={"detected": False, "dpu_count": 0},
                        reachable=False,
                        cloud_provider=getattr(cluster, "cloud_provider", None),
                        region=getattr(cluster, "region", None),
                    ))

        result = {
            "timestamp": datetime.now(UTC).isoformat(),
            "fleet_summary": aggregate_fleet_summary(cluster_results),
            "clusters": cluster_results,
        }
        cache.set(self._BNK_CONSUMPTION_CACHE_KEY, result, ttl_seconds=self._BNK_CONSUMPTION_TTL_SECONDS)
        return result

    # ================================================================
    # Queue Metrics
    # ================================================================

    def get_queue_metrics(self) -> dict[str, Any]:
        """Get task queue depth and worker metrics (cached 30s)."""
        cached = cache.get("system:queue-metrics")
        if cached:
            return cached

        metrics = {"queues": {}, "workers": {}, "tasks": {}}

        try:
            worker_status = heartbeat.get_worker_status()
            queue_stats = heartbeat.get_queue_stats()
            metrics["queues"] = queue_stats
            metrics["workers"] = {
                "total": worker_status["total"], "active": worker_status["active"],
                "offline": worker_status["offline"], "inspection_timeout": False
            }
            total_pending = sum(q["pending"] for q in queue_stats.values())
            total_active = sum(q["active"] for q in queue_stats.values())
        except Exception as e:
            logger.error(f"Failed to get Celery metrics: {e}")
            metrics["queues"] = {"default": {"pending": 0, "active": 0},
                                 "opentofu": {"pending": 0, "active": 0}}
            metrics["workers"] = {"total": 0, "active": 0, "offline": 0}
            total_pending = 0
            total_active = 0

        one_hour_ago = datetime.now(UTC) - timedelta(hours=1)
        completed_last_hour = self.db.query(Task).filter(
            Task.status == TaskStatus.COMPLETED, Task.completed_at >= one_hour_ago
        ).count()
        queued_in_db = self.db.query(Task).filter(Task.status == TaskStatus.QUEUED).count()

        metrics["tasks"] = {
            "pending": max(total_pending, queued_in_db),
            "active": total_active,
            "completed_last_hour": completed_last_hour
        }

        cache.set("system:queue-metrics", metrics, ttl_seconds=30)
        return metrics

    # ================================================================
    # Performance Metrics
    # ================================================================

    def get_performance_metrics(self) -> dict[str, Any]:
        """Get system performance metrics (cached 30s)."""
        cache_key = "system:performance"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        metrics = {"api": {}, "database": {}, "tasks": {}}
        one_hour_ago = datetime.now(UTC) - timedelta(hours=1)

        avg_result = self.db.query(func.avg(Task.duration_seconds)).filter(
            Task.status == TaskStatus.COMPLETED, Task.completed_at >= one_hour_ago,
            Task.duration_seconds.isnot(None)
        ).scalar()
        avg_duration = float(avg_result) if avg_result else 0

        failed_tasks_count = self.db.query(func.count(Task.id)).filter(
            Task.status == TaskStatus.FAILED, Task.completed_at >= one_hour_ago
        ).scalar() or 0

        total_tasks_last_hour = self.db.query(func.count(Task.id)).filter(
            Task.created_at >= one_hour_ago
        ).scalar() or 0

        longest_running = self.db.query(Task).filter(
            Task.status == TaskStatus.IN_PROGRESS
        ).order_by(Task.started_at.asc()).first()

        metrics["api"] = {
            "avg_task_duration_ms": round(avg_duration * 1000, 2) if avg_duration else 0,
            "avg_task_duration_seconds": round(avg_duration, 2) if avg_duration else 0,
            "tasks_last_hour": total_tasks_last_hour,
            "failed_tasks_last_hour": failed_tasks_count,
            "avg_response_time_ms": round(avg_duration * 1000, 2) if avg_duration else 0,
            "requests_last_hour": total_tasks_last_hour,
            "failed_requests_last_hour": failed_tasks_count
        }

        try:
            result = self.db.execute(text(
                "SELECT pg_database_size(current_database()) as size"
            )).fetchone()
            db_size_mb = round((result[0] if result else 0) / (1024 * 1024), 2)

            result = self.db.execute(text(
                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
            )).fetchone()
            connection_count = result[0] if result else 0

            metrics["database"] = {"size_mb": db_size_mb, "connections": connection_count,
                                   "slow_queries_last_hour": 0}
        except Exception as e:
            logger.error(f"Database metrics failed: {e}")
            metrics["database"] = {"size_mb": 0, "connections": 0, "slow_queries_last_hour": 0}

        if longest_running:
            duration = (datetime.now(UTC) - longest_running.started_at).total_seconds()
            metrics["tasks"] = {
                "avg_duration_seconds": round(avg_duration, 2),
                "longest_running": {
                    "id": longest_running.id,
                    "duration": round(duration, 2),
                    "type": longest_running.task_type
                }
            }
        else:
            metrics["tasks"] = {"avg_duration_seconds": round(avg_duration, 2),
                                "longest_running": None}

        cache.set(cache_key, metrics, ttl_seconds=30)
        return metrics

    # ================================================================
    # Recent Errors
    # ================================================================

    def get_recent_errors(self, limit: int = 10) -> dict[str, Any]:
        """Get recent failed tasks (cached 30s)."""
        if limit > 50:
            limit = 50

        cache_key = f"system:errors:{limit}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        failed_tasks = self.db.query(Task).options(
            joinedload(Task.project).load_only(Project.id, Project.name),
            joinedload(Task.module).joinedload(ProjectModule.library_module).load_only(
                ModuleLibrary.id, ModuleLibrary.name,
            ),
        ).filter(Task.status == TaskStatus.FAILED).order_by(Task.created_at.desc()).limit(limit).all()

        errors_list = []
        for task in failed_tasks:
            errors_list.append({
                "task_id": task.id, "type": task.task_type,
                "error": task.error or "No error message",
                "timestamp": task.created_at.isoformat(),
                "project": task.project.name if task.project else "Unknown",
                "module": (task.module.library_module.name if task.module and task.module.library_module else task.module.path_in_project if task.module else None),
                "exit_code": task.exit_code
            })

        total_failed = self.db.query(Task).filter(Task.status == TaskStatus.FAILED).count()

        result = {"errors": errors_list, "total": total_failed}
        cache.set(cache_key, result, ttl_seconds=30)
        return result

    # ================================================================
    # Database Management
    # ================================================================

    def get_database_stats(self) -> dict[str, Any]:
        """Get detailed database statistics including table sizes."""
        result = self.db.execute(text(
            "SELECT pg_database_size(current_database()) as size"
        )).fetchone()
        total_size_mb = round((result[0] if result else 0) / (1024 * 1024), 2)

        tables = {"tasks": Task, "deployment_logs": DeploymentLog, "audit_logs": AuditLog}
        table_stats = {}
        for table_name, model in tables.items():
            row_count = self.db.query(model).count()
            try:
                result = self.db.execute(
                    text("SELECT pg_total_relation_size(:tbl) as size"),
                    {"tbl": model.__tablename__}
                ).fetchone()
                table_size_mb = round((result[0] if result else 0) / (1024 * 1024), 2)
            except Exception:
                table_size_mb = 0
            table_stats[table_name] = {"rows": row_count, "size_mb": table_size_mb}

        return {"size_mb": total_size_mb, "tables": table_stats}

    def cleanup_database(self, cleanup_type: str, older_than_days: int = 30) -> dict[str, Any]:
        """Cleanup old records from database."""
        if older_than_days < 7:
            raise BadRequestError("Cannot delete records newer than 7 days",
                                  code="CLEANUP_TOO_RECENT")

        cutoff_date = datetime.now(UTC) - timedelta(days=older_than_days)

        result = self.db.execute(text(
            "SELECT pg_database_size(current_database()) as size"
        )).fetchone()
        size_before = result[0] if result else 0

        if cleanup_type == "deployment_logs":
            deleted_count = self.db.query(DeploymentLog).filter(
                DeploymentLog.timestamp < cutoff_date
            ).delete()
        elif cleanup_type == "audit_logs":
            deleted_count = self.db.query(AuditLog).filter(
                AuditLog.timestamp < cutoff_date
            ).delete()
        elif cleanup_type == "completed_tasks":
            deleted_count = self.db.query(Task).filter(
                Task.status.in_([TaskStatus.COMPLETED, TaskStatus.FAILED]),
                Task.completed_at < cutoff_date
            ).delete()
        else:
            raise BadRequestError(f"Invalid cleanup type: {cleanup_type}",
                                  code="INVALID_CLEANUP_TYPE")

        self.db.flush()

        result = self.db.execute(text(
            "SELECT pg_database_size(current_database()) as size"
        )).fetchone()
        size_after = result[0] if result else 0
        freed_mb = round((size_before - size_after) / (1024 * 1024), 2)

        logger.info(f"Cleanup completed: {deleted_count} records deleted, {freed_mb}MB freed")
        return {"deleted": deleted_count, "freed_mb": freed_mb}

    def vacuum_database(self) -> dict[str, Any]:
        """Run VACUUM ANALYZE on the database."""
        start_time = time.time()
        self.db.flush()  # Flush pending changes before switching to AUTOCOMMIT for VACUUM
        raw_connection = self.db.connection().connection
        old_isolation_level = raw_connection.isolation_level

        try:
            raw_connection.set_isolation_level(0)  # AUTOCOMMIT
            cursor = raw_connection.cursor()
            cursor.execute("VACUUM ANALYZE")
            cursor.close()
        except Exception as e:
            logger.warning(f"VACUUM command failed: {e}")
            return {"status": "skipped",
                    "message": "VACUUM operation not permitted or not needed",
                    "duration_seconds": 0}
        finally:
            raw_connection.set_isolation_level(old_isolation_level)

        duration = round(time.time() - start_time, 2)
        logger.info(f"Database vacuum completed in {duration}s")
        return {"status": "success", "duration_seconds": duration}

    # ================================================================
    # Version & Upgrade
    # ================================================================

    def get_system_version(self) -> dict[str, Any]:
        """Get current and available system versions."""
        result = {
            "current_version": "unknown", "latest_version": "unknown",
            "update_available": False, "commits_behind": 0, "error": None
        }

        version_paths = ["/app/VERSION", "/VERSION", "VERSION", "/app/data/VERSION"]
        for version_file in version_paths:
            if os.path.exists(version_file):
                with open(version_file) as f:
                    result["current_version"] = f.read().strip()
                break

        # Get GitHub PAT (optional — used for private repos)
        git_token = ""
        try:
            from core.encryption import decrypt_value_or_none
            pat_setting = self.db.query(ApplicationSetting).filter(
                ApplicationSetting.key == "module_library.git_token"
            ).first()
            if pat_setting and pat_setting.value:
                git_token = pat_setting.value
                if pat_setting.is_encrypted:
                    git_token = decrypt_value_or_none(git_token) or git_token
                if git_token.lower() in ["change_me", "", "your_token_here", "placeholder"]:
                    git_token = ""
        except Exception as e:
            logger.warning(f"Could not load GitHub PAT from settings: {e}")

        # Get update repo URL from DB (configurable via System > Defaults)
        repo_url = DEFAULT_REPO_URL
        try:
            repo_setting = self.db.query(ApplicationSetting).filter(
                ApplicationSetting.key == "system.update_repo_url"
            ).first()
            if repo_setting and repo_setting.value:
                repo_url = repo_setting.value.rstrip("/")
        except Exception as e:
            logger.warning(f"Could not load update repo URL from settings: {e}")

        # Derive raw content URL from repo URL
        # Supports: https://github.com/owner/repo or https://github.com/owner/repo.git
        raw_url = ""
        try:
            clean_url = repo_url.rstrip("/").removesuffix(".git")
            if "github.com" in clean_url:
                # GitHub: https://raw.githubusercontent.com/owner/repo/main/VERSION
                raw_url = clean_url.replace("github.com", "raw.githubusercontent.com") + "/main/VERSION"
            elif "gitswarm" in clean_url or "gitlab" in clean_url:
                # GitLab/GitSwarm: https://host/group/project/-/raw/main/VERSION
                raw_url = clean_url + "/-/raw/main/VERSION"
            else:
                # Fallback: assume GitHub-like raw URL
                raw_url = clean_url.replace("github.com", "raw.githubusercontent.com") + "/main/VERSION"
        except Exception as e:
            logger.warning(f"Could not derive raw URL from {repo_url}: {e}")

        if not raw_url:
            # Silently skip version check if we can't build a URL
            return result

        try:
            headers = {"Authorization": f"token {git_token}"} if git_token else {}
            response = requests.get(raw_url, headers=headers, timeout=10)

            if response.status_code == 200:
                result["latest_version"] = response.text.strip()
                current = result["current_version"]
                latest = result["latest_version"]

                if current != "unknown" and latest != "unknown":
                    try:
                        current_parts = [int(x) for x in current.split('.')]
                        latest_parts = [int(x) for x in latest.split('.')]
                        while len(current_parts) < len(latest_parts):
                            current_parts.append(0)
                        while len(latest_parts) < len(current_parts):
                            latest_parts.append(0)
                        result["update_available"] = latest_parts > current_parts
                        if result["update_available"]:
                            major_diff = latest_parts[0] - current_parts[0]
                            minor_diff = latest_parts[1] - current_parts[1] if len(latest_parts) > 1 else 0
                            patch_diff = latest_parts[2] - current_parts[2] if len(latest_parts) > 2 else 0
                            result["commits_behind"] = max(1, major_diff * 100 + minor_diff * 10 + patch_diff)
                    except (ValueError, IndexError):
                        result["update_available"] = current != latest
                        if result["update_available"]:
                            result["commits_behind"] = 1
            elif response.status_code == 404:
                # Silently handle — repo may be private or not yet created
                logger.info(f"Version check returned 404 for {raw_url} — repo may be private or inaccessible")
            elif response.status_code == 403:
                # Rate limit or auth issue — only warn, don't show error to user
                logger.info(f"Version check returned 403 for {raw_url} — rate limited or auth required")
            else:
                logger.info(f"Version check returned {response.status_code} for {raw_url}")
        except requests.exceptions.Timeout:
            logger.info("Version check timed out — network may be unavailable")
        except requests.exceptions.ConnectionError:
            # Graceful handling for local/offline mode
            logger.info("Version check failed — no network connectivity (local mode?)")
        except requests.exceptions.RequestException as e:
            logger.info(f"Version check failed: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error during version check: {e}")

        return result

    # ================================================================
    # Upgrade Lock — prevent deployments while system is upgrading
    # ================================================================

    _upgrade_in_progress = False

    @classmethod
    def is_upgrade_in_progress(cls) -> bool:
        """Check if a system upgrade is currently running."""
        return cls._upgrade_in_progress

    # ---- Upgrade state persistence (UP-003) ----

    _UPGRADE_STATE_KEY = "system.upgrade_state"

    @staticmethod
    def _save_upgrade_state_standalone(state: dict[str, Any]) -> None:
        """Persist upgrade state from the background thread (creates its own DB session).

        The upgrade runs in a daemon thread that outlives the request-scoped session,
        so we open a short-lived session, write, commit, close.  Best-effort — never
        lets a DB error interrupt the upgrade.
        """
        import json as _json
        try:
            from database import SessionLocal
            db = SessionLocal()
            try:
                setting = db.query(ApplicationSetting).filter(
                    ApplicationSetting.key == SystemService._UPGRADE_STATE_KEY
                ).first()
                value = _json.dumps(state)
                if setting:
                    setting.value = value
                    setting.updated_at = datetime.now(UTC)
                else:
                    db.add(ApplicationSetting(
                        key=SystemService._UPGRADE_STATE_KEY,
                        value=value,
                        value_type="json",
                        category="system",
                        description="Last system upgrade state (persisted across restarts)",
                    ))
                db.commit()
            except Exception as e:
                logger.warning(f"Failed to save upgrade state: {e}")
                try:
                    db.rollback()
                except Exception:
                    pass
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"Failed to create DB session for upgrade state: {e}")

    def _save_upgrade_state(self, state: dict[str, Any]) -> None:
        """Persist upgrade state using the request-scoped session (for pre-thread calls)."""
        import json as _json
        try:
            setting = self.db.query(ApplicationSetting).filter(
                ApplicationSetting.key == self._UPGRADE_STATE_KEY
            ).first()
            value = _json.dumps(state)
            if setting:
                setting.value = value
                setting.updated_at = datetime.now(UTC)
            else:
                self.db.add(ApplicationSetting(
                    key=self._UPGRADE_STATE_KEY,
                    value=value,
                    value_type="json",
                    category="system",
                    description="Last system upgrade state (persisted across restarts)",
                ))
            self.db.commit()
        except Exception as e:
            logger.warning(f"Failed to save upgrade state: {e}")
            try:
                self.db.rollback()
            except Exception:
                pass

    def get_upgrade_state(self) -> dict[str, Any] | None:
        """Read the persisted upgrade state from DB.

        Returns a dict with keys: status, old_version, new_version,
        started_at, completed_at, current_phase, phase_label, log (list of lines).
        """
        import json as _json
        try:
            setting = self.db.query(ApplicationSetting).filter(
                ApplicationSetting.key == self._UPGRADE_STATE_KEY
            ).first()
            if setting and setting.value:
                return _json.loads(setting.value)
        except Exception as e:
            logger.warning(f"Failed to read upgrade state: {e}")
        return None

    def get_upgrade_readiness(self) -> dict[str, Any]:
        """Pre-flight check: can the GUI trigger an upgrade?"""
        host_repo_path = os.environ.get("HOST_REPO_PATH", "")
        docker_sock = os.path.exists("/var/run/docker.sock")
        deployment_mode = "local" if os.environ.get("BNK_FORGE_DEPLOY_MODE") == "local" else "server"
        if deployment_mode == "local":
            recommended_command = "make local-deploy"
            recommended_label = "Local redeploy"
        else:
            recommended_command = "make deploy"
            recommended_label = "Server deploy"
        return {
            "host_repo_path_set": bool(host_repo_path),
            "docker_socket_available": docker_sock,
            "upgrade_ready": bool(host_repo_path) and docker_sock,
            "upgrade_in_progress": self._upgrade_in_progress,
            "deployment_mode": deployment_mode,
            "recommended_command": recommended_command,
            "recommended_label": recommended_label,
            "gui_upgrade_supported": bool(host_repo_path) and docker_sock,
        }

    def verify_post_upgrade(self) -> dict[str, Any]:
        """Post-upgrade verification (UP-011).

        Checks all services, verifies version changed, checks migration status.
        Returns overall verdict: 'healthy', 'degraded', or 'unhealthy'.
        """
        checks: dict[str, dict[str, Any]] = {}

        # 1. Version verification
        upgrade_state = self.get_upgrade_state()
        version_paths = ["/app/VERSION", "/VERSION", "VERSION", "/app/data/VERSION"]
        current_version = "unknown"
        for vp in version_paths:
            if os.path.exists(vp):
                with open(vp) as f:
                    current_version = f.read().strip()
                break

        expected_version = upgrade_state.get("new_version") if upgrade_state else None
        if expected_version and current_version == expected_version:
            checks["version"] = {"status": "pass", "current": current_version, "expected": expected_version}
        elif expected_version:
            checks["version"] = {"status": "fail", "current": current_version, "expected": expected_version,
                                 "error": f"Expected {expected_version}, got {current_version}"}
        else:
            checks["version"] = {"status": "pass", "current": current_version, "note": "No expected version to compare"}

        # 2. Database health
        try:
            self.db.execute(text("SELECT 1"))
            checks["database"] = {"status": "pass"}
        except Exception as e:
            checks["database"] = {"status": "fail", "error": str(e)}

        # 3. Redis health
        try:
            celery_app.backend.client.ping()
            checks["redis"] = {"status": "pass"}
        except Exception as e:
            checks["redis"] = {"status": "fail", "error": str(e)}

        # 4. Celery workers
        try:
            worker_status = heartbeat.get_worker_status()
            if worker_status.get("error"):
                checks["celery"] = {"status": "warn", "error": worker_status["error"],
                                    "note": "Workers may still be starting after upgrade"}
            elif worker_status["total"] > 0:
                checks["celery"] = {"status": "pass", "workers": worker_status["total"]}
            else:
                checks["celery"] = {"status": "warn", "workers": 0,
                                    "note": "No workers yet — may still be starting"}
        except Exception as e:
            checks["celery"] = {"status": "warn", "error": str(e),
                                "note": "Workers may still be starting after upgrade"}

        # 5. Alembic migration status
        try:
            from alembic.config import Config as AlembicConfig
            from alembic.runtime.migration import MigrationContext
            from alembic.script import ScriptDirectory

            alembic_cfg = AlembicConfig("/app/alembic.ini")
            script = ScriptDirectory.from_config(alembic_cfg)
            context = MigrationContext.configure(self.db.connection())
            current_rev = context.get_current_revision()
            head_rev = script.get_current_head()

            if current_rev == head_rev:
                checks["migrations"] = {"status": "pass", "current_revision": current_rev}
            else:
                checks["migrations"] = {"status": "warn", "current_revision": current_rev,
                                        "head_revision": head_rev,
                                        "note": "Database may have pending migrations"}
        except Exception as e:
            # Alembic not available in this context — not critical
            checks["migrations"] = {"status": "skip", "note": f"Could not check: {e}"}

        # Overall verdict
        statuses = [c["status"] for c in checks.values()]
        if all(s == "pass" for s in statuses):
            verdict = "healthy"
        elif any(s == "fail" for s in statuses):
            verdict = "unhealthy"
        else:
            verdict = "degraded"

        return {
            "verdict": verdict,
            "checks": checks,
            "timestamp": datetime.now(UTC).isoformat() + "Z",
        }

    def trigger_upgrade(self) -> dict[str, Any]:
        """Trigger a system upgrade via upgrade.sh with live output streaming."""
        # Pre-flight checks
        readiness = self.get_upgrade_readiness()
        if not readiness["upgrade_ready"]:
            missing = []
            if not readiness["host_repo_path_set"]:
                missing.append("HOST_REPO_PATH not set in docker-compose.yml")
            if not readiness["docker_socket_available"]:
                missing.append("Docker socket not mounted (see docker-compose.override.example.yml)")
            return {
                "status": "not_configured",
                "message": f"GUI upgrade not available: {'; '.join(missing)}",
                "readiness": readiness,
            }

        if self._upgrade_in_progress:
            return {
                "status": "already_upgrading",
                "message": "An upgrade is already in progress",
            }

        version_info = self.get_system_version()
        if not version_info.get("update_available"):
            return {
                "status": "no_update",
                "message": "System is already at the latest version",
                "current_version": version_info.get("current_version")
            }

        # ---- UP-010: Pre-upgrade safety checks ----

        # 1. Check for active deployments (in-progress or queued tasks)
        active_tasks = self.db.query(Task).filter(
            Task.status.in_([TaskStatus.IN_PROGRESS, TaskStatus.QUEUED])
        ).count()
        if active_tasks > 0:
            return {
                "status": "blocked",
                "message": f"Cannot upgrade: {active_tasks} deployment(s) in progress or queued. "
                           f"Wait for them to complete or cancel them first.",
                "active_tasks": active_tasks,
            }

        # 2. Verify Docker daemon is responsive
        try:
            docker_check = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True, text=True, timeout=10
            )
            if docker_check.returncode != 0:
                return {
                    "status": "docker_error",
                    "message": "Docker daemon is not responding. Cannot start upgrade.",
                    "error": docker_check.stderr.strip() or "docker info failed",
                }
        except subprocess.TimeoutExpired:
            return {
                "status": "docker_error",
                "message": "Docker daemon timed out. Cannot start upgrade.",
            }
        except FileNotFoundError:
            return {
                "status": "docker_error",
                "message": "Docker CLI not found. Cannot start upgrade.",
            }

        # 3. Record current git commit SHA for rollback reference
        pre_upgrade_commit = None
        try:
            git_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=5,
                cwd=os.environ.get("HOST_REPO_PATH", "/app")
            )
            if git_result.returncode == 0:
                pre_upgrade_commit = git_result.stdout.strip()
        except Exception:
            pass  # Not critical — upgrade.sh also records commit

        old_version = version_info.get("current_version", "unknown")
        new_version = version_info.get("latest_version", "unknown")
        logger.info(f"Starting system upgrade: {old_version} -> {new_version}"
                     f" (pre-commit: {pre_upgrade_commit or 'unknown'})")

        # Persist initial upgrade state BEFORE spawning thread (uses request-scoped session)
        upgrade_state: dict[str, Any] = {
            "status": "in_progress",
            "old_version": old_version,
            "new_version": new_version,
            "started_at": datetime.now(UTC).isoformat() + "Z",
            "completed_at": None,
            "current_phase": "start",
            "phase_label": "Initializing upgrade",
            "pre_upgrade_commit": pre_upgrade_commit,
            "log": [],
        }
        self._save_upgrade_state(upgrade_state)

        # Phase labels for human-readable progress messages
        _phase_labels = {
            "start": "Initializing upgrade",
            "pull": "Pulling latest code",
            "build": "Building containers",
            "restart": "Restarting services",
            "migrate": "Running database migrations",
            "verify": "Verifying health",
            "complete": "Upgrade complete",
            "failed": "Upgrade failed",
        }

        def _publish_upgrade_msg(line: str, level: str = "info", phase: str | None = None):
            """Publish an upgrade message to WebSocket via Redis."""
            try:
                from services.websocket_service import TASK_UPDATES_CHANNEL, get_sync_redis_client
                redis_client = get_sync_redis_client()
                import json as _json
                msg: dict[str, Any] = {
                    "type": "system_upgrade",
                    "level": level,
                    "line": line,
                    "timestamp": datetime.now(UTC).isoformat() + "Z",
                }
                if phase:
                    msg["phase"] = phase
                    msg["phase_label"] = _phase_labels.get(phase, phase)
                redis_client.publish(TASK_UPDATES_CHANNEL, _json.dumps(msg))
            except Exception:
                pass  # Don't break upgrade if Redis publish fails

        # Exit code descriptions from upgrade.sh
        _exit_code_msgs = {
            1: "Docker Compose not found on the system",
            2: "Docker image build failed — no changes applied, system still on old version",
            3: "Failed to restart services — containers may be in a bad state",
            4: "Database migration failed — services running but DB may be inconsistent",
            5: "Services did not become healthy within timeout",
        }

        # Keep limited log buffer for DB persistence (avoid unbounded growth)
        _MAX_LOG_LINES = 200

        def _persist_phase(phase: str, status: str = "in_progress",
                           log_lines: list[str] | None = None) -> None:
            """Helper to persist upgrade phase to DB from the background thread."""
            label = _phase_labels.get(phase, phase)
            state: dict[str, Any] = {
                "status": status,
                "old_version": old_version,
                "new_version": new_version,
                "started_at": upgrade_state["started_at"],
                "completed_at": datetime.now(UTC).isoformat() + "Z" if status in ("completed", "failed") else None,
                "current_phase": phase,
                "phase_label": label,
                "log": (log_lines or [])[-_MAX_LOG_LINES:],
            }
            SystemService._save_upgrade_state_standalone(state)

        def run_upgrade():
            current_phase = "start"
            log_lines: list[str] = []
            try:
                SystemService._upgrade_in_progress = True
                _publish_upgrade_msg(
                    f"Starting upgrade: {old_version} -> {new_version}",
                    "info", phase="start"
                )
                log_lines.append(f"Starting upgrade: {old_version} -> {new_version}")

                host_repo_path = os.environ.get("HOST_REPO_PATH", "")
                host_user_home = os.environ.get(
                    "HOST_USER_HOME",
                    f"/home/{host_repo_path.split('/')[2] if len(host_repo_path.split('/')) > 2 else 'root'}"
                )

                _publish_upgrade_msg("Launching upgrade container...", "info")
                log_lines.append("Launching upgrade container...")

                docker_cmd = [
                    "docker", "run", "--rm",
                    "-v", f"{host_repo_path}:/repo",
                    "-v", "/var/run/docker.sock:/var/run/docker.sock",
                    "-v", f"{host_user_home}/.gitconfig:/root/.gitconfig:ro",
                    "-w", "/repo", "--network", "host",
                    "docker:cli", "sh", "-c",
                    "apk add --no-cache bash git && bash upgrade.sh"
                ]
                process = subprocess.Popen(docker_cmd, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, text=True)
                try:
                    for line in iter(process.stdout.readline, ''):
                        if not line:
                            break
                        stripped = line.rstrip()

                        # Parse ##PHASE:xxx markers from upgrade.sh
                        if stripped.startswith("##PHASE:"):
                            current_phase = stripped[8:]  # e.g. "build", "restart", "failed"
                            level = "error" if current_phase == "failed" else "info"
                            label = _phase_labels.get(current_phase, current_phase)
                            logger.info(f"[upgrade] Phase: {current_phase} ({label})")
                            _publish_upgrade_msg(label, level, phase=current_phase)
                            log_lines.append(f"[PHASE] {label}")
                            # Persist phase transition to DB
                            _persist_phase(current_phase, log_lines=log_lines)
                        else:
                            logger.info(f"[upgrade] {stripped}")
                            # Detect error-level lines from the script output
                            level = "info"
                            if stripped.startswith("ERROR:") or stripped.startswith("  ERROR:"):
                                level = "error"
                            elif stripped.startswith("WARNING:") or stripped.startswith("  WARNING:"):
                                level = "warning"
                            _publish_upgrade_msg(stripped, level)
                            log_lines.append(stripped)
                except Exception:
                    pass

                exit_code = process.wait()
                if exit_code != 0:
                    error_msg = _exit_code_msgs.get(exit_code, f"Unknown error (exit code {exit_code})")
                    logger.error(f"Upgrade failed with exit code {exit_code}: {error_msg}")
                    _publish_upgrade_msg(
                        f"Upgrade failed: {error_msg}",
                        "error", phase="failed"
                    )
                    log_lines.append(f"ERROR: {error_msg}")
                    _persist_phase("failed", status="failed", log_lines=log_lines)
                else:
                    _publish_upgrade_msg(
                        f"Upgrade complete: {old_version} -> {new_version}",
                        "success", phase="complete"
                    )
                    log_lines.append(f"Upgrade complete: {old_version} -> {new_version}")
                    _persist_phase("complete", status="completed", log_lines=log_lines)
            except Exception as e:
                logger.error(f"Upgrade failed: {e}")
                _publish_upgrade_msg(f"Upgrade failed: {e}", "error", phase="failed")
                log_lines.append(f"ERROR: {e}")
                _persist_phase("failed", status="failed", log_lines=log_lines)
            finally:
                SystemService._upgrade_in_progress = False

        upgrade_thread = threading.Thread(target=run_upgrade, daemon=True)
        upgrade_thread.start()

        return {
            "status": "upgrading",
            "message": f"Upgrade started: {old_version} -> {new_version}. Services will restart.",
            "old_version": old_version, "new_version": new_version,
            "note": "The backend will restart. Watch the output log below for progress."
        }
