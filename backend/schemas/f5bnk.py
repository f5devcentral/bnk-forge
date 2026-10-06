"""
Pydantic response schemas for F5 BNK insight endpoints.

These models type the standalone /f5bnk/gateway-topology and
/f5bnk/policy-gateway-associations endpoints. The unified /f5bnk/data
endpoint keeps loose topology/palette keys (see schemas.bnk.BnkDataResponse)
to avoid coupling the entire nested response to Pydantic, while still
exposing strongly-typed trafficStats and health sections.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Shared operational-state building blocks
# ---------------------------------------------------------------------------


class TopologyCondition(BaseModel):
    type: str
    status: str
    reason: str | None = None
    message: str | None = None
    lastTransitionTime: str | None = None


class PolicyStatus(BaseModel):
    resolved: bool = False
    programmed: bool = False
    messages: dict[str, str | None] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Gateway topology
# ---------------------------------------------------------------------------


class TopologyRouteBackend(BaseModel):
    name: str
    namespace: str | None = None
    port: int | None = None
    weight: int | None = None
    kind: str = "Service"
    group: str = ""


class TopologyAnalyzer(BaseModel):
    name: str
    schedule: str
    scriptType: str
    dataSources: list[str]
    parameters: dict[str, str]


class TopologyMcpInfo(BaseModel):
    auth: str
    tools: list[str]


class TopologyRoute(BaseModel):
    name: str
    namespace: str
    kind: str
    resourceType: str | None = None  # registry key; L4Route has two (l4route, l4route_24)
    hostnames: list[str]
    backends: list[TopologyRouteBackend]
    analyzers: list[TopologyAnalyzer]
    accepted: bool = False
    conditions: list[TopologyCondition] = Field(default_factory=list)
    conditionMessage: str | None = None
    isMcp: bool = False
    mcpInfo: TopologyMcpInfo | None = None
    # L4Route per-backend service settings, keyed by backend then setting.
    serviceSettings: dict[str, dict[str, float]] | None = None


class TopologyNetworkPolicyExtension(BaseModel):
    kind: str
    name: str
    group: str
    lineCount: int | None = None
    eventHandlers: list[str] = Field(default_factory=list)


class TopologyNetworkPolicy(BaseModel):
    name: str
    kind: str = ""
    namespace: str
    extensions: list[TopologyNetworkPolicyExtension]
    resolvedCount: int
    totalExtensions: int
    resolved: bool = False
    programmed: bool = False
    messages: dict[str, str | None] = Field(default_factory=dict)


class TopologyAddressList(BaseModel):
    name: str
    addresses: list[Any]


class TopologyPortList(BaseModel):
    name: str
    ports: list[Any]


class TopologyFwRule(BaseModel):
    name: str
    action: str
    ipProtocol: str
    logging: bool


class TopologyFirewallPolicy(BaseModel):
    name: str
    rules: list[TopologyFwRule]
    addressLists: list[TopologyAddressList]
    portLists: list[TopologyPortList]


class TopologySecurityPolicy(BaseModel):
    name: str
    kind: str = ""
    namespace: str
    targetListener: str
    firewallPolicies: list[TopologyFirewallPolicy]
    resolved: bool = False
    programmed: bool = False
    messages: dict[str, str | None] = Field(default_factory=dict)


class TopologyListener(BaseModel):
    name: str
    protocol: str
    port: int | None = None
    attachedRouteCount: int = 0
    conditions: list[TopologyCondition] = Field(default_factory=list)
    routes: list[TopologyRoute]
    networkPolicies: list[TopologyNetworkPolicy]


class TopologyGatewaySettingsRef(BaseModel):
    """GatewaySettings resolved from a Gateway's spec.infrastructure.parametersRef (BNK 2.4)."""
    name: str
    namespace: str
    ingressConfig: dict[str, Any] = Field(default_factory=dict)
    sourceNATPools: list[dict[str, Any]] = Field(default_factory=list)
    egressConfigs: list[dict[str, Any]] = Field(default_factory=list)


class TopologyGateway(BaseModel):
    name: str
    namespace: str
    gatewayClassName: str
    addresses: list[str]
    accepted: bool = False
    programmed: bool = False
    conditions: list[TopologyCondition] = Field(default_factory=list)
    listeners: list[TopologyListener]
    securityPolicies: list[TopologySecurityPolicy]
    gatewaySettings: TopologyGatewaySettingsRef | None = None


# ---------------------------------------------------------------------------
# Data plane
# ---------------------------------------------------------------------------


class TopologyVlan(BaseModel):
    name: str
    namespace: str
    kind: str = ""
    infraName: str | None = None  # parent Infra when projected from a BNK 2.4 Infra network
    interfaces: list[Any]
    selfipV4s: list[str]
    prefixLen: int | str | None = None
    mtu: int | None = None
    internal: bool
    autoLasthop: str
    ready: bool
    type: str | None = None
    tag: int | None = None


class TopologyCneInstance(BaseModel):
    name: str
    namespace: str
    features: dict[str, bool]
    networkAttachments: list[Any]
    containerPlatform: str
    phase: str
    ready: bool


class TopologyStaticRoute(BaseModel):
    name: str
    namespace: str
    destination: str
    gateway: str
    kind: str = ""
    infraName: str | None = None


class TopologySnatPool(BaseModel):
    name: str
    namespace: str
    addresses: list[Any]


class TopologyEgress(BaseModel):
    name: str
    namespace: str
    kind: str = ""
    snatType: str
    egressSnatpool: str | None = None
    firewallEnforcedPolicy: str | None = None
    logProfile: str | None = None
    capturedNamespaces: list[str]
    vxlan: dict[str, str] | None = None
    ready: bool
    gatewayClassName: str | None = None
    parametersRef: dict[str, Any] | None = None
    sourceSelector: dict[str, Any] | None = None


class TopologyInfra(BaseModel):
    name: str
    namespace: str
    networks: list[dict[str, Any]] = Field(default_factory=list)
    ipams: list[dict[str, Any]] = Field(default_factory=list)
    networkAttachments: list[dict[str, Any]] = Field(default_factory=list)
    staticRoutes: list[dict[str, Any]] = Field(default_factory=list)
    vrfs: list[Any] = Field(default_factory=list)
    egressDefaults: dict[str, Any] = Field(default_factory=dict)
    ready: bool
    conditions: list[TopologyCondition] = Field(default_factory=list)


class TopologyGatewaySettings(TopologyGatewaySettingsRef):
    ready: bool
    conditions: list[TopologyCondition] = Field(default_factory=list)


class TopologyLogging(BaseModel):
    hslPublishers: list[dict[str, Any]]
    logProfiles: list[dict[str, Any]]


class TopologyDataPlane(BaseModel):
    vlans: list[TopologyVlan]
    cneInstances: list[TopologyCneInstance]
    staticRoutes: list[TopologyStaticRoute]
    snatPools: list[TopologySnatPool]
    egresses: list[TopologyEgress]
    logging: TopologyLogging
    infra: list[TopologyInfra] = Field(default_factory=list)
    gatewaySettings: list[TopologyGatewaySettings] = Field(default_factory=list)
    egressGateways: list[TopologyEgress] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Reference grants + counts + response wrappers
# ---------------------------------------------------------------------------


class TopologyReferenceGrantFrom(BaseModel):
    group: str
    kind: str
    namespace: str


class TopologyReferenceGrantTo(BaseModel):
    group: str
    kind: str


class TopologyReferenceGrant(BaseModel):
    name: str
    namespace: str
    from_: list[TopologyReferenceGrantFrom] = Field(alias="from")
    to: list[TopologyReferenceGrantTo]


class TopologyCounts(BaseModel):
    gateways: int
    listeners: int
    httpRoutes: int
    grpcRoutes: int
    tcpRoutes: int
    udpRoutes: int
    tlsRoutes: int
    l4Routes: int
    totalRoutes: int
    referenceGrants: int
    securityPolicies: int
    networkPolicies: int
    firewallPolicies: int
    iRules: int
    analyzers: int
    vlans: int
    cneInstances: int
    staticRoutes: int
    snatPools: int
    egresses: int
    hslPublishers: int
    logProfiles: int
    infra: int = 0
    gatewaySettings: int = 0
    egressGateways: int = 0
    inferencePools: int = 0
    f5epps: int = 0


class GatewayTopologyResponse(BaseModel):
    topology: list[TopologyGateway]
    dataPlane: TopologyDataPlane
    referenceGrants: list[TopologyReferenceGrant]
    counts: TopologyCounts
    cluster_id: int
    namespace: str | None = None


# ---------------------------------------------------------------------------
# Policy-gateway associations
# ---------------------------------------------------------------------------


class F5FirewallRuleEndpoint(BaseModel):
    addresses: list[Any]
    ports: list[str]
    addressLists: list[str]
    portLists: list[str]


class F5FirewallRule(BaseModel):
    name: str
    action: str
    ipProtocol: str
    source: F5FirewallRuleEndpoint
    destination: F5FirewallRuleEndpoint
    logging: bool


class F5GatewayPolicyAssociation(BaseModel):
    kind: Literal["gateway"] = "gateway"
    bnk_policy_name: str
    namespace: str
    gateway_name: str | None = None
    listener_name: str | None = None
    firewall_policy_name: str
    gateway_ip: str | None = None
    port: int | None = None
    protocol: str | None = None
    rules_count: int | None = None
    rules: list[F5FirewallRule] = Field(default_factory=list)
    bnk_policy_status: PolicyStatus = Field(default_factory=PolicyStatus)


class F5EgressPolicyAssociation(BaseModel):
    kind: Literal["egress"] = "egress"
    egress_name: str | None = None
    namespace: str
    captured_namespaces: list[str] = Field(default_factory=list)
    snat_type: str | None = None
    firewall_policy_name: str
    rules_count: int | None = None
    rules: list[F5FirewallRule] = Field(default_factory=list)
    egress_status: PolicyStatus = Field(default_factory=PolicyStatus)


class F5PolicyGatewayAssociationsResponse(BaseModel):
    associations: list[F5GatewayPolicyAssociation | F5EgressPolicyAssociation]
    count: int
    cluster_id: int
    namespace: str | None = None
