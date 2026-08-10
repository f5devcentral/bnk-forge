"""
BNK Platform Recovery routes.

Two recovery workflows for issues that commonly occur after cluster reboots:

1. CWC Cert Re-sync — The CWC pod reads `cwc-license-certs` via K8s API on
   startup. After a reboot, the CWC may revert to the original F5 self-signed
   certs, breaking mTLS from our cert-manager client certs. This endpoint
   re-copies cert-manager certs into `cwc-license-certs` and restarts CWC.

2. Platform Restart — After a reboot, the controller may fail to push config
   to TMM via gRPC (startup ordering issue). VLANs get stuck as
   "Programmed: False (Failed)". Restarting the controller forces a
   re-reconcile. Optionally restart FLO and/or TMM too.

These are safe idempotent operations — they just restart pods that the
Deployment controller will recreate.
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from routes.auth import require_operator
from services.kubernetes_service import KubernetesService
from services.qkview_service import (
    CWC_LICENSE_CERTS_SECRET,
    CWC_SERVER_CERT_SECRET,
    QKViewError,
    _cleanup_all_client_pods,
    _copy_cert_to_cwc_license_secret,
    _detect_cwc_namespace,
    _restart_cwc_pod,
    _wait_for_secret,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["k8s-recovery"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class CWCCertResyncResponse(BaseModel):
    """Response for CWC cert re-sync."""

    success: bool
    message: str
    steps: list[dict] = Field(default_factory=list)


class PlatformRestartRequest(BaseModel):
    """Request for platform restart — choose which components to restart."""

    restart_controller: bool = Field(
        default=True,
        description="Restart the CNE Controller pod (fixes VLAN/route gRPC sync issues)",
    )
    restart_flo: bool = Field(
        default=False,
        description="Restart the FLO lifecycle operator pod",
    )
    restart_tmm: bool = Field(
        default=False,
        description="Restart the TMM pod (causes brief traffic interruption!)",
    )


class PlatformRestartResponse(BaseModel):
    """Response for platform restart."""

    success: bool
    message: str
    restarted: list[dict] = Field(default_factory=list)


class RecoveryStatusResponse(BaseModel):
    """Pre-flight check — what needs recovery?"""

    cwc_cert_stale: bool = Field(
        description="True if cwc-license-certs doesn't match cert-manager certs"
    )
    cwc_cert_status: str = Field(
        default="unknown",
        description=(
            "ok | stale | not_applicable | unknown. 'not_applicable' means CWC "
            "certs are managed outside Forge (direct-helm install) — no Forge "
            "cert-manager secret to compare against."
        ),
    )
    vlans_failed: bool = Field(
        description="True if any VLANs have Programmed=False"
    )
    cwc_cert_detail: str | None = None
    vlans_detail: str | None = None
    platform_healthy: bool = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

from kubernetes import client as k8s_client  # noqa: E402

from services.bnk_pod_discovery import (  # noqa: E402
    BNK_NAMESPACES,
    classify_f5_pods,
    detect_install_shape,
    discover_f5_pods,
)

BNK_DEFAULT_TENANT_NAMESPACE = "f5-bnk"  # Legacy default; auto-detected at runtime

# Pod prefixes that indicate a BNK tenant namespace
# f5ingress covers the direct-helm controller (no FLO) — see bnk_pod_discovery.
_BNK_TENANT_POD_PREFIXES = ("f5-cne-controller", "f5-tmm", "f5ingress")


def _get_install_shape(api_client: k8s_client.ApiClient) -> str:
    """
    Detect how BNK was installed on this cluster ("flo" / "helm" / "unknown").

    Used to distinguish Forge deploy-flow (FLO-provisioned) clusters from
    direct-helm installs so recovery messaging doesn't read as a fault when a
    Forge-managed component (e.g. FLO, cert-manager CWC certs) simply isn't
    present by design.
    """
    try:
        tenant_pods, utils_pods = discover_f5_pods(api_client)
        classified = classify_f5_pods(tenant_pods, utils_pods)
        return detect_install_shape(classified)
    except Exception as e:
        logger.debug(f"Could not detect BNK install shape (non-fatal): {e}")
        return "unknown"


def _detect_bnk_tenant_namespace(
    api_client: k8s_client.ApiClient,
    cluster=None,
) -> str:
    """
    Auto-detect which namespace contains the BNK tenant workload pods
    (f5-cne-controller / f5ingress, f5-tmm).

    Phase 1: Check known BNK namespaces for pods matching tenant prefixes.
    Phase 2: If not found, sweep all namespaces cluster-wide to catch
             BNK components deployed in non-standard namespaces.
    Phase 3: If still not found and a cluster record is available, prefer its
             `default_namespace` (e.g. set from live discovery at registration
             time — direct-helm installs commonly don't use "f5-bnk").

    Falls back to BNK_DEFAULT_TENANT_NAMESPACE ("f5-bnk") only as a last
    resort, so existing clusters keep working.
    """
    core_v1 = k8s_client.CoreV1Api(api_client)

    # Phase 1: known namespaces (fast)
    for ns in BNK_NAMESPACES:
        try:
            pods = core_v1.list_namespaced_pod(ns)
            for pod in (pods.items or []):
                pod_name = pod.metadata.name or ""
                if any(pod_name.startswith(prefix) for prefix in _BNK_TENANT_POD_PREFIXES):
                    logger.debug(f"BNK tenant pods found in namespace '{ns}' (pod: {pod_name})")
                    return ns
        except k8s_client.rest.ApiException:
            continue

    # Phase 2: cluster-wide sweep
    try:
        all_pods = core_v1.list_pod_for_all_namespaces(_request_timeout=15)
        for pod in (all_pods.items or []):
            pod_name = pod.metadata.name or ""
            if any(pod_name.startswith(prefix) for prefix in _BNK_TENANT_POD_PREFIXES):
                ns = pod.metadata.namespace
                logger.info(
                    f"BNK tenant pods found in non-standard namespace '{ns}' "
                    f"via cluster-wide sweep (pod: {pod_name})"
                )
                return ns
    except Exception as e:
        logger.debug(f"Cluster-wide tenant pod sweep failed (non-fatal): {e}")

    # Phase 3: cluster record's own default_namespace (e.g. direct-helm installs)
    cluster_default_ns = getattr(cluster, "default_namespace", None)
    if isinstance(cluster_default_ns, str) and cluster_default_ns and cluster_default_ns != "default":
        logger.info(
            f"BNK tenant pods not found via discovery; using cluster's "
            f"default_namespace '{cluster_default_ns}'."
        )
        return cluster_default_ns

    logger.warning(
        f"BNK tenant pods not found in any namespace. "
        f"Falling back to '{BNK_DEFAULT_TENANT_NAMESPACE}'."
    )
    return BNK_DEFAULT_TENANT_NAMESPACE


def _check_cwc_cert_stale(api_client: k8s_client.ApiClient) -> tuple[bool, str, str]:
    """
    Check if cwc-license-certs is out of sync with the cert-manager server cert.

    Returns (is_stale, detail_message, status), where status is one of:
      "ok" | "stale" | "not_applicable" | "unknown".

    "not_applicable" is returned when the Forge-managed cert-manager secret is
    absent on a direct-helm install — CWC certs there are managed entirely
    outside Forge, so there's nothing for the re-sync workflow to fix.
    """
    core_v1 = k8s_client.CoreV1Api(api_client)
    cwc_ns = _detect_cwc_namespace(api_client)

    try:
        cm_secret = core_v1.read_namespaced_secret(
            CWC_SERVER_CERT_SECRET, cwc_ns,
        )
    except k8s_client.rest.ApiException:
        if _get_install_shape(api_client) == "helm":
            return (
                False,
                "CWC certs are managed outside Forge (helm/cert-manager) — no Forge re-sync needed",
                "not_applicable",
            )
        return (
            False,
            "cert-manager server cert not found — setup may not have run yet",
            "unknown",
        )

    try:
        cwc_secret = core_v1.read_namespaced_secret(
            CWC_LICENSE_CERTS_SECRET, cwc_ns,
        )
    except k8s_client.rest.ApiException:
        return True, "cwc-license-certs secret not found", "stale"

    # Compare: the server-cert in cwc-license-certs should match tls.crt from cert-manager
    cm_cert = (cm_secret.data or {}).get("tls.crt", "")
    cwc_cert = (cwc_secret.data or {}).get("server-cert", "")

    if cm_cert != cwc_cert:
        return True, "cwc-license-certs has stale certs (doesn't match cert-manager)", "stale"
    return False, "cwc-license-certs matches cert-manager — OK", "ok"


def _check_vlans_failed(api_client: k8s_client.ApiClient) -> tuple[bool, str]:
    """
    Check if any F5SPKVlan CRDs have Programmed=False.

    Returns (has_failures, detail_message).
    """
    custom_api = k8s_client.CustomObjectsApi(api_client)

    try:
        vlans = custom_api.list_cluster_custom_object(
            group="k8s.f5net.com",
            version="v1",
            plural="f5spkvlans",
        )
    except k8s_client.rest.ApiException:
        return False, "VLAN CRD not available on this cluster"

    items = vlans.get("items", [])
    if not items:
        return False, "No VLANs configured"

    failed = []
    for v in items:
        name = v.get("metadata", {}).get("name", "?")
        conditions = v.get("status", {}).get("conditions", [])
        for c in conditions:
            if c.get("type") == "Programmed" and c.get("status") != "True":
                msg = c.get("message", "unknown reason")
                failed.append(f"{name}: {msg}")

    if failed:
        return True, "; ".join(failed)
    return False, f"All {len(items)} VLANs programmed — OK"


def _find_and_restart_pods(
    api_client: k8s_client.ApiClient,
    namespace: str,
    label_selector: str,
    component_name: str,
) -> dict:
    """Find pods by label and delete them (Deployment will recreate)."""
    core_v1 = k8s_client.CoreV1Api(api_client)
    pods = core_v1.list_namespaced_pod(namespace, label_selector=label_selector)

    if not pods.items:
        return {
            "component": component_name,
            "status": "not_found",
            "message": f"No {component_name} pods found (label: {label_selector})",
        }

    deleted = []
    for p in pods.items:
        pod_name = p.metadata.name
        core_v1.delete_namespaced_pod(pod_name, namespace, grace_period_seconds=10)
        deleted.append(pod_name)
        logger.info(f"Recovery: deleted {component_name} pod {pod_name}")

    return {
        "component": component_name,
        "status": "restarted",
        "deleted_pods": deleted,
        "message": f"Deleted {len(deleted)} {component_name} pod(s) — Deployment will recreate",
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/k8s/clusters/{cluster_id}/recovery/status",
    response_model=RecoveryStatusResponse,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("check recovery status")
def get_recovery_status(
    cluster_id: int,
    db: Session = Depends(get_db),
):
    """
    Pre-flight check: what (if anything) needs recovery on this cluster?

    Checks:
    - CWC cert staleness (cwc-license-certs vs cert-manager)
    - VLAN programming failures (Programmed: False)
    """
    k8s_service = KubernetesService(db)
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)

    cert_stale, cert_detail, cert_status = _check_cwc_cert_stale(api_client)
    vlans_failed, vlans_detail = _check_vlans_failed(api_client)

    return RecoveryStatusResponse(
        cwc_cert_stale=cert_stale,
        cwc_cert_status=cert_status,
        vlans_failed=vlans_failed,
        cwc_cert_detail=cert_detail,
        vlans_detail=vlans_detail,
        platform_healthy=not cert_stale and not vlans_failed,
    )


@router.post(
    "/k8s/clusters/{cluster_id}/recovery/cwc-certs",
    response_model=CWCCertResyncResponse,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("resync CWC certs")
def resync_cwc_certs(
    cluster_id: int,
    db: Session = Depends(get_db),
):
    """
    Re-sync cwc-license-certs from cert-manager and restart CWC.

    This is the recovery action for the CWC SSL cert mismatch that
    occurs after cluster reboots. Steps:
      1. Read cert-manager server cert (bnk-forge-cwc-rest-api-tls)
      2. Copy into cwc-license-certs (server-cert, server-key, ca-root-cert)
      3. Restart CWC pod (so it re-reads the updated secret)
      4. Clean up stale agent pods (they may have old certs cached)
    """
    k8s_service = KubernetesService(db)
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)

    cwc_ns = _detect_cwc_namespace(api_client)
    steps: list[dict] = []

    # Step 1: Check if cert-manager server cert exists
    try:
        cert_data = _wait_for_secret(
            api_client, CWC_SERVER_CERT_SECRET, cwc_ns, timeout_sec=10,
        )
        steps.append({"step": "read_cert_manager_cert", "status": "ok"})
    except QKViewError as e:
        if _get_install_shape(api_client) == "helm":
            return CWCCertResyncResponse(
                success=True,
                message=(
                    "CWC certs are managed outside Forge (helm/cert-manager) on "
                    "this cluster — no Forge re-sync needed."
                ),
                steps=[{
                    "step": "read_cert_manager_cert",
                    "status": "skipped",
                    "detail": "No Forge-managed cert-manager secret on this cluster (direct-helm install)",
                }],
            )
        return CWCCertResyncResponse(
            success=False,
            message=f"cert-manager server cert not found: {e}. Run QKView setup first.",
            steps=[{"step": "read_cert_manager_cert", "status": "failed", "error": str(e)}],
        )

    # Step 2: Copy to cwc-license-certs
    try:
        _copy_cert_to_cwc_license_secret(api_client, cert_data, cwc_ns)
        steps.append({"step": "update_cwc_license_certs", "status": "ok"})
    except QKViewError as e:
        return CWCCertResyncResponse(
            success=False,
            message=f"Failed to update cwc-license-certs: {e}",
            steps=steps + [{"step": "update_cwc_license_certs", "status": "failed", "error": str(e)}],
        )

    # Step 3: Restart CWC pod
    try:
        deleted_pod = _restart_cwc_pod(api_client, cwc_ns)
        steps.append({
            "step": "restart_cwc_pod",
            "status": "ok",
            "detail": f"Deleted pod: {deleted_pod}",
        })
    except QKViewError as e:
        steps.append({
            "step": "restart_cwc_pod",
            "status": "failed",
            "error": str(e),
        })
        # Continue — cert update was successful

    # Step 4: Clean up stale agent pods
    try:
        _cleanup_all_client_pods(api_client, cwc_ns)
        steps.append({"step": "cleanup_agent_pods", "status": "ok"})
    except Exception as e:
        steps.append({
            "step": "cleanup_agent_pods",
            "status": "warning",
            "error": str(e),
        })
        # Non-fatal — agent pod will be recreated on next request

    return CWCCertResyncResponse(
        success=True,
        message=(
            "CWC certs re-synced from cert-manager. "
            "Licensing and QKView should work after CWC pod restarts (~30s)."
        ),
        steps=steps,
    )


@router.post(
    "/k8s/clusters/{cluster_id}/recovery/platform-restart",
    response_model=PlatformRestartResponse,
    dependencies=[Depends(require_operator)],
)
@handle_route_errors("platform restart")
def platform_restart(
    cluster_id: int,
    request: PlatformRestartRequest,
    db: Session = Depends(get_db),
):
    """
    Restart BNK platform components to recover from gRPC sync issues.

    After cluster reboots, the CNE Controller may fail to push config
    to TMM via gRPC (startup ordering issue). VLANs get stuck as
    "Programmed: False (Failed)". Restarting the controller forces a
    full re-reconcile.

    Components:
    - Controller: Fixes VLAN/route/gateway Programmed=False issues
    - FLO: Fixes lifecycle operator issues
    - TMM: Nuclear option — restarts the data plane (brief traffic interruption)
    """
    k8s_service = KubernetesService(db)
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)

    restarted: list[dict] = []
    bnk_ns = _detect_bnk_tenant_namespace(api_client, cluster)

    # The controller label is a dynamic one — search by pod name prefix.
    # "f5ingress" covers the direct-helm controller (f5ingress-f5ingress),
    # which has no FLO/Forge deploy-flow naming.
    if request.restart_controller:
        core_v1 = k8s_client.CoreV1Api(api_client)
        pods = core_v1.list_namespaced_pod(bnk_ns)
        controller_pods = [
            p for p in pods.items
            if p.metadata.name.startswith("f5-cne-controller")
            or p.metadata.name.startswith("f5ingress")
        ]
        if controller_pods:
            deleted = []
            for p in controller_pods:
                core_v1.delete_namespaced_pod(
                    p.metadata.name, bnk_ns, grace_period_seconds=10,
                )
                deleted.append(p.metadata.name)
                logger.info(f"Recovery: deleted controller pod {p.metadata.name}")
            restarted.append({
                "component": "CNE Controller",
                "status": "restarted",
                "deleted_pods": deleted,
                "message": (
                    f"Deleted {len(deleted)} controller pod(s). "
                    "VLANs and routes will re-sync within ~60s."
                ),
            })
        else:
            restarted.append({
                "component": "CNE Controller",
                "status": "not_found",
                "message": f"No controller pods found in {bnk_ns} namespace",
            })

    if request.restart_flo:
        # FLO uses a known label pattern
        result = _find_and_restart_pods(
            api_client, bnk_ns,
            label_selector="app.kubernetes.io/name=f5-lifecycle-operator",
            component_name="FLO Operator",
        )
        # Fallback: try by pod name prefix if label doesn't match
        if result["status"] == "not_found":
            core_v1 = k8s_client.CoreV1Api(api_client)
            pods = core_v1.list_namespaced_pod(bnk_ns)
            flo_pods = [
                p for p in pods.items
                if "lifecycle-operator" in (p.metadata.name or "")
            ]
            if flo_pods:
                deleted = []
                for p in flo_pods:
                    core_v1.delete_namespaced_pod(
                        p.metadata.name, bnk_ns, grace_period_seconds=10,
                    )
                    deleted.append(p.metadata.name)
                result = {
                    "component": "FLO Operator",
                    "status": "restarted",
                    "deleted_pods": deleted,
                    "message": f"Deleted {len(deleted)} FLO pod(s)",
                }
            else:
                # No FLO pod anywhere — most likely a direct-helm install where
                # FLO (Forge deploy-flow) was never deployed. Not a fault.
                result = {
                    "component": "FLO Operator",
                    "status": "not_found",
                    "message": (
                        f"No FLO Operator pods found in {bnk_ns}. FLO is only "
                        "present on Forge deploy-flow installs — this is expected "
                        "on a direct-helm install, not necessarily a fault."
                    ),
                }
        restarted.append(result)

    if request.restart_tmm:
        # TMM pods are DaemonSet-managed
        core_v1 = k8s_client.CoreV1Api(api_client)
        pods = core_v1.list_namespaced_pod(bnk_ns)
        tmm_pods = [
            p for p in pods.items
            if p.metadata.name.startswith("f5-tmm")
        ]
        if tmm_pods:
            deleted = []
            for p in tmm_pods:
                core_v1.delete_namespaced_pod(
                    p.metadata.name, bnk_ns, grace_period_seconds=30,
                )
                deleted.append(p.metadata.name)
                logger.info(f"Recovery: deleted TMM pod {p.metadata.name}")
            restarted.append({
                "component": "TMM",
                "status": "restarted",
                "deleted_pods": deleted,
                "message": (
                    f"Deleted {len(deleted)} TMM pod(s). "
                    "WARNING: Traffic processing interrupted until TMM restarts (~2-3 min)."
                ),
            })
        else:
            restarted.append({
                "component": "TMM",
                "status": "not_found",
                "message": f"No TMM pods found in {bnk_ns} namespace",
            })

    component_list = ", ".join(r["component"] for r in restarted if r["status"] == "restarted")
    return PlatformRestartResponse(
        success=any(r["status"] == "restarted" for r in restarted),
        message=f"Restarted: {component_list}" if component_list else "No components restarted",
        restarted=restarted,
    )
