"""Celery tasks for bare-metal DPU deployment."""

import logging

from celery_app import celery_app
from database import get_db_context

logger = logging.getLogger(__name__)


@celery_app.task(
    name="tasks.bare_metal.execute_deployment",
    bind=True,
    time_limit=7200,  # 2 hours hard limit (full deploy: ~45 min)
    soft_time_limit=6600,  # 110 min soft limit
)
def execute_bare_metal_deployment(self, deployment_id: int, project_id: int) -> dict:  # noqa: ARG001
    """Execute a bare-metal deployment asynchronously.

    Iterates through steps, calling the appropriate phase service for each.
    Commits after each step for progress persistence.
    """
    logger.info("Starting bare-metal deployment %d for project %d", deployment_id, project_id)

    with get_db_context() as db:
        from services.bare_metal.orchestrator import BareMetalDeploymentService

        svc = BareMetalDeploymentService(db)

        # Link Celery task ID to deployment
        deployment = svc.get_deployment(deployment_id)
        deployment.celery_task_id = self.request.id
        db.commit()

        return svc.execute_deployment(deployment_id, task=self)


@celery_app.task(
    name="tasks.bare_metal.run_discovery",
    bind=True,
    time_limit=300,  # 5 min for discovery
    soft_time_limit=240,
    acks_late=True,
    max_retries=0,
)
def run_bare_metal_discovery(self, host_id: int, project_id: int, options: dict) -> dict:  # noqa: ARG001
    """Run bare-metal discovery probes asynchronously."""
    logger.info("Starting bare-metal discovery for host %d, project %d", host_id, project_id)

    with get_db_context() as db:
        from services.bare_metal.discovery import BareMetalDiscoveryService

        svc = BareMetalDiscoveryService(db)
        try:
            result = svc.run_discovery(host_id, options)
        except Exception:
            # The service's own except already tried _update_stage("failed").
            # Belt-and-suspenders: ensure the host row is marked failed even
            # if the service-level handler couldn't write (e.g. broken session).
            try:
                db.rollback()
                from models.bare_metal import BareMetalHost
                host = db.query(BareMetalHost).filter(BareMetalHost.id == host_id).first()
                if host and host.last_discovery_status != "failed":
                    import traceback
                    host.last_discovery_status = "failed"
                    host.last_discovery_error = traceback.format_exc()[-500:]
                    db.commit()
            except Exception:
                logger.exception("Could not mark host %d as failed after discovery error", host_id)
            return {"host_id": host_id, "status": "failed"}

        return {
            "host_id": result.host_id,
            "topology_recommendation": result.topology_recommendation,
            "assessment_count": len(result.assessment),
            "status": "completed",
        }
