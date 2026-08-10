"""
BNK Upgrade Workflow API routes — Sprint 8.2

Endpoints:
  - GET  /api/k8s/clusters/{id}/bnk/upgrade/versions    — Available BNK versions
  - GET  /api/k8s/clusters/{id}/bnk/upgrade/current      — Current BNK version info
  - POST /api/k8s/clusters/{id}/bnk/upgrade/plan          — Create upgrade plan (pre-validate + generate)
  - POST /api/k8s/clusters/{id}/bnk/upgrade/{uid}/execute — Start upgrade execution (async)
  - POST /api/k8s/clusters/{id}/bnk/upgrade/{uid}/rollback — Rollback a failed upgrade (async)
  - POST /api/k8s/clusters/{id}/bnk/upgrade/{uid}/cancel  — Cancel a pending upgrade
  - GET  /api/k8s/clusters/{id}/bnk/upgrade/history       — List upgrade history
  - GET  /api/k8s/clusters/{id}/bnk/upgrade/{uid}         — Get upgrade details
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.errors import BadRequestError, NotFoundError, handle_route_errors
from database import get_db
from models import User
from routes.auth import require_cluster_owner, require_viewer

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["bnk-upgrade"])


# ================================================================
# Request/Response Models
# ================================================================

class CreateUpgradePlanRequest(BaseModel):
    target_version: str


class UpgradeStepResponse(BaseModel):
    step: int
    action: str
    label: str
    module: str | None = None
    phase: str | None = None
    timeout: int | None = None


class PreCheckResponse(BaseModel):
    name: str
    label: str
    status: str  # pass, fail, warn
    detail: str
    critical: bool | None = True


class UpgradeResponse(BaseModel):
    id: int
    cluster_id: int
    project_id: int | None = None
    from_version: str | None = None
    to_version: str
    status: str
    pre_check_passed: bool
    pre_checks: list[dict] | None = None
    plan: list[dict] | None = None
    total_steps: int = 0
    current_step: int = 0
    step_results: list[dict] | None = None
    pre_health: dict | None = None
    post_health: dict | None = None
    health_gate_passed: bool | None = None
    rollback_available: bool = False
    error_message: str | None = None
    error_step: int | None = None
    triggered_by: str | None = None
    approved_by: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float | None = None
    celery_task_id: str | None = None
    created_at: str | None = None


class VersionInfoResponse(BaseModel):
    version: str
    label: str
    release_date: str | None = None
    notes: str | None = None
    min_k8s: str | None = None
    max_k8s: str | None = None
    source: str | None = None  # "registry" or None (hardcoded fallback)


class CurrentVersionResponse(BaseModel):
    status: str
    health: str | None = None
    flo_version: str | None = None
    ga_label: str | None = None
    min_k8s: str | None = None
    max_k8s: str | None = None
    helm_release: dict | None = None
    tmm_pods: dict | None = None
    vlans: list[dict] | None = None


# ================================================================
# Helper: serialize BnkUpgrade to response dict
# ================================================================

def _serialize_upgrade(upgrade) -> dict:
    """Convert BnkUpgrade ORM object to response dict."""
    return {
        "id": upgrade.id,
        "cluster_id": upgrade.cluster_id,
        "project_id": upgrade.project_id,
        "from_version": upgrade.from_version,
        "to_version": upgrade.to_version,
        "status": upgrade.status,
        "pre_check_passed": upgrade.pre_check_passed or False,
        "pre_checks": upgrade.pre_checks,
        "plan": upgrade.plan,
        "total_steps": upgrade.total_steps or 0,
        "current_step": upgrade.current_step or 0,
        "step_results": upgrade.step_results,
        "pre_health": upgrade.pre_health,
        "post_health": upgrade.post_health,
        "health_gate_passed": upgrade.health_gate_passed,
        "rollback_available": upgrade.rollback_available or False,
        "error_message": upgrade.error_message,
        "error_step": upgrade.error_step,
        "triggered_by": upgrade.triggered_by,
        "approved_by": upgrade.approved_by,
        "started_at": upgrade.started_at.isoformat() if upgrade.started_at else None,
        "completed_at": upgrade.completed_at.isoformat() if upgrade.completed_at else None,
        "duration_seconds": upgrade.duration_seconds,
        "celery_task_id": upgrade.celery_task_id,
        "created_at": upgrade.created_at.isoformat() if upgrade.created_at else None,
    }


# ================================================================
# Endpoints
# ================================================================

@router.get(
    "/k8s/clusters/{cluster_id}/bnk/upgrade/versions",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get available BNK versions")
def get_available_versions(
    cluster_id: int,
    db: Session = Depends(get_db),
):
    """
    List available BNK versions for upgrade.

    Returns known FLO chart versions with compatibility info.
    """
    from services.bnk_upgrade_service import BnkUpgradeService
    service = BnkUpgradeService(db)
    versions, registry_error = service.get_available_versions(cluster_id)

    # Also get current version for comparison
    try:
        current = service.get_cluster_bnk_version(cluster_id)
    except Exception:
        current = {"flo_version": None}

    return {
        "current_version": current.get("flo_version"),
        "available_versions": versions,
        "registry_available": registry_error is None,
        "registry_error": registry_error,
    }


@router.get(
    "/k8s/clusters/{cluster_id}/bnk/upgrade/current",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get current BNK version")
def get_current_version(
    cluster_id: int,
    db: Session = Depends(get_db),
):
    """
    Get current BNK version and health info from cluster.
    """
    from services.bnk_upgrade_service import BnkUpgradeService
    service = BnkUpgradeService(db)
    return service.get_cluster_bnk_version(cluster_id)


@router.post("/k8s/clusters/{cluster_id}/bnk/upgrade/plan")
@handle_route_errors("create upgrade plan")
def create_upgrade_plan(
    cluster_id: int,
    request: CreateUpgradePlanRequest,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """
    Create a new upgrade plan.

    Runs pre-upgrade validation (health checks, version compatibility,
    prerequisite checks) and generates an ordered upgrade plan.

    Returns the plan with status 'ready' if all checks pass,
    or 'failed' with details of what failed.
    """
    from services.bnk_upgrade_service import BnkUpgradeService
    service = BnkUpgradeService(db)

    username = user.username if user else "user"
    upgrade = service.create_upgrade(
        cluster_id=cluster_id,
        target_version=request.target_version,
        user=username,
    )

    return _serialize_upgrade(upgrade)


@router.post("/k8s/clusters/{cluster_id}/bnk/upgrade/{upgrade_id}/execute")
@handle_route_errors("execute upgrade")
def execute_upgrade(
    cluster_id: int,
    upgrade_id: int,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """
    Start executing an approved upgrade plan.

    Dispatches to a Celery task for async execution with streaming output.
    Returns immediately with the upgrade status and celery task ID.
    """
    from models import BnkUpgrade
    upgrade = db.query(BnkUpgrade).filter(
        BnkUpgrade.id == upgrade_id,
        BnkUpgrade.cluster_id == cluster_id,
    ).first()

    if not upgrade:
        raise NotFoundError("upgrade", upgrade_id)

    if upgrade.status != "ready":
        raise BadRequestError(
            f"Upgrade is '{upgrade.status}', must be 'ready' to execute",
        )

    # Mark as approved
    username = user.username if user else "user"
    upgrade.approved_by = username
    db.commit()

    # Dispatch Celery task
    from tasks.bnk_upgrade_tasks import execute_upgrade_task
    task = execute_upgrade_task.delay(upgrade_id)

    upgrade.celery_task_id = task.id
    db.commit()

    return {
        "message": "Upgrade execution started",
        "upgrade_id": upgrade_id,
        "celery_task_id": task.id,
        "status": "in_progress",
    }


@router.post("/k8s/clusters/{cluster_id}/bnk/upgrade/{upgrade_id}/rollback")
@handle_route_errors("rollback upgrade")
def rollback_upgrade(
    cluster_id: int,
    upgrade_id: int,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """
    Roll back a failed upgrade to the previous version.

    Dispatches to a Celery task for async execution.
    """
    from models import BnkUpgrade
    upgrade = db.query(BnkUpgrade).filter(
        BnkUpgrade.id == upgrade_id,
        BnkUpgrade.cluster_id == cluster_id,
    ).first()

    if not upgrade:
        raise NotFoundError("upgrade", upgrade_id)

    if not upgrade.rollback_available:
        raise BadRequestError("No rollback available")

    if upgrade.status not in ("failed", "in_progress", "health_check"):
        raise BadRequestError(
            f"Cannot rollback from status '{upgrade.status}'",
        )

    from tasks.bnk_upgrade_tasks import rollback_upgrade_task
    task = rollback_upgrade_task.delay(upgrade_id)

    return {
        "message": "Rollback started",
        "upgrade_id": upgrade_id,
        "celery_task_id": task.id,
        "status": "rolling_back",
    }


@router.post("/k8s/clusters/{cluster_id}/bnk/upgrade/{upgrade_id}/cancel")
@handle_route_errors("cancel upgrade")
def cancel_upgrade(
    cluster_id: int,
    upgrade_id: int,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """
    Cancel a pending/ready upgrade plan.
    """
    from services.bnk_upgrade_service import BnkUpgradeService
    service = BnkUpgradeService(db)
    upgrade = service.cancel_upgrade(upgrade_id)
    return _serialize_upgrade(upgrade)


@router.get(
    "/k8s/clusters/{cluster_id}/bnk/upgrade/history",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("list upgrade history")
def get_upgrade_history(
    cluster_id: int,
    limit: int = 20,
    db: Session = Depends(get_db),
):
    """
    List upgrade history for a cluster.
    """
    from services.bnk_upgrade_service import BnkUpgradeService
    service = BnkUpgradeService(db)
    upgrades = service.list_upgrades(cluster_id, limit=limit)
    return {
        "upgrades": [_serialize_upgrade(u) for u in upgrades],
        "total": len(upgrades),
    }


@router.get(
    "/k8s/clusters/{cluster_id}/bnk/upgrade/{upgrade_id}",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("get upgrade detail")
def get_upgrade_detail(
    cluster_id: int,
    upgrade_id: int,
    db: Session = Depends(get_db),
):
    """
    Get detailed information about a specific upgrade.
    """
    from services.bnk_upgrade_service import BnkUpgradeService
    service = BnkUpgradeService(db)
    upgrade = service.get_upgrade(upgrade_id)

    if not upgrade:
        raise NotFoundError("upgrade", upgrade_id)

    if upgrade.cluster_id != cluster_id:
        raise NotFoundError("upgrade", f"{upgrade_id} for cluster {cluster_id}")

    return _serialize_upgrade(upgrade)


# ================================================================
# Release Registry endpoints (issue #217)
# ================================================================

class ReleaseRegistryItemResponse(BaseModel):
    id: int
    ga_label: str
    product_line: str
    manifest_version: str | None = None
    flo_version_prefix: str | None = None
    flo_version_min: str | None = None
    flo_version_max: str | None = None
    min_k8s: str | None = None
    max_k8s: str | None = None
    source_type: str
    source_url: str | None = None
    notes: str | None = None
    is_active: bool


class BnkReleaseListResponse(BaseModel):
    releases: list[ReleaseRegistryItemResponse]
    total: int


class BnkReleaseSyncResponse(BaseModel):
    tags_fetched: int
    matched: int
    unmatched: int
    upserted: int


@router.get(
    "/bnk/releases",
    dependencies=[Depends(require_viewer)],
    response_model=BnkReleaseListResponse,
)
@handle_route_errors("list BNK release registry")
def list_bnk_releases(
    active_only: bool = True,
    db: Session = Depends(get_db),
):
    """
    List BNK release registry rows with source citations.
    """
    from services.release_registry_service import ReleaseRegistryService
    svc = ReleaseRegistryService(db)
    rows = svc.list_releases(active_only=active_only)
    return {
        "releases": [
            ReleaseRegistryItemResponse(
                id=r.id,
                ga_label=r.ga_label,
                product_line=r.product_line,
                manifest_version=r.manifest_version,
                flo_version_prefix=r.flo_version_prefix,
                flo_version_min=r.flo_version_min,
                flo_version_max=r.flo_version_max,
                min_k8s=r.min_k8s,
                max_k8s=r.max_k8s,
                source_type=r.source_type,
                source_url=r.source_url,
                notes=r.notes,
                is_active=r.is_active,
            )
            for r in rows
        ],
        "total": len(rows),
    }


@router.post(
    "/k8s/clusters/{cluster_id}/bnk/releases/sync",
    response_model=BnkReleaseSyncResponse,
)
@handle_route_errors("sync BNK release registry from OCI")
def sync_releases_from_oci(
    cluster_id: int,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """
    Sync OCI-observed FLO tags into the release registry.

    Fetches available FLO tags from the F5 OCI registry using the cluster's
    cne_pull_secret and annotates the registry with observed versions.
    Curated clouddocs rows are not modified.
    """
    from services.bnk_upgrade_service import BnkUpgradeService
    from services.release_registry_service import ReleaseRegistryService

    upgrade_svc = BnkUpgradeService(db)
    oci_versions = upgrade_svc._fetch_oci_versions(cluster_id)
    tags = [v["version"] for v in oci_versions]

    registry_svc = ReleaseRegistryService(db)
    result = registry_svc.sync_from_oci(tags)
    db.commit()

    return {
        "tags_fetched": len(tags),
        **result,
    }
