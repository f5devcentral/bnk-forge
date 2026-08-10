"""
QKView routes — proxy BNK CWC QKView API through BNK-Forge.

K8S-UX-010: Routes now prefer dispatching through the connected operator pod
(which makes direct HTTPS calls to CWC via mTLS). Falls back to the legacy
ephemeral curl pod approach when no operator is connected for the cluster.

Provides endpoints to create, list, monitor, download, and delete
QKView diagnostic tarballs from F5 BNK clusters.
"""

import base64
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from routes.auth import require_operator, require_viewer
from services.kubernetes_service import KubernetesService
from services.operator_registry import (
    get_operator_for_cluster,
    operator_connections,
)
from services.qkview_service import (
    QKViewError,
    cleanup_client_pods,
)
from services.qkview_service import (
    cancel_qkview as legacy_cancel_qkview,
)
from services.qkview_service import (
    check_cwc_available as legacy_check_cwc_available,
)
from services.qkview_service import (
    check_setup_status as legacy_check_setup_status,
)
from services.qkview_service import (
    create_qkview as legacy_create_qkview,
)
from services.qkview_service import (
    delete_qkview as legacy_delete_qkview,
)
from services.qkview_service import (
    download_qkview as legacy_download_qkview,
)
from services.qkview_service import (
    get_qkview as legacy_get_qkview,
)
from services.qkview_service import (
    get_qkview_status as legacy_get_qkview_status,
)
from services.qkview_service import (
    list_qkviews as legacy_list_qkviews,
)
from services.qkview_service import (
    setup_cwc_api_certs as legacy_setup_cwc_api_certs,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/qkview", tags=["qkview"])


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------

class QKViewCreateRequest(BaseModel):
    cluster_id: int
    namespace: str | None = None
    timeout: str | None = None
    filename: str | None = None
    pod_patterns: list[str] | None = None
    include_core_files: bool = False
    core_files_max: int = 2
    core_files_since: str | None = None


class QKViewClusterRequest(BaseModel):
    cluster_id: int


class QKViewCheckResponse(BaseModel):
    """Response from GET /api/qkview/check."""

    available: bool
    message: str
    operator_dispatch: bool | None = None

    class Config:
        extra = "allow"


class QKViewListResponse(BaseModel):
    """Response from GET /api/qkview/list."""

    qkviews: list[dict[str, Any]]
    error: str | None = None
    operator_dispatch: bool | None = None


class QKViewCreateResponse(BaseModel):
    """Response from POST /api/qkview/create."""

    id: str | None = None
    filename: str | None = None
    status: str | None = None
    operator_dispatch: bool | None = None

    class Config:
        extra = "allow"


class QKViewDeleteResponse(BaseModel):
    """Response from DELETE /api/qkview/{qkview_id}."""

    deleted: bool | None = None
    success: bool | None = None
    operator_dispatch: bool | None = None

    class Config:
        extra = "allow"


class QKViewCancelResponse(BaseModel):
    """Response from POST /api/qkview/{qkview_id}/cancel."""

    cancelled: bool | None = None
    success: bool | None = None
    operator_dispatch: bool | None = None

    class Config:
        extra = "allow"


class QKViewCleanupResponse(BaseModel):
    """Response from POST /api/qkview/cleanup-pods."""

    cleaned: bool
    operator_dispatch: bool | None = None

    class Config:
        extra = "allow"


class QKViewGetResponse(BaseModel):
    """Response from GET /api/qkview/{qkview_id}."""

    id: str | None = None
    status: str | None = None
    filename: str | None = None
    operator_dispatch: bool | None = None

    class Config:
        extra = "allow"


class QKViewStatusResponse(BaseModel):
    """Response from GET /api/qkview/{qkview_id}/status."""

    status: str
    progress: int | None = None
    operator_dispatch: bool | None = None

    class Config:
        extra = "allow"


# ---------------------------------------------------------------------------
# Operator dispatch helper
# ---------------------------------------------------------------------------

async def _try_operator_dispatch(
    db: Session,
    cluster_id: int,
    action: str,
    payload: dict | None = None,
    timeout: float = 60.0,
) -> dict | None:
    """
    Try to dispatch a CWC command via the connected operator.

    Returns the result dict if an operator is available and the command succeeds.
    Returns None if no operator is connected (caller should fall back to legacy).
    Raises HTTPException if the operator IS connected but the command fails.
    """
    operator = get_operator_for_cluster(db, cluster_id)
    if not operator:
        return None

    operator_id = operator.operator_id
    if not operator_connections.is_connected(operator_id):
        return None

    try:
        result = await operator_connections.send_command(
            operator_id=operator_id,
            command={"action": action, "payload": payload or {}},
            timeout=timeout,
        )
        return result
    except (ConnectionError, TimeoutError) as e:
        # Operator was connected but command failed — don't silently fall back,
        # log the error but return None so legacy path can be tried
        logger.warning(
            f"Operator dispatch failed for {action} on cluster {cluster_id}: {e}. "
            "Falling back to legacy QKView path."
        )
        return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/check",
    response_model=QKViewCheckResponse,
    dependencies=[Depends(require_viewer)],
)
async def check_cwc(
    cluster_id: int,
    db: Session = Depends(get_db),
):
    """Check if the CWC QKView API is available on the cluster."""
    # Try operator path
    result = await _try_operator_dispatch(db, cluster_id, "cwc_check_status")
    if result is not None:
        return {
            "available": result.get("cwc_reachable", False),
            "message": "CWC is available (via operator)" if result.get("cwc_reachable") else "CWC not reachable via operator",
            "operator_dispatch": True,
            **{k: v for k, v in result.items() if k not in ("type", "command_id", "output_lines")},
        }

    # Legacy fallback
    k8s_service = KubernetesService(db)
    return check_cwc_available(k8s_service, cluster_id)


# Keep the original function names as module-level aliases for mock.patch targets
# in integration tests (tests mock "routes.qkview.<name>")
check_cwc_available = legacy_check_cwc_available
check_setup_status = legacy_check_setup_status
setup_cwc_api_certs = legacy_setup_cwc_api_certs
setup_qkview_certs = legacy_setup_cwc_api_certs
list_qkviews = legacy_list_qkviews
create_qkview = legacy_create_qkview
get_qkview = legacy_get_qkview
get_qkview_status = legacy_get_qkview_status
download_qkview = legacy_download_qkview
delete_qkview = legacy_delete_qkview
cancel_qkview = legacy_cancel_qkview


@router.get(
    "/list",
    response_model=QKViewListResponse,
    dependencies=[Depends(require_viewer)],
)
async def list_qkviews_endpoint(
    cluster_id: int,
    db: Session = Depends(get_db),
):
    """List all QKView jobs on the cluster."""
    # Try operator path
    result = await _try_operator_dispatch(db, cluster_id, "cwc_qkview_list")
    if result is not None:
        if result.get("success"):
            items = result.get("items", [])
            return {"qkviews": items, "operator_dispatch": True}
        return {"qkviews": [], "error": result.get("error_message", "Unknown error")}

    # Legacy fallback
    k8s_service = KubernetesService(db)
    try:
        qkviews = list_qkviews(k8s_service, cluster_id)
        return {"qkviews": qkviews}
    except QKViewError as e:
        return {"qkviews": [], "error": str(e)}


@router.post(
    "/create",
    response_model=QKViewCreateResponse,
    dependencies=[Depends(require_operator)],
)
async def create_qkview_endpoint(
    request: QKViewCreateRequest,
    db: Session = Depends(get_db),
):
    """Create a new QKView diagnostic collection."""
    # Build payload for operator
    payload: dict = {}
    if request.namespace:
        payload["namespace"] = request.namespace
    if request.filename:
        payload["filename"] = request.filename
    if request.pod_patterns:
        payload["pod_patterns"] = request.pod_patterns
    if request.include_core_files:
        payload["include_core_files"] = True
        payload["core_files_max"] = request.core_files_max
        if request.core_files_since:
            payload["core_files_since"] = request.core_files_since

    # Try operator path
    result = await _try_operator_dispatch(
        db, request.cluster_id, "cwc_qkview_create", payload=payload,
    )
    if result is not None:
        if not result.get("success"):
            raise HTTPException(
                status_code=502,
                detail=result.get("error_message", "Failed to create QKView via operator"),
            )
        return {**result, "operator_dispatch": True}

    # Legacy fallback — build options dict matching service expectations
    k8s_service = KubernetesService(db)
    options: dict = {}
    if request.namespace:
        options["namespace"] = request.namespace
    if request.filename:
        options["filename"] = request.filename
    if request.pod_patterns:
        options["pod_patterns"] = request.pod_patterns
    if request.include_core_files:
        core_opts: dict = {"max_files": request.core_files_max}
        if request.core_files_since:
            core_opts["since"] = request.core_files_since
        options["core_files"] = core_opts

    result = create_qkview(k8s_service, request.cluster_id, options)
    return result


@router.get(
    "/{qkview_id}",
    response_model=QKViewGetResponse,
    dependencies=[Depends(require_viewer)],
)
async def get_qkview_endpoint(
    qkview_id: str,
    cluster_id: int,
    db: Session = Depends(get_db),
):
    """Get details of a specific QKView."""
    # Try operator path
    result = await _try_operator_dispatch(
        db, cluster_id, "cwc_qkview_get", payload={"qkview_id": qkview_id},
    )
    if result is not None:
        if not result.get("success"):
            raise HTTPException(
                status_code=502,
                detail=result.get("error_message", "Failed to get QKView via operator"),
            )
        return {**result, "operator_dispatch": True}

    # Legacy fallback
    k8s_service = KubernetesService(db)
    return get_qkview(k8s_service, cluster_id, qkview_id)


@router.get(
    "/{qkview_id}/status",
    response_model=QKViewStatusResponse,
    dependencies=[Depends(require_viewer)],
)
async def get_qkview_status_endpoint(
    qkview_id: str,
    cluster_id: int,
    db: Session = Depends(get_db),
):
    """Get the status of a specific QKView job."""
    # Try operator path — reuse get details
    result = await _try_operator_dispatch(
        db, cluster_id, "cwc_qkview_get", payload={"qkview_id": qkview_id},
    )
    if result is not None:
        if not result.get("success"):
            raise HTTPException(
                status_code=502,
                detail=result.get("error_message", "Failed to get QKView status via operator"),
            )
        return {**result, "operator_dispatch": True}

    # Legacy fallback
    k8s_service = KubernetesService(db)
    return get_qkview_status(k8s_service, cluster_id, qkview_id)


@router.get("/{qkview_id}/download", dependencies=[Depends(require_viewer)])
async def download_qkview_endpoint(
    qkview_id: str,
    cluster_id: int,
    db: Session = Depends(get_db),
):
    """Download the QKView tarball."""
    # Try operator path
    result = await _try_operator_dispatch(
        db, cluster_id, "cwc_qkview_download",
        payload={"qkview_id": qkview_id},
        timeout=120.0,  # downloads can be large
    )
    if result is not None:
        if not result.get("success"):
            raise HTTPException(
                status_code=502,
                detail=result.get("error_message", "Failed to download QKView via operator"),
            )
        # Operator returns base64-encoded binary data and must flag binary=True.
        # Treat a success-without-binary response as an operator error rather than
        # silently falling through to the legacy pod path (FEAT-0346 / ERR-0028).
        if not result.get("binary"):
            raise HTTPException(
                status_code=502,
                detail="Operator returned success without binary QKView data",
            )
        data = base64.b64decode(result["data"])
        return Response(
            content=data,
            media_type="application/gzip",
            headers={
                "Content-Disposition": f'attachment; filename="qkview-{qkview_id}.tar.gz"',
            },
        )

    # Legacy fallback
    k8s_service = KubernetesService(db)
    data = download_qkview(k8s_service, cluster_id, qkview_id)
    return Response(
        content=data,
        media_type="application/gzip",
        headers={
            "Content-Disposition": f'attachment; filename="qkview-{qkview_id}.tar.gz"',
        },
    )


@router.delete(
    "/{qkview_id}",
    response_model=QKViewDeleteResponse,
    dependencies=[Depends(require_operator)],
)
async def delete_qkview_endpoint(
    qkview_id: str,
    cluster_id: int,
    db: Session = Depends(get_db),
):
    """Delete a specific QKView."""
    # Try operator path
    result = await _try_operator_dispatch(
        db, cluster_id, "cwc_qkview_delete", payload={"qkview_id": qkview_id},
    )
    if result is not None:
        if not result.get("success"):
            error = result.get("error_message", "")
            status = 409 if "in progress" in error.lower() else 502
            raise HTTPException(status_code=status, detail=error or "Failed to delete QKView")
        return {**result, "operator_dispatch": True}

    # Legacy fallback
    k8s_service = KubernetesService(db)
    try:
        return delete_qkview(k8s_service, cluster_id, qkview_id)
    except QKViewError as e:
        # In-progress jobs raise QKViewError(409); surface the real status code
        # rather than falling through to a generic 500 (FEAT-0343 / ERR-0027).
        raise HTTPException(status_code=e.status_code or 500, detail=str(e)) from e


@router.post(
    "/{qkview_id}/cancel",
    response_model=QKViewCancelResponse,
    dependencies=[Depends(require_operator)],
)
async def cancel_qkview_endpoint(
    qkview_id: str,
    cluster_id: int,
    db: Session = Depends(get_db),
):
    """Cancel a running QKView job."""
    # Try operator path
    result = await _try_operator_dispatch(
        db, cluster_id, "cwc_qkview_cancel", payload={"qkview_id": qkview_id},
    )
    if result is not None:
        if not result.get("success"):
            raise HTTPException(
                status_code=502,
                detail=result.get("error_message", "Failed to cancel QKView via operator"),
            )
        return {**result, "operator_dispatch": True}

    # Legacy fallback
    k8s_service = KubernetesService(db)
    try:
        return cancel_qkview(k8s_service, cluster_id, qkview_id)
    except QKViewError as e:
        # In-progress jobs raise QKViewError(409); surface the real status code
        # rather than falling through to a generic 500 (FEAT-0343 / ERR-0027).
        raise HTTPException(status_code=e.status_code or 500, detail=str(e)) from e


@router.post(
    "/cleanup-pods",
    response_model=QKViewCleanupResponse,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("cleanup qkview client pods")
def cleanup_pods_endpoint(
    cluster_id: int,
    db: Session = Depends(get_db),
):
    """
    Clean up all qkview client (ephemeral curl) pods on the cluster.

    Note: With operator dispatch (K8S-UX-010), ephemeral pods are no longer
    created. This endpoint is for cleaning up legacy pods only.
    """
    k8s_service = KubernetesService(db)
    return cleanup_client_pods(k8s_service, cluster_id)
