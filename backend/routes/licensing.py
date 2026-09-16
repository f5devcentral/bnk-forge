"""
BNK Licensing routes — CWC license management.

Operator-first, legacy-fallback pattern (same as QKView routes):
  1. Try dispatching through the connected operator pod (mTLS via aiohttp)
  2. Fall back to legacy ephemeral curl pod approach via kubeconfig

Endpoints:
  GET  /api/licensing/{cluster_id}/status     — License + telemetry status
  GET  /api/licensing/{cluster_id}/report     — Telemetry report
  POST /api/licensing/{cluster_id}/activate   — Reactivate/switch license (raw JWT)
  POST /api/licensing/{cluster_id}/renew      — Full renewal: CPCL cleanup → CWC restart → activate
  POST /api/licensing/{cluster_id}/receipt    — Send signed manifest to CWC
  GET  /api/licensing/{cluster_id}/cwc-status — Full CWC connectivity check
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import get_db
from routes.auth import require_operator, require_viewer
from services.kubernetes_service import KubernetesService
from services.operator_registry import (
    get_operator_for_cluster,
    operator_connections,
)
from services.qkview_service import (
    QKViewError,
)
from services.qkview_service import (
    activate_license as legacy_activate_license,
)
from services.qkview_service import (
    check_cwc_available as legacy_check_cwc_available,
)
from services.qkview_service import (
    check_setup_status as legacy_check_cwc_api_setup_status,
)
from services.qkview_service import (
    ensure_valid_jwks as legacy_ensure_valid_jwks,
)
from services.qkview_service import (
    get_license_report as legacy_get_license_report,
)
from services.qkview_service import (
    get_license_status as legacy_get_license_status,
)
from services.qkview_service import (
    post_license_receipt as legacy_post_license_receipt,
)
from services.qkview_service import (
    renew_license as legacy_renew_license,
)
from services.qkview_service import (
    setup_cwc_api_certs as legacy_setup_cwc_api_certs,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/licensing", tags=["licensing"])


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class LicenseActivateRequest(BaseModel):
    """
    License activation request.

    The jwt field contains raw JWT object data (NOT JSON-wrapped).
    Per F5 docs, /reactivate expects raw JWT data sent with -d <JWT>.
    """
    jwt: str = Field(..., description="Raw JWT string for license activation")


class LicenseReceiptRequest(BaseModel):
    """
    License receipt request.

    The manifest field contains the raw signed manifest string from
    the F5 telemetry server. Per F5 docs, /receipt expects raw manifest
    data without curly brackets or quotes.
    """
    manifest: str = Field(..., description="Raw manifest string from F5 telemetry server")


class LicenseRenewRequest(BaseModel):
    """
    License renewal request.

    Default: Clean license switch via CWC /reactivate API.
    force=True: Nuclear renewal (CPCL cleanup + CWC restart + reactivate).
    """
    jwt: str = Field(..., description="Raw JWT string for the new/renewed license")
    force: bool = Field(
        False,
        description=(
            "If true, perform nuclear renewal: delete all CPCL state secrets, "
            "restart CWC pod, then reactivate. Use only when CWC is unresponsive. "
            "Default (false) uses the clean API-based switch."
        ),
    )


class LicenseActionResponse(BaseModel):
    """Response for license mutation endpoints (activate/receipt)."""

    success: bool
    operator_dispatch: bool | None = None

    class Config:
        extra = "allow"


class LicenseReportResponse(BaseModel):
    """Response for GET /api/licensing/{cluster_id}/report."""

    success: bool
    raw: str | None = None
    operator_dispatch: bool | None = None

    class Config:
        extra = "allow"


class LicenseStatusResponse(BaseModel):
    """Response for GET /api/licensing/{cluster_id}/status."""

    success: bool
    operator_dispatch: bool | None = None

    class Config:
        extra = "allow"


class CWCStatusResponse(BaseModel):
    """Response for GET /api/licensing/{cluster_id}/cwc-status."""

    cwc_service_found: bool
    cwc_reachable: bool
    certs_mounted: bool
    setup_complete: bool
    cert_manager_available: bool
    message: str | None = None
    operator_dispatch: bool | None = None
    success: bool | None = None
    server_cert_exists: bool | None = None
    client_cert_exists: bool | None = None

    class Config:
        extra = "allow"


class CWCAPISetupStatusResponse(BaseModel):
    """Response for GET /api/licensing/{cluster_id}/cwc-setup-status."""

    setup_complete: bool
    cert_manager_available: bool
    server_cert_exists: bool
    client_cert_exists: bool
    message: str
    certs_mounted: bool | None = None
    operator_dispatch: bool | None = None


class CWCAPISetupResponse(BaseModel):
    """Response for POST /api/licensing/{cluster_id}/cwc-setup."""

    success: bool
    message: str | None = None
    steps: list[dict[str, object]] | None = None
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
    Raises HTTPException if the operator IS connected but the command fails
    with a non-recoverable error.
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
        # Operator was connected but command failed — log and fall back to legacy
        logger.warning(
            f"Operator dispatch failed for {action} on cluster {cluster_id}: {e}. "
            "Falling back to legacy CWC path."
        )
        return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/{cluster_id}/status",
    response_model=LicenseStatusResponse,
    dependencies=[Depends(require_viewer)],
)
async def get_license_status_endpoint(
    cluster_id: int,
    force: bool = False,
    db: Session = Depends(get_db),
):
    """Get CWC license and telemetry status for a cluster."""
    from core.cache import cache

    cache_key = f"license:status:{cluster_id}"
    if not force:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    # Try operator path
    result = await _try_operator_dispatch(
        db, cluster_id, "cwc_license_status",
    )
    if result is not None:
        if not result.get("success"):
            raise HTTPException(
                status_code=502,
                detail=result.get("error_message", "Failed to get license status"),
            )
        response = {**result, "operator_dispatch": True}
        cache.set(cache_key, response, ttl_seconds=30)
        return response

    # Legacy fallback
    try:
        k8s_service = KubernetesService(db)
        return legacy_get_license_status(k8s_service, cluster_id, force=force)
    except QKViewError as e:
        raise HTTPException(
            status_code=e.status_code or 502,
            detail=str(e),
        )


@router.get(
    "/{cluster_id}/report",
    response_model=LicenseReportResponse,
    dependencies=[Depends(require_viewer)],
)
async def get_license_report_endpoint(
    cluster_id: int,
    force: bool = False,
    db: Session = Depends(get_db),
):
    """
    Get CWC telemetry report for a cluster.

    The report is only available when CWC telemetry state is
    "Config Report Ready to Download".
    """
    from core.cache import cache

    cache_key = f"license:report:{cluster_id}"
    if not force:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    # Try operator path
    result = await _try_operator_dispatch(
        db, cluster_id, "cwc_license_report", timeout=30.0,
    )
    if result is not None:
        if not result.get("success"):
            raise HTTPException(
                status_code=502,
                detail=result.get("error_message", "Failed to get license report"),
            )
        response = {**result, "operator_dispatch": True}
        cache.set(cache_key, response, ttl_seconds=60)
        return response

    # Legacy fallback
    try:
        k8s_service = KubernetesService(db)
        return legacy_get_license_report(k8s_service, cluster_id, force=force)
    except QKViewError as e:
        raise HTTPException(
            status_code=e.status_code or 502,
            detail=str(e),
        )


@router.post(
    "/{cluster_id}/activate",
    response_model=LicenseActionResponse,
    dependencies=[Depends(require_operator)],
)
async def activate_license_endpoint(
    cluster_id: int,
    request: LicenseActivateRequest,
    db: Session = Depends(get_db),
):
    """
    Reactivate/switch CWC license on a cluster.

    Per F5 docs, this sends raw JWT data to the CWC /reactivate endpoint.
    Use this to activate an evaluation license, switch from eval to paid,
    or update an existing license.
    """
    from core.cache import cache

    # Try operator path
    result = await _try_operator_dispatch(
        db, cluster_id, "cwc_license_activate",
        payload={"jwt": request.jwt},
    )
    if result is not None:
        if not result.get("success"):
            raise HTTPException(
                status_code=502,
                detail=result.get("error_message", "Failed to activate license"),
            )
        cache.delete(f"license:status:{cluster_id}")
        cache.delete(f"license:report:{cluster_id}")
        return {**result, "operator_dispatch": True}

    # Legacy fallback
    try:
        k8s_service = KubernetesService(db)
        result = legacy_activate_license(k8s_service, cluster_id, request.jwt)
    except QKViewError as e:
        raise HTTPException(
            status_code=e.status_code or 502,
            detail=str(e),
        )
    if not result.get("success"):
        raise HTTPException(
            status_code=502,
            detail=result.get("error_message", "License activation failed"),
        )
    cache.delete(f"license:status:{cluster_id}")
    cache.delete(f"license:report:{cluster_id}")
    return result


@router.post(
    "/{cluster_id}/renew",
    response_model=LicenseActionResponse,
    dependencies=[Depends(require_operator)],
)
async def renew_license_endpoint(
    cluster_id: int,
    request: LicenseRenewRequest,
    db: Session = Depends(get_db),
):
    """
    Renew or switch a BNK license.

    Default (force=false): Clean license switch via CWC /reactivate API.
      - No CWC downtime, telemetry history preserved.
      - Validates JWKS ConfigMap and patches if needed.
      - Patches JWT into cpcl-config-cm for persistence.
      - Sends JWT to CWC /reactivate endpoint.

    Force (force=true): Nuclear renewal for broken CWC state.
      - Deletes all CPCL state secrets (resets licensing state machine).
      - Restarts CWC pod (~2 min downtime).
      - Reactivates with new JWT.
      - Use only when CWC is unresponsive or in a wedged state.

    Use this for license renewals (eval or paid). For first-time activation
    where no previous license state exists, use POST /activate instead.
    """
    # Try operator path
    from core.cache import cache

    action = "cwc_license_force_renew" if request.force else "cwc_license_renew"
    timeout = 180.0 if request.force else 60.0
    result = await _try_operator_dispatch(
        db, cluster_id, action,
        payload={"jwt": request.jwt, "force": request.force},
        timeout=timeout,
    )
    if result is not None:
        if not result.get("success"):
            raise HTTPException(
                status_code=502,
                detail=result.get("error_message", "Failed to renew license"),
            )
        cache.delete(f"license:status:{cluster_id}")
        cache.delete(f"license:report:{cluster_id}")
        return {**result, "operator_dispatch": True}

    # Legacy fallback
    try:
        k8s_service = KubernetesService(db)
        result = legacy_renew_license(
            k8s_service, cluster_id, request.jwt, force=request.force,
        )
    except QKViewError as e:
        raise HTTPException(
            status_code=e.status_code or 502,
            detail=str(e),
        )
    if not result.get("success"):
        raise HTTPException(
            status_code=502,
            detail=result.get("error_message", "License renewal failed"),
        )
    cache.delete(f"license:status:{cluster_id}")
    cache.delete(f"license:report:{cluster_id}")
    return result


@router.post(
    "/{cluster_id}/receipt",
    response_model=LicenseActionResponse,
    dependencies=[Depends(require_operator)],
)
async def post_license_receipt_endpoint(
    cluster_id: int,
    request: LicenseReceiptRequest,
    db: Session = Depends(get_db),
):
    """
    Send a signed manifest (receipt) back to CWC.

    After downloading the telemetry report (/report), sending it to
    the F5 licensing server, and receiving a signed manifest back,
    use this endpoint to deliver the manifest to CWC.
    """
    from core.cache import cache

    # Try operator path
    result = await _try_operator_dispatch(
        db, cluster_id, "cwc_license_receipt",
        payload={"manifest": request.manifest},
    )
    if result is not None:
        if not result.get("success"):
            raise HTTPException(
                status_code=502,
                detail=result.get("error_message", "Failed to post license receipt"),
            )
        cache.delete(f"license:report:{cluster_id}")
        return {**result, "operator_dispatch": True}

    # Legacy fallback
    try:
        k8s_service = KubernetesService(db)
        result = legacy_post_license_receipt(
            k8s_service, cluster_id, request.manifest,
        )
    except QKViewError as e:
        raise HTTPException(
            status_code=e.status_code or 502,
            detail=str(e),
        )
    if not result.get("success"):
        raise HTTPException(
            status_code=502,
            detail=result.get("error_message", "License receipt failed"),
        )
    cache.delete(f"license:report:{cluster_id}")
    return result


@router.post(
    "/{cluster_id}/validate-jwks",
    response_model=LicenseActionResponse,
    dependencies=[Depends(require_operator)],
)
async def validate_jwks_endpoint(
    cluster_id: int,
    db: Session = Depends(get_db),
):
    """
    Validate and fix the JWKS ConfigMap (cpcl-key-cm) on a cluster.

    The FLO Helm chart creates cpcl-key-cm with placeholder JWKS values
    when license.modulus and license.x5c are not set. This causes all
    licensing to fail with "illegal base64 data at input byte 0".

    This endpoint checks the ConfigMap and patches it with real F5 public
    keys if needed. Idempotent — safe to call repeatedly.
    """
    try:
        k8s_service = KubernetesService(db)
        cluster = k8s_service.get_cluster(cluster_id)
        api_client = k8s_service.load_kubeconfig(cluster)
        result = legacy_ensure_valid_jwks(api_client)
        return {"success": True, **result}
    except QKViewError as e:
        raise HTTPException(
            status_code=e.status_code or 502,
            detail=str(e),
        )


@router.get(
    "/{cluster_id}/cwc-setup-status",
    response_model=CWCAPISetupStatusResponse,
    dependencies=[Depends(require_viewer)],
)
async def get_cwc_setup_status_endpoint(
    cluster_id: int,
    db: Session = Depends(get_db),
):
    """Check whether shared CWC API mTLS setup has been completed."""
    result = await _try_operator_dispatch(db, cluster_id, "cwc_check_status")
    if result is not None:
        return {
            "setup_complete": result.get("setup_complete", False),
            "cert_manager_available": result.get("cert_manager_available", False),
            "server_cert_exists": result.get("server_cert_exists", False),
            "client_cert_exists": result.get("client_cert_exists", False),
            "certs_mounted": result.get("certs_mounted", False),
            "message": (
                "CWC API mTLS setup is complete"
                if result.get("setup_complete")
                else "CWC API mTLS setup is required"
            ),
            "operator_dispatch": True,
        }

    k8s_service = KubernetesService(db)
    return legacy_check_cwc_api_setup_status(k8s_service, cluster_id)


@router.post(
    "/{cluster_id}/cwc-setup",
    response_model=CWCAPISetupResponse,
    dependencies=[Depends(require_operator)],
)
async def run_cwc_setup_endpoint(
    cluster_id: int,
    db: Session = Depends(get_db),
):
    """Run one-time cert-manager setup for the shared CWC REST API mTLS certs."""
    result = await _try_operator_dispatch(
        db, cluster_id, "cwc_setup_certs", timeout=120.0,
    )
    if result is not None:
        if not result.get("success"):
            raise HTTPException(
                status_code=502,
                detail=result.get("error_message", "CWC cert setup failed via operator"),
            )
        return {**result, "operator_dispatch": True}

    k8s_service = KubernetesService(db)
    return legacy_setup_cwc_api_certs(k8s_service, cluster_id)


@router.get(
    "/{cluster_id}/cwc-status",
    response_model=CWCStatusResponse,
    dependencies=[Depends(require_viewer)],
)
async def get_cwc_status_endpoint(
    cluster_id: int, db: Session = Depends(get_db),
):
    """
    Full CWC connectivity and certificate status check.

    Returns: certs_mounted, cwc_service_found, cwc_reachable, setup_complete,
    cert_manager_available, and the full CWC /status response if reachable.
    """
    # Try operator path
    result = await _try_operator_dispatch(
        db, cluster_id, "cwc_check_status", timeout=30.0,
    )
    if result is not None:
        return {**result, "operator_dispatch": True}

    # Legacy fallback — check CWC availability + cert-manager via kubeconfig
    try:
        k8s_service = KubernetesService(db)
        available = legacy_check_cwc_available(k8s_service, cluster_id)
        cwc_found = available.get("available", False)

        # Also check cert-manager availability (ClusterIssuer exists)
        cert_manager_ok = False
        try:
            cluster = k8s_service.get_cluster(cluster_id)
            api_client = k8s_service.load_kubeconfig(cluster)
            from services.qkview_service import _check_cert_manager_available
            cert_manager_ok = _check_cert_manager_available(api_client)
        except Exception:
            pass

        # Also check if the CWC licensing cert (cwc-license-certs) exists.
        # This is created by FLO/BNK and is separate from the QKView certs
        # that BNK-Forge creates.  If it exists, licensing mTLS is available
        # even without the BNK-Forge-specific QKView cert setup.
        cwc_license_certs_ok = False
        try:
            from services.qkview_service import (
                CWC_LICENSE_CERTS_SECRET,
                _detect_cwc_namespace,
                _secret_exists,
            )
            cwc_ns = _detect_cwc_namespace(api_client)
            cwc_license_certs_ok = _secret_exists(api_client, CWC_LICENSE_CERTS_SECRET, cwc_ns)
        except Exception:
            pass

        response = {
            "cwc_service_found": cwc_found,
            "cwc_reachable": cwc_found,
            "certs_mounted": cwc_found or cwc_license_certs_ok,
            "setup_complete": cwc_found,
            "cert_manager_available": cert_manager_ok,
            "message": available.get("message", ""),
        }

        # When CWC is reachable, include the raw CWC /status payload so callers
        # get the full health check, not just boolean flags (FEAT-0261 / ERR-0008).
        if cwc_found:
            try:
                from services.qkview_service import get_license_status
                response["status"] = get_license_status(k8s_service, cluster_id)
            except Exception as status_err:
                logger.debug("Could not fetch CWC /status payload: %s", status_err)

        return response
    except QKViewError as e:
        return {
            "cwc_service_found": False,
            "cwc_reachable": False,
            "certs_mounted": False,
            "setup_complete": False,
            "cert_manager_available": False,
            "message": str(e),
        }
