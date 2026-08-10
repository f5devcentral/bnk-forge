"""Benchmark models for LLM inference load testing.

Phase 2: Forge Dashboard — stores benchmark results from aiperf/llm-bench CLI tools,
tracks test client agents, and provides proxy-vs-proxy comparison data.
Phase 4b: Benchmark Targets — K8s clusters + LLM endpoints that benchmarks run against,
with proxy deployments (envoy, nginx, haproxy, f5-bnk) managed via Helm.

⚠️  TERMINOLOGY (see ai-perf DESIGN.md disambiguation section):
  - BenchmarkRun    = one load test execution through one proxy → stores BenchmarkResult JSON
  - BenchmarkConfig = a saved RunConfig (base_url, model, proxy, phases, etc.) — NOT "model provider"
  - BenchmarkAgent  = a test client MACHINE (registered via curl) — NOT an "AI model"
  - BenchmarkTarget = a K8s cluster + LLM endpoint that benchmarks are run against
  - ProxyDeployment = a proxy (envoy/nginx/etc.) deployed to a target cluster via Helm
"""

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base
from models.enums import (
    BenchmarkAgentStatus,
    BenchmarkRunStatus,
    BenchmarkTargetStatus,
    ProxyDeploymentStatus,
)


class BenchmarkRun(Base):
    """A single benchmark execution and its results.

    Each row = one run of aiperf/llm-bench through one proxy against one LLM endpoint.
    The result_json column stores the full BenchmarkResult from the CLI tool.
    Created either by POST /api/benchmarks/results (CLI push) or POST /api/benchmarks/runs (UI trigger).
    """
    __tablename__ = "benchmark_runs"

    id = Column(Integer, primary_key=True, index=True)

    # Which tool produced this result
    tool = Column(String(50), nullable=False, default="aiperf", index=True)  # aiperf | llm-bench | etc.

    # Labels (denormalized from result_json for quick querying/filtering)
    proxy = Column(String(50), nullable=False, default="nodeport", index=True)  # envoy|nginx|haproxy|f5-bnk|nodeport
    model = Column(String(255), nullable=False, index=True)  # vLLM model name
    base_url = Column(String(512), nullable=False)  # target endpoint URL
    run_label = Column(String(255), nullable=True)  # human-friendly label
    tags = Column(JSON, nullable=True)  # arbitrary metadata {"env": "prod", "cluster": "us-east-1"}

    # Link to saved config (if run was triggered from a preset)
    config_id = Column(Integer, ForeignKey("benchmark_configs.id", ondelete="SET NULL"), nullable=True, index=True)

    # Link to agent (if run was triggered remotely)
    agent_id = Column(Integer, ForeignKey("benchmark_agents.id", ondelete="SET NULL"), nullable=True, index=True)

    # Link to target/proxy (Phase 4b — optional, backward compatible)
    # target_id: index supplied by idx_benchmark_run_target in __table_args__ (avoid duplicate ix_*).
    target_id = Column(Integer, ForeignKey("benchmark_targets.id", ondelete="SET NULL"), nullable=True)
    proxy_deployment_id = Column(Integer, ForeignKey("proxy_deployments.id", ondelete="SET NULL"), nullable=True, index=True)

    # Link to parent run-group (Phase 6 — scenario expansion). A scenario expands into
    # one parent BenchmarkRunGroup + N child BenchmarkRuns (one per concurrency/phase).
    # Nullable + backward compatible: a standalone run has no group.
    # run_group_id: index supplied by idx_benchmark_run_group in __table_args__ (avoid duplicate ix_*).
    run_group_id = Column(Integer, ForeignKey("benchmark_run_groups.id", ondelete="CASCADE"), nullable=True)
    scenario_key = Column(String(100), nullable=True, index=True)  # e.g. "prefix-cache"
    variant_label = Column(String(100), nullable=True)  # e.g. "cfg1-c100", "warmup", "trace"

    # Baseline flag — marks the reference run for its (target_id, scenario_key, config_id, proxy, variant_label)
    # context. Only one baseline per context; enforced in BenchmarkService.set_baseline.
    is_baseline = Column(Boolean, nullable=False, default=False, server_default="false", index=True)

    # Status lifecycle
    status = Column(String(50), default=BenchmarkRunStatus.PENDING, nullable=False, index=True)
    error_message = Column(Text, nullable=True)

    # Config snapshot (full RunConfig JSON — preserved even if config is later edited/deleted)
    config_snapshot = Column(JSON, nullable=True)

    # Full result (populated on completion — the complete BenchmarkResult JSON from CLI)
    result_json = Column(JSON, nullable=True)

    # Denormalized result metrics (for quick querying without parsing result_json)
    duration_seconds = Column(Float, nullable=True)
    total_requests = Column(Integer, nullable=True)
    successful_requests = Column(Integer, nullable=True)
    failed_requests = Column(Integer, nullable=True)
    success_rate_pct = Column(Float, nullable=True)
    latency_p50 = Column(Float, nullable=True)  # seconds
    latency_p99 = Column(Float, nullable=True)  # seconds
    overall_rps = Column(Float, nullable=True)
    peak_rps = Column(Float, nullable=True)
    tokens_per_sec = Column(Float, nullable=True)  # gen_tokens_per_sec
    total_output_tokens = Column(Integer, nullable=True)

    # Timestamps
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    config = relationship("BenchmarkConfig", back_populates="runs")
    agent = relationship("BenchmarkAgent", back_populates="runs")
    target = relationship("BenchmarkTarget", back_populates="runs")
    proxy_deployment = relationship("ProxyDeployment", back_populates="runs")
    run_group = relationship("BenchmarkRunGroup", back_populates="runs")

    __table_args__ = (
        Index("idx_benchmark_run_proxy_created", "proxy", "created_at"),
        Index("idx_benchmark_run_tool_proxy", "tool", "proxy"),
        Index("idx_benchmark_run_model", "model"),
        Index("idx_benchmark_run_status_created", "status", "created_at"),
        Index("idx_benchmark_run_target", "target_id"),
        Index("idx_benchmark_run_group", "run_group_id"),
    )


class BenchmarkRunGroup(Base):
    """A parent run-group produced by expanding a SCENARIO into N child runs.

    A user picks a SCENARIO (e.g. prefix-cache); the backend expands it into this
    parent group plus one child BenchmarkRun per concurrency point and/or phase.
    Each child run remains a single aiperf invocation. Aggregate metrics are rolled
    up here once all children reach a terminal state.
    """
    __tablename__ = "benchmark_run_groups"

    id = Column(Integer, primary_key=True, index=True)

    # Scenario identity
    # scenario_key: index supplied by idx_benchmark_run_group_scenario in __table_args__.
    scenario_key = Column(String(100), nullable=False)  # e.g. "prefix-cache"
    scenario_name = Column(String(255), nullable=True)  # human-friendly display name
    run_label = Column(String(255), nullable=True)

    # Target / proxy linkage (mirrors BenchmarkRun, nullable for backward compat)
    # target_id: index supplied by idx_benchmark_run_group_target in __table_args__.
    target_id = Column(Integer, ForeignKey("benchmark_targets.id", ondelete="SET NULL"), nullable=True)
    proxy_id = Column(Integer, ForeignKey("proxy_deployments.id", ondelete="SET NULL"), nullable=True, index=True)
    agent_id = Column(Integer, ForeignKey("benchmark_agents.id", ondelete="SET NULL"), nullable=True, index=True)

    proxy = Column(String(50), nullable=True, index=True)  # denormalized proxy type
    model = Column(String(255), nullable=True)
    base_url = Column(String(512), nullable=True)
    tags = Column(JSON, nullable=True)

    # Status lifecycle of the group (reuses BenchmarkRunStatus values)
    status = Column(String(50), default=BenchmarkRunStatus.PENDING, nullable=False, index=True)
    error_message = Column(Text, nullable=True)

    # Child counts
    total_runs = Column(Integer, nullable=False, default=0)
    completed_runs = Column(Integer, nullable=False, default=0)
    failed_runs = Column(Integer, nullable=False, default=0)

    # Aggregate metrics (nullable — populated when all children terminal)
    aggregate_json = Column(JSON, nullable=True)  # full per-variant rollup
    avg_latency_p50 = Column(Float, nullable=True)
    avg_latency_p99 = Column(Float, nullable=True)
    peak_rps = Column(Float, nullable=True)
    total_output_tokens = Column(Integer, nullable=True)

    # Timestamps
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    runs = relationship("BenchmarkRun", back_populates="run_group", order_by="BenchmarkRun.id")
    target = relationship("BenchmarkTarget")

    __table_args__ = (
        Index("idx_benchmark_run_group_scenario", "scenario_key"),
        Index("idx_benchmark_run_group_status_created", "status", "created_at"),
        Index("idx_benchmark_run_group_target", "target_id"),
    )


class BenchmarkConfig(Base):
    """A saved test configuration (reusable RunConfig preset).

    Stores a full RunConfig as JSON — base_url, model, proxy, burst phases, concurrency, etc.
    The aiperf CLI can pull this via GET /api/benchmarks/configs/{id}.

    ⚠️  This is NOT "model_provider + model_name". It's a load test configuration.
    """
    __tablename__ = "benchmark_configs"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)

    # Which benchmark tool this config is for
    tool = Column(String(50), nullable=False, default="aiperf")  # aiperf | llm-bench

    # The full RunConfig as JSON (base_url, model, proxy, phases, etc.)
    config_json = Column(JSON, nullable=False)

    # Metadata
    created_by = Column(String(255), nullable=True)  # username who created it
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    runs = relationship("BenchmarkRun", back_populates="config")

    __table_args__ = (
        Index("idx_benchmark_config_tool", "tool"),
    )


class BenchmarkAgent(Base):
    """A registered test client MACHINE that can run benchmarks.

    Each row = one load-generator client (e.g. loadgen-01, loadgen-02).
    Registered via curl; optionally connects to Forge via WebSocket for live orchestration.

    Two kinds of agents share this table:
      - Built-in / self-registered agents: managed=False, project_id=NULL.
        These register themselves at startup and reconnect via WebSocket.
      - Forge-managed remote host agents: managed=True, project_id=<project>.
        Created via POST /api/benchmarks/agent-hosts; Forge SSH-provisions them.

    ⚠️  This is a PHYSICAL/VIRTUAL MACHINE, NOT an "AI model being evaluated".
    """
    __tablename__ = "benchmark_agents"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True, index=True)  # "loadgen-01"
    hostname = Column(String(255), nullable=True)  # OS hostname
    ip_address = Column(String(45), nullable=True)  # IPv4 or IPv6 — the address the agent *advertises* (self-registered)
    tags = Column(JSON, nullable=True)  # {"datacenter": "us-east", "gpu": false}
    capabilities = Column(JSON, nullable=True)  # {"engines": ["burst"], "platform": "Linux", "python": "3.11"}

    # Status (managed by WebSocket connection lifecycle)
    status = Column(String(50), default=BenchmarkAgentStatus.DISCONNECTED, nullable=False, index=True)
    last_heartbeat = Column(DateTime(timezone=True), nullable=True)

    # ── Project-scoped SSH-host registration columns (Slice 1) ──────────────
    # project_id NULL  → global/built-in agent (legacy, no ownership)
    # project_id set   → managed remote host owned by a project
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True)

    # The IP Forge will SSH to when provisioning/managing this host.
    # Distinct from ip_address, which is what the agent self-advertises after boot.
    host_ip = Column(String(45), nullable=True)

    ssh_credential_id = Column(Integer, ForeignKey("ssh_credentials.id", ondelete="SET NULL"), nullable=True)
    ssh_port = Column(Integer, nullable=True, default=22)

    # Jumphost chain — same shape as bare_metal: list of {"ssh_credential_id": N}
    jumphost_chain = Column(JSON, nullable=True)

    # Provision lifecycle: unprovisioned | provisioning | provisioned | failed
    provision_status = Column(String(50), nullable=True, default="unprovisioned")
    provision_message = Column(Text, nullable=True)

    # Runtime readiness snapshot (populated after successful provision)
    readiness = Column(JSON, nullable=True)

    # True = Forge-managed remote host; False = self-registered built-in agent
    managed = Column(Boolean, nullable=False, default=False)
    # ── end Slice 1 columns ─────────────────────────────────────────────────

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    runs = relationship("BenchmarkRun", back_populates="agent")
    project = relationship("Project", foreign_keys=[project_id])
    ssh_credential = relationship("SSHCredential", foreign_keys=[ssh_credential_id])

    __table_args__ = (
        Index("idx_benchmark_agent_status", "status"),
        Index("idx_benchmark_agent_project", "project_id"),
    )


class BenchmarkTarget(Base):
    """A K8s cluster + LLM endpoint that benchmarks are run against.

    References an existing KubernetesCluster record and stores the LLM
    endpoint details (base URL, model, namespace). Proxy deployments
    (envoy, nginx, etc.) are children of this target.
    """
    __tablename__ = "benchmark_targets"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)

    # Link to existing K8s cluster
    cluster_id = Column(Integer, ForeignKey("kubernetes_clusters.id", ondelete="CASCADE"), nullable=False, index=True)

    # LLM endpoint details (what the proxy will forward to)
    llm_base_url = Column(String(500), nullable=False)  # e.g. http://vllm-svc.default:8000
    llm_model = Column(String(255), nullable=False)  # e.g. openai/gpt-oss-120b
    llm_namespace = Column(String(255), nullable=False, default="default")
    llm_endpoint = Column(String(255), nullable=False, default="/v1/chat/completions")

    # Proxy routing
    proxy_namespace = Column(String(255), nullable=False, default="perf-proxies")

    # Status
    status = Column(String(50), default=BenchmarkTargetStatus.ACTIVE, nullable=False, index=True)
    last_validated = Column(DateTime(timezone=True), nullable=True)
    validation_msg = Column(Text, nullable=True)

    # Metadata
    tags = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    cluster = relationship("KubernetesCluster")
    proxy_deployments = relationship("ProxyDeployment", back_populates="target", cascade="all, delete-orphan")
    runs = relationship("BenchmarkRun", back_populates="target")

    @property
    def proxy_count(self) -> int:
        """Number of proxy deployments for this target (used in list view)."""
        return len(self.proxy_deployments) if self.proxy_deployments else 0

    __table_args__ = (
        Index("idx_benchmark_target_cluster", "cluster_id"),
        Index("idx_benchmark_target_status", "status"),
    )


class ProxyDeployment(Base):
    """A proxy (envoy, nginx, haproxy, f5-bnk) deployed to a target cluster via Helm.

    Each row represents one proxy type deployed to one target. The proxy
    forwards LLM inference traffic from the test agent to the target's
    LLM endpoint.
    """
    __tablename__ = "proxy_deployments"

    id = Column(Integer, primary_key=True, index=True)
    target_id = Column(Integer, ForeignKey("benchmark_targets.id", ondelete="CASCADE"), nullable=False, index=True)
    proxy_type = Column(String(50), nullable=False)  # envoy|nginx|haproxy|f5-bnk

    # Helm release info
    helm_release = Column(String(255), nullable=True)  # e.g. perf-envoy-cluster1
    helm_chart = Column(String(255), nullable=True)  # e.g. envoy-gateway/gateway-helm
    helm_version = Column(String(50), nullable=True)  # Chart version
    helm_values = Column(JSON, nullable=True)  # Custom values.yaml overrides

    # Deployed endpoint (populated after successful deploy)
    proxy_url = Column(String(500), nullable=True)  # e.g. http://envoy-proxy.perf-proxies:10080
    external_url = Column(String(500), nullable=True)  # NodePort/LB URL for external agents

    # Status
    status = Column(String(50), default=ProxyDeploymentStatus.PENDING, nullable=False, index=True)
    status_message = Column(Text, nullable=True)
    deployed_at = Column(DateTime(timezone=True), nullable=True)
    celery_task_id = Column(String(255), nullable=True)  # Celery task ID for async deploy/undeploy

    # Heartbeat + fencing-token lock columns (Phase 1.6 — EntityLockService)
    lock_fence_token = Column(BigInteger, nullable=False, server_default="0")
    holding_task_id = Column(BigInteger, nullable=True)
    heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    lock_acquired_at = Column(DateTime(timezone=True), nullable=True)

    # Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    target = relationship("BenchmarkTarget", back_populates="proxy_deployments")
    runs = relationship("BenchmarkRun", back_populates="proxy_deployment")

    __table_args__ = (
        Index("idx_proxy_deployment_target_type", "target_id", "proxy_type", unique=True),
        Index("idx_proxy_deployment_status", "status"),
    )
