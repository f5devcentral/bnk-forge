"""
Pydantic response models for the WAF Policy Manager routes.

These mirror the *actual* return shapes of routes/k8s/waf_policies.py and
routes/k8s/waf_logs.py so `response_model=` can type the generated OpenAPI
contract (and the frontend types derived from it) per the AGENTS.md convention.

The individual appprotect.f5.com/v1 custom resources (APPolicy, APLogConf,
APSignatures, APUserSig) are returned verbatim from the Kubernetes API, so
``WafResource`` allows extra keys rather than pinning a partial CRD schema.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WafResource(BaseModel):
    """A single appprotect.f5.com/v1 custom resource, returned verbatim from the cluster.

    Extra keys are allowed so the raw Kubernetes object (arbitrary CRD spec/status
    fields, managedFields, etc.) passes through the response model unchanged.
    """

    model_config = ConfigDict(extra="allow")

    apiVersion: str | None = None
    kind: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    spec: dict[str, Any] = Field(default_factory=dict)
    status: dict[str, Any] | None = None


class WafPolicyListResponse(BaseModel):
    """GET /waf/policies — APPolicy list envelope."""

    policies: list[dict[str, Any]]
    count: int


class WafLogConfListResponse(BaseModel):
    """GET /waf/logconfs — APLogConf list envelope."""

    log_confs: list[dict[str, Any]]
    count: int


class WafUserSigListResponse(BaseModel):
    """GET /waf/usersigs — APUserSig list envelope."""

    user_sigs: list[dict[str, Any]]
    count: int


class WafOperationResponse(BaseModel):
    """Delete responses from KubernetesService.delete_resource."""

    success: bool = True
    message: str


class WafRecompileResponse(BaseModel):
    """POST /waf/policies/{name}/recompile."""

    message: str
    resource: dict[str, Any] = Field(default_factory=dict)


class WafSecurityLogsResponse(BaseModel):
    """GET /waf/security-logs — security log entries plus resolution metadata.

    Covers all return branches (ClickHouse, resolved syslog endpoint, and the
    no-endpoint / unparseable-endpoint fallbacks), so most metadata fields are
    optional.
    """

    entries: list[dict[str, Any]]
    total: int
    source_endpoint: str | None = None
    cr_kind: str | None = None
    cr_name: str | None = None
    all_endpoints: list[str] | None = None
    source: str | None = None
    error: str | None = None
    warning: str | None = None
