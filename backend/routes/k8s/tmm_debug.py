"""
TMM Debug Sidecar routes — exec diagnostic commands into TMM debug containers.

Provides REST endpoints for one-shot command execution in the debug sidecar
container that exists in every f5-tmm-* pod. Supports:
  - tmctl (traffic statistics)
  - configview (CR config inspection)
  - bdt_cli (networking: ARP, routes, connections)
  - Raw command execution

Architecture: Direct pod exec via kubeconfig. Does NOT use the bnk-forge-agent
pod (that's for CWC REST API access). See Decision D4.

All endpoints are read-only diagnostics → require_viewer auth.
"""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from routes.auth import require_viewer
from services.kubernetes_service import KubernetesService
from services.tmm_debug_service import (
    discover_configview_uuids,
    exec_bdt_cli,
    exec_configview,
    exec_debug_command,
    exec_tmctl,
    list_tmm_debug_pods,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["k8s-tmm-debug"])


# ============================================================================
# Pydantic Request Schemas
# ============================================================================


_NAMESPACE_DESCRIPTION = (
    "Namespace of the TMM pod. Defaults to the cluster's default_namespace "
    "(falls back to 'f5-bnk' if the cluster has none)."
)


class TMMDebugExecRequest(BaseModel):
    """Raw command execution in the debug sidecar."""
    pod_name: str = Field(..., description="TMM pod name (e.g. 'f5-tmm-znvz2')")
    namespace: str | None = Field(default=None, description=_NAMESPACE_DESCRIPTION)
    command: str = Field(..., description="Shell command string to execute")
    timeout: int = Field(default=30, ge=1, le=120, description="Command timeout in seconds")


class TMMDebugTmctlRequest(BaseModel):
    """Structured tmctl query — builds the command and parses tabular output."""
    pod_name: str = Field(..., description="TMM pod name")
    namespace: str | None = Field(default=None, description=_NAMESPACE_DESCRIPTION)
    table: str = Field(..., description="tmctl table name (e.g. 'virtual_server_stat')")
    columns: list[str] | None = Field(default=None, description="Specific columns to select")
    width: int = Field(default=200, ge=1, le=9999, description="Column width (-w flag)")
    directory: str = Field(default="blade", description="tmctl directory (-d flag)")


class TMMDebugConfigviewRequest(BaseModel):
    """configview uuid query — inspect a specific CR config."""
    pod_name: str = Field(..., description="TMM pod name")
    namespace: str | None = Field(default=None, description=_NAMESPACE_DESCRIPTION)
    uuid: str = Field(..., description="Configuration UUID to inspect")


class TMMDebugConfigviewUuidsRequest(BaseModel):
    """Discover available configview UUIDs."""
    pod_name: str = Field(..., description="TMM pod name")
    namespace: str | None = Field(default=None, description=_NAMESPACE_DESCRIPTION)


class TMMDebugBdtRequest(BaseModel):
    """bdt_cli networking diagnostic command."""
    pod_name: str = Field(..., description="TMM pod name")
    namespace: str | None = Field(default=None, description=_NAMESPACE_DESCRIPTION)
    subcommand: str = Field(..., description="bdt_cli subcommand (e.g. 'arp', 'route', 'connection list')")


def _resolve_namespace(cluster) -> str:
    """
    Resolve the TMM pod namespace when the caller doesn't pass one explicitly.

    Prefers the cluster's own `default_namespace` (set from live discovery —
    direct-helm installs commonly aren't in "f5-bnk"). Falls back to "f5-bnk"
    when the cluster has none (e.g. unset, or not a real string).
    """
    default_ns = getattr(cluster, "default_namespace", None)
    if isinstance(default_ns, str) and default_ns and default_ns != "default":
        return default_ns
    return "f5-bnk"


# ============================================================================
# Endpoints
# ============================================================================


@router.get(
    "/k8s/clusters/{cluster_id}/tmm-debug/pods",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("list TMM debug pods")
def get_tmm_debug_pods(
    cluster_id: int,
    db: Session = Depends(get_db),
):
    """
    List TMM pods with debug sidecar availability.

    Returns all f5-tmm-* pods in the cluster, indicating whether each
    has the 'debug' container available for diagnostic commands.
    """
    k8s_service = KubernetesService(db)
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)

    pods = list_tmm_debug_pods(api_client, cluster_id)

    return {
        "pods": pods,
        "count": len(pods),
        "debug_available": sum(1 for p in pods if p["has_debug"]),
    }


@router.post(
    "/k8s/clusters/{cluster_id}/tmm-debug/exec",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("exec TMM debug command")
def post_tmm_debug_exec(
    cluster_id: int,
    body: TMMDebugExecRequest,
    db: Session = Depends(get_db),
):
    """
    Execute a raw command in the debug sidecar of a TMM pod.

    For advanced users who want to run arbitrary debug sidecar commands.
    The command string is split into arguments and executed directly.
    """
    k8s_service = KubernetesService(db)
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)
    namespace = body.namespace or _resolve_namespace(cluster)

    # Split command string into list for exec
    # Use shell-like splitting to handle quoted arguments
    import shlex
    try:
        command_parts = shlex.split(body.command)
    except ValueError:
        command_parts = body.command.split()

    result = exec_debug_command(
        api_client, body.pod_name, namespace, command_parts, body.timeout
    )

    return result


@router.post(
    "/k8s/clusters/{cluster_id}/tmm-debug/tmctl",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("exec tmctl command")
def post_tmm_debug_tmctl(
    cluster_id: int,
    body: TMMDebugTmctlRequest,
    db: Session = Depends(get_db),
):
    """
    Execute a structured tmctl query and return parsed tabular output.

    Builds the tmctl command from the request body parameters, executes it,
    and parses the output into columns + rows for table rendering.
    """
    k8s_service = KubernetesService(db)
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)
    namespace = body.namespace or _resolve_namespace(cluster)

    result = exec_tmctl(
        api_client,
        body.pod_name,
        namespace,
        body.table,
        body.columns,
        body.width,
        body.directory,
        cluster_id=cluster_id,
    )

    return result


@router.post(
    "/k8s/clusters/{cluster_id}/tmm-debug/configview",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("exec configview command")
def post_tmm_debug_configview(
    cluster_id: int,
    body: TMMDebugConfigviewRequest,
    db: Session = Depends(get_db),
):
    """
    Execute 'configview uuid <uuid>' to inspect a specific CR config.
    """
    k8s_service = KubernetesService(db)
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)
    namespace = body.namespace or _resolve_namespace(cluster)

    result = exec_configview(
        api_client, body.pod_name, namespace, body.uuid, cluster_id=cluster_id
    )

    return result


@router.post(
    "/k8s/clusters/{cluster_id}/tmm-debug/configview/uuids",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("discover configview UUIDs")
def post_tmm_debug_configview_uuids(
    cluster_id: int,
    body: TMMDebugConfigviewUuidsRequest,
    db: Session = Depends(get_db),
):
    """
    Discover available configuration UUIDs via 'configview list'.
    """
    k8s_service = KubernetesService(db)
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)
    namespace = body.namespace or _resolve_namespace(cluster)

    result = discover_configview_uuids(
        api_client, body.pod_name, namespace, cluster_id=cluster_id
    )

    return result


@router.post(
    "/k8s/clusters/{cluster_id}/tmm-debug/bdt",
    dependencies=[Depends(require_viewer)],
)
@handle_route_errors("exec bdt_cli command")
def post_tmm_debug_bdt(
    cluster_id: int,
    body: TMMDebugBdtRequest,
    db: Session = Depends(get_db),
):
    """
    Execute 'bdt_cli -u -s tmm0:8850 <subcommand>' for networking diagnostics.

    Common subcommands: arp, route, connection list
    """
    k8s_service = KubernetesService(db)
    cluster = k8s_service.get_cluster(cluster_id)
    api_client = k8s_service.load_kubeconfig(cluster)
    namespace = body.namespace or _resolve_namespace(cluster)

    result = exec_bdt_cli(
        api_client, body.pod_name, namespace, body.subcommand, cluster_id=cluster_id
    )

    return result
