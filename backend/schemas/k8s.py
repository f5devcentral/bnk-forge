"""
Pydantic schemas for the Kubernetes domain.

Request schemas (ClusterCreateRequest, ResourceCreateRequest, etc.) are
defined in routes/k8s/_shared.py. This file adds RESPONSE schemas.
"""

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from models.enums import ConnectivityStatus

# Compiled at module load — used by CreateMigrationRequest.validate_kubectl_resources.
# Requires "kind/name" (slash mandatory) optionally followed by one namespace token
# (whitespace-separated).  Rejects leading dashes (kubectl flags), embedded newlines,
# and shell metacharacters.
_KUBECTL_RESOURCE_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*([ \t]+[A-Za-z0-9][A-Za-z0-9._-]*)?\Z",
    re.ASCII,
)


class PlatformCapabilities(BaseModel):
    secondary_networks: bool | None = None
    network_attachment_definitions: bool | None = None
    sriov: bool | None = None
    hugepages: bool | None = None
    gateway_api: bool | None = None
    openshift_routes: bool | None = None
    scc: bool | None = None
    machine_config: bool | None = None
    operator_framework: bool | None = None
    cloud_load_balancer: bool | None = None


class PlatformConstraints(BaseModel):
    managed_control_plane: bool | None = None
    cluster_lifecycle_external: bool | None = None
    operator_required_for_networking: bool | None = None
    privileged_workloads_require_platform_security_binding: bool | None = None
    direct_node_package_mutation_not_supported: bool | None = None

# =============================================================================
# Cluster Responses
# =============================================================================

class ClusterSummary(BaseModel):
    """Single cluster in list response."""
    id: int
    name: str
    context: str | None = None
    api_server: str | None = None
    version: str | None = None
    status: str = "active"
    cloud_provider: str | None = None
    detected_platform_profile: str = "unknown"
    detected_platform_provider: str | None = None
    platform_capabilities: PlatformCapabilities = Field(default_factory=PlatformCapabilities)
    platform_constraints: PlatformConstraints = Field(default_factory=PlatformConstraints)
    region: str | None = None
    default_namespace: str = "default"
    project_id: int | None = None
    ssh_tunnel_enabled: bool = False
    ssh_remote_k8s_host: str | None = None
    ssh_remote_k8s_port: int | None = None
    ssh_credential_id: int | None = None
    ssh_host_override: str | None = None
    enabled_prerequisites: list[str] | None = None
    node_count: int | None = None
    last_synced_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ClusterListResponse(BaseModel):
    """Response for GET /api/k8s/clusters."""
    clusters: list[ClusterSummary]
    count: int | None = None


class ClusterDetailResponse(BaseModel):
    """Response for GET /api/k8s/clusters/{id}."""
    id: int
    name: str
    context: str | None = None
    api_server: str | None = None
    version: str | None = None
    status: str = "active"
    cloud_provider: str | None = None
    detected_platform_profile: str = "unknown"
    detected_platform_provider: str | None = None
    platform_capabilities: PlatformCapabilities = Field(default_factory=PlatformCapabilities)
    platform_constraints: PlatformConstraints = Field(default_factory=PlatformConstraints)
    region: str | None = None
    default_namespace: str = "default"
    project_id: int | None = None
    ssh_tunnel_enabled: bool = False
    ssh_remote_k8s_host: str | None = None
    ssh_remote_k8s_port: int | None = None
    ssh_credential_id: int | None = None
    ssh_host_override: str | None = None
    enabled_prerequisites: list[str] | None = None
    meta_data: dict[str, Any] | None = None
    last_synced_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ClusterCreateResponse(BaseModel):
    """Response for POST /api/projects/{id}/k8s/clusters — returns created cluster."""
    id: int
    name: str
    context: str | None = None
    api_server: str | None = None
    cloud_provider: str | None = None
    detected_platform_profile: str = "unknown"
    detected_platform_provider: str | None = None
    platform_capabilities: PlatformCapabilities = Field(default_factory=PlatformCapabilities)
    platform_constraints: PlatformConstraints = Field(default_factory=PlatformConstraints)
    region: str | None = None
    default_namespace: str = "default"
    status: str = "active"
    project_id: int | None = None


class ClusterOperationResponse(BaseModel):
    """Generic response for cluster mutations (delete)."""
    success: bool = True
    message: str | None = None
    cluster_id: int | None = None


class ClusterRefreshResponse(BaseModel):
    """Response for POST /api/k8s/clusters/{id}/refresh-kubeconfig."""
    message: str
    cluster_id: int
    cluster_name: str | None = None
    region: str | None = None
    refresh_method: str | None = None


class ClusterConnectionTestResponse(BaseModel):
    """Response for POST /api/k8s/clusters/{id}/test."""
    success: bool
    message: str
    cluster_name: str | None = None
    version: str | None = None
    api_server: str | None = None
    cloud_provider: str | None = None
    region: str | None = None
    status_code: int | None = None


# =============================================================================
# Namespace / Node Responses
# =============================================================================

class NamespaceInfo(BaseModel):
    """Single namespace."""
    name: str
    status: str | None = None
    labels: dict[str, str] | None = None
    created_at: str | None = None


class NamespaceListResponse(BaseModel):
    """Response for GET /api/k8s/clusters/{id}/namespaces."""
    namespaces: list[NamespaceInfo]
    count: int | None = None
    cluster_id: int


class NodeCountResponse(BaseModel):
    """Response for GET /api/k8s/clusters/{id}/nodes/count."""
    node_count: int
    cluster_id: int


# =============================================================================
# Resource Responses
# =============================================================================

class K8sResourceItem(BaseModel):
    """Single K8s resource in list responses."""
    name: str
    namespace: str | None = None
    kind: str | None = None
    api_version: str | None = None
    status: str | None = None
    age: str | None = None
    labels: dict[str, str] | None = None
    annotations: dict[str, str] | None = None
    conditions: list[dict[str, Any]] | None = None


class K8sResourceListResponse(BaseModel):
    """Response for GET /api/k8s/clusters/{id}/resources/{type}."""
    resources: list[K8sResourceItem]
    resource_type: str
    namespace: str | None = None
    cluster_id: int


class K8sResourceOperationResponse(BaseModel):
    """Generic response for resource CRUD operations."""
    success: bool = True
    message: str
    resource_name: str | None = None
    namespace: str | None = None


class ResourceListEnvelope(BaseModel):
    """Typed envelope for GET /api/k8s/clusters/{id}/resources/{type}."""

    resources: list[dict[str, Any]]
    count: int
    resource_type: str
    namespace: str | None = None
    cluster_id: int
    info: str | None = None


class ResourceDescribeEnvelope(BaseModel):
    """Typed envelope for GET /api/k8s/clusters/{id}/resources/{type}/{name}/describe."""

    name: str
    namespace: str | None = None
    kind: str
    api_version: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    spec: dict[str, Any] = Field(default_factory=dict)
    status: dict[str, Any] = Field(default_factory=dict)
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    relationships: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    cluster_id: int
    resource_type: str
    resource_name: str


class PodLogsResponse(BaseModel):
    """Typed response for GET /api/k8s/clusters/{id}/pods/{name}/logs."""

    logs: str
    pod_name: str
    namespace: str
    cluster_id: int
    container: str | None = None


class ClusterEventsResponse(BaseModel):
    """Typed response for GET /api/k8s/clusters/{id}/events."""

    events: list[dict[str, Any]]
    count: int
    cluster_id: int
    namespace: str | None = None
    resource_type: str | None = None
    resource_name: str | None = None
    event_type: str | None = None


class PodTopResponse(BaseModel):
    """Typed response for GET /api/k8s/clusters/{id}/top/pods."""

    available: bool
    metrics: list[dict[str, Any]] | None = None
    error: str | None = None
    cluster_id: int
    namespace: str | None = None
    sort_by: str | None = None


class NodeTopResponse(BaseModel):
    """Typed response for GET /api/k8s/clusters/{id}/top/nodes."""

    available: bool
    metrics: list[dict[str, Any]] | None = None
    error: str | None = None
    cluster_id: int
    sort_by: str | None = None


class PodRestartResponse(BaseModel):
    """Typed response for POST /api/k8s/clusters/{id}/pods/{name}/restart."""

    success: bool
    message: str
    pod_name: str
    namespace: str
    cluster_id: int


# =============================================================================
# Scan / Adaptive Module Responses
# =============================================================================

class ClusterScanResponse(BaseModel):
    """Response for POST /api/k8s/clusters/{id}/scan."""
    success: bool
    message: str
    scan_results: dict[str, Any] | None = None


class ClusterScanEnvelope(BaseModel):
    """Typed envelope for POST /api/k8s/clusters/{id}/scan."""

    cluster_id: int
    cluster_name: str
    cluster_info: dict[str, Any]
    prerequisites: dict[str, Any]
    bnk_install: dict[str, Any]
    recommendations: list[dict[str, Any]]
    enabled_prerequisites: list[str] = Field(default_factory=list)
    scan_metadata: dict[str, Any]
    platform_context: dict[str, Any] | None = None


# =============================================================================
# HugePages Deploy (Recommendation Action)
# =============================================================================

class HugePagesDeployRequest(BaseModel):
    """Request body for POST /api/k8s/clusters/{id}/recommendations/hugepages/deploy.

    ``size`` drives the per-node 2Mi page count via the F5 BNK sizing table
    (small=1536, medium=3072, large=6144, max=12288).
    """
    size: Literal["small", "medium", "large", "max"]
    namespace: str = Field(default="kube-system", max_length=253)
    image: str | None = Field(
        default=None,
        description=(
            "Override for the tuner container image. Defaults to the "
            "BNK_FORGE_HUGEPAGES_IMAGE env var, then busybox:1.36.1. "
            "Airgapped clusters should point this at a mirrored image."
        ),
        max_length=512,
    )


class HugePagesDeployResponse(BaseModel):
    """Response for the HugePages deploy action."""
    success: bool
    job_name: str
    namespace: str
    size: str
    page_count: int
    memory_gib_per_node: float
    target_node_count: int
    target_nodes: list[str]
    image: str
    message: str


# =============================================================================
# Node Readiness Probe (issue #387 part A — detection only)
# =============================================================================

class NodeReadinessProbeRequest(BaseModel):
    """Request body for POST /api/k8s/clusters/{id}/node-readiness/probe."""
    namespace: str = Field(default="kube-system", max_length=253)
    image: str | None = Field(
        default=None,
        description=(
            "Override for the probe container image. Defaults to the "
            "BNK_FORGE_NODEPROBE_IMAGE env var, then busybox:1.36.1. "
            "Airgapped clusters should point this at a mirrored image."
        ),
        max_length=512,
    )


class NodeCniPlugins(BaseModel):
    """Presence of the CNI delegate plugins F5 BNK's data plane requires."""
    macvlan: bool = False
    host_device: bool = False
    ipvlan: bool = False


class NodeReadinessResult(BaseModel):
    """Per-node CNI/core_pattern/hugepages readiness, from the privileged probe."""
    node: str
    cni_plugins: NodeCniPlugins
    cni_ok: bool
    core_pattern: str | None = None
    core_pattern_ok: bool
    hugepages_2mi: str | None = None
    hugepages_ok: bool


class NodeReadinessProbeResponse(BaseModel):
    """Response for POST /api/k8s/clusters/{id}/node-readiness/probe."""
    cluster_id: int
    job_name: str
    is_kind: bool
    is_local: bool
    nodes: list[NodeReadinessResult]
    all_ready: bool
    message: str


# =============================================================================
# Resource Type Catalog
# =============================================================================

class ResourceTypeInfo(BaseModel):
    """Single supported resource type."""
    name: str
    api_version: str
    kind: str
    namespaced: bool
    verbs: list[str] | None = None


class ResourceTypeListResponse(BaseModel):
    """Response for GET /api/k8s/resource-types."""
    resource_types: list[ResourceTypeInfo]


class ResourceTypeCatalogItem(BaseModel):
    """Single supported resource type from core.k8s_resource_registry."""

    key: str
    kind: str
    api_group: str
    api_version: str
    plural: str
    namespaced: bool
    display_name: str
    description: str
    category: str


class ResourceTypeCatalogResponse(BaseModel):
    """Typed response for GET /api/k8s/resource-types."""

    resource_types: list[ResourceTypeCatalogItem]
    count: int


# =============================================================================
# Tunnel Responses
# =============================================================================

class TunnelStatus(BaseModel):
    """SSH tunnel status for a cluster."""
    cluster_id: int
    cluster_name: str | None = None
    active: bool = False
    local_port: int | None = None
    ssh_host: str | None = None
    uptime_seconds: float | None = None


class TunnelListResponse(BaseModel):
    """Response for GET /api/k8s/tunnels."""
    tunnels: list[TunnelStatus]


# =============================================================================
# Connectivity Probe Responses
# =============================================================================

class ConnectivityIcmpResult(BaseModel):
    """ICMP ping result."""
    reachable: bool = False
    latency_ms: float | None = None


class ConnectivityTcpResult(BaseModel):
    """TCP port probe result."""
    open: bool = False
    connect_ms: float | None = None
    port: int | None = None


class ConnectivityK8sApiResult(BaseModel):
    """K8s API /version probe result."""
    accessible: bool = False
    version: str | None = None
    status_code: int | None = None


class ClusterConnectivityResponse(BaseModel):
    """Response for GET /api/k8s/clusters/{id}/connectivity."""
    cluster_id: int
    cluster_name: str
    api_server: str | None = None
    status: ConnectivityStatus  # Canonical: connected | reachable | partial | unreachable | unknown
    message: str
    suggestion: str | None = None
    icmp: ConnectivityIcmpResult
    tcp: ConnectivityTcpResult
    k8s_api: ConnectivityK8sApiResult
    checked_at: str


class ConnectivitySummary(BaseModel):
    """Summary counts for batch connectivity check (canonical vocabulary)."""
    total: int = 0
    connected: int = 0
    reachable: int = 0
    partial: int = 0
    unreachable: int = 0
    unknown: int = 0


class BatchConnectivityResponse(BaseModel):
    """Response for GET /api/k8s/clusters/connectivity."""
    results: list[ClusterConnectivityResponse]
    summary: ConnectivitySummary


# =============================================================================
# CRD Discovery (D-018)
# =============================================================================

class CRDInfo(BaseModel):
    """Single discovered CRD, optionally enriched with static registry metadata."""

    name: str
    """<plural>.<group> — stable identity key consumed by P2 CNF dashboard."""
    kind: str
    plural: str
    group: str
    version: str | None = None
    namespaced: bool = False
    display_name: str | None = None
    category: str | None = None
    source: str = "discovered"
    """'discovered' | 'registry-enriched'"""


class CrdListEnvelope(BaseModel):
    """Response for GET /api/k8s/clusters/{cluster_id}/crds."""

    crds: list[CRDInfo]
    count: int
    cluster_id: int
    group_filter: list[str] | None = None
    info: str | None = None
    """Non-null only for the reachable-but-empty case (no error, just no matches)."""


# =============================================================================
# Topology (D-018 P4)
# =============================================================================

class TopologyNode(BaseModel):
    """A single node in the topology graph (Service, Pod, or owning workload)."""

    id: str
    """Stable unique id: f"{kind}/{namespace}/{name}"."""
    kind: str
    """Pod | Service | Deployment | StatefulSet | DaemonSet"""
    name: str
    namespace: str
    uid: str | None = None
    severity: str = "unknown"
    """healthy | degraded | unhealthy | unknown"""
    meta: dict[str, Any] = Field(default_factory=dict)
    """Free meta bag: phase, ready_replicas, replicas, container_count, etc."""


class TopologyEdge(BaseModel):
    """A directed edge in the topology graph."""

    id: str
    source: str
    """id of the source TopologyNode."""
    target: str
    """id of the target TopologyNode."""
    kind: str
    """'selects' (Service→Pod, animated) | 'owns' (Workload→Pod, static)."""


class TopologyGraphResponse(BaseModel):
    """Response for GET /api/k8s/clusters/{cluster_id}/topology."""

    nodes: list[TopologyNode]
    edges: list[TopologyEdge]
    cluster_id: int
    namespace: str
    info: str | None = None
    """Non-null when results are truncated or namespace-cardinality guard fires."""


# =============================================================================
# Proxy Translation (D-021 P2)
# =============================================================================


class ProxyTranslateRequest(BaseModel):
    """Request body for POST /api/k8s/clusters/{id}/proxies/translate.

    Identifies which proxy (by class + namespace) to translate.  The handler
    re-fetches the live Ingress/HTTPRoute objects from the cluster before
    calling translate_to_bnk().
    """

    proxy_type: str
    """One of 'nginx', 'haproxy', 'envoy', 'f5-bnk', or an unknown controller string."""
    source_kind: str
    """'IngressClass' or 'GatewayClass'."""
    class_name: str
    """The IngressClass / GatewayClass name to match against live objects."""
    namespace: str | None = Field(
        default=None,
        description=(
            "Namespace hint — used to scope the Ingress list or Gateway lookup. "
            "If omitted the handler searches all namespaces."
        ),
    )
    gateway_class_name: str = Field(
        default="f5-bnk",
        description="Target BNK GatewayClass name.",
    )
    gateway_name: str | None = Field(
        default=None,
        description="Override target Gateway name (auto-derived if omitted).",
    )
    target_namespace: str | None = Field(
        default=None,
        description=(
            "Override target namespace for the Gateway. Defaults to the first "
            "backend namespace to avoid ReferenceGrant."
        ),
    )


class ProxyTranslateUnmapped(BaseModel):
    """One lossy/unsupported construct that could not be auto-translated (D-017)."""

    source_kind: str
    source_name: str
    source_namespace: str
    construct_type: str
    """Type of construct (e.g. 'annotation', 'extensionRef', 'path_type')."""
    detail: str
    reason: str


class ProxyTranslateResponse(BaseModel):
    """Response for POST /api/k8s/clusters/{id}/proxies/translate."""

    cluster_id: int
    proxy_type: str
    source_kind: str
    class_name: str
    gatewayclass_yaml: str
    gateway_yaml: str
    httproute_yaml: str
    combined_yaml: str
    unmapped: list[ProxyTranslateUnmapped] = Field(default_factory=list)
    source: dict[str, Any] = Field(default_factory=dict)
    """Translation source metadata (ingress_count, httproute_count, etc.)."""


# =============================================================================
# Proxy Migration (D-021 P3)
# =============================================================================


class ProxyMigrationStepResponse(BaseModel):
    """A single step in a proxy migration run."""

    id: int
    step_index: int
    name: str
    description: str
    status: str
    requires_confirm: bool = False
    output_log: str | None = None
    result_data: dict[str, Any] | None = None
    error_message: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float | None = None


class ProxyMigrationResponse(BaseModel):
    """A proxy migration run."""

    id: int
    cluster_id: int
    project_id: int | None = None
    source_descriptor: dict[str, Any]
    bnk_gateway_name: str | None = None
    bnk_gateway_namespace: str | None = None
    verify_result: dict[str, Any] | None = None
    teardown_info: dict[str, Any] | None = None
    status: str
    current_step: str | None = None
    error_message: str | None = None
    error_step: str | None = None
    triggered_by: str | None = None
    cutover_confirmed_by: str | None = None
    cutover_confirmed_at: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: float | None = None
    steps: list[ProxyMigrationStepResponse] = Field(default_factory=list)


class ProxyMigrationListResponse(BaseModel):
    """List of proxy migration runs."""

    migrations: list[ProxyMigrationResponse]
    count: int
    cluster_id: int


class CreateMigrationRequest(BaseModel):
    """Request body for POST /api/k8s/clusters/{id}/proxies/migrate.

    Caller must have already run the translate endpoint to get combined_yaml.
    """

    source_descriptor: dict[str, Any] = Field(
        description=(
            "Descriptor of the source proxy "
            "(proxy_type, source_kind, class_name, namespace, gateway_name, gateway_namespace, etc.)"
        ),
    )
    combined_yaml: str = Field(
        description="Multi-doc YAML from the translate endpoint (combined_yaml field).",
    )
    project_id: int | None = Field(
        default=None,
        description="Optional project scope.",
    )
    teardown_info: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Optional old-proxy teardown hint. Keys: helm_release, helm_namespace, "
            "kubectl_resources (list of 'kind/name namespace' strings). "
            "If omitted, teardown step is surfaced as a manual action."
        ),
    )

    @field_validator("teardown_info", mode="before")
    @classmethod
    def validate_kubectl_resources(cls, v: Any) -> Any:
        """Validate teardown_info.kubectl_resources elements are safe 'kind/name [namespace]' strings.

        Rejects leading dashes (e.g. --all), empty strings, and any token that
        doesn't match the expected kind/name format to prevent kubectl flag injection.
        """
        if not isinstance(v, dict):
            return v
        resources = v.get("kubectl_resources")
        if resources is None:
            return v
        if not isinstance(resources, list):
            raise ValueError("teardown_info.kubectl_resources must be a list of strings")
        for item in resources:
            if not isinstance(item, str):
                raise ValueError(
                    f"teardown_info.kubectl_resources element must be a string, got {type(item).__name__!r}"
                )
            stripped = item.strip()
            if not stripped:
                raise ValueError(
                    "teardown_info.kubectl_resources contains an empty string — each element must be 'kind/name [namespace]'"
                )
            if not _KUBECTL_RESOURCE_RE.match(stripped):
                raise ValueError(
                    f"teardown_info.kubectl_resources element {stripped!r} is not a valid 'kind/name [namespace]' "
                    "token. Elements must not start with '-' or contain shell metacharacters."
                )
        return v


class ConfirmCutoverRequest(BaseModel):
    """Request body for POST .../migrate/{id}/confirm-cutover."""

    confirmed_by: str = Field(description="User or system identifier of the confirming operator.")


class ConfirmTeardownRequest(BaseModel):
    """Request body for POST .../migrate/{id}/confirm-teardown."""

    confirmed_by: str = Field(description="User or system identifier.")


class AbortMigrationRequest(BaseModel):
    """Request body for POST .../migrate/{id}/abort."""

    by: str = Field(default="user", description="User or system identifier.")


class MigrationActionResponse(BaseModel):
    """Generic response for migration action endpoints."""

    success: bool = True
    message: str
    migration_id: int
    status: str


# =============================================================================
# CIS Translation (D-023 P3)
# =============================================================================


class CisTranslateRequest(BaseModel):
    """Request body for POST /api/k8s/clusters/{id}/proxies/cis/translate.

    Identifies which CIS VirtualServer(s)/TransportServer(s) to translate,
    or triggers AS3-ConfigMap / Ingress translation modes.

    The handler re-fetches full CR specs / ConfigMaps / Ingresses from the cluster.
    """

    source_mode: str = Field(
        default="virtualserver",
        description=(
            "Translation source mode. One of: "
            "'virtualserver' (default — CIS VirtualServer/TransportServer CRs), "
            "'as3_configmap' (AS3 ConfigMaps labeled f5type=virtual-server), "
            "'ingress' (F5-annotated Kubernetes Ingresses), "
            "'route' (OpenShift Routes), "
            "'ingresslink' (CIS IngressLink CRs), "
            "'both' (VS/TS + AS3 ConfigMaps + Ingresses + Routes + IngressLinks combined)."
        ),
    )
    vs_names: list[str] = Field(
        default_factory=list,
        description=(
            "VirtualServer names to translate. "
            "If empty, all VirtualServers in vs_namespace are translated."
        ),
    )
    vs_namespace: str | None = Field(
        default=None,
        description="Namespace for VirtualServer lookup (all namespaces if omitted).",
    )
    ts_names: list[str] = Field(
        default_factory=list,
        description=(
            "TransportServer names to translate. "
            "If empty, all TransportServers in ts_namespace are translated."
        ),
    )
    ts_namespace: str | None = Field(
        default=None,
        description="Namespace for TransportServer lookup (all namespaces if omitted).",
    )
    configmap_names: list[str] = Field(
        default_factory=list,
        description=(
            "AS3 ConfigMap names to translate. "
            "If empty, all AS3 ConfigMaps (f5type=virtual-server) are translated. "
            "Only used when source_mode is 'as3_configmap' or 'both'."
        ),
    )
    configmap_namespace: str | None = Field(
        default=None,
        description=(
            "Namespace filter for AS3 ConfigMap lookup (all namespaces if omitted). "
            "Only used when source_mode is 'as3_configmap' or 'both'."
        ),
    )
    ingress_names: list[str] = Field(
        default_factory=list,
        description=(
            "F5-annotated Ingress names to translate. "
            "If empty, all F5-annotated Ingresses in ingress_namespace are translated. "
            "Only used when source_mode is 'ingress' or 'both'."
        ),
    )
    ingress_namespace: str | None = Field(
        default=None,
        description=(
            "Namespace filter for F5-annotated Ingress lookup (all namespaces if omitted). "
            "Only used when source_mode is 'ingress' or 'both'."
        ),
    )
    route_names: list[str] = Field(
        default_factory=list,
        description=(
            "OpenShift Route names to translate. "
            "If empty, all Routes in route_namespace are translated. "
            "Only used when source_mode is 'route' or 'both'."
        ),
    )
    route_namespace: str | None = Field(
        default=None,
        description=(
            "Namespace filter for OpenShift Route lookup (all namespaces if omitted). "
            "Only used when source_mode is 'route' or 'both'."
        ),
    )
    gateway_class_name: str = Field(
        default="f5-bnk",
        description="Target BNK GatewayClass name.",
    )
    gateway_name: str | None = Field(
        default=None,
        description="Override target Gateway name (auto-derived if omitted).",
    )
    target_namespace: str | None = Field(
        default=None,
        description="Override target namespace for the Gateway.",
    )
