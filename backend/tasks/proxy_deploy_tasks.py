"""
Celery tasks for Proxy Deployment (Phase 4c).

Async deploy/undeploy of reverse proxies (envoy, nginx, haproxy, f5-bnk)
to benchmark target clusters via Helm.

Phase 1.6: Lock acquired in the task entrypoint (matches Phase 1.5 pattern
from ssh_tasks.py). The service receives ``lock`` as a keyword arg and routes
all status writes through set_locked_entity_fields (fence-protected).
"""

import json
import logging
import os
from datetime import UTC, datetime

from celery_app import celery_app
from database import get_db_context
from models.benchmark import ProxyDeployment
from services.entity_lock import EntityLockError, EntityLockLostError, entity_lock

logger = logging.getLogger(__name__)


def _celery_task_int(celery_task_id: str | None, fallback: int = 0) -> int:
    """Convert a Celery UUID task ID to a stable BIGINT for holding_task_id.

    The holding_task_id column is BIGINT. Celery task IDs are UUIDs (strings).
    We take the lower 63 bits of the UUID bytes so the value always fits in a
    signed BIGINT and is deterministic for the same task invocation.

    Returns ``fallback`` when ``celery_task_id`` is None (e.g. direct call in tests).
    """
    if celery_task_id is None:
        return fallback
    import hashlib
    digest = hashlib.sha256(celery_task_id.encode()).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF

# Redis pub/sub channel for proxy deploy events
PROXY_UPDATES_CHANNEL = "bnk-forge:proxy-updates"


def _publish_proxy_event(proxy_id: int, event_type: str, message: str, **extra: object) -> None:
    """Publish a proxy deploy event to Redis for WebSocket broadcast."""
    try:
        import redis as sync_redis

        redis_url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
        client = sync_redis.Redis.from_url(redis_url, decode_responses=True)
        payload = {
            "type": event_type,
            "proxy_id": proxy_id,
            "message": message,
            "timestamp": datetime.now(UTC).isoformat() + "Z",
            **extra,
        }
        client.publish(PROXY_UPDATES_CHANNEL, json.dumps(payload))
    except Exception as exc:
        logger.debug("Proxy event publish failed (non-blocking): %s", exc)


@celery_app.task(bind=True, name="tasks.proxy_deploy.deploy_proxy", queue="default")
def deploy_proxy_task(self, proxy_id: int) -> dict:
    """Deploy a proxy to a benchmark target cluster via Helm.

    Acquires an entity lock on the ProxyDeployment row before calling the
    service so concurrent deploy_proxy_task invocations on the same proxy_id
    block until the lock is released or reclaimed.

    Updates ProxyDeployment.celery_task_id for frontend status polling.
    Publishes streaming status via Redis pub/sub.
    """
    with get_db_context() as db:
        deploy = None
        try:
            deploy = db.query(ProxyDeployment).filter(ProxyDeployment.id == proxy_id).first()
            if not deploy:
                logger.error("ProxyDeployment %d not found", proxy_id)
                return {"success": False, "error": "ProxyDeployment not found"}

            # Store Celery task ID for status polling (before locking — not
            # a fence-protected write; just metadata for the UI).
            deploy.celery_task_id = self.request.id
            db.commit()

            def on_status(msg: str) -> None:
                _publish_proxy_event(proxy_id, "proxy_deploy_output", msg)

            from services.proxy_deploy_service import ProxyDeployService

            svc = ProxyDeployService(db)

            with entity_lock(db, table="proxy_deployments", entity_id=proxy_id, task_id=_celery_task_int(self.request.id, fallback=proxy_id)) as lock:
                result = svc.deploy(proxy_id, lock=lock, on_status=on_status)

            _publish_proxy_event(
                proxy_id,
                "proxy_deploy_complete",
                f"Proxy deployed: {result.proxy_type} → {result.proxy_url}",
                status=result.status,
                proxy_url=result.proxy_url,
            )

            return {
                "success": True,
                "proxy_id": proxy_id,
                "status": result.status,
                "proxy_url": result.proxy_url,
            }

        except EntityLockError as e:
            logger.error("deploy_proxy_task failed - entity locked: proxy_id=%d err=%s", proxy_id, e)
            if deploy:
                try:
                    deploy.status = "failed"  # type: ignore[assignment]
                    deploy.status_message = f"Could not acquire lock: {e}"
                    db.commit()
                except Exception:
                    pass
            _publish_proxy_event(
                proxy_id,
                "proxy_deploy_failed",
                f"Deploy failed — proxy already locked: {e}",
                status="failed",
            )
            raise

        except EntityLockLostError as e:
            logger.warning("deploy_proxy_task aborted - lock lost mid-operation: proxy_id=%d err=%s", proxy_id, e)
            if deploy:
                try:
                    deploy.status = "failed"  # type: ignore[assignment]
                    deploy.status_message = f"Lock lost mid-deploy: {e}"
                    db.commit()
                except Exception:
                    pass
            _publish_proxy_event(
                proxy_id,
                "proxy_deploy_failed",
                f"Deploy failed — lock lost: {e}",
                status="failed",
            )
            raise

        except Exception as exc:
            logger.exception("deploy_proxy_task failed: proxy_id=%d", proxy_id)
            _publish_proxy_event(
                proxy_id,
                "proxy_deploy_failed",
                f"Deploy failed: {exc}",
                status="failed",
            )
            return {"success": False, "proxy_id": proxy_id, "error": str(exc)}


@celery_app.task(bind=True, name="tasks.proxy_deploy.undeploy_proxy", queue="default")
def undeploy_proxy_task(self, proxy_id: int) -> dict:
    """Undeploy (Helm uninstall) a proxy from a benchmark target cluster.

    Acquires an entity lock on the ProxyDeployment row before calling the
    service. Matches the same Phase 1.5 lock-in-entrypoint pattern.
    """
    with get_db_context() as db:
        deploy = None
        try:
            deploy = db.query(ProxyDeployment).filter(ProxyDeployment.id == proxy_id).first()
            if not deploy:
                logger.error("ProxyDeployment %d not found", proxy_id)
                return {"success": False, "error": "ProxyDeployment not found"}

            # Store Celery task ID
            deploy.celery_task_id = self.request.id
            db.commit()

            def on_status(msg: str) -> None:
                _publish_proxy_event(proxy_id, "proxy_undeploy_output", msg)

            from services.proxy_deploy_service import ProxyDeployService

            svc = ProxyDeployService(db)

            with entity_lock(db, table="proxy_deployments", entity_id=proxy_id, task_id=_celery_task_int(self.request.id, fallback=proxy_id)) as lock:
                result = svc.undeploy(proxy_id, lock=lock, on_status=on_status)

            _publish_proxy_event(
                proxy_id,
                "proxy_undeploy_complete",
                f"Proxy uninstalled: {result.proxy_type}",
                status=result.status,
            )

            return {
                "success": True,
                "proxy_id": proxy_id,
                "status": result.status,
            }

        except EntityLockError as e:
            logger.error("undeploy_proxy_task failed - entity locked: proxy_id=%d err=%s", proxy_id, e)
            if deploy:
                try:
                    deploy.status = "failed"  # type: ignore[assignment]
                    deploy.status_message = f"Could not acquire lock: {e}"
                    db.commit()
                except Exception:
                    pass
            _publish_proxy_event(
                proxy_id,
                "proxy_undeploy_failed",
                f"Undeploy failed — proxy already locked: {e}",
                status="failed",
            )
            raise

        except EntityLockLostError as e:
            logger.warning("undeploy_proxy_task aborted - lock lost mid-operation: proxy_id=%d err=%s", proxy_id, e)
            if deploy:
                try:
                    deploy.status = "failed"  # type: ignore[assignment]
                    deploy.status_message = f"Lock lost mid-undeploy: {e}"
                    db.commit()
                except Exception:
                    pass
            _publish_proxy_event(
                proxy_id,
                "proxy_undeploy_failed",
                f"Undeploy failed — lock lost: {e}",
                status="failed",
            )
            raise

        except Exception as exc:
            logger.exception("undeploy_proxy_task failed: proxy_id=%d", proxy_id)
            _publish_proxy_event(
                proxy_id,
                "proxy_undeploy_failed",
                f"Undeploy failed: {exc}",
                status="failed",
            )
            return {"success": False, "proxy_id": proxy_id, "error": str(exc)}
