"""Persistence models for project-scoped bare-metal discovery contracts."""

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from database import Base
from models.enums import DiscoveredNodeStatus, DiscoveryJobStatus


class DiscoveryJob(Base):
    """A project-scoped discovery run over one or more target nodes."""

    __tablename__ = "discovery_jobs"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)

    # Optional shared SSH credential reference
    ssh_credential_id = Column(Integer, ForeignKey("ssh_credentials.id", ondelete="SET NULL"), nullable=True)

    # Optional inline shared SSH material (encrypted-at-rest for secret fields)
    ssh_username = Column(String(255), nullable=True)
    ssh_port = Column(Integer, default=22)
    ssh_auth_type = Column(String(50), nullable=True)
    ssh_password_encrypted = Column(Text, nullable=True)
    ssh_key_encrypted = Column(Text, nullable=True)

    status = Column(String(50), default=DiscoveryJobStatus.PENDING.value, nullable=False, index=True)
    total_nodes = Column(Integer, default=0)
    completed_nodes = Column(Integer, default=0)
    failed_nodes = Column(Integer, default=0)

    celery_task_id = Column(String(255), nullable=True, index=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    created_by = Column(String(255), nullable=True)

    # Post-discovery inter-node connectivity sweep, populated after all nodes
    # finish discovery. Status: skipped | pending | in_progress | completed | failed.
    # Matrix shape: { groups: { builtin: {endpoints, results}, bluefield: {...} } }.
    connectivity_status = Column(String(50), nullable=True)
    connectivity_matrix = Column(JSON, nullable=True)

    project = relationship("Project", back_populates="discovery_jobs")
    ssh_credential = relationship("SSHCredential", foreign_keys=[ssh_credential_id])
    nodes = relationship(
        "DiscoveredNode",
        back_populates="discovery_job",
        cascade="all, delete-orphan",
        order_by="DiscoveredNode.ip_address",
    )

    __table_args__ = (
        Index("idx_discovery_job_project_status", "project_id", "status"),
    )


class DiscoveredNode(Base):
    """Per-node discovery results attached to a discovery job."""

    __tablename__ = "discovered_nodes"

    id = Column(Integer, primary_key=True, index=True)
    discovery_job_id = Column(Integer, ForeignKey("discovery_jobs.id", ondelete="CASCADE"), nullable=False, index=True)

    ip_address = Column(String(255), nullable=False)
    hostname = Column(String(255), nullable=True)

    # Optional per-node SSH override
    ssh_credential_id = Column(Integer, ForeignKey("ssh_credentials.id", ondelete="SET NULL"), nullable=True)
    ssh_username = Column(String(255), nullable=True)
    ssh_port = Column(Integer, nullable=True)
    ssh_auth_type = Column(String(50), nullable=True)
    ssh_password_encrypted = Column(Text, nullable=True)
    ssh_key_encrypted = Column(Text, nullable=True)

    status = Column(String(50), default=DiscoveredNodeStatus.PENDING.value, nullable=False, index=True)
    error_message = Column(Text, nullable=True)

    # Discovery result shape
    os_type = Column(String(100), nullable=True)
    os_version = Column(String(100), nullable=True)
    os_pretty_name = Column(String(255), nullable=True)
    kernel_version = Column(String(100), nullable=True)
    architecture = Column(String(50), nullable=True)

    is_dpu_host = Column(Boolean, default=False)
    is_dpu_node = Column(Boolean, default=False)
    dpu_count = Column(Integer, default=0)
    dpu_details = Column(JSON, nullable=True)

    # BlueField BMC detection — set by Redfish service-root probe during discovery
    # (see services.discovery_service._probe_bluefield_bmc). When True, the
    # discovered IP is the DPU's embedded OpenBMC, and the node can be
    # registered as a DPU with bmc_ip = ip_address.
    is_dpu_bmc = Column(Boolean, default=False)
    bmc_product = Column(String(255), nullable=True)
    bmc_serial_number = Column(String(255), nullable=True)
    # Richer Redfish capture (Phase 2). Populated when the BMC probe
    # has working auth — Manufacturer, Model, BIOS version, BMC FW
    # version, NicMode, HostPrivilegeLevel, Power state, MemoryGB.
    # JSON keyed by Redfish-aligned names so the UI can render anything
    # that happens to be present without us churning the column set.
    # Empty / missing on BMCs whose auth ladder didn't authenticate.
    bmc_redfish_payload = Column(JSON, nullable=True)

    k8s_installed = Column(Boolean, default=False)
    k8s_running = Column(Boolean, default=False)
    k8s_version = Column(String(100), nullable=True)
    k8s_distribution = Column(String(100), nullable=True)
    k8s_role = Column(String(50), nullable=True)
    container_runtime = Column(String(100), nullable=True)

    network_interfaces = Column(JSON, nullable=True)
    discovery_log = Column(Text, nullable=True)

    discovered_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    discovery_job = relationship("DiscoveryJob", back_populates="nodes")
    ssh_credential = relationship("SSHCredential", foreign_keys=[ssh_credential_id])

    __table_args__ = (
        Index("idx_discovered_node_job_ip", "discovery_job_id", "ip_address", unique=True),
    )
