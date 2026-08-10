"""
Drift Detection Service — DB and business logic for drift checks.

Extracted from routes/drift.py to separate HTTP handling from domain logic.

Covers:
- Drift settings CRUD (get, update, enable, disable)
- Drift check history and details
- Drift check triggering (project-wide and per-module)
- Drift summary/stats (global, per-project, per-cluster)
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import joinedload

from core.cache import cache
from core.errors import BadRequestError, NotFoundError, ServiceError
from models import DriftCheck, DriftSettings, ModuleLibrary, ProjectModule
from services.base_service import BaseService
from services.reachability import with_breaker

logger = logging.getLogger(__name__)


class DriftService(BaseService):
    """Service layer for drift detection operations."""

    # ================================================================
    # Drift Settings
    # ================================================================

    def get_settings(self, project_id: int) -> DriftSettings:
        """Get drift settings for a project, creating defaults if none exist."""
        self._get_project(project_id)

        settings = self.db.query(DriftSettings).filter(
            DriftSettings.project_id == project_id
        ).first()

        if not settings:
            settings = DriftSettings(
                project_id=project_id,
                enabled=False,
                schedule_type="interval",
                schedule_value="300",
                check_all_modules=True,
                notify_on_drift=True,
                notification_channels=["toast"],
                ignore_insignificant_changes=False,
            )
            self.db.add(settings)
            self.db.flush()

        return settings

    def update_settings(self, project_id: int, settings_data: dict) -> DriftSettings:
        """Update drift settings for a project."""
        self._get_project(project_id)

        settings = self.db.query(DriftSettings).filter(
            DriftSettings.project_id == project_id
        ).first()

        if not settings:
            settings = DriftSettings(project_id=project_id)
            self.db.add(settings)

        settings.enabled = settings_data.get("enabled", False)
        settings.schedule_type = settings_data.get("schedule_type", "cron")
        settings.schedule_value = settings_data.get("schedule_value", "0 2 * * *")
        settings.check_all_modules = settings_data.get("check_all_modules", True)
        settings.module_ids = settings_data.get("module_ids")
        settings.notify_on_drift = settings_data.get("notify_on_drift", True)
        settings.notification_channels = settings_data.get("notification_channels")
        settings.notification_config = settings_data.get("notification_config")
        settings.ignore_insignificant_changes = settings_data.get("ignore_insignificant_changes", False)
        settings.ignore_patterns = settings_data.get("ignore_patterns")
        settings.updated_at = datetime.now(UTC)

        self.db.flush()
        return settings

    def enable_drift(self, project_id: int) -> DriftSettings:
        """Enable drift detection for a project."""
        self._get_project(project_id)

        settings = self.db.query(DriftSettings).filter(
            DriftSettings.project_id == project_id
        ).first()

        if not settings:
            settings = DriftSettings(
                project_id=project_id,
                enabled=True,
                schedule_type="interval",
                schedule_value="300",
                check_all_modules=True,
                notify_on_drift=True,
            )
            self.db.add(settings)
        else:
            settings.enabled = True
            settings.updated_at = datetime.now(UTC)

        self.db.flush()
        return settings

    def disable_drift(self, project_id: int) -> DriftSettings:
        """Disable drift detection for a project."""
        self._get_project(project_id)

        settings = self.db.query(DriftSettings).filter(
            DriftSettings.project_id == project_id
        ).first()

        if not settings:
            # Create default disabled settings if none exist
            settings = DriftSettings(
                project_id=project_id,
                enabled=False,
                schedule_type="interval",
                schedule_value="300",
                check_all_modules=True,
                notify_on_drift=True,
            )
            self.db.add(settings)
        else:
            settings.enabled = False
            settings.updated_at = datetime.now(UTC)

        self.db.flush()
        return settings

    # ================================================================
    # Drift Checks
    # ================================================================

    def get_checks(
        self,
        project_id: int,
        module_id: int | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Get drift check history for a project."""
        self._get_project(project_id)

        query = (
            self.db.query(DriftCheck)
            .options(joinedload(DriftCheck.module).joinedload(ProjectModule.library_module).load_only(ModuleLibrary.id, ModuleLibrary.name))
            .filter(DriftCheck.project_id == project_id)
        )

        if module_id:
            query = query.filter(DriftCheck.module_id == module_id)
        if status:
            query = query.filter(DriftCheck.status == status)

        checks = query.order_by(DriftCheck.created_at.desc()).limit(limit).offset(offset).all()

        return [self._serialize_check(c) for c in checks]

    def get_check_details(self, check_id: int) -> dict:
        """Get detailed information about a specific drift check."""
        check = (
            self.db.query(DriftCheck)
            .options(joinedload(DriftCheck.module).joinedload(ProjectModule.library_module).load_only(ModuleLibrary.id, ModuleLibrary.name))
            .filter(DriftCheck.id == check_id)
            .first()
        )
        if not check:
            raise NotFoundError("drift_check", check_id)
        return self._serialize_check(check)

    def trigger_project_check(self, project_id: int, module_ids: list[int] | None = None) -> dict:
        """Trigger drift checks for project modules."""
        from tasks.drift_tasks import run_drift_check

        project = self._get_project(project_id)

        if module_ids:
            modules = (
                self.db.query(ProjectModule)
                .options(joinedload(ProjectModule.library_module).load_only(ModuleLibrary.id, ModuleLibrary.name))
                .filter(
                    ProjectModule.id.in_(module_ids),
                    ProjectModule.project_id == project_id,
                    ProjectModule.status.in_(["initialized", "planned", "applied"]),
                )
                .all()
            )
        else:
            modules = (
                self.db.query(ProjectModule)
                .options(joinedload(ProjectModule.library_module).load_only(ModuleLibrary.id, ModuleLibrary.name))
                .filter(
                    ProjectModule.project_id == project_id,
                    ProjectModule.status.in_(["initialized", "planned", "applied"]),
                )
                .all()
            )

        if not modules:
            raise BadRequestError("No deployed modules found to check", code="NO_DEPLOYED_MODULES")

        drift_checks = []
        for module in modules:
            drift_check = DriftCheck(
                project_id=project.id,
                module_id=module.id,
                schedule_enabled=False,
                status="scheduled",
            )
            self.db.add(drift_check)
            self.db.commit()
            self.db.refresh(drift_check)

            try:
                run_drift_check.delay(drift_check.id, module.id)
                drift_checks.append({
                    "check_id": drift_check.id,
                    "module_id": module.id,
                    "module_name": module.library_module.name,
                })
            except Exception as e:
                drift_check.status = "failed"
                drift_check.error_message = f"Failed to queue task: {str(e)}"
                self.db.commit()
                raise ServiceError("celery", f"Failed to queue drift check: {str(e)}")

        return {
            "message": f"Triggered drift check for {len(drift_checks)} module(s)",
            "drift_checks": drift_checks,
        }

    def trigger_module_check(self, module_id: int) -> dict:
        """Trigger a drift check for a specific module."""
        from tasks.drift_tasks import run_drift_check

        module = (
            self.db.query(ProjectModule)
            .options(joinedload(ProjectModule.library_module).load_only(ModuleLibrary.id, ModuleLibrary.name))
            .filter(ProjectModule.id == module_id)
            .first()
        )
        if not module:
            raise NotFoundError("module", module_id)

        if module.status not in ["initialized", "planned", "applied"]:
            raise BadRequestError(
                f"Cannot check drift for module with status '{module.status}'. "
                "Module must be initialized, planned, or applied.",
                code="INVALID_MODULE_STATUS",
            )

        drift_check = DriftCheck(
            project_id=module.project_id,
            module_id=module.id,
            schedule_enabled=False,
            status="scheduled",
        )
        self.db.add(drift_check)
        self.db.commit()
        self.db.refresh(drift_check)

        try:
            run_drift_check.delay(drift_check.id, module.id)
        except Exception as e:
            drift_check.status = "failed"
            drift_check.error_message = f"Failed to queue task: {str(e)}"
            self.db.commit()
            raise ServiceError("celery", f"Failed to queue drift check: {str(e)}")

        return {
            "message": "Drift check triggered",
            "drift_check_id": drift_check.id,
            "module_id": module.id,
            "module_name": module.library_module.name,
        }

    # ================================================================
    # Summaries & Stats
    # ================================================================

    def get_global_summary(self) -> dict:
        """Get global drift summary across all projects."""
        total_checks = self.db.query(DriftCheck).count()
        drift_detected = self.db.query(DriftCheck).filter(DriftCheck.drift_detected).count()
        no_drift = self.db.query(DriftCheck).filter(
            not DriftCheck.drift_detected, DriftCheck.status == "completed"
        ).count()
        failed = self.db.query(DriftCheck).filter(DriftCheck.status == "failed").count()

        last_check = self.db.query(DriftCheck).order_by(DriftCheck.last_check_at.desc()).first()
        last_check_at = last_check.last_check_at if last_check else None

        projects_with_drift = (
            self.db.query(func.count(func.distinct(DriftCheck.project_id)))
            .filter(DriftCheck.drift_detected)
            .scalar()
        )
        modules_with_drift = (
            self.db.query(func.count(func.distinct(DriftCheck.module_id)))
            .filter(DriftCheck.drift_detected, DriftCheck.module_id.isnot(None))
            .scalar()
        )

        return {
            "total_checks": total_checks,
            "drift_detected_count": drift_detected,
            "no_drift_count": no_drift,
            "failed_count": failed,
            "last_check_at": last_check_at,
            "projects_with_drift": projects_with_drift,
            "modules_with_drift": modules_with_drift,
        }

    def get_project_summary(self, project_id: int) -> dict:
        """Get drift summary for a specific project."""
        self._get_project(project_id)

        total_checks = self.db.query(DriftCheck).filter(DriftCheck.project_id == project_id).count()
        drift_detected = self.db.query(DriftCheck).filter(
            DriftCheck.project_id == project_id, DriftCheck.drift_detected
        ).count()
        no_drift = self.db.query(DriftCheck).filter(
            DriftCheck.project_id == project_id,
            not DriftCheck.drift_detected,
            DriftCheck.status == "completed",
        ).count()
        failed = self.db.query(DriftCheck).filter(
            DriftCheck.project_id == project_id, DriftCheck.status == "failed"
        ).count()

        last_check = (
            self.db.query(DriftCheck)
            .filter(DriftCheck.project_id == project_id)
            .order_by(DriftCheck.last_check_at.desc())
            .first()
        )
        last_check_at = last_check.last_check_at if last_check else None

        modules_with_drift = (
            self.db.query(func.count(func.distinct(DriftCheck.module_id)))
            .filter(
                DriftCheck.project_id == project_id,
                DriftCheck.drift_detected,
                DriftCheck.module_id.isnot(None),
            )
            .scalar()
        )

        return {
            "total_checks": total_checks,
            "drift_detected_count": drift_detected,
            "no_drift_count": no_drift,
            "failed_count": failed,
            "last_check_at": last_check_at,
            "projects_with_drift": 1 if drift_detected > 0 else 0,
            "modules_with_drift": modules_with_drift,
        }

    def get_stats(self, project_id: int | None = None, days: int | None = None) -> dict:
        """Get drift statistics (cached 2 min), optionally filtered by project and time range."""
        effective_days = days or 7
        cache_key = f"drift:stats:{project_id or 'all'}:{effective_days}"
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            return cached_result

        enabled_query = self.db.query(DriftSettings).filter(DriftSettings.enabled)
        if project_id:
            enabled_query = enabled_query.filter(DriftSettings.project_id == project_id)
        enabled_projects = enabled_query.count()

        cutoff = datetime.now(UTC) - timedelta(days=effective_days)
        checks_query = self.db.query(DriftCheck).filter(DriftCheck.created_at >= cutoff)
        if project_id:
            checks_query = checks_query.filter(DriftCheck.project_id == project_id)
        recent_checks = checks_query.count()

        drift_query = self.db.query(DriftCheck).filter(
            DriftCheck.created_at >= cutoff, DriftCheck.drift_detected
        )
        if project_id:
            drift_query = drift_query.filter(DriftCheck.project_id == project_id)
        recent_drift = drift_query.count()

        result = {
            "enabled_projects": enabled_projects,
            "recent_checks_7d": recent_checks,
            "recent_drift_7d": recent_drift,
            "drift_rate_7d": (recent_drift / recent_checks * 100) if recent_checks > 0 else 0,
        }

        cache.set(cache_key, result, ttl_seconds=120)
        return result

    @with_breaker("cluster", target_id_arg="cluster_id")
    def get_cluster_drift_status(self, cluster_id: int) -> dict:
        """Get drift status for all modules deployed to a cluster's project."""
        from models import KubernetesCluster

        cluster = self.db.query(KubernetesCluster).filter(
            KubernetesCluster.id == cluster_id
        ).first()
        if not cluster:
            raise NotFoundError("cluster", cluster_id)

        project_id = cluster.project_id

        modules = (
            self.db.query(ProjectModule)
            .options(joinedload(ProjectModule.library_module).load_only(ModuleLibrary.id, ModuleLibrary.name))
            .filter(
                ProjectModule.project_id == project_id,
                ProjectModule.status.in_(["applied", "initialized", "planned"]),
            )
            .all()
        )

        if not modules:
            return {
                "cluster_id": cluster_id,
                "project_id": project_id,
                "drift_enabled": False,
                "total_modules": 0,
                "modules_with_drift": 0,
                "modules_ok": 0,
                "modules_unchecked": 0,
                "overall_status": "unchecked",
                "module_statuses": [],
            }

        settings = self.db.query(DriftSettings).filter(
            DriftSettings.project_id == project_id
        ).first()
        drift_enabled = settings.enabled if settings else False

        module_statuses = []
        drift_count = 0
        ok_count = 0
        unchecked_count = 0

        for module in modules:
            latest_check = (
                self.db.query(DriftCheck)
                .filter(
                    DriftCheck.project_id == project_id,
                    DriftCheck.module_id == module.id,
                    DriftCheck.status == "completed",
                )
                .order_by(DriftCheck.created_at.desc())
                .first()
            )

            engine_type = self._detect_engine_type(module)

            if latest_check:
                if latest_check.drift_detected:
                    drift_count += 1
                    status = "drift"
                else:
                    ok_count += 1
                    status = "ok"

                module_statuses.append({
                    "module_id": module.id,
                    "module_name": module.library_module.name if module.library_module else module.path_in_project,
                    "module_path": module.path_in_project,
                    "engine_type": engine_type,
                    "status": status,
                    "drift_detected": latest_check.drift_detected,
                    "drift_summary": latest_check.drift_summary,
                    "drift_details": latest_check.drift_details,
                    "last_check_at": latest_check.last_check_at.isoformat() if latest_check.last_check_at else None,
                    "check_id": latest_check.id,
                })
            else:
                unchecked_count += 1
                module_statuses.append({
                    "module_id": module.id,
                    "module_name": module.library_module.name if module.library_module else module.path_in_project,
                    "module_path": module.path_in_project,
                    "engine_type": engine_type,
                    "status": "unchecked",
                    "drift_detected": False,
                    "drift_summary": None,
                    "drift_details": None,
                    "last_check_at": None,
                    "check_id": None,
                })

        if drift_count > 0:
            overall_status = "drift_detected"
        elif unchecked_count == len(modules):
            overall_status = "unchecked"
        elif ok_count == len(modules):
            overall_status = "ok"
        else:
            overall_status = "partial"

        return {
            "cluster_id": cluster_id,
            "project_id": project_id,
            "drift_enabled": drift_enabled,
            "total_modules": len(modules),
            "modules_with_drift": drift_count,
            "modules_ok": ok_count,
            "modules_unchecked": unchecked_count,
            "overall_status": overall_status,
            "module_statuses": module_statuses,
        }

    def get_recent_drifted(self, limit: int = 20) -> list[dict]:
        """Get recent drift checks where drift was detected, across all projects.

        Returns the latest completed checks with drift_detected=True,
        enriched with project name and module name for dashboard display.
        """
        from sqlalchemy.orm import joinedload as jl
        from sqlalchemy.orm import undefer

        checks = (
            self.db.query(DriftCheck)
            .options(
                undefer(DriftCheck.drift_details),
                jl(DriftCheck.module).joinedload(ProjectModule.library_module),
                jl(DriftCheck.module).joinedload(ProjectModule.project),
            )
            .filter(
                DriftCheck.drift_detected,
                DriftCheck.status == "completed",
            )
            .order_by(DriftCheck.last_check_at.desc())
            .limit(limit)
            .all()
        )

        results = []
        for check in checks:
            module = check.module
            project = module.project if module else None
            results.append({
                "id": check.id,
                "project_id": check.project_id,
                "project_name": project.name if project else "Unknown",
                "module_id": check.module_id,
                "module_name": (
                    module.library_module.name
                    if module and module.library_module
                    else module.path_in_project if module else "Unknown"
                ),
                "drift_summary": check.drift_summary,
                "drift_details": check.drift_details,
                "last_check_at": check.last_check_at.isoformat() if check.last_check_at else None,
                "resource_changes": check.drift_details.get("resource_changes") if check.drift_details else None,
                "changed_resources": check.drift_details.get("changed_resources", []) if check.drift_details else [],
            })
        return results

    # ================================================================
    # Private Helpers
    # ================================================================

    def _serialize_check(self, check: DriftCheck) -> dict:
        """Serialize a DriftCheck to response dict."""
        return {
            "id": check.id,
            "project_id": check.project_id,
            "module_id": check.module_id,
            "module_name": check.module.library_module.name if check.module else None,
            "schedule_enabled": check.schedule_enabled,
            "schedule_cron": check.schedule_cron,
            "last_check_at": check.last_check_at,
            "next_check_at": check.next_check_at,
            "drift_detected": check.drift_detected,
            "drift_summary": check.drift_summary,
            "drift_details": check.drift_details,
            "task_id": check.task_id,
            "status": check.status,
            "error_message": check.error_message,
            "created_at": check.created_at,
            "updated_at": check.updated_at,
        }

    def _detect_engine_type(self, module: ProjectModule) -> str:
        """Detect engine type for a module (kubernetes vs opentofu)."""
        lib_module = getattr(module, "library_module", None)
        if lib_module is None:
            return "opentofu"

        execution_engine = getattr(lib_module, "execution_engine", None)
        if isinstance(execution_engine, str) and execution_engine.strip().lower() in {"kubernetes-direct", "operator"}:
            return "kubernetes"

        return "opentofu"
