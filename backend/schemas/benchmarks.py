"""
Pydantic schemas for the Benchmark domain (Phase 2: LLM Inference Load Testing).

⚠️  TERMINOLOGY: This is a load-testing dashboard, NOT an AI evaluation harness.
  - BenchmarkRun   = one load test execution through one proxy
  - BenchmarkConfig = a saved RunConfig (base_url, model, proxy, phases)
  - BenchmarkAgent  = a test client MACHINE (registered via curl)

Covers:
  - Result ingestion (POST /api/benchmarks/results — from aiperf CLI)
  - Config CRUD (RunConfig presets)
  - Run lifecycle
  - Agent registration
  - Comparison and summary
"""

from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel, Field, field_validator

# =============================================================================
# Result Ingestion — what aiperf CLI pushes via ForgeCollector.push_result()
# =============================================================================

class BenchmarkResultPush(BaseModel):
    """Schema for POST /api/benchmarks/results.

    This is the full BenchmarkResult JSON from the aiperf CLI.
    We accept any valid JSON and extract key fields for denormalization.
    The full result is stored as-is in result_json.
    """
    # Identity
    result_id: str
    result_version: str = "1.0"

    # Labels — we extract proxy, model, base_url from here
    labels: dict  # {"proxy": "envoy", "run_label": "...", "base_url": "...", "model": "...", "endpoint": "..."}
    tags: dict = Field(default_factory=dict)

    # Timing
    run_start: str
    run_end: str
    duration_seconds: float
    duration_minutes: float

    # Config used
    config: dict  # Full RunConfig as dict

    # Proxy detection
    proxy_detection: dict | None = None

    # Summary
    total_requests: int
    successful: int
    failed: int
    success_rate_pct: float
    total_input_tokens: int
    total_output_tokens: int
    avg_input_tokens: float
    avg_output_tokens: float

    # Aggregates
    latency: dict  # LatencyStats as dict
    throughput: dict  # ThroughputStats as dict

    # Per-phase breakdown
    phases: dict  # {phase_name: PhaseResult}

    # Raw timeline (optional — can be very large)
    timeline: list | None = None

    # Agent metadata
    agent_name: str | None = None
    agent_hostname: str | None = None

    # Linkage (optional) — associate this run with a registered target/config/proxy.
    # The benchmark_runs table already has these FK columns; backward compatible.
    target_id: int | None = None
    config_id: int | None = None
    proxy_deployment_id: int | None = None


class BenchmarkResultPushResponse(BaseModel):
    """Response from POST /api/benchmarks/results."""
    id: int
    run_id: int  # alias for id — what ForgeCollector expects
    proxy: str
    model: str
    status: str
    target_id: int | None = None
    config_id: int | None = None
    proxy_deployment_id: int | None = None


# =============================================================================
# Config Schemas — saved RunConfig presets
# =============================================================================

class BenchmarkConfigCreate(BaseModel):
    """Create a saved RunConfig preset."""
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    tool: str = Field(default="aiperf", description="Benchmark tool: aiperf | llm-bench")
    config_json: dict = Field(..., description="Full RunConfig as JSON")


class BenchmarkConfigUpdate(BaseModel):
    """Update a saved RunConfig preset."""
    name: str | None = None
    description: str | None = None
    tool: str | None = None
    config_json: dict | None = None


class BenchmarkConfigResponse(BaseModel):
    """Response for a saved RunConfig preset."""
    id: int
    name: str
    description: str | None
    tool: str
    config_json: dict
    created_by: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# =============================================================================
# Run Schemas
# =============================================================================

class BenchmarkRunCreate(BaseModel):
    """Create a benchmark run (typically triggered from UI to send to an agent)."""
    config_id: int | None = None  # FK to saved config
    agent_id: int | None = None  # FK to agent to run on
    tool: str = Field(default="aiperf")
    proxy: str = Field(default="nodeport")
    model: str = Field(...)
    base_url: str = Field(...)
    run_label: str | None = None
    tags: dict | None = None
    config_snapshot: dict | None = None  # RunConfig JSON if not using saved config


class BenchmarkRunResponse(BaseModel):
    """Response for a benchmark run (list view)."""
    id: int
    tool: str
    proxy: str
    model: str
    base_url: str
    run_label: str | None
    tags: dict | None
    config_id: int | None
    agent_id: int | None
    target_id: int | None
    proxy_deployment_id: int | None
    scenario_key: str | None = None
    status: str
    error_message: str | None
    is_baseline: bool = False
    # Baseline reference for this run's (target_id, scenario_key, config_id, proxy, variant_label) context —
    # populated only when a different run holds the baseline. Frontend badge logic
    # (isRegression in benchmark-utils.tsx) diffs against these.
    baseline_latency_p99: float | None = None
    baseline_overall_rps: float | None = None

    # Denormalized metrics
    duration_seconds: float | None
    total_requests: int | None
    successful_requests: int | None
    failed_requests: int | None
    success_rate_pct: float | None
    latency_p50: float | None
    latency_p99: float | None
    overall_rps: float | None
    peak_rps: float | None
    tokens_per_sec: float | None
    total_output_tokens: int | None

    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BenchmarkRunDetailResponse(BenchmarkRunResponse):
    """Detailed run response including full result JSON and config."""
    result_json: dict | None = None
    config_snapshot: dict | None = None
    config: BenchmarkConfigResponse | None = None
    agent: "BenchmarkAgentResponse | None" = None


class BenchmarkRunListResponse(BaseModel):
    """Paginated list of benchmark runs."""
    runs: list[BenchmarkRunResponse]
    total: int
    limit: int
    offset: int


# =============================================================================
# Agent Schemas — test client machines
# =============================================================================

class BenchmarkAgentRegister(BaseModel):
    """Schema for POST /api/benchmarks/agents — agent self-registration."""
    name: str = Field(..., min_length=1, max_length=255)
    hostname: str | None = None
    ip_address: str | None = None
    tags: dict | None = None
    capabilities: dict | None = None  # {"engines": ["burst"], "platform": "Linux", "python": "3.11"}


class BenchmarkAgentResponse(BaseModel):
    """Response for a registered test client agent."""
    id: int
    name: str
    hostname: str | None
    ip_address: str | None
    tags: dict | None
    capabilities: dict | None
    status: str
    last_heartbeat: datetime | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# =============================================================================
# Comparison Schemas — proxy-vs-proxy side-by-side
# =============================================================================

class BenchmarkCompareRequest(BaseModel):
    """Request to compare multiple runs side-by-side."""
    run_ids: list[int] = Field(..., min_length=2, max_length=10)


class BenchmarkCompareRunMetrics(BaseModel):
    """Per-run metrics in a comparison."""
    run_id: int
    proxy: str
    model: str
    tool: str
    run_label: str | None
    config_id: int | None = None
    scenario_key: str | None = None
    variant_label: str | None = None
    status: str
    total_requests: int | None
    success_rate_pct: float | None
    latency_p50: float | None
    latency_p99: float | None
    overall_rps: float | None
    peak_rps: float | None
    tokens_per_sec: float | None
    duration_seconds: float | None
    # aiperf-specific metrics (avg values for comparison)
    ttft_avg: float | None = None
    itl_avg: float | None = None
    tst_avg: float | None = None
    osl_avg: float | None = None
    isl_avg: float | None = None
    per_user_throughput_avg: float | None = None


class BenchmarkCompareResponse(BaseModel):
    """Response for proxy-vs-proxy comparison."""
    runs: list[BenchmarkCompareRunMetrics]
    winners: dict  # {"latency_p50": run_id, "overall_rps": run_id, ...}
    # True when the compared runs don't share the same config_id/scenario_key —
    # frontend shows a "comparing mismatched configs" warning instead of silently
    # implying an apples-to-apples comparison.
    context_mismatch: bool = False


# =============================================================================
# Trends Schemas — time-series + baseline for a (target, proxy, scenario/config)
# =============================================================================

class BenchmarkTrendPoint(BaseModel):
    """One time-series point for the Trends view."""
    id: int
    run_label: str | None
    created_at: datetime
    is_baseline: bool
    latency_p50: float | None
    latency_p99: float | None
    overall_rps: float | None
    peak_rps: float | None
    success_rate_pct: float | None
    tokens_per_sec: float | None
    total_output_tokens: int | None

    class Config:
        from_attributes = True


class BenchmarkTrendsResponse(BaseModel):
    """Time-ordered (oldest-first) completed-run metrics for a target/proxy/scenario/config
    context, plus the current baseline run id (included in points even if outside limit)."""
    points: list[BenchmarkTrendPoint]
    baseline_run_id: int | None


# =============================================================================
# Summary Schema — dashboard overview
# =============================================================================

class BenchmarkSummaryResponse(BaseModel):
    """Dashboard summary of benchmark activity."""
    total_runs: int
    completed_runs: int
    failed_runs: int
    running_count: int
    avg_latency_p50: float | None  # seconds, across completed runs
    avg_rps: float | None
    avg_success_rate: float | None
    last_run_at: datetime | None
    runs_last_7d: int
    runs_by_proxy: list[dict]  # [{"proxy": "envoy", "count": 5, "avg_p50": 0.12}]
    runs_by_tool: list[dict]  # [{"tool": "aiperf", "count": 10}]


# =============================================================================
# Benchmark Target Schemas (Phase 4b) — K8s cluster + LLM endpoint
# =============================================================================

class BenchmarkTargetCreate(BaseModel):
    """Create a benchmark target."""
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    cluster_id: int = Field(..., description="FK to kubernetes_clusters.id")
    llm_base_url: str = Field(..., min_length=1, max_length=500)
    llm_model: str = Field(..., min_length=1, max_length=255)
    llm_namespace: str = Field(default="default", max_length=255)
    llm_endpoint: str = Field(default="/v1/chat/completions", max_length=255)
    proxy_namespace: str = Field(default="perf-proxies", max_length=255)
    tags: dict | None = None


class BenchmarkTargetUpdate(BaseModel):
    """Update a benchmark target."""
    name: str | None = None
    description: str | None = None
    cluster_id: int | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_namespace: str | None = None
    llm_endpoint: str | None = None
    proxy_namespace: str | None = None
    tags: dict | None = None


class ProxyDeploymentResponse(BaseModel):
    """Response for a proxy deployment."""
    id: int
    target_id: int
    proxy_type: str
    helm_release: str | None
    helm_chart: str | None
    helm_version: str | None
    helm_values: dict | None
    proxy_url: str | None
    external_url: str | None
    status: str
    status_message: str | None
    celery_task_id: str | None = None
    deployed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BenchmarkTargetResponse(BaseModel):
    """Response for a benchmark target."""
    id: int
    name: str
    description: str | None
    cluster_id: int
    llm_base_url: str
    llm_model: str
    llm_namespace: str
    llm_endpoint: str
    proxy_namespace: str
    status: str
    last_validated: datetime | None
    validation_msg: str | None
    tags: dict | None
    proxy_count: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class BenchmarkTargetDetailResponse(BenchmarkTargetResponse):
    """Detailed target response including proxy deployments."""
    proxy_deployments: list[ProxyDeploymentResponse] = []


class BenchmarkTargetListResponse(BaseModel):
    """List of benchmark targets."""
    targets: list[BenchmarkTargetResponse]
    total: int


# =============================================================================
# Proxy Deployment Schemas (Phase 4b)
# =============================================================================

class ProxyDeployRequest(BaseModel):
    """Request to deploy a proxy to a target cluster."""
    proxy_type: str = Field(
        ...,
        description="Proxy type: envoy|nginx|haproxy|f5-bnk|nodeport|envoy-ai-gateway|llm-d-router",
    )
    helm_values: dict | None = None  # Custom Helm values overrides
    helm_chart: str | None = None  # Override default chart
    helm_version: str | None = None  # Override default chart version


class ProxyDeploymentUpdate(BaseModel):
    """Update a proxy deployment (e.g. change Helm values, URLs)."""
    helm_values: dict | None = None
    proxy_url: str | None = None
    external_url: str | None = None
    status: str | None = None
    status_message: str | None = None


# =============================================================================
# Run Orchestration (Phase 4d)
# =============================================================================

class TriggerRunRequest(BaseModel):
    """Request to trigger a benchmark run against a deployed proxy.

    The backend builds the full RunConfig from the proxy's target info
    (base_url, model, endpoint) combined with the config preset.
    """
    config_id: int | None = Field(default=None, description="Saved BenchmarkConfig ID to use as base")
    agent_id: int | None = Field(default=None, description="Agent to run on. None = first connected agent.")
    run_label: str | None = None
    tags: dict | None = None
    # Inline overrides (used when no config_id)
    total_requests: int | None = Field(default=None, description="Override total requests count")
    max_tokens: int | None = Field(default=None, description="Override max output tokens per request")
    timeout: int | None = Field(default=None, description="Override HTTP request timeout (seconds)")


class TriggerRunResponse(BaseModel):
    """Response from triggering a benchmark run."""
    run_id: int
    agent_id: int
    proxy_id: int
    target_id: int
    status: str
    message: str


# =============================================================================
# Scenario Run Orchestration (Phase 6) — scenario → run-group + child runs
# =============================================================================

class ScenarioCatalogItem(BaseModel):
    """Catalog metadata for one benchmark scenario preset."""
    key: str
    name: str
    description: str
    trace_driven: bool
    child_run_count: int = Field(description="Number of child runs this scenario expands into")
    tags: list[str] = Field(default_factory=list)


class ScenarioCatalogResponse(BaseModel):
    """Response from GET /api/benchmarks/scenarios."""
    scenarios: list[ScenarioCatalogItem]
    total: int


class ScenarioRunRequest(BaseModel):
    """Request to run a SCENARIO against a deployed proxy.

    The backend expands ``scenario_key`` into a parent run-group plus N child runs
    (one per concurrency point and/or phase) and dispatches each child to a connected
    agent over the existing WebSocket protocol.
    """
    scenario_key: str = Field(..., description="Scenario preset key, e.g. 'prefix-cache'")
    agent_id: int | None = Field(default=None, description="Agent to run on. None = first connected agent.")
    run_label: str | None = None
    tags: dict | None = None
    overrides: dict | None = Field(
        default=None,
        description="Per-child aiperf flag overrides applied to every child config (e.g. {'model': '...'}).",
    )

    # Keys that callers must never be allowed to override — they control which
    # dataset/endpoint the agent fetches and must be set exclusively by the
    # server-side preset, not user-supplied input (SSRF defence).
    _FORBIDDEN_OVERRIDE_KEYS: ClassVar[frozenset[str]] = frozenset({"trace_url"})

    @field_validator("overrides")
    @classmethod
    def no_internal_keys(cls, v: dict | None) -> dict | None:
        if v is None:
            return v
        forbidden_exact = cls._FORBIDDEN_OVERRIDE_KEYS & v.keys()
        forbidden_prefix = {k for k in v if k.startswith("_")}
        bad = forbidden_exact | forbidden_prefix
        if bad:
            raise ValueError(
                f"Override key(s) not permitted: {sorted(bad)}. "
                "Keys 'trace_url' and any key starting with '_' are reserved for "
                "server-side use and cannot be supplied by callers."
            )
        return v


class RunGroupChildSummary(BaseModel):
    """Compact child-run summary within a run-group response."""
    id: int
    variant_label: str | None
    status: str
    concurrency: int | None = None
    latency_p50: float | None = None
    latency_p99: float | None = None
    overall_rps: float | None = None
    tokens_per_sec: float | None = None


class RunGroupSummary(BaseModel):
    """Compact run-group summary (list view)."""
    id: int
    scenario_key: str
    scenario_name: str | None
    run_label: str | None
    status: str
    target_id: int | None
    proxy: str | None
    model: str | None
    total_runs: int
    completed_runs: int
    failed_runs: int
    created_at: datetime

    class Config:
        from_attributes = True


class RunGroupResponse(RunGroupSummary):
    """Full run-group response: group + child runs + aggregate metrics."""
    agent_id: int | None
    base_url: str | None
    tags: dict | None
    error_message: str | None
    aggregate_json: dict | None
    avg_latency_p50: float | None
    avg_latency_p99: float | None
    peak_rps: float | None
    total_output_tokens: int | None
    started_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime
    runs: list[RunGroupChildSummary] = Field(default_factory=list)


class ScenarioRunResponse(BaseModel):
    """Response from triggering a scenario run."""
    run_group_id: int
    scenario_key: str
    agent_id: int
    proxy_id: int
    target_id: int
    status: str
    total_runs: int
    dispatched_runs: int
    message: str


# =============================================================================
# Proxy Discovery Schemas (Phase 5)
# =============================================================================

class ProxyDiscoveryResultItem(BaseModel):
    """Discovery result for a single proxy type."""
    proxy_type: str
    found: bool
    proxy_url: str | None = None
    external_url: str | None = None
    namespace: str | None = None
    details: dict = Field(default_factory=dict)
    error: str | None = None


class ProxyDiscoveryResponse(BaseModel):
    """Response from POST /api/benchmarks/targets/{id}/discover-proxies."""
    target_id: int
    target_name: str
    cluster_id: int
    results: list[ProxyDiscoveryResultItem]
    discovered_count: int = Field(description="Number of proxy types found on cluster")
    total_scanned: int = Field(description="Total proxy types scanned")


class ProxyTaskStatusResponse(BaseModel):
    """Response from GET /api/benchmarks/targets/{target_id}/proxies/{proxy_id}/task-status."""

    proxy_id: int
    status: str
    status_message: str | None = None
    proxy_url: str | None = None
    celery_task_id: str | None = None
    celery_state: str | None = None


# =============================================================================
# Target Discovery Schemas (Phase 5b) — auto-discover LLM services on cluster
# =============================================================================

class DiscoverTargetsRequest(BaseModel):
    """Request to scan a cluster for LLM services."""
    cluster_id: int = Field(..., description="K8s cluster to scan")
    auto_create: bool = Field(default=False, description="If True, auto-create targets for all discovered services")
    selected_services: list[str] | None = Field(
        default=None,
        description="List of service base_urls to create targets for. If None and auto_create=True, creates all.",
    )


class DiscoveredLLMServiceItem(BaseModel):
    """An LLM service found during cluster scan."""
    service_name: str
    namespace: str
    base_url: str
    port: int
    confidence: str = Field(description="Detection confidence: high | medium | low")
    reason: str = Field(description="Why we think this is an LLM service")
    model_hint: str | None = None
    gpu_count: int = 0
    image: str | None = None


class CreatedTargetItem(BaseModel):
    """A BenchmarkTarget auto-created from discovery."""
    id: int
    name: str
    llm_base_url: str
    status: str


class TargetProxyDiscoveryResult(BaseModel):
    """Proxy discovery result for a single auto-created target."""
    target_id: int
    target_name: str
    discovered_proxies: int = 0
    total_scanned: int = 0
    error: str | None = None


class CreatedConfigItem(BaseModel):
    """A BenchmarkConfig auto-created from discovery."""
    name: str
    target_name: str
    proxy_type: str


class DiscoverTargetsResponse(BaseModel):
    """Response from POST /api/benchmarks/discover-targets."""
    cluster_id: int
    cluster_name: str
    discovered_services: list[DiscoveredLLMServiceItem]
    discovered_count: int = Field(description="Number of LLM services found")
    created_targets: list[CreatedTargetItem] = Field(default_factory=list)
    proxy_results: list[TargetProxyDiscoveryResult] = Field(default_factory=list)
    created_configs: list[CreatedConfigItem] = Field(default_factory=list)


# =============================================================================
# Forge-managed remote benchmark agent hosts (Slice 1) — SSH-host registration
# =============================================================================

class BenchmarkAgentHostCreate(BaseModel):
    """Create a Forge-managed remote benchmark agent host.

    The host will be registered with provision_status='unprovisioned'.
    SSH provisioning happens in a later slice.
    """
    # Charset is restricted to safe identifier chars: the name is interpolated
    # into a shell command (nohup fallback re-parses /etc/forge/agent.env via
    # `$(cat ... | xargs)`), so spaces / shell metacharacters must never reach it.
    name: str = Field(..., min_length=1, max_length=255, pattern=r"^[A-Za-z0-9._-]+$")
    project_id: int = Field(..., description="FK to projects.id — host is scoped to this project")
    host_ip: str = Field(..., min_length=1, max_length=45, description="IP Forge will SSH to")
    ssh_credential_id: int = Field(..., description="FK to ssh_credentials.id — credential for SSH access")
    ssh_port: int = Field(default=22, ge=1, le=65535)
    jumphost_chain: list[dict] | None = Field(
        default=None,
        description="Optional jumphost chain: [{'ssh_credential_id': N}, ...]",
    )


class BenchmarkAgentHostResponse(BaseModel):
    """Response for a Forge-managed remote benchmark agent host."""
    id: int
    name: str
    hostname: str | None
    ip_address: str | None
    tags: dict | None
    capabilities: dict | None
    status: str
    last_heartbeat: datetime | None

    # Project-scoped SSH-host fields
    project_id: int | None
    host_ip: str | None
    ssh_credential_id: int | None
    ssh_port: int | None
    jumphost_chain: list | None
    provision_status: str | None
    provision_message: str | None
    readiness: dict | None
    managed: bool

    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# =============================================================================
# Agent Host Scan (Slice 2) — SSH suitability probe
# =============================================================================

class AgentHostScanRequest(BaseModel):
    """Optional body for POST /api/benchmarks/agent-hosts/{id}/scan."""
    target_ids: list[int] | None = Field(
        default=None,
        description=(
            "BenchmarkTarget IDs to probe for network reachability from the host. "
            "When omitted, all active project targets are probed (up to 20)."
        ),
    )


class AgentHostScanResponse(BaseModel):
    """Response from POST /api/benchmarks/agent-hosts/{id}/scan."""
    host_id: int
    message: str
    celery_task_id: str


# =============================================================================
# Agent Host Provision (Slice 3) — SSH install + systemd
# =============================================================================

class AgentHostProvisionResponse(BaseModel):
    """Response from POST /api/benchmarks/agent-hosts/{id}/provision."""
    host_id: int
    message: str
    celery_task_id: str


# =============================================================================
# Agent Host Candidates (Slice 5) — aggregated project host picker
# =============================================================================

class AgentHostCandidate(BaseModel):
    """A pre-fillable candidate host from an existing Forge resource."""
    label: str = Field(description="Human-readable display name")
    host_ip: str = Field(description="IP address to SSH to")
    ssh_credential_id: int | None = Field(
        default=None,
        description="Matching SSHCredential ID, or null if not yet imported",
    )
    ssh_port: int = Field(default=22)
    jumphost_chain: list[dict] | None = Field(default=None)
    source: str = Field(description="bare_metal | cluster_bastion | ssh_credential | aws_jumphost")
    source_ref: str | None = Field(default=None, description="ID or slug of the originating record")
    # SSH credential health — surfaced for ssh_credential sources
    last_test_status: str | None = Field(default=None)
    # AWS-specific: key not yet imported as an SSHCredential
    needs_credential_import: bool = Field(default=False)
    infra_key_path: str | None = Field(default=None, description="Durable PEM path for import")
    module_id: int | None = Field(default=None, description="ProjectModule ID for AWS jumphost import")


class AgentHostCandidatesResponse(BaseModel):
    """Response from GET /api/benchmarks/agent-host-candidates."""
    candidates: list[AgentHostCandidate]
    project_id: int


class ImportAwsJumphostRequest(BaseModel):
    """Request body for POST /api/benchmarks/agent-host-candidates/import-aws-jumphost."""
    project_id: int = Field(..., description="FK to projects.id")
    module_id: int = Field(..., description="ProjectModule whose infra key to import")
