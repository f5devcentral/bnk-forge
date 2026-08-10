# DPU Bare-Metal Deployment — Architecture Specification

> Comprehensive architecture spec for integrating bare-metal DPU deployment into BNK Forge.
>
> Date: 2026-04-13
> Branch: `feat/dpu-bare-metal-deploy`
> Status: **DRAFT — Architect Review**
> References:
> - [DPU_DEPLOY_ANALYSIS.md](DPU_DEPLOY_ANALYSIS.md)
> - [DPU_DEPLOY_REQUIREMENTS.md](DPU_DEPLOY_REQUIREMENTS.md)
> - [DPU_DEPLOY_ARCHITECTURE_BRIEF.md](DPU_DEPLOY_ARCHITECTURE_BRIEF.md)

---

## 0. Architecture Decisions Summary

| Q# | Question | Decision | Rationale |
|----|----------|----------|-----------|
| 4.1 | Service decomposition | **B: Package per domain** under `services/bare_metal/` | Forge convention for complex domains (see `services/bnk/`, `services/scanner/`, `services/dpf/`). Keeps SSH, Redfish, DPU provisioning cleanly separated. |
| 4.2 | Workflow orchestration | **B: New `BareMetalDeploymentService`** with its own state model | Fundamentally different from stack deployment — phases are SSH-driven, not K8s/Tofu-driven. Reuses Celery patterns but has its own workflow table. |
| 4.3 | Discovery integration | **B: Separate `services/bare_metal/discovery/`** with shared probe layer | Bare-metal discovery is SSH+IPMI+Redfish, not K8s API. Different data sources and output shapes from cluster scanner. But discovery models extend existing `models/discovery.py`. |
| 4.4 | SSH session lifecycle | **ControlMaster multiplexing** with per-deployment session pool | Subprocess-based SSH (like bnk-poc-deployer) with ControlMaster for connection reuse. Credentials from Forge's existing `SSHCredential` model via `core.encryption`. |
| 4.5 | Data model | New models: `BareMetalHost`, `BareMetalDeployment`, `DeploymentStep`, `BnkVersionProfile`. Extend existing: `DiscoveryJob`/`DiscoveredNode` | Minimal new tables. Host is the anchor entity. Deployment tracks the multi-phase workflow. VersionProfile is a seed table. |
| 4.6 | Phase 4 overlap | Forge modules take precedence; missing steps become new services | FLO, CNEInstance, cert-manager use existing Forge modules. CWC certs, OTEL certs, observability become new `services/bare_metal/phase4/` modules. |
| 4.7 | Frontend scope | Deferred — backend-first, API-driven | Spec covers API routes; frontend pages are follow-on work. |
| 4.8 | API routes | Two route files: `routes/bare_metal_hosts.py`, `routes/bare_metal_deployments.py` | Follows Forge pattern of domain-scoped route files. |
| 4.9 | MCP tools | Deferred to after API is stable | MCP tools wrap API routes; define after routes are finalized. |

---

## A. Service Decomposition

### Package Structure

```
backend/services/bare_metal/
├── __init__.py                     # Package exports
├── ssh_session.py                  # SSHSession — subprocess SSH execution
├── ssh_pool.py                     # SSHConnectionPool — ControlMaster management
├── ipmi_client.py                  # IPMIClient — ipmitool wrapper
├── redfish/
│   ├── __init__.py
│   ├── client.py                   # RedfishClient — HTTP client for BMC
│   ├── vendor_base.py              # VendorPlugin ABC
│   ├── supermicro.py               # Supermicro vendor plugin
│   ├── lenovo.py                   # Lenovo XClarity vendor plugin
│   ├── dell.py                     # Dell iDRAC vendor plugin
│   └── hpe.py                      # HPE iLO vendor plugin
├── discovery/
│   ├── __init__.py                 # BareMetalDiscoveryService
│   ├── host_probe.py               # SSH-based host probes
│   ├── bmc_probe.py                # BMC/IPMI probes
│   ├── dpu_probe.py                # DPU state probes (via rshim SSH)
│   ├── vlan_probe.py               # VLAN path validation
│   └── topology_detector.py        # Auto-detect topology from probe results
├── provisioning/
│   ├── __init__.py
│   ├── bfb_config.py               # BFB configuration rendering
│   ├── phase1_dpu.py               # Phase 1: DPU provisioning service
│   ├── phase2_k8s.py               # Phase 2: Host K8s bootstrap service
│   ├── phase3_join.py              # Phase 3: DPU cluster join service
│   └── phase4_platform.py          # Phase 4: BNK platform bridge to Forge modules
├── connectivity/
│   ├── __init__.py
│   ├── netplan_stager.py           # Pre-stage VF netplan config
│   ├── fallback_timer.py           # Fallback systemd timer deposit
│   ├── phase_c_bootstrap.py        # Phase C bootstrap service deposit
│   └── post_flash_watcher.py       # Post-flash recovery watcher deposit
├── orchestrator.py                 # BareMetalDeploymentService — workflow orchestrator
└── version_profiles.py             # BnkVersionProfileService — version matrix CRUD
```

### Service Classes — Responsibilities and Dependencies

| Service | File | Responsibility | Dependencies |
|---------|------|---------------|-------------|
| `SSHSession` | `ssh_session.py` | Execute commands, transfer files, stream output on a remote host via subprocess SSH | `SSHConnectionPool`, `core.encryption` |
| `SSHConnectionPool` | `ssh_pool.py` | Manage ControlMaster sockets for SSH multiplexing per host | None (stdlib only) |
| `IPMIClient` | `ipmi_client.py` | Execute ipmitool commands over LAN for power management | None (subprocess ipmitool) |
| `RedfishClient` | `redfish/client.py` | HTTP client for Redfish BMC operations, vendor dispatch | `VendorPlugin` implementations |
| `BareMetalDiscoveryService` | `discovery/__init__.py` | Orchestrate discovery probes, produce structured results | `SSHSession`, `RedfishClient`, `IPMIClient`, probe modules |
| `TopologyDetector` | `discovery/topology_detector.py` | Auto-detect deployment topology from probe results | None (pure function) |
| `BfbConfigGenerator` | `provisioning/bfb_config.py` | Render BFB configuration for DPU flash | `BnkVersionProfile` |
| `Phase1DpuService` | `provisioning/phase1_dpu.py` | DPU provisioning: BFB flash, K8s prereq install | `SSHSession`, `BfbConfigGenerator`, connectivity services |
| `Phase2K8sService` | `provisioning/phase2_k8s.py` | kubeadm init, CNI, cert-manager, SR-IOV, storage | `SSHSession`, `BnkVersionProfile` |
| `Phase3JoinService` | `provisioning/phase3_join.py` | kubeadm join, node label, taint | `SSHSession` |
| `Phase4PlatformService` | `provisioning/phase4_platform.py` | Bridge to Forge modules (FLO, CNEInstance) + new services (CWC certs, OTEL) | Forge modules, `SSHSession` |
| `NetplanStager` | `connectivity/netplan_stager.py` | Pre-stage VF netplan config on host before mode change | `SSHSession` |
| `FallbackTimer` | `connectivity/fallback_timer.py` | Deposit fallback systemd timer for connectivity revert | `SSHSession` |
| `PhaseCBootstrap` | `connectivity/phase_c_bootstrap.py` | Deposit Phase C systemd one-shot for DPU OVS config | `SSHSession` |
| `PostFlashWatcher` | `connectivity/post_flash_watcher.py` | Deposit post-flash recovery watcher script | `SSHSession` |
| `BareMetalDeploymentService` | `orchestrator.py` | Multi-phase deployment orchestration, state machine, resume/retry | All phase services, `SSHSession`, DB |
| `BnkVersionProfileService` | `version_profiles.py` | CRUD for BNK version profiles | DB |

### Integration with Existing Services

```
┌──────────────────────────────────────────────────────┐
│  Existing Forge Services                              │
│  ┌──────────────────┐  ┌──────────────────────────┐  │
│  │ SSHCredentialSvc  │  │ DiscoveryService         │  │
│  │ (CRUD/test)       │  │ (project-scoped SSH      │  │
│  │                   │  │  discovery — existing)    │  │
│  └────────┬─────────┘  └────────────┬─────────────┘  │
│           │                          │                 │
│  ┌────────┴──────────────────────────┴─────────────┐  │
│  │  core/encryption.py — encrypt/decrypt secrets    │  │
│  └──────────────────────────────────────────────────┘  │
│  ┌──────────────────┐  ┌──────────────────────────┐  │
│  │ AuditService      │  │ Forge Modules            │  │
│  │ (action logging)  │  │ (FLO, CNEInstance, etc.) │  │
│  └──────────────────┘  └──────────────────────────┘  │
└──────────────────────────────────────────────────────┘
              │                    │
              ▼                    ▼
┌──────────────────────────────────────────────────────┐
│  New: services/bare_metal/                            │
│  ┌───────────┐  ┌────────────┐  ┌─────────────────┐ │
│  │SSHSession  │  │RedfishClient│ │IPMIClient       │ │
│  │(subprocess)│  │(HTTP)       │ │(subprocess)     │ │
│  └─────┬─────┘  └──────┬─────┘ └────────┬────────┘ │
│        │               │                 │          │
│  ┌─────┴───────────────┴─────────────────┴────────┐ │
│  │  BareMetalDiscoveryService                      │ │
│  │  → host_probe, bmc_probe, dpu_probe, vlan_probe │ │
│  └──────────────────────┬─────────────────────────┘ │
│                          │                           │
│  ┌───────────────────────┴──────────────────────┐   │
│  │  BareMetalDeploymentService (orchestrator)    │   │
│  │  → Phase1..4 services + connectivity services │   │
│  └──────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────┘
```

**Key integration points:**

1. **SSHCredential** — Bare-metal hosts reference existing `SSHCredential` records. Passwords/keys decrypted via `core.encryption.decrypt_value()`.
2. **DiscoveryJob/DiscoveredNode** — Bare-metal discovery extends existing discovery models with new DPU-specific fields (via JSON columns).
3. **Audit** — All deployment actions logged via existing `AuditService`.
4. **Forge Modules** — Phase 4 delegates FLO/CNEInstance/cert-manager to existing Forge modules. The `Phase4PlatformService` bridges between the bare-metal workflow and Forge's module execution.
5. **Celery** — New `tasks/bare_metal_tasks.py` follows existing Celery patterns (bind=True, get_db_context, CallbackTask).

---

## B. Data Model Design

### B.1 New Enums (`models/enums.py`)

```python
# ---------------------------------------------------------------------------
# BareMetalTopology — deployment topology classification
# ---------------------------------------------------------------------------

class BareMetalTopology(StrEnum):
    """Deployment topology for bare-metal DPU hosts."""

    REGULAR = "regular"      # Host has independent NIC, DPU is worker only
    BF3 = "bf3"              # Host network through BF3 PF, mode change via BFB
    BF3_IPMI = "bf3_ipmi"    # BF3 + mlxconfig mode change + IPMI power cycle
    BMC = "bmc"              # BF3 + Redfish BIOS mode change + Redfish power cycle


# ---------------------------------------------------------------------------
# BareMetalDeploymentStatus — lifecycle of a bare-metal deployment
# ---------------------------------------------------------------------------

class BareMetalDeploymentStatus(StrEnum):
    """Status values for ``BareMetalDeployment.status``."""

    PENDING = "pending"
    DISCOVERING = "discovering"
    PLANNING = "planning"
    IN_PROGRESS = "in_progress"
    PAUSED = "paused"            # User-initiated pause between phases
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @classmethod
    def terminal_states(cls) -> frozenset["BareMetalDeploymentStatus"]:
        return frozenset({cls.COMPLETED, cls.FAILED, cls.CANCELLED})


# ---------------------------------------------------------------------------
# DeploymentStepStatus — lifecycle of a single deployment step
# ---------------------------------------------------------------------------

class DeploymentStepStatus(StrEnum):
    """Status values for ``DeploymentStep.status``."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"          # Probe detected already done


# ---------------------------------------------------------------------------
# DeploymentPhase — the four deployment phases
# ---------------------------------------------------------------------------

class DeploymentPhase(StrEnum):
    """Phase identifiers for the four-phase deployment."""

    PHASE_1_DPU = "phase_1_dpu"
    PHASE_2_K8S = "phase_2_k8s"
    PHASE_3_JOIN = "phase_3_join"
    PHASE_4_PLATFORM = "phase_4_platform"


# ---------------------------------------------------------------------------
# HostAccessTier — BMC/IPMI capability tier
# ---------------------------------------------------------------------------

class HostAccessTier(StrEnum):
    """BMC access tier detected during discovery."""

    NONE = "none"          # No BMC access
    IPMI_ONLY = "ipmi"     # IPMI LAN reachable, no Redfish
    REDFISH = "redfish"    # Full Redfish available
```

### B.2 New SQLAlchemy Models

#### `models/bare_metal.py`

```python
"""Bare-metal host and DPU deployment models."""

from sqlalchemy import (
    JSON, Boolean, Column, DateTime, Float,
    ForeignKey, Index, Integer, String, Text,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base
from models.enums import (
    BareMetalDeploymentStatus,
    BareMetalTopology,
    DeploymentPhase,
    DeploymentStepStatus,
    HostAccessTier,
)


class BareMetalHost(Base):
    """A registered bare-metal server with DPU(s) managed by Forge."""

    __tablename__ = "bare_metal_hosts"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)

    # Identity
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    hostname = Column(String(255), nullable=True)

    # Host SSH access
    host_ip = Column(String(255), nullable=False)
    ssh_credential_id = Column(Integer, ForeignKey("ssh_credentials.id", ondelete="SET NULL"), nullable=True)
    ssh_port = Column(Integer, default=22)

    # Jumphost chain (optional) — JSON array of SSHCredential IDs in order
    jumphost_chain = Column(JSON, nullable=True)  # [{"ssh_credential_id": 1}, {"ssh_credential_id": 2}]

    # BMC/IPMI access
    bmc_ip = Column(String(255), nullable=True)
    bmc_username_encrypted = Column(Text, nullable=True)
    bmc_password_encrypted = Column(Text, nullable=True)
    bmc_access_tier = Column(String(50), default=HostAccessTier.NONE)
    bmc_vendor = Column(String(100), nullable=True)  # Detected: supermicro, lenovo, dell, hpe

    # IPMI access (may differ from BMC IP)
    ipmi_ip = Column(String(255), nullable=True)
    ipmi_username_encrypted = Column(Text, nullable=True)
    ipmi_password_encrypted = Column(Text, nullable=True)

    # Topology
    topology = Column(String(50), nullable=True)  # BareMetalTopology value
    topology_auto_detected = Column(Boolean, default=False)

    # Network config for bf3/bmc topologies
    network_mode = Column(String(20), nullable=True)  # "vlan" or "flat"
    vlan_id = Column(Integer, nullable=True)
    gateway_ip = Column(String(255), nullable=True)
    host_mgmt_ip = Column(String(255), nullable=True)  # CIDR notation
    dpu_mgmt_ip = Column(String(255), nullable=True)   # CIDR notation (optional)

    # Target BNK version
    version_profile_id = Column(Integer, ForeignKey("bnk_version_profiles.id", ondelete="SET NULL"), nullable=True)

    # Hardware discovery cache (updated by discovery)
    os_info = Column(JSON, nullable=True)        # {os_type, os_version, kernel, arch}
    dpu_info = Column(JSON, nullable=True)        # [{index, nic_mode, pf_name, vf_count, sw_versions}]
    k8s_info = Column(JSON, nullable=True)        # {installed, running, version, node_count}
    last_discovery_at = Column(DateTime(timezone=True), nullable=True)

    # Resulting K8s cluster (linked after Phase 2 completes)
    kubernetes_cluster_id = Column(
        Integer, ForeignKey("kubernetes_clusters.id", ondelete="SET NULL"), nullable=True
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by = Column(String(255), nullable=True)

    # Relationships
    project = relationship("Project", backref="bare_metal_hosts")
    ssh_credential = relationship("SSHCredential", foreign_keys=[ssh_credential_id])
    version_profile = relationship("BnkVersionProfile", foreign_keys=[version_profile_id])
    kubernetes_cluster = relationship("KubernetesCluster", foreign_keys=[kubernetes_cluster_id])
    deployments = relationship(
        "BareMetalDeployment", back_populates="host",
        cascade="all, delete-orphan", order_by="BareMetalDeployment.created_at.desc()"
    )

    __table_args__ = (
        Index("idx_bm_host_project_name", "project_id", "name", unique=True),
        Index("idx_bm_host_project_ip", "project_id", "host_ip"),
    )


class BareMetalDeployment(Base):
    """A deployment execution against a bare-metal host."""

    __tablename__ = "bare_metal_deployments"

    id = Column(Integer, primary_key=True, index=True)
    host_id = Column(Integer, ForeignKey("bare_metal_hosts.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)

    # Topology snapshot (frozen at deployment start)
    topology = Column(String(50), nullable=False)
    version_profile_snapshot = Column(JSON, nullable=True)  # Snapshot of version profile at start

    # State machine
    status = Column(
        String(50), default=BareMetalDeploymentStatus.PENDING,
        nullable=False, index=True,
    )
    current_phase = Column(String(50), nullable=True)       # DeploymentPhase value
    current_step_index = Column(Integer, default=0)

    # Celery integration
    celery_task_id = Column(String(255), nullable=True, index=True)

    # Resume support
    resume_from_step = Column(Integer, nullable=True)  # Step index to resume from

    # Timing
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Float, nullable=True)

    # Error tracking
    error_message = Column(Text, nullable=True)
    error_phase = Column(String(50), nullable=True)
    error_step_index = Column(Integer, nullable=True)

    # Audit
    triggered_by = Column(String(255), default="user")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    host = relationship("BareMetalHost", back_populates="deployments")
    project = relationship("Project")
    steps = relationship(
        "DeploymentStep", back_populates="deployment",
        cascade="all, delete-orphan", order_by="DeploymentStep.step_index",
    )

    __table_args__ = (
        Index("idx_bm_deploy_host_status", "host_id", "status"),
        Index("idx_bm_deploy_project_status", "project_id", "status"),
        Index("idx_bm_deploy_created", "created_at"),
    )


class DeploymentStep(Base):
    """Individual step within a bare-metal deployment."""

    __tablename__ = "bare_metal_deployment_steps"

    id = Column(Integer, primary_key=True, index=True)
    deployment_id = Column(
        Integer, ForeignKey("bare_metal_deployments.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    # Step identity
    step_index = Column(Integer, nullable=False)  # 0-based order
    phase = Column(String(50), nullable=False)     # DeploymentPhase value
    name = Column(String(255), nullable=False)     # e.g., "flash_dpu", "kubeadm_init"
    description = Column(Text, nullable=True)

    # State
    status = Column(
        String(50), default=DeploymentStepStatus.PENDING,
        nullable=False, index=True,
    )

    # Probe-based idempotency
    probe_result = Column(JSON, nullable=True)     # Result of pre-execution probe
    already_done = Column(Boolean, default=False)   # Probe detected step is complete

    # Connectivity risk flag
    connectivity_risk = Column(Boolean, default=False)  # Step may disrupt SSH
    connectivity_mechanism = Column(String(100), nullable=True)  # Which preservation mechanism

    # Timing
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    duration_seconds = Column(Float, nullable=True)

    # Output
    output_log = Column(Text, nullable=True)       # Streaming command output
    error_message = Column(Text, nullable=True)
    exit_code = Column(Integer, nullable=True)

    # Metadata
    meta_data = Column(JSON, nullable=True)         # Step-specific data (e.g., BFB config used)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    deployment = relationship("BareMetalDeployment", back_populates="steps")

    __table_args__ = (
        Index("idx_bm_step_deploy_index", "deployment_id", "step_index", unique=True),
        Index("idx_bm_step_status", "status"),
    )


class BnkVersionProfile(Base):
    """Coordinated component version matrix for a BNK release."""

    __tablename__ = "bnk_version_profiles"

    id = Column(Integer, primary_key=True, index=True)

    # Identity
    name = Column(String(100), nullable=False, unique=True, index=True)  # e.g., "bnk-2.1", "bnk-2.2"
    display_name = Column(String(255), nullable=False)  # e.g., "BNK 2.1 (GA)"
    description = Column(Text, nullable=True)
    is_default = Column(Boolean, default=False)

    # Core versions
    bnk_manifest_version = Column(String(50), nullable=False)
    bnk_cr_kind = Column(String(50), nullable=False)    # "BNKGatewayClass" or "CNEInstance"
    flo_version = Column(String(50), nullable=False)
    k8s_version = Column(String(50), nullable=False)
    doca_version = Column(String(50), nullable=False)

    # Runtime versions
    containerd_version = Column(String(50), nullable=False)
    runc_version = Column(String(50), nullable=False)

    # Ecosystem versions
    calico_version = Column(String(50), nullable=False)
    cert_manager_version = Column(String(50), nullable=False)
    gateway_api_version = Column(String(50), nullable=False)
    multus_version = Column(String(50), nullable=False)
    sriov_version = Column(String(50), nullable=False)

    # Storage
    storage_class_type = Column(String(50), nullable=False)       # "local-path" or "nfs"
    storage_provisioner = Column(String(255), nullable=False)

    # Feature flags
    feature_flags = Column(JSON, nullable=True)  # {"ipv6": false, "tmm_node_labels": true, ...}

    # Full version manifest (catch-all for additional components)
    full_manifest = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_version_profile_default", "is_default"),
    )
```

### B.3 Model Registration

Add to `models/__init__.py`:

```python
from models.bare_metal import BareMetalDeployment, BareMetalHost, BnkVersionProfile, DeploymentStep
```

Add relationship to `Project` model (or rely on `backref="bare_metal_hosts"` in `BareMetalHost`).

### B.4 New Pydantic Schemas

#### `schemas/bare_metal.py`

```python
"""Pydantic schemas for bare-metal DPU deployment API."""

from datetime import datetime

from pydantic import BaseModel, Field


# --- Host Schemas ---

class BareMetalHostCreate(BaseModel):
    name: str
    description: str | None = None
    host_ip: str
    ssh_credential_id: int | None = None
    ssh_port: int = 22
    jumphost_chain: list[dict] | None = None
    bmc_ip: str | None = None
    bmc_username: str | None = None
    bmc_password: str | None = None
    ipmi_ip: str | None = None
    ipmi_username: str | None = None
    ipmi_password: str | None = None
    topology: str | None = None  # Optional override, otherwise auto-detected
    network_mode: str | None = None
    vlan_id: int | None = None
    gateway_ip: str | None = None
    host_mgmt_ip: str | None = None
    dpu_mgmt_ip: str | None = None
    version_profile_id: int | None = None


class BareMetalHostUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    host_ip: str | None = None
    ssh_credential_id: int | None = None
    ssh_port: int | None = None
    jumphost_chain: list[dict] | None = None
    bmc_ip: str | None = None
    bmc_username: str | None = None
    bmc_password: str | None = None
    ipmi_ip: str | None = None
    ipmi_username: str | None = None
    ipmi_password: str | None = None
    topology: str | None = None
    network_mode: str | None = None
    vlan_id: int | None = None
    gateway_ip: str | None = None
    host_mgmt_ip: str | None = None
    dpu_mgmt_ip: str | None = None
    version_profile_id: int | None = None


class BareMetalHostResponse(BaseModel):
    id: int
    project_id: int
    name: str
    description: str | None
    hostname: str | None
    host_ip: str
    ssh_credential_id: int | None
    ssh_port: int
    has_jumphost_chain: bool
    bmc_ip: str | None
    bmc_access_tier: str
    bmc_vendor: str | None
    ipmi_ip: str | None
    has_bmc_credentials: bool
    has_ipmi_credentials: bool
    topology: str | None
    topology_auto_detected: bool
    network_mode: str | None
    vlan_id: int | None
    gateway_ip: str | None
    host_mgmt_ip: str | None
    dpu_mgmt_ip: str | None
    version_profile_id: int | None
    os_info: dict | None
    dpu_info: list[dict] | None
    k8s_info: dict | None
    last_discovery_at: datetime | None
    kubernetes_cluster_id: int | None
    created_at: datetime
    updated_at: datetime


class BareMetalHostListResponse(BaseModel):
    hosts: list[BareMetalHostResponse]


# --- Deployment Schemas ---

class BareMetalDeploymentCreate(BaseModel):
    host_id: int
    resume_from_step: int | None = None  # Optional: resume from this step index
    skip_discovery: bool = False          # Skip pre-flight discovery


class DeploymentStepResponse(BaseModel):
    id: int
    step_index: int
    phase: str
    name: str
    description: str | None
    status: str
    already_done: bool
    connectivity_risk: bool
    connectivity_mechanism: str | None
    started_at: datetime | None
    completed_at: datetime | None
    duration_seconds: float | None
    error_message: str | None
    exit_code: int | None
    created_at: datetime


class BareMetalDeploymentResponse(BaseModel):
    id: int
    host_id: int
    project_id: int
    topology: str
    status: str
    current_phase: str | None
    current_step_index: int
    celery_task_id: str | None
    resume_from_step: int | None
    started_at: datetime | None
    completed_at: datetime | None
    duration_seconds: float | None
    error_message: str | None
    error_phase: str | None
    error_step_index: int | None
    triggered_by: str
    created_at: datetime
    steps: list[DeploymentStepResponse]


class BareMetalDeploymentListResponse(BaseModel):
    deployments: list[BareMetalDeploymentResponse]


class BareMetalDeploymentResumeRequest(BaseModel):
    from_step: int | None = None  # If None, resume from failed step


# --- Discovery Schemas ---

class BareMetalDiscoveryRequest(BaseModel):
    """Trigger bare-metal-specific discovery for a host."""
    probe_bmc: bool = True
    probe_ipmi: bool = True
    probe_dpu: bool = True
    probe_vlan: bool = False  # Destructive-ish — requires explicit opt-in


class BareMetalDiscoveryResponse(BaseModel):
    host_id: int
    host_ip: str
    topology_recommendation: str | None
    probes: dict  # Structured probe results
    assessment: list[dict]  # [{check, status: pass/warn/fail, message}]
    version_drift: list[dict] | None  # [{component, installed, expected}]
    recommendations: list[str]
    discovered_at: datetime


# --- Version Profile Schemas ---

class BnkVersionProfileResponse(BaseModel):
    id: int
    name: str
    display_name: str
    description: str | None
    is_default: bool
    bnk_manifest_version: str
    bnk_cr_kind: str
    flo_version: str
    k8s_version: str
    doca_version: str
    containerd_version: str
    runc_version: str
    calico_version: str
    cert_manager_version: str
    gateway_api_version: str
    multus_version: str
    sriov_version: str
    storage_class_type: str
    storage_provisioner: str
    feature_flags: dict | None
    created_at: datetime


class BnkVersionProfileListResponse(BaseModel):
    profiles: list[BnkVersionProfileResponse]
```

---

## C. API Route Design

### C.1 Route Files

#### `routes/bare_metal_hosts.py`

Prefix: `/api/projects/{project_id}/bare-metal/hosts`

| Method | Path | Handler | Auth | Description |
|--------|------|---------|------|-------------|
| `GET` | `/` | `list_hosts` | `require_viewer` | List bare-metal hosts for a project |
| `POST` | `/` | `create_host` | `require_project_owner` | Register a new bare-metal host |
| `GET` | `/{host_id}` | `get_host` | `require_viewer` | Get host details |
| `PUT` | `/{host_id}` | `update_host` | `require_project_owner` | Update host config |
| `DELETE` | `/{host_id}` | `delete_host` | `require_project_owner` | Remove host registration |
| `POST` | `/{host_id}/discover` | `trigger_discovery` | `require_project_owner` | Run bare-metal discovery |
| `GET` | `/{host_id}/discovery` | `get_discovery_results` | `require_viewer` | Get latest discovery results |
| `POST` | `/{host_id}/test-ssh` | `test_ssh_connection` | `require_project_owner` | Test SSH connectivity |
| `POST` | `/{host_id}/test-bmc` | `test_bmc_connection` | `require_project_owner` | Test BMC/IPMI connectivity |

```python
# routes/bare_metal_hosts.py — skeleton

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from core.errors import handle_route_errors
from database import get_db
from models import User
from routes.auth import require_project_owner, require_viewer
from schemas.bare_metal import (
    BareMetalDiscoveryRequest,
    BareMetalDiscoveryResponse,
    BareMetalHostCreate,
    BareMetalHostListResponse,
    BareMetalHostResponse,
    BareMetalHostUpdate,
)

router = APIRouter(
    prefix="/api/projects/{project_id}/bare-metal/hosts",
    tags=["bare-metal"],
)


@router.get("", response_model=BareMetalHostListResponse, dependencies=[Depends(require_viewer)])
@handle_route_errors("list bare-metal hosts")
def list_hosts(project_id: int, db: Session = Depends(get_db)) -> BareMetalHostListResponse:
    from services.bare_metal import BareMetalHostService
    return BareMetalHostService(db).list_hosts(project_id)


@router.post("", response_model=BareMetalHostResponse)
@handle_route_errors("create bare-metal host")
def create_host(
    project_id: int,
    data: BareMetalHostCreate,
    _user: User = Depends(require_project_owner),
    db: Session = Depends(get_db),
) -> BareMetalHostResponse:
    from services.bare_metal import BareMetalHostService
    result = BareMetalHostService(db).create_host(project_id, data)
    db.commit()
    return result


# ... (thin handlers for all endpoints, following existing pattern)
```

#### `routes/bare_metal_deployments.py`

Prefix: `/api/projects/{project_id}/bare-metal/deployments`

| Method | Path | Handler | Auth | Description |
|--------|------|---------|------|-------------|
| `GET` | `/` | `list_deployments` | `require_viewer` | List deployments for a project |
| `POST` | `/` | `create_deployment` | `require_project_owner` | Start a new deployment |
| `GET` | `/{deployment_id}` | `get_deployment` | `require_viewer` | Get deployment status + steps |
| `POST` | `/{deployment_id}/resume` | `resume_deployment` | `require_project_owner` | Resume from failed/paused step |
| `POST` | `/{deployment_id}/cancel` | `cancel_deployment` | `require_project_owner` | Cancel a running deployment |
| `GET` | `/{deployment_id}/steps/{step_index}/logs` | `get_step_logs` | `require_viewer` | Stream/get step output log |

#### `routes/bare_metal_version_profiles.py`

Prefix: `/api/bare-metal/version-profiles`

| Method | Path | Handler | Auth | Description |
|--------|------|---------|------|-------------|
| `GET` | `/` | `list_version_profiles` | `require_viewer` | List all BNK version profiles |
| `GET` | `/{profile_id}` | `get_version_profile` | `require_viewer` | Get profile details |
| `POST` | `/` | `create_version_profile` | `require_admin` | Create a version profile (admin only) |

### C.2 Route Pattern Compliance

Every route follows Forge conventions:

1. **Thin handlers** — instantiate service, call one method, return result
2. **`@handle_route_errors("description")`** on every handler
3. **`response_model=`** on every route for typed responses
4. **Auth dependencies** — `require_viewer` for reads, `require_project_owner` for mutations, `require_admin` for global config
5. **`db.commit()` in route** for non-Celery writes (Celery tasks manage their own commits)
6. **Pydantic schemas** for request/response models — inline for small request models, `schemas/bare_metal.py` for responses

---

## D. Workflow Orchestration Design

### D.1 Step Registry Pattern

Each topology maps to an ordered list of steps. Steps are defined as a registry:

```python
# services/bare_metal/orchestrator.py

from dataclasses import dataclass
from collections.abc import Callable
from models.enums import DeploymentPhase


@dataclass(frozen=True)
class StepDefinition:
    """Definition of a deployment step."""
    phase: DeploymentPhase
    name: str                              # Machine name, e.g., "flash_dpu"
    description: str                       # Human description
    connectivity_risk: bool = False        # May disrupt SSH?
    connectivity_mechanism: str | None = None  # Which preservation mech


# --- Step registries per topology ---

REGULAR_STEPS: list[StepDefinition] = [
    # Phase 1
    StepDefinition(DeploymentPhase.PHASE_1_DPU, "probe_dpu_state", "Probe DPU current state"),
    StepDefinition(DeploymentPhase.PHASE_1_DPU, "flash_dpu", "Flash DPU with BFB image", connectivity_risk=False),
    StepDefinition(DeploymentPhase.PHASE_1_DPU, "wait_dpu_ready", "Wait for DPU to become SSH-reachable"),
    StepDefinition(DeploymentPhase.PHASE_1_DPU, "install_dpu_prereqs", "Install containerd, runc, kubeadm on DPU"),
    # Phase 2
    StepDefinition(DeploymentPhase.PHASE_2_K8S, "install_k8s_prereqs", "Install kubeadm, kubelet, kubectl on host"),
    StepDefinition(DeploymentPhase.PHASE_2_K8S, "kubeadm_init", "Initialize K8s control plane"),
    StepDefinition(DeploymentPhase.PHASE_2_K8S, "install_cni", "Install Calico CNI"),
    StepDefinition(DeploymentPhase.PHASE_2_K8S, "install_cert_manager", "Install cert-manager"),
    StepDefinition(DeploymentPhase.PHASE_2_K8S, "install_sriov", "Install SR-IOV network operator"),
    StepDefinition(DeploymentPhase.PHASE_2_K8S, "install_multus", "Install Multus CNI"),
    StepDefinition(DeploymentPhase.PHASE_2_K8S, "install_storage", "Install storage provisioner"),
    # Phase 3
    StepDefinition(DeploymentPhase.PHASE_3_JOIN, "kubeadm_join", "Join DPU node to cluster"),
    StepDefinition(DeploymentPhase.PHASE_3_JOIN, "label_dpu_node", "Label DPU node (app=f5-tmm)"),
    StepDefinition(DeploymentPhase.PHASE_3_JOIN, "taint_dpu_node", "Taint DPU node (dpu=true:NoSchedule)"),
    # Phase 4
    StepDefinition(DeploymentPhase.PHASE_4_PLATFORM, "install_flo", "Install FLO via Helm"),
    StepDefinition(DeploymentPhase.PHASE_4_PLATFORM, "deploy_bnk_cr", "Deploy BNK CR (CNEInstance/BNKGatewayClass)"),
    StepDefinition(DeploymentPhase.PHASE_4_PLATFORM, "install_cwc_certs", "Install CWC certificates"),
    StepDefinition(DeploymentPhase.PHASE_4_PLATFORM, "install_otel_certs", "Install OTEL certificates"),
    StepDefinition(DeploymentPhase.PHASE_4_PLATFORM, "deploy_observability", "Deploy observability stack"),
]

BF3_STEPS: list[StepDefinition] = [
    # Phase 1 (with connectivity preservation)
    StepDefinition(DeploymentPhase.PHASE_1_DPU, "probe_dpu_state", "Probe DPU current state"),
    StepDefinition(DeploymentPhase.PHASE_1_DPU, "stage_vf_netplan", "Pre-stage VF netplan config",
                   connectivity_risk=False),
    StepDefinition(DeploymentPhase.PHASE_1_DPU, "install_fallback_timer", "Install connectivity fallback timer",
                   connectivity_risk=False),
    StepDefinition(DeploymentPhase.PHASE_1_DPU, "flash_dpu", "Flash DPU with BFB image (mode change via bfb_post_install)",
                   connectivity_risk=True, connectivity_mechanism="pre_staged_netplan"),
    StepDefinition(DeploymentPhase.PHASE_1_DPU, "wait_connectivity_restore", "Wait for host connectivity via VF"),
    StepDefinition(DeploymentPhase.PHASE_1_DPU, "install_dpu_prereqs", "Install containerd, runc, kubeadm on DPU"),
    # Phase 2-4 same as REGULAR
    *REGULAR_STEPS[4:],  # Reuse from Phase 2 onward
]

BF3_IPMI_STEPS: list[StepDefinition] = [
    # Phase 1 (mlxconfig + IPMI power cycle)
    StepDefinition(DeploymentPhase.PHASE_1_DPU, "probe_dpu_state", "Probe DPU current state"),
    StepDefinition(DeploymentPhase.PHASE_1_DPU, "set_nic_mode_mlxconfig", "Set NIC mode via mlxconfig"),
    StepDefinition(DeploymentPhase.PHASE_1_DPU, "deposit_phase_c", "Deposit Phase C bootstrap service",
                   connectivity_risk=False),
    StepDefinition(DeploymentPhase.PHASE_1_DPU, "ipmi_power_cycle", "IPMI cold boot",
                   connectivity_risk=True, connectivity_mechanism="phase_c_bootstrap"),
    StepDefinition(DeploymentPhase.PHASE_1_DPU, "wait_connectivity_restore", "Wait for host connectivity via VF"),
    StepDefinition(DeploymentPhase.PHASE_1_DPU, "flash_dpu", "Flash DPU with BFB image",
                   connectivity_risk=True, connectivity_mechanism="post_flash_watcher"),
    StepDefinition(DeploymentPhase.PHASE_1_DPU, "wait_dpu_ready", "Wait for DPU to become SSH-reachable"),
    StepDefinition(DeploymentPhase.PHASE_1_DPU, "install_dpu_prereqs", "Install containerd, runc, kubeadm on DPU"),
    # Phase 2-4 same as REGULAR
    *REGULAR_STEPS[4:],
]

BMC_STEPS: list[StepDefinition] = [
    # Phase 1 (Redfish BIOS + Redfish cold boot)
    StepDefinition(DeploymentPhase.PHASE_1_DPU, "probe_dpu_state", "Probe DPU current state"),
    StepDefinition(DeploymentPhase.PHASE_1_DPU, "set_nic_mode_redfish", "Set NIC mode via Redfish BIOS attribute"),
    StepDefinition(DeploymentPhase.PHASE_1_DPU, "deposit_phase_c", "Deposit Phase C bootstrap service",
                   connectivity_risk=False),
    StepDefinition(DeploymentPhase.PHASE_1_DPU, "redfish_cold_boot", "Redfish cold boot (GracefulShutdown → On)",
                   connectivity_risk=True, connectivity_mechanism="phase_c_bootstrap"),
    StepDefinition(DeploymentPhase.PHASE_1_DPU, "wait_connectivity_restore", "Wait for host connectivity via VF"),
    StepDefinition(DeploymentPhase.PHASE_1_DPU, "flash_dpu", "Flash DPU with BFB image",
                   connectivity_risk=True, connectivity_mechanism="post_flash_watcher"),
    StepDefinition(DeploymentPhase.PHASE_1_DPU, "wait_dpu_ready", "Wait for DPU to become SSH-reachable"),
    StepDefinition(DeploymentPhase.PHASE_1_DPU, "install_dpu_prereqs", "Install containerd, runc, kubeadm on DPU"),
    # Phase 2-4 same as REGULAR
    *REGULAR_STEPS[4:],
]

TOPOLOGY_STEPS: dict[str, list[StepDefinition]] = {
    "regular": REGULAR_STEPS,
    "bf3": BF3_STEPS,
    "bf3_ipmi": BF3_IPMI_STEPS,
    "bmc": BMC_STEPS,
}
```

### D.2 State Machine

```
Deployment:  PENDING → DISCOVERING → PLANNING → IN_PROGRESS → COMPLETED
                                                     │
                                                     ├→ PAUSED (user pause)
                                                     │      │
                                                     │      └→ IN_PROGRESS (resume)
                                                     │
                                                     └→ FAILED
                                                            │
                                                            └→ IN_PROGRESS (resume from step)

Step:        PENDING → RUNNING → COMPLETED
                │                    ↑
                └→ SKIPPED ──────────┘  (probe detected already done)
                │
                └→ RUNNING → FAILED
```

### D.3 Celery Task Structure

#### `tasks/bare_metal_tasks.py`

```python
"""Celery tasks for bare-metal DPU deployment."""

import logging
from celery_app import celery_app
from database import get_db_context
from tasks._tofu_helpers import CallbackTask

logger = logging.getLogger(__name__)


class BareMetalCallbackTask(CallbackTask):
    """Bare-metal task with WebSocket progress callbacks."""


@celery_app.task(
    name="tasks.bare_metal.execute_deployment",
    base=BareMetalCallbackTask,
    bind=True,
    time_limit=7200,       # 2 hours hard limit (full deploy: ~45 min)
    soft_time_limit=6600,  # 110 min soft limit
)
def execute_bare_metal_deployment(self, deployment_id: int, project_id: int) -> dict:
    """
    Execute a bare-metal deployment asynchronously.

    Iterates through steps, calling the appropriate phase service for each.
    Commits after each step for progress persistence.
    Publishes WebSocket updates for real-time UI.
    """
    with get_db_context() as db:
        from services.bare_metal.orchestrator import BareMetalDeploymentService
        svc = BareMetalDeploymentService(db)
        return svc.execute_deployment(deployment_id, task=self)


@celery_app.task(
    name="tasks.bare_metal.run_discovery",
    base=BareMetalCallbackTask,
    bind=True,
    time_limit=300,        # 5 min for discovery
    soft_time_limit=240,
)
def run_bare_metal_discovery(self, host_id: int, project_id: int, options: dict) -> dict:
    """Run bare-metal discovery probes asynchronously."""
    with get_db_context() as db:
        from services.bare_metal.discovery import BareMetalDiscoveryService
        svc = BareMetalDiscoveryService(db)
        return svc.run_discovery(host_id, options)
```

### D.4 Real-Time Status Streaming

Status streaming follows the existing Forge WebSocket pattern:

1. **Celery task** calls `publish_task_update()` after each step completes
2. **WebSocket service** broadcasts to connected frontend clients
3. **Step output** is appended to `DeploymentStep.output_log` with intermediate commits

```python
# In orchestrator.py — after each step
from services.websocket_service import publish_task_update

def _notify_step_progress(self, deployment: BareMetalDeployment, step: DeploymentStep):
    """Publish WebSocket update for step progress."""
    publish_task_update(
        task_id=deployment.celery_task_id,
        status=deployment.status,
        project_id=deployment.project_id,
        meta={
            "deployment_id": deployment.id,
            "current_phase": deployment.current_phase,
            "current_step": step.step_index,
            "step_name": step.name,
            "step_status": step.status,
            "total_steps": len(deployment.steps),
        },
    )
```

For **streaming SSH output**, the `SSHSession.execute_streaming()` method yields lines:

```python
def execute_streaming(self, command: str) -> Generator[str, None, int]:
    """Execute command and yield output lines. Returns exit code."""
    ...
```

The Celery task collects these lines and does periodic DB flushes of the step's `output_log`.

### D.5 Resume/Retry Design

Resume is probe-based, not state-file-based:

```python
def execute_deployment(self, deployment_id: int, task=None) -> dict:
    deployment = self._get_deployment(deployment_id)
    steps = deployment.steps

    start_index = deployment.resume_from_step or 0

    for step in steps[start_index:]:
        # 1. Probe: is this step already done?
        probe_result = self._probe_step(deployment.host, step)
        if probe_result.already_done:
            step.status = DeploymentStepStatus.SKIPPED
            step.already_done = True
            step.probe_result = probe_result.to_dict()
            self.db.commit()
            continue

        # 2. Pre-step: activate connectivity preservation if needed
        if step.connectivity_risk:
            self._activate_connectivity_mechanism(deployment.host, step)

        # 3. Execute step
        step.status = DeploymentStepStatus.RUNNING
        step.started_at = datetime.now(UTC)
        self.db.commit()

        try:
            result = self._execute_step(deployment, step)
            step.status = DeploymentStepStatus.COMPLETED
            step.exit_code = result.exit_code
            step.output_log = result.output
        except Exception as e:
            step.status = DeploymentStepStatus.FAILED
            step.error_message = str(e)
            deployment.status = BareMetalDeploymentStatus.FAILED
            deployment.error_phase = step.phase
            deployment.error_step_index = step.step_index
            self.db.commit()
            raise

        step.completed_at = datetime.now(UTC)
        step.duration_seconds = (step.completed_at - step.started_at).total_seconds()
        deployment.current_step_index = step.step_index
        deployment.current_phase = step.phase
        self.db.commit()
        self._notify_step_progress(deployment, step)

    deployment.status = BareMetalDeploymentStatus.COMPLETED
    deployment.completed_at = datetime.now(UTC)
    self.db.commit()
```

### D.6 Connectivity Preservation Trigger Points

| Step | Connectivity Risk | Mechanism | Service |
|------|------------------|-----------|---------|
| `flash_dpu` (bf3) | Yes | Pre-staged netplan + fallback timer | `NetplanStager`, `FallbackTimer` |
| `ipmi_power_cycle` (bf3_ipmi) | Yes | Phase C bootstrap service | `PhaseCBootstrap` |
| `redfish_cold_boot` (bmc) | Yes | Phase C bootstrap service | `PhaseCBootstrap` |
| `flash_dpu` (bf3_ipmi/bmc, reflash) | Yes | Post-flash recovery watcher | `PostFlashWatcher` |

The orchestrator activates the appropriate mechanism before executing the risky step, then waits for connectivity to restore after:

```python
def _activate_connectivity_mechanism(self, host: BareMetalHost, step: DeploymentStep):
    if step.connectivity_mechanism == "pre_staged_netplan":
        NetplanStager(self._get_ssh(host)).stage_vf_netplan(host)
    elif step.connectivity_mechanism == "phase_c_bootstrap":
        PhaseCBootstrap(self._get_ssh(host)).deposit(host)
    elif step.connectivity_mechanism == "post_flash_watcher":
        PostFlashWatcher(self._get_ssh(host)).deposit(host)
```

---

## E. SSH Execution Architecture

### E.1 SSHSession Service Design

```python
# services/bare_metal/ssh_session.py

import logging
import subprocess
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class SSHResult:
    """Result of an SSH command execution."""
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float


class SSHSession:
    """
    SSH execution via subprocess — mirrors bnk-poc-deployer SSHSession.

    Uses subprocess ssh (not paramiko) to inherit:
    - ssh-agent (pre-loaded keys)
    - ProxyJump / -J for jumphost chains
    - ControlMaster for connection multiplexing

    This is a deliberate design choice: subprocess SSH inherits the
    user's SSH environment (agent, config, known_hosts) which is
    critical for jumphost scenarios.
    """

    def __init__(
        self,
        host: str,
        username: str,
        port: int = 22,
        *,
        private_key_path: str | None = None,
        password: str | None = None,
        jumphost_chain: list[dict] | None = None,
        control_path: str | None = None,
        connect_timeout: int = 15,
    ):
        self.host = host
        self.username = username
        self.port = port
        self.private_key_path = private_key_path
        self.password = password
        self.jumphost_chain = jumphost_chain or []
        self.control_path = control_path
        self.connect_timeout = connect_timeout

    def _build_ssh_args(self) -> list[str]:
        """Build the SSH command arguments."""
        args = [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", f"ConnectTimeout={self.connect_timeout}",
            "-p", str(self.port),
        ]

        if self.private_key_path:
            args.extend(["-i", self.private_key_path])

        if self.control_path:
            args.extend([
                "-o", f"ControlPath={self.control_path}",
                "-o", "ControlMaster=auto",
                "-o", "ControlPersist=300",
            ])

        if self.jumphost_chain:
            jump_spec = self._build_jump_spec()
            args.extend(["-J", jump_spec])

        args.append(f"{self.username}@{self.host}")
        return args

    def _build_jump_spec(self) -> str:
        """Build ProxyJump spec from jumphost chain."""
        # e.g., "user1@host1:22,user2@host2:22"
        parts = []
        for jh in self.jumphost_chain:
            user = jh.get("username", "root")
            host = jh["host"]
            port = jh.get("port", 22)
            parts.append(f"{user}@{host}:{port}")
        return ",".join(parts)

    def execute(self, command: str, *, timeout: int = 300) -> SSHResult:
        """Execute a command and return the result."""
        ...

    def execute_streaming(self, command: str) -> Generator[str, None, SSHResult]:
        """Execute a command and yield output lines as they arrive."""
        ...

    def upload_file(self, local_path: str, remote_path: str) -> None:
        """Upload a file via scp."""
        ...

    def upload_content(self, content: str, remote_path: str) -> None:
        """Upload string content to a remote file."""
        ...

    def wait_for_ssh(self, *, timeout: int = 600, interval: int = 10) -> bool:
        """Poll until SSH becomes available (post-reboot)."""
        ...

    def is_reachable(self) -> bool:
        """Quick connectivity check."""
        ...
```

### E.2 Jumphost Chain Support

Jumphost chain is built from `BareMetalHost.jumphost_chain` — a JSON array of SSH credential IDs:

```json
[
  {"ssh_credential_id": 1},
  {"ssh_credential_id": 2}
]
```

At runtime, the orchestrator resolves each credential and builds the chain:

```python
def _build_ssh_session(self, host: BareMetalHost) -> SSHSession:
    """Build an SSHSession for a bare-metal host, including jumphosts."""
    # Resolve host credential
    cred = self._resolve_credential(host.ssh_credential_id)
    key_path = self._write_temp_key(cred) if cred.private_key_encrypted else None

    # Resolve jumphost chain
    jump_chain = []
    for jh_entry in (host.jumphost_chain or []):
        jh_cred = self._resolve_credential(jh_entry["ssh_credential_id"])
        jump_chain.append({
            "host": jh_cred.host,
            "port": jh_cred.port,
            "username": jh_cred.username,
        })

    return SSHSession(
        host=host.host_ip,
        username=cred.username if cred else "root",
        port=host.ssh_port,
        private_key_path=key_path,
        jumphost_chain=jump_chain,
        control_path=self._pool.get_control_path(host.host_ip),
    )
```

### E.3 Connection Lifecycle Management

```python
# services/bare_metal/ssh_pool.py

class SSHConnectionPool:
    """Manages ControlMaster sockets for SSH connection multiplexing."""

    def __init__(self, base_dir: str = "/tmp/bnk-forge-ssh"):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def get_control_path(self, host: str) -> str:
        """Get the ControlMaster socket path for a host."""
        safe_host = host.replace(".", "_").replace(":", "_")
        return str(self.base_dir / f"ctrl-{safe_host}-%r@%h:%p")

    def cleanup_host(self, host: str) -> None:
        """Close ControlMaster connection for a host."""
        control_path = self.get_control_path(host)
        subprocess.run(
            ["ssh", "-O", "exit", "-o", f"ControlPath={control_path}", "dummy"],
            capture_output=True, timeout=5,
        )

    def cleanup_all(self) -> None:
        """Close all ControlMaster connections."""
        for sock in self.base_dir.glob("ctrl-*"):
            sock.unlink(missing_ok=True)
```

### E.4 Credential Storage

Credentials use Forge's existing encrypted secrets:

- **SSH keys/passwords** — stored in `SSHCredential` model (existing), encrypted via `core.encryption`
- **BMC/IPMI credentials** — stored on `BareMetalHost` model as `*_encrypted` columns
- **At runtime** — decrypted via `core.encryption.decrypt_value()`, written to temp files for SSH keys (mode 600), cleaned up after session

```python
def _write_temp_key(self, cred: SSHCredential) -> str:
    """Decrypt SSH key and write to temp file. Returns path."""
    key_content = decrypt_value(cred.private_key_encrypted)
    key_path = Path(tempfile.mktemp(prefix="forge-ssh-", suffix=".key"))
    key_path.write_text(key_content)
    key_path.chmod(0o600)
    return str(key_path)
```

### E.5 Streaming Output Pipeline

```
Remote Host                    Celery Worker                    Frontend
    │                              │                              │
    │  ← SSH subprocess ──────────│                              │
    │    (stdout line by line)     │                              │
    │                              │── append to step.output_log  │
    │                              │── db.flush() every N lines   │
    │                              │── publish_task_update() ────→│
    │                              │   (WebSocket broadcast)      │
    │                              │                              │
    │  (command completes)         │                              │
    │                              │── db.commit()                │
    │                              │── publish final status ─────→│
```

---

## F. Discovery Architecture

### F.1 Probe Organization

Each probe is a pure function that takes an `SSHSession` (or `RedfishClient`/`IPMIClient`) and returns a typed result:

```python
# services/bare_metal/discovery/host_probe.py

@dataclass
class HostProbeResult:
    ssh_reachable: bool
    os_type: str | None           # "ubuntu", "rhel", etc.
    os_version: str | None
    os_pretty_name: str | None
    kernel_version: str | None
    architecture: str | None
    nic_mode: str | None          # "supernic" or "dpu" (from mlxconfig)
    pf_interfaces: list[dict]     # [{name, link_state, speed}]
    vf_count: int
    hugepages_gb: int
    rshim_present: bool
    error: str | None


def probe_host(ssh: SSHSession) -> HostProbeResult:
    """Run SSH-based probes on a bare-metal host."""
    ...


# services/bare_metal/discovery/bmc_probe.py

@dataclass
class BmcProbeResult:
    redfish_reachable: bool
    ipmi_reachable: bool
    vendor: str | None             # "supermicro", "lenovo", "dell", "hpe"
    bios_attributes: dict | None   # Subset of BIOS attributes (NIC-related)
    nic_mode_bios: str | None      # NIC mode from BIOS (if Redfish available)
    power_state: str | None        # "On", "Off", "PoweringOn", etc.
    error: str | None


def probe_bmc(
    bmc_ip: str,
    redfish: RedfishClient | None,
    ipmi: IPMIClient | None,
) -> BmcProbeResult:
    """Run BMC/IPMI probes."""
    ...


# services/bare_metal/discovery/dpu_probe.py

@dataclass
class DpuProbeResult:
    ssh_reachable: bool
    doca_version: str | None
    containerd_version: str | None
    runc_version: str | None
    k8s_version: str | None
    ovs_installed: bool
    ovs_bridge_ports: list[str]
    vf_representors: list[str]
    hugepages_gb: int
    error: str | None


def probe_dpu(ssh: SSHSession) -> DpuProbeResult:
    """Probe DPU state via SSH (rshim or management IP)."""
    ...


# services/bare_metal/discovery/vlan_probe.py

@dataclass
class VlanProbeResult:
    vlan_path_valid: bool
    test_interface: str | None
    gateway_reachable: bool
    error: str | None


def probe_vlan(
    ssh: SSHSession,
    pf_name: str,
    vlan_id: int,
    gateway_ip: str,
    test_ip: str,
) -> VlanProbeResult:
    """Test VLAN path by creating temporary sub-interface."""
    ...
```

### F.2 Discovery Results Storage

Discovery results are stored in two places:

1. **`BareMetalHost` cache columns** (`os_info`, `dpu_info`, `k8s_info`, `last_discovery_at`) — latest snapshot for quick access
2. **`DiscoveredNode` records** (existing model) — full history, linked via a `DiscoveryJob`

The bare-metal discovery service creates a `DiscoveryJob` (reusing the existing model) and updates the host cache:

```python
class BareMetalDiscoveryService:
    """Bare-metal discovery using SSH + BMC + IPMI probes."""

    def __init__(self, db: Session):
        self.db = db

    def run_discovery(self, host_id: int, options: dict) -> BareMetalDiscoveryResponse:
        host = self._get_host(host_id)

        # Build clients
        ssh = self._build_ssh(host)
        redfish = self._build_redfish(host) if options.get("probe_bmc") else None
        ipmi = self._build_ipmi(host) if options.get("probe_ipmi") else None

        # Run probes
        host_result = probe_host(ssh)
        bmc_result = probe_bmc(host.bmc_ip, redfish, ipmi) if (redfish or ipmi) else None
        dpu_result = probe_dpu(self._build_dpu_ssh(ssh, host)) if options.get("probe_dpu") else None
        vlan_result = None
        if options.get("probe_vlan") and host.vlan_id:
            vlan_result = probe_vlan(ssh, host_result.pf_interfaces[0]["name"],
                                     host.vlan_id, host.gateway_ip, host.host_mgmt_ip)

        # Auto-detect topology
        topology = detect_topology(host_result, bmc_result)

        # Build assessment
        assessment = self._build_assessment(host_result, bmc_result, dpu_result, vlan_result)

        # Version drift
        version_drift = self._check_version_drift(dpu_result, host.version_profile) if dpu_result else None

        # Update host cache
        host.os_info = {"os_type": host_result.os_type, ...}
        host.dpu_info = [dpu_result.__dict__] if dpu_result else None
        host.topology = topology if host.topology is None else host.topology
        host.topology_auto_detected = True if host.topology is None else host.topology_auto_detected
        host.last_discovery_at = datetime.now(UTC)
        self.db.commit()

        return BareMetalDiscoveryResponse(...)
```

### F.3 Dual-Mode Usage

Discovery is both **standalone** and **deployment prerequisite**:

```python
# Standalone: API route calls discovery directly
@router.post("/{host_id}/discover")
def trigger_discovery(host_id: int, data: BareMetalDiscoveryRequest, ...):
    return BareMetalDiscoveryService(db).run_discovery(host_id, data.model_dump())

# Deployment prerequisite: orchestrator calls same service
class BareMetalDeploymentService:
    def execute_deployment(self, deployment_id: int, ...):
        ...
        if not deployment.resume_from_step:
            # Fresh deployment — run discovery first
            discovery_svc = BareMetalDiscoveryService(self.db)
            discovery_result = discovery_svc.run_discovery(
                deployment.host_id,
                {"probe_bmc": True, "probe_ipmi": True, "probe_dpu": True, "probe_vlan": True},
            )
            # Validate discovery before proceeding
            self._validate_discovery(deployment, discovery_result)
```

---

## G. Test Strategy

### G.1 Unit Tests (`tests/unit/`)

| Module | Test File | What to Test |
|--------|-----------|-------------|
| `SSHSession` | `test_ssh_session.py` | Command building, jump spec, args construction (no subprocess) |
| `SSHConnectionPool` | `test_ssh_pool.py` | Control path generation, cleanup |
| `IPMIClient` | `test_ipmi_client.py` | Command building, result parsing |
| `RedfishClient` | `test_redfish_client.py` | URL construction, auth headers, vendor dispatch |
| Vendor plugins | `test_redfish_vendors.py` | Vendor-specific BIOS attribute paths |
| `TopologyDetector` | `test_topology_detector.py` | Detection logic from probe results (pure function) |
| `BfbConfigGenerator` | `test_bfb_config.py` | Config rendering from version profile |
| Step registries | `test_step_registry.py` | Step counts, phase coverage, connectivity flags |
| Schemas | `test_schemas_bare_metal.py` | Pydantic validation, serialization |
| Enums | `test_enums_bare_metal.py` | Terminal states, valid values |
| `BareMetalDiscoveryResponse` | `test_discovery_response.py` | Assessment building, version drift detection |

**Mocking strategy:**

```python
# Mock SSH execution for unit tests
@pytest.fixture
def mock_ssh():
    session = MagicMock(spec=SSHSession)
    session.execute.return_value = SSHResult(exit_code=0, stdout="...", stderr="", duration_seconds=0.1)
    return session

# Mock Redfish HTTP for unit tests
@pytest.fixture
def mock_redfish(requests_mock):
    requests_mock.get("https://10.0.0.1/redfish/v1", json={"...": "..."})
    return RedfishClient("10.0.0.1", "admin", "password")
```

### G.2 Component Tests (`tests/component/`)

| Module | Test File | What to Test |
|--------|-----------|-------------|
| `BareMetalHostService` | `test_bare_metal_host_service.py` | CRUD with DB, credential resolution |
| `BareMetalDeploymentService` | `test_bare_metal_orchestrator.py` | Step sequencing, state transitions, resume logic |
| `BareMetalDiscoveryService` | `test_bare_metal_discovery.py` | Discovery → host cache update flow |
| `Phase1DpuService` | `test_phase1_dpu.py` | Phase 1 step execution with mocked SSH |
| `Phase2K8sService` | `test_phase2_k8s.py` | Phase 2 step execution with mocked SSH |
| `BnkVersionProfileService` | `test_version_profile_service.py` | CRUD, default profile selection |
| Connectivity services | `test_connectivity_services.py` | Script deposit, netplan rendering |
| Celery tasks | `test_bare_metal_tasks.py` | Task dispatching, error handling, status updates |

### G.3 Contract Tests (`tests/contract/`)

| Test File | What to Test |
|-----------|-------------|
| `test_bare_metal_host_contract.py` | `BareMetalHostResponse` shape matches route output |
| `test_bare_metal_deployment_contract.py` | `BareMetalDeploymentResponse` shape matches route output |
| `test_discovery_contract.py` | `BareMetalDiscoveryResponse` shape matches service output |

### G.4 E2E Tests (`tests/e2e/`)

Runnable against a real target host (configured via env vars):

```python
# tests/e2e/test_bare_metal_e2e.py

@pytest.mark.e2e
@pytest.mark.skipif(not os.getenv("BM_TEST_HOST"), reason="No bare-metal test host configured")
class TestBareMetalE2E:

    def test_ssh_connectivity(self):
        """Test SSH connection to real host."""
        ...

    def test_full_discovery(self):
        """Run full discovery against real host."""
        ...

    def test_vlan_validation(self):
        """Test VLAN probe against real host."""
        ...

    def test_dpu_version_detection(self):
        """Detect DPU software versions on real host."""
        ...
```

E2E env vars:
- `BM_TEST_HOST` — target host IP
- `BM_TEST_SSH_USER` — SSH username
- `BM_TEST_SSH_KEY` — path to SSH private key
- `BM_TEST_BMC_IP` — BMC IP (optional)
- `BM_TEST_BMC_USER` / `BM_TEST_BMC_PASS` — BMC credentials (optional)

---

## H. Implementation Phasing

### Phase 0: Foundation (Week 1)

**Goal:** Data models, enums, schemas — everything compiles, nothing executes yet.

| Slice | Files | Dependencies |
|-------|-------|-------------|
| 0a: Enums | `models/enums.py` additions | None |
| 0b: Models | `models/bare_metal.py` | 0a |
| 0c: Migration | Alembic migration for new tables | 0b |
| 0d: Schemas | `schemas/bare_metal.py` | 0a |
| 0e: Version profile seed | `services/bare_metal/version_profiles.py` + seed data | 0b, 0d |
| 0f: Unit tests | Schema validation, enum tests | 0a-0d |

**Testable independently:** Yes — schema tests, model creation, migration.

### Phase 1: SSH Execution Layer (Week 1-2)

**Goal:** `SSHSession` and `SSHConnectionPool` working and tested.

| Slice | Files | Dependencies |
|-------|-------|-------------|
| 1a: SSHSession | `services/bare_metal/ssh_session.py` | None |
| 1b: SSHConnectionPool | `services/bare_metal/ssh_pool.py` | None |
| 1c: Unit tests | `tests/unit/test_ssh_session.py` | 1a, 1b |
| 1d: Integration test | Real SSH connection test (E2E marker) | 1a |

**Testable independently:** Yes — SSH to any reachable host.

### Phase 2: Discovery (Week 2)

**Goal:** Full discovery pipeline working via API.

| Slice | Files | Dependencies |
|-------|-------|-------------|
| 2a: Host probes | `services/bare_metal/discovery/host_probe.py` | Phase 1 |
| 2b: DPU probes | `services/bare_metal/discovery/dpu_probe.py` | Phase 1 |
| 2c: BMC probes | `services/bare_metal/discovery/bmc_probe.py` + `redfish/` | None (HTTP) |
| 2d: IPMI client | `services/bare_metal/ipmi_client.py` | None (subprocess) |
| 2e: VLAN probe | `services/bare_metal/discovery/vlan_probe.py` | Phase 1 |
| 2f: Topology detector | `services/bare_metal/discovery/topology_detector.py` | None (pure) |
| 2g: Discovery service | `services/bare_metal/discovery/__init__.py` | 2a-2f |
| 2h: Host CRUD route | `routes/bare_metal_hosts.py` (CRUD + discover) | Phase 0 |
| 2i: Unit + component tests | All discovery tests | 2a-2h |

**Testable independently:** Yes — discover a real host via API without any deployment.

### Phase 3: Deployment Orchestration (Week 3)

**Goal:** Step registry, state machine, Celery task, resume.

| Slice | Files | Dependencies |
|-------|-------|-------------|
| 3a: Step registry | `services/bare_metal/orchestrator.py` (step definitions) | Phase 0 |
| 3b: Orchestrator service | `services/bare_metal/orchestrator.py` (execution loop) | 3a, Phase 1 |
| 3c: Celery task | `tasks/bare_metal_tasks.py` | 3b |
| 3d: Deployment routes | `routes/bare_metal_deployments.py` | Phase 0, 3c |
| 3e: Component tests | Orchestrator state machine, resume logic | 3a-3d |

**Testable independently:** Yes — create/list deployments, test state transitions with mocked phase services.

### Phase 4: Phase 1 (DPU Provisioning) (Week 3-4)

**Goal:** BFB flash and DPU prereq installation working.

| Slice | Files | Dependencies |
|-------|-------|-------------|
| 4a: BFB config | `services/bare_metal/provisioning/bfb_config.py` | Phase 0 |
| 4b: Connectivity services | `services/bare_metal/connectivity/` (all 4 files) | Phase 1 |
| 4c: Phase 1 service | `services/bare_metal/provisioning/phase1_dpu.py` | 4a, 4b, Phase 1 |
| 4d: Unit + component tests | BFB config rendering, connectivity script deposits | 4a-4c |

### Phase 5: Phase 2-3 (K8s Bootstrap + Join) (Week 4)

**Goal:** Host K8s cluster bootstrapped, DPU joined.

| Slice | Files | Dependencies |
|-------|-------|-------------|
| 5a: Phase 2 service | `services/bare_metal/provisioning/phase2_k8s.py` | Phase 1, Phase 0 |
| 5b: Phase 3 service | `services/bare_metal/provisioning/phase3_join.py` | Phase 1 |
| 5c: Tests | Phase 2-3 with mocked SSH | 5a, 5b |

### Phase 6: Phase 4 (Platform) + Integration (Week 5)

**Goal:** Full end-to-end deployment, linking to existing Forge modules.

| Slice | Files | Dependencies |
|-------|-------|-------------|
| 6a: Phase 4 service | `services/bare_metal/provisioning/phase4_platform.py` | Forge modules |
| 6b: K8s cluster linking | Link `BareMetalHost.kubernetes_cluster_id` after Phase 2 | Phase 5 |
| 6c: E2E tests | Full deployment against real host | All phases |
| 6d: Contract tests | Response shape verification | All routes |

### Phase 7: Polish (Week 5-6)

| Slice | Files | Dependencies |
|-------|-------|-------------|
| 7a: Version profiles route | `routes/bare_metal_version_profiles.py` | Phase 0 |
| 7b: MCP tools | `mcp-server/` additions | All API routes |
| 7c: Audit integration | Audit events for all bare-metal operations | All services |
| 7d: OpenAPI types | `make openapi-types` after all routes finalized | All routes |

---

## I. Risk Assessment

### I.1 Hardest Parts

| Risk | Impact | Mitigation |
|------|--------|-----------|
| **Connectivity preservation during BFB flash** | High — lost connectivity means bricked deployment | Port exact mechanisms from bnk-poc-deployer shell scripts. Test on real hardware early. The connectivity services (Section D.6) must be battle-tested before any destructive operation. |
| **SSH reconnection after reboot/flash** | High — all subsequent steps depend on SSH | `SSHSession.wait_for_ssh()` with configurable timeout (600s). Celery task soft_time_limit accounts for wait. |
| **Vendor-specific Redfish behavior** | Medium — each BMC vendor has quirks | Strategy pattern (VendorPlugin) isolates vendor-specific logic. Start with Supermicro (our test hardware), add others incrementally. |
| **Phase 4 overlap with Forge modules** | Medium — modules assume existing cluster with kubeconfig | Phase 4 service must register the new cluster in Forge and create a kubeconfig before invoking Forge modules. Bridge code is the tricky part. |
| **Long-running Celery tasks (40+ min)** | Medium — task timeouts, worker restarts | Set `time_limit=7200`, use intermediate commits. Task can be resumed from any step. Redis-backed result backend. |

### I.2 Potential Blockers

| Blocker | Likelihood | Workaround |
|---------|-----------|-----------|
| No `ipmitool` in Docker container | High | Add `ipmitool` to backend Docker image. Trivial — `apt-get install ipmitool`. |
| No `ssh` client in Docker container | High | Already present (needed for Forge's SSH tunnel). Verify `ssh-keygen`, `scp` also available. |
| Test hardware availability | Medium | Need at least one BF3 DPU host accessible from dev environment. Fallback: unit/component tests only, defer E2E. |
| SSH agent forwarding into Docker | Medium | Mount SSH agent socket (`SSH_AUTH_SOCK`). Already needed for Forge's existing SSH features. |
| Celery task serialization for large output_log | Low | Paginate/truncate step logs. Store logs in DB (existing DeploymentLog pattern). |

### I.3 Areas Requiring Special Attention

1. **Security:** BMC/IPMI credentials are high-value targets. Must be encrypted at rest (`*_encrypted` columns) and never logged. The `scrub_secrets()` utility from `core.errors` must cover BMC passwords.

2. **Idempotency testing:** Every step's probe function must be tested with "already done" and "not done" scenarios. False negatives (probe says not done when it is) waste time. False positives (probe says done when it isn't) skip critical steps.

3. **Timeout tuning:** BFB flash takes 15-40 minutes. SSH reconnection after reboot takes 2-5 minutes. These are highly variable. Timeouts must be generous but bounded. Configuration via version profile or host settings.

4. **Concurrent deployments:** Two deployments against the same host must be prevented. The orchestrator should check for existing active deployments before starting a new one.

5. **Cleanup on cancel:** Cancelling a deployment mid-flight must clean up:
   - ControlMaster SSH sockets
   - Temporary key files
   - Revoke Celery task
   - Set deployment status to CANCELLED

---

## Appendix: File Inventory

### New Files

| Path | Type | Phase |
|------|------|-------|
| `models/bare_metal.py` | SQLAlchemy models | 0 |
| `models/enums.py` (additions) | Enums | 0 |
| `schemas/bare_metal.py` | Pydantic schemas | 0 |
| `routes/bare_metal_hosts.py` | API routes | 2 |
| `routes/bare_metal_deployments.py` | API routes | 3 |
| `routes/bare_metal_version_profiles.py` | API routes | 7 |
| `services/bare_metal/__init__.py` | Package init | 0 |
| `services/bare_metal/ssh_session.py` | SSH execution | 1 |
| `services/bare_metal/ssh_pool.py` | SSH multiplexing | 1 |
| `services/bare_metal/ipmi_client.py` | IPMI client | 2 |
| `services/bare_metal/redfish/__init__.py` | Package init | 2 |
| `services/bare_metal/redfish/client.py` | Redfish HTTP client | 2 |
| `services/bare_metal/redfish/vendor_base.py` | Vendor plugin ABC | 2 |
| `services/bare_metal/redfish/supermicro.py` | Supermicro plugin | 2 |
| `services/bare_metal/redfish/lenovo.py` | Lenovo plugin | 2 |
| `services/bare_metal/redfish/dell.py` | Dell plugin | 2 |
| `services/bare_metal/redfish/hpe.py` | HPE plugin | 2 |
| `services/bare_metal/discovery/__init__.py` | Discovery orchestrator | 2 |
| `services/bare_metal/discovery/host_probe.py` | Host SSH probes | 2 |
| `services/bare_metal/discovery/bmc_probe.py` | BMC probes | 2 |
| `services/bare_metal/discovery/dpu_probe.py` | DPU probes | 2 |
| `services/bare_metal/discovery/vlan_probe.py` | VLAN validation | 2 |
| `services/bare_metal/discovery/topology_detector.py` | Topology auto-detect | 2 |
| `services/bare_metal/provisioning/__init__.py` | Package init | 4 |
| `services/bare_metal/provisioning/bfb_config.py` | BFB config rendering | 4 |
| `services/bare_metal/provisioning/phase1_dpu.py` | Phase 1 service | 4 |
| `services/bare_metal/provisioning/phase2_k8s.py` | Phase 2 service | 5 |
| `services/bare_metal/provisioning/phase3_join.py` | Phase 3 service | 5 |
| `services/bare_metal/provisioning/phase4_platform.py` | Phase 4 bridge | 6 |
| `services/bare_metal/connectivity/__init__.py` | Package init | 4 |
| `services/bare_metal/connectivity/netplan_stager.py` | Netplan pre-staging | 4 |
| `services/bare_metal/connectivity/fallback_timer.py` | Fallback timer | 4 |
| `services/bare_metal/connectivity/phase_c_bootstrap.py` | Phase C deposit | 4 |
| `services/bare_metal/connectivity/post_flash_watcher.py` | Post-flash watcher | 4 |
| `services/bare_metal/orchestrator.py` | Deployment orchestrator | 3 |
| `services/bare_metal/version_profiles.py` | Version profile CRUD | 0 |
| `tasks/bare_metal_tasks.py` | Celery tasks | 3 |
| `tests/unit/test_ssh_session.py` | Unit tests | 1 |
| `tests/unit/test_ssh_pool.py` | Unit tests | 1 |
| `tests/unit/test_ipmi_client.py` | Unit tests | 2 |
| `tests/unit/test_redfish_client.py` | Unit tests | 2 |
| `tests/unit/test_topology_detector.py` | Unit tests | 2 |
| `tests/unit/test_bfb_config.py` | Unit tests | 4 |
| `tests/unit/test_step_registry.py` | Unit tests | 3 |
| `tests/unit/test_schemas_bare_metal.py` | Unit tests | 0 |
| `tests/component/test_bare_metal_host_service.py` | Component tests | 2 |
| `tests/component/test_bare_metal_orchestrator.py` | Component tests | 3 |
| `tests/component/test_bare_metal_discovery.py` | Component tests | 2 |
| `tests/component/test_bare_metal_tasks.py` | Component tests | 3 |
| `tests/component/test_version_profile_service.py` | Component tests | 0 |
| `tests/contract/test_bare_metal_host_contract.py` | Contract tests | 6 |
| `tests/contract/test_bare_metal_deployment_contract.py` | Contract tests | 6 |
| `tests/e2e/test_bare_metal_e2e.py` | E2E tests | 6 |

### Modified Files

| Path | Change | Phase |
|------|--------|-------|
| `models/__init__.py` | Import new models | 0 |
| `models/enums.py` | Add new enums | 0 |
| `routes/api.py` | Register new routers | 2-3 |
| `Dockerfile` (backend) | Add `ipmitool` package | 1 |

### Total: ~50 new files, ~3 modified files

---

## Appendix: Step Definitions Detail

### Regular Topology — 19 steps

```
Phase 1 (DPU Provisioning):
  0: probe_dpu_state
  1: flash_dpu
  2: wait_dpu_ready
  3: install_dpu_prereqs

Phase 2 (Host K8s):
  4: install_k8s_prereqs
  5: kubeadm_init
  6: install_cni
  7: install_cert_manager
  8: install_sriov
  9: install_multus
  10: install_storage

Phase 3 (DPU Join):
  11: kubeadm_join
  12: label_dpu_node
  13: taint_dpu_node

Phase 4 (Platform):
  14: install_flo
  15: deploy_bnk_cr
  16: install_cwc_certs
  17: install_otel_certs
  18: deploy_observability
```

### BF3 Topology — 22 steps (3 extra connectivity steps)

```
Phase 1 (DPU Provisioning — with connectivity preservation):
  0: probe_dpu_state
  1: stage_vf_netplan                          <- NEW
  2: install_fallback_timer                     <- NEW
  3: flash_dpu [CONNECTIVITY RISK: pre_staged_netplan]
  4: wait_connectivity_restore                  <- NEW
  5: install_dpu_prereqs

Phase 2-4: same as regular (steps 6-20)
```

### BF3-IPMI Topology — 24 steps (5 extra steps)

```
Phase 1 (DPU Provisioning — mlxconfig + IPMI):
  0: probe_dpu_state
  1: set_nic_mode_mlxconfig                    <- NEW
  2: deposit_phase_c                           <- NEW
  3: ipmi_power_cycle [CONNECTIVITY RISK: phase_c_bootstrap] <- NEW
  4: wait_connectivity_restore                  <- NEW
  5: flash_dpu [CONNECTIVITY RISK: post_flash_watcher]
  6: wait_dpu_ready                            <- NEW
  7: install_dpu_prereqs

Phase 2-4: same as regular (steps 8-22)
```

### BMC Topology — 24 steps (same count as bf3-ipmi, different mode change)

```
Phase 1 (DPU Provisioning — Redfish BIOS + Redfish cold boot):
  0: probe_dpu_state
  1: set_nic_mode_redfish                      <- Redfish instead of mlxconfig
  2: deposit_phase_c
  3: redfish_cold_boot [CONNECTIVITY RISK: phase_c_bootstrap] <- Redfish instead of IPMI
  4: wait_connectivity_restore
  5: flash_dpu [CONNECTIVITY RISK: post_flash_watcher]
  6: wait_dpu_ready
  7: install_dpu_prereqs

Phase 2-4: same as regular (steps 8-22)
```

---

*End of specification. This document is the source of truth for the DPU bare-metal deployment feature. All implementation work should reference this spec and any deviations must be documented.*
