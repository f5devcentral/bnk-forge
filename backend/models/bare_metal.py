"""Bare-metal host and DPU deployment models."""

from sqlalchemy import (
    JSON,
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
from sqlalchemy.orm import backref, relationship
from sqlalchemy.sql import func

from database import Base
from models.enums import (
    BareMetalDeploymentStatus,
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

    # DPU SSH access (rshim / management IP)
    dpu_credential_id = Column(Integer, ForeignKey("ssh_credentials.id", ondelete="SET NULL"), nullable=True)

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

    # Target BNK release (pre-fill anchor; column name kept stable per ADR-478 Option A)
    version_profile_id = Column(Integer, ForeignKey("bnk_deployable_release.id", ondelete="SET NULL"), nullable=True)

    # Hardware discovery cache (updated by discovery)
    os_info = Column(JSON, nullable=True)        # {os_type, os_version, kernel, arch}
    dpu_info = Column(JSON, nullable=True)        # [{index, nic_mode, pf_name, vf_count, sw_versions}]
    k8s_info = Column(JSON, nullable=True)        # {installed, running, version, node_count}
    last_discovery_at = Column(DateTime(timezone=True), nullable=True)
    last_discovery_status = Column(String(32), nullable=True)   # pending/ssh_probe/bmc_probe/dpu_probe/assessment/completed/failed
    last_discovery_error = Column(Text, nullable=True)          # Stage detail while running, error message when failed

    # Hardware discovery facts (promoted from JSON for typed access by step executors)
    nic_mode = Column(String(50), nullable=True)              # "SEPARATED_HOST(0)" (SuperNIC) or "EMBEDDED_CPU(1)" (DPU)
    mst_device = Column(String(255), nullable=True)           # "/dev/mst/mt41692_pciconf0"
    rshim_present = Column(Boolean, nullable=True)            # DPU detected via rshim
    default_route_iface = Column(String(100), nullable=True)  # e.g. "ens4f1" — for connectivity preservation
    phase_c_deposited = Column(Boolean, nullable=True)
    phase_c_completed = Column(Boolean, nullable=True)
    pf_interfaces = Column(JSON, nullable=True)               # [{name, link_state, speed_mbps, driver}]
    vf_count = Column(Integer, nullable=True)
    hugepages_host_gb = Column(Integer, nullable=True)

    # DPU selection (dual_dpu_obmc topology — DD-4, DD-5)
    deploy_dpu_pci_address = Column(String(50), nullable=True)   # PCI address of selected deployment DPU
    deploy_dpu_index = Column(Integer, nullable=True)             # 0-based index into dpu_info
    rshim_source = Column(String(20), nullable=True)              # "host" | "bmc"
    bond_mode = Column(String(20), nullable=True)                 # "independent" | "lag"

    # Host-level tmfifo MAC base override (ADR-478 / BM2-005).
    # When set, all DPUs flashed on this host enumerate their NET_RSHIM_MAC
    # from this base (e.g. "00:1a:ca:ff:ff:2") instead of the default
    # "00:1a:ca:ff:ff:1".  Unset (None) = use the default base.
    net_rshim_mac_base = Column(String(50), nullable=True)

    # Full discovery result cache (for UI re-display without re-probing)
    last_discovery_result = Column(JSON, nullable=True)       # Complete BareMetalDiscoveryResponse dict

    # Resulting K8s cluster (linked after Phase 2 completes)
    kubernetes_cluster_id = Column(
        Integer, ForeignKey("kubernetes_clusters.id", ondelete="SET NULL"), nullable=True
    )
    is_control_plane = Column(Boolean, default=False, nullable=False, server_default="false")

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_by = Column(String(255), nullable=True)

    # Relationships.
    # passive_deletes=True defers to the DB's ON DELETE CASCADE — without
    # it, SQLAlchemy iterates the backref collection on project delete
    # and emits `UPDATE bare_metal_hosts SET project_id=NULL`, which the
    # NOT NULL constraint on `project_id` rejects with an IntegrityError.
    # Same pattern is already used for `Project.dpu_settings` /
    # `Project.dpus` in models/dpu.py.
    project = relationship(
        "Project",
        backref=backref(
            "bare_metal_hosts",
            cascade="all, delete-orphan",
            passive_deletes=True,
        ),
    )
    ssh_credential = relationship("SSHCredential", foreign_keys=[ssh_credential_id])
    dpu_credential = relationship("SSHCredential", foreign_keys=[dpu_credential_id])
    version_profile = relationship("BnkDeployableRelease", foreign_keys=[version_profile_id])
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

    # Deployable release selected for this deployment (nullable — legacy rows pre-ADR-478 have NULL)
    deployable_release_id = Column(
        Integer, ForeignKey("bnk_deployable_release.id", ondelete="SET NULL"), nullable=True
    )

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

    # Phase/step selection — what the user requested (audit trail)
    selected_phases = Column(JSON, nullable=True)  # ["phase_1_dpu"] or None for all
    selected_steps = Column(JSON, nullable=True)   # ["flash_dpu", "wait_dpu_ready"] or None

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
    deployable_release = relationship("BnkDeployableRelease", foreign_keys=[deployable_release_id])
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


