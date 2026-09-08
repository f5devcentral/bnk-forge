"""
Use-Case Artifact routes (D-034 Phase 0 tracer).

Endpoints:
  - POST /api/clusters/{cluster_id}/usecase-artifacts/capture
        Capture F5SPKVlan CRs from a cluster into a versioned use-case artifact.
  - POST /api/clusters/{cluster_id}/usecase-artifact-versions/{version_id}/apply
        Render a use-case artifact version and apply it via the shared write path.
  - POST /api/clusters/{cluster_id}/usecase-artifact-versions/{version_id}/drift
        Render desired-state and diff it against the live cluster.
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.errors import NotFoundError, handle_route_errors
from database import get_db
from models import KubernetesCluster, UseCaseArtifactVersion, User
from routes.auth import require_cluster_owner, require_operator
from schemas.usecase_artifact import (
    UseCaseApplyRequest,
    UseCaseApplyResponse,
    UseCaseCaptureRequest,
    UseCaseCaptureResponse,
    UseCaseDriftRequest,
    UseCaseDriftResponse,
)
from services.k8s_drift_service import check_usecase_drift
from services.usecase_artifact_service import apply_usecase_artifact, capture_usecase_artifact

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["usecase-artifacts"])


def _get_cluster(cluster_id: int, db: Session) -> KubernetesCluster:
    cluster = db.query(KubernetesCluster).filter(KubernetesCluster.id == cluster_id).first()
    if not cluster:
        raise NotFoundError("cluster", cluster_id)
    return cluster


def _get_version(version_id: int, db: Session) -> UseCaseArtifactVersion:
    version = db.query(UseCaseArtifactVersion).filter(UseCaseArtifactVersion.id == version_id).first()
    if not version:
        raise NotFoundError("usecase artifact version", version_id)
    return version


@router.post(
    "/clusters/{cluster_id}/usecase-artifacts/capture",
    response_model=UseCaseCaptureResponse,
)
@handle_route_errors("capture use-case artifact")
def capture_artifact(
    cluster_id: int,
    body: UseCaseCaptureRequest,
    user: User = Depends(require_operator),
    db: Session = Depends(get_db),
):
    """Capture F5SPKVlan CRs from a cluster into a versioned use-case artifact."""
    _get_cluster(cluster_id, db)
    version, created = capture_usecase_artifact(
        db,
        cluster_id,
        name=body.name,
        version=body.version,
        matching_bnk_version=body.matching_bnk_version,
        created_by=user.username,
    )
    db.commit()
    db.refresh(version)
    return UseCaseCaptureResponse(version=version, already_captured=not created)


@router.post(
    "/clusters/{cluster_id}/usecase-artifact-versions/{version_id}/apply",
    response_model=UseCaseApplyResponse,
)
@handle_route_errors("apply use-case artifact")
def apply_artifact(
    cluster_id: int,
    version_id: int,
    body: UseCaseApplyRequest,
    user: User = Depends(require_cluster_owner),
    db: Session = Depends(get_db),
):
    """Render a use-case artifact version and apply it to a cluster via the shared write path."""
    cluster = _get_cluster(cluster_id, db)
    version = _get_version(version_id, db)
    results, application = apply_usecase_artifact(db, cluster, version, body.param_values, applied_by=user.username)
    db.commit()
    db.refresh(application)
    return UseCaseApplyResponse(
        message=(
            f"Applied use-case artifact v{version.version}: "
            f"{len(results['applied'])} applied, {len(results['failed'])} failed, "
            f"{len(results['skipped'])} skipped"
        ),
        results=results,
        application=application,
    )


@router.post(
    "/clusters/{cluster_id}/usecase-artifact-versions/{version_id}/drift",
    response_model=UseCaseDriftResponse,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("check use-case artifact drift")
def drift_artifact(
    cluster_id: int,
    version_id: int,
    body: UseCaseDriftRequest,
    db: Session = Depends(get_db),
):
    """Render desired-state from a use-case artifact version and diff against the live cluster."""
    cluster = _get_cluster(cluster_id, db)
    version = _get_version(version_id, db)
    return check_usecase_drift(db, cluster, version, body.param_values)
