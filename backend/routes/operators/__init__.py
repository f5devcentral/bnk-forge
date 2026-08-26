"""
Operator Management API — operator listing, command dispatch, fleet health.

Endpoints:
  GET  /api/operators                           — List all registered operators
  GET  /api/operators/{operator_id}             — Get operator details
  DELETE /api/operators/{operator_id}           — Delete an operator
  POST /api/operators/{operator_id}/command     — Send a command to an operator
  GET  /api/operators/fleet-health              — Aggregate fleet health for all operators (UX-012)
  POST /api/operators/fleet/compare             — Compare configs between two operators (UX-012)

Note: Registration token and install-command endpoints were removed in D3-CLEANUP.
The kubeconfig-first fleet architecture (Decision D3) made them obsolete.
"""
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel

from services.operator_registry import operator_connections

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schemas (shared across sub-modules)
# ---------------------------------------------------------------------------

class OperatorResponse(BaseModel):
    id: int
    operator_id: str
    cluster_name: str
    cluster_id: int | None
    connectivity_mode: str
    connectivity_config: dict | None
    labels: dict
    is_connected: bool
    status: str
    operator_version: str | None
    kubernetes_version: str | None
    node_count: int | None
    nodes_ready: int | None
    last_heartbeat_at: str | None
    last_connected_at: str | None
    last_disconnected_at: str | None
    disconnect_reason: str | None
    last_health_at: str | None
    commands_executed: int
    commands_failed: int
    uptime_seconds: int
    created_at: str

    class Config:
        from_attributes = True


class OperatorDetailResponse(OperatorResponse):
    last_health_report: dict | None
    registration_token_name: str | None


class LinkClusterRequest(BaseModel):
    cluster_id: int  # KubernetesCluster ID to link this operator to


class SetConnectivityModeRequest(BaseModel):
    mode: str  # direct_ws, reverse_ssh, polling, ngrok_tunnel, in_cluster
    config: dict | None = None  # Mode-specific configuration


class SendCommandRequest(BaseModel):
    action: str  # apply_manifests, install_helm, scan_cluster, get_health, etc.
    payload: dict | None = None
    timeout: float = 300.0


class FanOutCommandRequest(BaseModel):
    """Send a command to multiple operators at once."""
    action: str
    payload: dict | None = None
    timeout: float = 300.0
    operator_ids: list[str] | None = None  # None = all connected operators
    labels: dict | None = None  # Filter by labels (e.g. {"env": "prod"})


class CommandResultResponse(BaseModel):
    success: bool
    command_id: str
    result: dict


class FleetOperatorHealth(BaseModel):
    """Health summary for a single operator/cluster in the fleet view."""
    operator_id: int
    operator_uuid: str
    cluster_id: int  # KubernetesCluster ID — for linking to cluster detail page
    cluster_name: str
    status: str  # healthy, warning, critical, offline
    bnk_severity: str = "unknown"
    effective_connectivity_status: str = "unknown"
    last_seen: str | None
    bnk_version: str | None
    route_count: int
    tmm_count: int
    gateway_count: int
    uptime: str
    health_summary: dict  # {"healthy": int, "warning": int, "critical": int}
    health_issues: list[dict] = []  # [{"component": str, "severity": str, "message": str}]
    kubernetes_version: str | None
    node_count: int | None
    operator_version: str | None
    connectivity_mode: str
    cloud_provider: str | None = None
    region: str | None = None
    account_id: str | None = None
    discovery_status: str | None = None
    # DPF (NVIDIA DPU) fields — P5 fleet integration
    dpf_detected: bool = False
    dpf_version: str | None = None
    dpf_status: str | None = None  # "ready" | "partial" | "not_installed" | None
    dpu_count: int = 0
    dpu_cluster_count: int = 0
    detected_platform_profile: str = "unknown"
    detected_platform_provider: str | None = None


class FleetHealthResponse(BaseModel):
    """Aggregate fleet health response."""
    total_clusters: int
    healthy: int
    warning: int
    critical: int
    offline: int
    unknown: int = 0
    operators: list[FleetOperatorHealth]
    platform_context: dict | None = None


class FleetCompareRequest(BaseModel):
    """Request to compare configs between two operators."""
    operator_a_id: int
    operator_b_id: int


class FleetCompareDifference(BaseModel):
    """Single config diff item in fleet comparison response."""

    field: str
    key: str
    value_a: object | None = None
    value_b: object | None = None


class FleetCompareResponse(BaseModel):
    """Response for fleet config comparison endpoint."""

    operator_a: str
    operator_b: str
    comparison_mode: str
    total_diffs: int
    summary: str
    differences: list[FleetCompareDifference]
    platform_context: dict | None = None


class FleetCompareResourceRef(BaseModel):
    """Structured resource reference for cluster-config comparisons."""

    kind: str
    name: str
    namespace: str | None = None


class FleetCompareChangedResource(BaseModel):
    """Changed Kubernetes resource payload in cluster-config comparisons."""

    resource: str
    kind: str
    spec_a: object | None = None
    spec_b: object | None = None


class FleetCompareChangedModule(BaseModel):
    """Changed module variables payload in cluster-config comparisons."""

    module_path: str
    variables_a: object | None = None
    variables_b: object | None = None


class FleetCompareResourcesGroup(BaseModel):
    """Resource diff grouping for cluster-config comparisons."""

    only_in_a: list[FleetCompareResourceRef]
    only_in_b: list[FleetCompareResourceRef]
    changed: list[FleetCompareChangedResource]


class FleetCompareModulesGroup(BaseModel):
    """Module diff grouping for cluster-config comparisons."""

    only_in_a: list[str]
    only_in_b: list[str]
    changed: list[FleetCompareChangedModule]


class FleetCompareClusterResponse(BaseModel):
    """Structured cluster-config comparison response."""

    operator_a: str
    operator_b: str
    comparison_mode: str
    total_diffs: int
    summary: str
    cluster_a: str
    cluster_b: str
    resources: FleetCompareResourcesGroup
    modules: FleetCompareModulesGroup
    platform_context: dict | None = None


# ---------------------------------------------------------------------------
# Helpers (shared across sub-modules)
# ---------------------------------------------------------------------------

def _get_user(request: Request) -> str:
    """Extract username from request (set by auth middleware)."""
    user = getattr(request.state, "user", None)
    if user:
        return user.get("username", "system")
    return "system"


def _dt_to_str(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat() + "Z"


def _operator_to_response(op) -> dict:
    # For polling-mode operators, check heartbeat recency instead of WS state
    is_connected_ws = operator_connections.is_connected(op.operator_id)
    is_connected_polling = False
    if op.connectivity_mode == "polling" and op.last_heartbeat_at:
        from datetime import timedelta
        heartbeat_age = (datetime.now(UTC) - op.last_heartbeat_at).total_seconds()
        is_connected_polling = heartbeat_age < 60  # Polling operator is "connected" if heartbeat within 60s

    is_connected = is_connected_ws or is_connected_polling

    return {
        "id": op.id,
        "operator_id": op.operator_id,
        "cluster_name": op.cluster_name,
        "cluster_id": op.cluster_id,
        "connectivity_mode": op.connectivity_mode or "direct_ws",
        "connectivity_config": op.connectivity_config or {},
        "labels": op.labels or {},
        "is_connected": is_connected,
        "status": "connected" if is_connected else op.status,
        "operator_version": op.operator_version,
        "kubernetes_version": op.kubernetes_version,
        "node_count": op.node_count,
        "nodes_ready": op.nodes_ready,
        "last_heartbeat_at": _dt_to_str(op.last_heartbeat_at),
        "last_connected_at": _dt_to_str(op.last_connected_at),
        "last_disconnected_at": _dt_to_str(op.last_disconnected_at),
        "disconnect_reason": op.disconnect_reason,
        "last_health_at": _dt_to_str(op.last_health_at),
        "commands_executed": op.commands_executed,
        "commands_failed": op.commands_failed,
        "uptime_seconds": op.uptime_seconds,
        "created_at": _dt_to_str(op.created_at),
    }


# ---------------------------------------------------------------------------
# Router assembly
# ---------------------------------------------------------------------------

from .commands import router as commands_router
from .crud import router as crud_router
from .fleet import router as fleet_router

router = APIRouter(prefix="/api/operators", tags=["operators"])
router.include_router(fleet_router)
router.include_router(commands_router)
router.include_router(crud_router)
