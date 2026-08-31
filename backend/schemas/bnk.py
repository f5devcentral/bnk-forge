"""
Pydantic schemas for F5 BNK responses.

The BNK health dashboard returns a nested legacy vocabulary ("critical" /
"warning" / "healthy" / "unknown"). These schemas type that response so the
frontend generated types stay in sync and OpenAPI captures the shape.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

HealthSeverityV1 = Literal["healthy", "warning", "critical", "unknown"]


class HealthRemediationAction(BaseModel):
    label: str
    action: Literal["view_logs", "restart_pod", "describe", "diagnostics"]
    target: str
    namespace: str


class HealthPodDetail(BaseModel):
    podName: str
    namespace: str
    nodeName: str | None = None
    nodeZone: str | None = None
    nodeInstanceType: str | None = None
    hostIP: str | None = None
    phase: str
    restartCount: int
    containersReady: str
    issue: str


class HealthComponentEnrichment(BaseModel):
    explanation: str
    podDetails: list[HealthPodDetail]
    remediationActions: list[HealthRemediationAction]
    namespaces: list[str] = Field(default_factory=list)
    zones: list[str] = Field(default_factory=list)
    nodes: list[str] = Field(default_factory=list)


class HealthPlatformComponent(HealthComponentEnrichment):
    total: int
    running: int | None = None
    completed: int | None = None
    severity: HealthSeverityV1


class HealthTmmComponent(HealthComponentEnrichment):
    pods: int
    running: int
    containersTotal: int
    containersReady: int
    totalRestarts: int
    severity: HealthSeverityV1


class HealthGatewayComponent(BaseModel):
    total: int
    programmed: int
    accepted: int
    severity: HealthSeverityV1
    explanation: str
    addresses: list[str]


class HealthVlanDetail(BaseModel):
    name: str
    programmed: bool
    interfaces: list[str]
    selfIPs: list[str]
    mtu: int | None = None


class HealthVlanComponent(BaseModel):
    total: int
    programmed: int
    severity: HealthSeverityV1
    explanation: str
    details: list[HealthVlanDetail]


class HealthIRuleDetail(BaseModel):
    name: str
    accepted: bool
    programmed: bool
    error: str | None = None


class HealthIRulesComponent(BaseModel):
    total: int
    accepted: int
    programmed: int
    severity: HealthSeverityV1
    explanation: str
    details: list[HealthIRuleDetail]


class HealthCneInstance(BaseModel):
    name: str

    class Config:
        extra = "allow"


class HealthAnalyzerDetail(BaseModel):
    name: str
    namespace: str
    schedule: str


class HealthCounts(BaseModel):
    gateways: int
    listeners: int
    httpRoutes: int
    vlans: int
    firewallPolicies: int
    irules: int
    analyzers: int
    cneInstances: int
    tmm_pods: int
    tmm_running: int
    tmm_containers: str


class BnkHealthPlatformSection(BaseModel):
    severity: HealthSeverityV1
    flo: HealthPlatformComponent
    controller: HealthPlatformComponent
    crdInstaller: HealthPlatformComponent
    analyzer: HealthPlatformComponent


class BnkHealthDataPlaneSection(BaseModel):
    severity: HealthSeverityV1
    tmm: HealthTmmComponent
    cneInstance: HealthCneInstance | dict[str, Any]


class BnkHealthNetworkingSection(BaseModel):
    severity: HealthSeverityV1
    gateways: HealthGatewayComponent
    vlans: HealthVlanComponent
    listeners: int
    httpRoutes: int
    staticRoutes: int
    snatPools: int


class BnkHealthSecuritySection(BaseModel):
    severity: HealthSeverityV1
    firewallPolicies: int
    securityPolicies: int
    networkPolicies: int
    addressLists: int
    portLists: int
    irules: HealthIRulesComponent


class BnkHealthAISection(BaseModel):
    severity: HealthSeverityV1
    analyzers: int
    analyzerDetails: list[HealthAnalyzerDetail]


class BnkHealthResponse(BaseModel):
    overall: HealthSeverityV1
    installShape: str = "unknown"
    installMethod: str = "Unknown"
    platform: BnkHealthPlatformSection
    dataPlane: BnkHealthDataPlaneSection
    networking: BnkHealthNetworkingSection
    security: BnkHealthSecuritySection
    ai: BnkHealthAISection
    counts: HealthCounts

    class Config:
        extra = "allow"


class BnkHealthEndpointResponse(BnkHealthResponse):
    cluster_id: int


class BnkDataResponse(BaseModel):
    """Wrapper for the unified /f5bnk/data endpoint.

    Only the ``health`` key is strongly typed here; the remaining keys are
    kept as loose dicts because their schemas are large and already typed
    manually in the frontend. This lets OpenAPI capture the enriched health
    shape without coupling the whole topology/palette response to Pydantic.
    """

    health: BnkHealthResponse
    topology: list[dict[str, Any]]
    dataPlane: dict[str, Any]
    referenceGrants: list[dict[str, Any]]
    topologyCounts: dict[str, Any]
    policyAssociations: list[dict[str, Any]]
    policyCount: int
    backends: list[dict[str, Any]] | None = None
    palette: dict[str, Any] | None = None
    cluster_id: int
    namespace: str | None = None

    class Config:
        extra = "allow"
