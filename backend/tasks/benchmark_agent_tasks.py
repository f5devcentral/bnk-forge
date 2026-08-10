"""Celery tasks for benchmark agent host operations."""

import logging

from celery_app import celery_app
from database import get_db_context

logger = logging.getLogger(__name__)


@celery_app.task(
    name="tasks.benchmark_agent.provision_host",
    bind=True,
    time_limit=1800,   # 30 min hard limit (pip install can be slow)
    soft_time_limit=1620,
    acks_late=True,
    max_retries=0,
)
def provision_benchmark_agent_host(
    self,  # noqa: ARG001
    host_id: int,
) -> dict:
    """SSH-provision a Forge-managed benchmark agent host.

    Dispatched by POST /api/benchmarks/agent-hosts/{id}/provision.
    Installs aiperf + forge_agent.py and starts the systemd service.
    Writes provision_status / provision_message back to the DB row so the
    frontend can poll GET /api/benchmarks/agent-hosts/{id}.
    """
    logger.info("Starting benchmark agent host provision for host_id=%d", host_id)

    with get_db_context() as db:
        from services.benchmark_agent_provision_service import BenchmarkAgentProvisionService

        svc = BenchmarkAgentProvisionService(db)
        try:
            return svc.provision(host_id)
        except Exception:
            logger.exception("Provision task failed for host_id=%d", host_id)
            # BenchmarkAgentProvisionService.provision() already persists failure;
            # re-raise so Celery records the task as FAILED.
            raise


@celery_app.task(
    name="tasks.benchmark_agent.cleanup_host",
    bind=True,
    time_limit=120,   # 2 min hard limit — best-effort, never blocks the delete
    soft_time_limit=90,
    acks_late=True,
    max_retries=0,
)
def cleanup_benchmark_agent_host(
    self,  # noqa: ARG001
    ssh_credential_id: int,
    host_ip: str,
    ssh_port: int | None,
    jumphost_chain: list | None,
) -> dict:
    """Best-effort: stop + disable the forge-agent service on a removed host.

    Dispatched by DELETE /api/benchmarks/agent-hosts/{id} so an unreachable
    host never stalls the HTTP request. The DB row is already gone by the time
    this runs, so connection params are passed directly. Failures are logged
    and swallowed — there is nothing left to mark.
    """
    with get_db_context() as db:
        try:
            from services.bare_metal.ssh_provision import build_ssh_session_from_credential

            with build_ssh_session_from_credential(
                db, ssh_credential_id, host_ip, ssh_port, jumphost_chain,
            ) as ssh:
                ssh.execute("sudo systemctl disable --now forge-agent 2>/dev/null || true", timeout=30)
            logger.info("forge-agent service stopped on removed host %s", host_ip)
            return {"host_ip": host_ip, "status": "cleaned"}
        except Exception as exc:
            logger.warning("Could not stop forge-agent on removed host %s (non-blocking): %s", host_ip, exc)
            return {"host_ip": host_ip, "status": "skipped", "error": str(exc)[:200]}


@celery_app.task(
    name="tasks.benchmark_agent.scan_host",
    bind=True,
    time_limit=300,   # 5 min hard limit
    soft_time_limit=240,
    acks_late=True,
    max_retries=0,
)
def scan_benchmark_agent_host(
    self,  # noqa: ARG001
    host_id: int,
    target_ids: list[int] | None = None,
) -> dict:
    """Run an SSH suitability scan on a Forge-managed benchmark agent host.

    Dispatched by POST /api/benchmarks/agent-hosts/{id}/scan.
    Writes readiness JSON + provision_status back to the DB row so the
    frontend can poll GET /api/benchmarks/agent-hosts/{id}.
    """
    logger.info("Starting benchmark agent host scan for host_id=%d", host_id)

    with get_db_context() as db:
        from services.benchmark_agent_scan_service import BenchmarkAgentScanService

        svc = BenchmarkAgentScanService(db)
        try:
            readiness = svc.scan(host_id, target_ids=target_ids)
        except Exception:
            logger.exception("Benchmark agent host scan failed for host_id=%d", host_id)
            # Belt-and-suspenders: mark as failed if the service didn't persist it
            try:
                db.rollback()
                from models.benchmark import BenchmarkAgent
                agent = db.query(BenchmarkAgent).filter_by(id=host_id).first()
                if agent and agent.provision_status == "scanning":
                    import traceback
                    agent.provision_status = "failed"
                    agent.provision_message = traceback.format_exc()[-300:]
                    db.commit()
            except Exception:
                logger.exception("Could not mark host %d as failed after scan error", host_id)
            return {"host_id": host_id, "verdict": "ssh_unreachable", "status": "failed"}

        return {
            "host_id": host_id,
            "verdict": readiness.get("verdict", "unknown"),
            "status": "completed",
        }
