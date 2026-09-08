"""DPU Provisioning models — BluefieldSoftwareImage, Dpu, ProjectDpuSettings.

See docs/TODO.md (DPU Provisioning, Blueprint 1) for scope. These tables
support the DPU Provisioning blueprint: storing the set of admin-approved
DOCA releases (paired BFB image + host DOCA package), per-project DPU
defaults (BFB choice, credentials, DNS, high-speed networking), and the
per-DPU inventory + user-edited VLAN plan.
"""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import backref, relationship
from sqlalchemy.sql import func

from database import Base


class BluefieldSoftwareImage(Base):
    """Admin-managed catalog of DOCA releases — paired BFB image + host DOCA package."""

    __tablename__ = "bluefield_software_images"

    id = Column(Integer, primary_key=True, index=True)
    # BFB image (DPU side)
    image_filename = Column(String(255), nullable=True)
    base_url = Column(String(512), nullable=True)
    # DOCA package (host side) — nullable for backward compat with existing rows
    doca_version = Column(String(64), nullable=True)  # e.g., "2.9.2"
    doca_package = Column(String(255), nullable=True, default="doca-all")
    # Resolved doca-host download URL — one row per OS/arch/version combo
    host_os = Column(String(32), nullable=True)  # e.g., "ubuntu", "rhel"
    host_os_version = Column(String(32), nullable=True)  # e.g., "22.04", "9.4"
    host_arch = Column(String(32), nullable=True)  # e.g., "arm64", "amd64"
    doca_host_url = Column(String(512), nullable=True)
    # Shared metadata
    is_default = Column(Boolean, default=False, nullable=False, server_default="false")
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("doca_version", "host_os", "host_os_version", "host_arch", name="uq_doca_release"),
    )


class BfConfTemplate(Base):
    """Admin-managed catalog of bf.conf Jinja2 templates.

    Two rows are seeded at install: one for the LAG/LACP path and one
    for the non-LAG path where VLANs are attached directly to p0/p1.
    Operators pick a template per project; the DPU Flash action renders
    it with per-DPU context (hostname, VLAN uplinks, password hash, …).
    """

    __tablename__ = "bf_conf_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    content = Column(Text, nullable=False)
    # Reserved for later — ties a template to a specific BNK version.
    matching_bnk_version = Column(String(64), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ProjectDpuSettings(Base):
    """Per-project DPU defaults applied to every DPU in that project."""

    __tablename__ = "project_dpu_settings"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )

    # Chosen BFB image from the admin catalog
    bluefield_image_id = Column(
        Integer, ForeignKey("bluefield_software_images.id", ondelete="SET NULL"), nullable=True
    )

    # Chosen bf.conf template (admin catalog). Null until the operator picks one.
    bf_template_id = Column(
        Integer, ForeignKey("bf_conf_templates.id", ondelete="SET NULL"), nullable=True
    )

    # High-speed interface config (applies to ARM-side eth0/eth1)
    high_speed_mtu = Column(Integer, nullable=True)

    # DNS servers (JSON array of IPs) — defaulted in schema layer to ["1.1.1.1", "8.8.8.8"]
    dns_servers = Column(JSON, nullable=True)
    # NTP servers (JSON array of hostnames/IPs) — defaulted to ["ntp.ubuntu.com"],
    # which matches systemd-timesyncd's OOTB config on Ubuntu/BFB images.
    ntp_servers = Column(JSON, nullable=True)

    # Default credentials (used when per-DPU creds are not set)
    default_oob_username = Column(String(255), nullable=True)
    default_oob_password_encrypted = Column(Text, nullable=True)
    default_os_username = Column(String(255), nullable=True)
    default_os_password_encrypted = Column(Text, nullable=True)

    # Project-level DPU-OS SSH credential — cross-project, reuses the shared
    # ssh_credentials catalog. The Flash engine injects this cred's public
    # key into the rendered bf.cfg so freshly-flashed DPUs trust Forge
    # immediately.
    default_os_ssh_credential_id = Column(
        Integer, ForeignKey("ssh_credentials.id", ondelete="SET NULL"), nullable=True
    )

    # Per-project VLAN defaults (applied to every DPU). VLAN id is project-wide;
    # subnet is used to auto-assign per-DPU IPs at DPU-create time.
    # External and internal are fixed rows (every BNK deployment needs both).
    # Anything else — storage, vdi, control-plane, … — goes in
    # `extra_vlan_interfaces` as a user-editable list.
    external_vlan_id = Column(Integer, nullable=True)
    external_vlan_ipv4_subnet = Column(String(64), nullable=True)
    external_vlan_ipv6_subnet = Column(String(128), nullable=True)
    external_vlan_start_host = Column(Integer, nullable=True)
    # "bond0" (with LAG template) or "p0" / "p1" (non-LAG).
    external_vlan_uplink = Column(String(16), nullable=True)
    internal_vlan_id = Column(Integer, nullable=True)
    internal_vlan_ipv4_subnet = Column(String(64), nullable=True)
    internal_vlan_ipv6_subnet = Column(String(128), nullable=True)
    internal_vlan_start_host = Column(Integer, nullable=True)
    internal_vlan_uplink = Column(String(16), nullable=True)

    # Extras — list of {name, vlan_id, ipv4_subnet, ipv6_subnet, start_host, uplink}.
    # Empty list means "just external + internal, nothing else."
    extra_vlan_interfaces = Column(JSON, nullable=False, default=list)

    # Latest DPU↔DPU connectivity matrix output (Phase 3). Shape mirrors
    # the discovery matrix: `{groups: {dpu: {endpoints: [...], results: [...]}}}`.
    # Updated by `tasks.dpu.run_dpu_connectivity_matrix`. Null until the
    # operator first runs the matrix.
    dpu_connectivity_matrix = Column(JSON, nullable=True)
    # Vocab: pending / in_progress / completed / failed. Used by the UI
    # to show a spinner while the task runs.
    dpu_connectivity_status = Column(String(32), nullable=True)
    dpu_connectivity_run_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    # passive_deletes=True defers to the DB's ON DELETE CASCADE — without it,
    # SQLAlchemy tries to UPDATE project_id=NULL on project delete and hits
    # the NOT NULL constraint.
    project = relationship(
        "Project",
        backref=backref("dpu_settings", cascade="all, delete-orphan", passive_deletes=True),
    )
    bluefield_image = relationship("BluefieldSoftwareImage")
    bf_template = relationship("BfConfTemplate")
    default_os_ssh_credential = relationship("SSHCredential")


class Dpu(Base):
    """A registered BlueField DPU managed by Forge for a specific project."""

    __tablename__ = "dpus"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)

    # Identity (user-provided or discovered)
    name = Column(String(255), nullable=True)

    # How this DPU is reached: "bmc" (via its own BMC/Redfish) or "in-band"
    # (via SSH to the host it's plugged into + rshim). BMC access fields
    # apply only to access_mode="bmc"; host_* fields apply only to "in-band".
    access_mode = Column(String(16), nullable=False, default="bmc")

    # BMC access — falls back to project-level defaults if null.
    # Nullable because in-band DPUs have no BMC IP at all.
    bmc_ip = Column(String(255), nullable=True)
    bmc_username_encrypted = Column(Text, nullable=True)
    bmc_password_encrypted = Column(Text, nullable=True)

    # In-band access — SSH to this host and talk to the DPU via rshim.
    host_node_ip = Column(String(255), nullable=True)
    # Host's own hostname (as reported by SSH discovery). Used for
    # display + as the DPU bf.conf hostname prefix ("{host}-dpu").
    host_hostname = Column(String(255), nullable=True)
    # Saved SSHCredential FK (optional) — used when the operator wants
    # to share one credential across many DPUs. The inline host_ssh_*
    # columns below take precedence when set; this FK is the fallback.
    host_ssh_credential_id = Column(
        Integer,
        ForeignKey("ssh_credentials.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Inline host-SSH credentials. Populated automatically when a DPU is
    # registered from a discovery job (we copy whatever worked), and
    # editable via the Add/Edit DPU dialog.
    host_ssh_username = Column(String(255), nullable=True)
    host_ssh_port = Column(Integer, nullable=True)
    host_ssh_auth_type = Column(String(16), nullable=True)  # "password" | "key"
    host_ssh_password_encrypted = Column(Text, nullable=True)
    host_ssh_private_key_encrypted = Column(Text, nullable=True)
    host_ssh_key_passphrase_encrypted = Column(Text, nullable=True)
    # PCI address of this DPU on the host (e.g. "0000:01:00", domain-prefixed
    # so DPUs that share a short BDF but differ only by domain remain
    # distinguishable). One host may carry several DPUs — (host_node_ip,
    # pci_address) identifies a unique in-band DPU within a project.
    pci_address = Column(String(32), nullable=True)

    # Which /dev/rshimN device on the host belongs to this DPU. The rshim
    # daemon assigns indexes in detection order, so two DPUs on one host land
    # on rshim0 and rshim1 (sometimes more). Populated during probe/discovery
    # by matching `/dev/rshim*/misc` `DEV_NAME` against `pci_address`. Stored
    # without the `/dev/` prefix, e.g. "rshim0", "rshim1". Null until probed —
    # callers fall back to "rshim0" so single-DPU hosts keep working.
    rshim_device = Column(String(16), nullable=True)

    # Discovered identity (populated by Phase 4 discovery)
    serial_number = Column(String(255), nullable=True)
    oob0_mac = Column(String(32), nullable=True)

    # User-editable oob0 IPv4 config. Literal "dhcp" means DHCP; otherwise CIDR notation.
    oob0_ipv4 = Column(String(64), nullable=False, default="dhcp")

    # VLAN plan (user-editable) — consumed by k8s/BNK blueprints later.
    # External and internal remain fixed columns; additional VLANs live in
    # `extra_vlan_interfaces` and mirror the project-level list by name.
    external_vlan_id = Column(Integer, nullable=True)
    external_vlan_ipv4 = Column(String(64), nullable=True)
    external_vlan_ipv6 = Column(String(128), nullable=True)
    internal_vlan_id = Column(Integer, nullable=True)
    internal_vlan_ipv4 = Column(String(64), nullable=True)
    internal_vlan_ipv6 = Column(String(128), nullable=True)

    # Per-DPU extras — list of {name, vlan_id, ipv4, ipv6}. Each entry's
    # `name` must match an entry in the project's extra_vlan_interfaces.
    extra_vlan_interfaces = Column(JSON, nullable=False, default=list)

    # Discovery outcome (Phase 4)
    last_discovery_at = Column(DateTime(timezone=True), nullable=True)
    last_discovery_status = Column(String(50), nullable=True)  # "ok" | "failed" | "running"
    last_discovery_error = Column(Text, nullable=True)
    last_discovery_payload = Column(JSON, nullable=True)  # Serialized DpuInventory for forensic view

    # DPU OS reachability (Phase 5 BMC-side ARP + SSH probe). "DPU OS"
    # matches Nvidia's terminology (bf-bundle file naming, DOCA docs) for
    # the main-core Linux, distinct from "DPU BMC" (OpenBMC on a secondary
    # ARMv7 core).
    dpu_os_ip = Column(String(255), nullable=True)
    dpu_os_reachable = Column(Boolean, nullable=True)
    # Fine-grained state for the UI to distinguish in-flight from settled:
    # "probing" | "reachable" | "unreachable" | "error" | NULL (never probed)
    dpu_os_status = Column(String(32), nullable=True)

    # Phase 4: "Flash BFB, Firmware & config" lifecycle state. Null means
    # the DPU has never been flashed by Forge.
    # One of: "uploading" | "waiting_reboot" | "os_online" | "failed" | "cancelled"
    flash_status = Column(String(32), nullable=True)
    flash_stage_detail = Column(String(255), nullable=True)
    flash_started_at = Column(DateTime(timezone=True), nullable=True)
    flash_completed_at = Column(DateTime(timezone=True), nullable=True)
    flash_error = Column(Text, nullable=True)
    flash_celery_task_id = Column(String(64), nullable=True)

    # The bf.conf that was rendered for the most recent flash. Persisted so
    # operators can inspect / download what actually went to the DPU when
    # something in the post-flash OS doesn't look right.
    last_rendered_bf_conf = Column(Text, nullable=True)
    last_rendered_bf_conf_at = Column(DateTime(timezone=True), nullable=True)

    # rshim readiness on the reachable side (in-band only today). Populated
    # by Discover + by the /rshim-status route so the UI shows per-row
    # availability without a secondary fetch. One of:
    #   "ready" | "inactive" | "unreachable" | NULL (never checked)
    # `rshim_state_detail` is the human-readable diagnostic (probe message
    # on ready/inactive, exception text on unreachable).
    rshim_state = Column(String(32), nullable=True)
    rshim_state_detail = Column(Text, nullable=True)
    rshim_checked_at = Column(DateTime(timezone=True), nullable=True)
    # DOCA host package readiness on the host side (same shape as bare-metal probe).
    # Populated by the rshim probe so the DPU tab surfaces DOCA status alongside rshim.
    doca_status = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Kubernetes Cluster membership & persisted tmfifo link allocation (ADR-424)
    kubernetes_cluster_id = Column(
        Integer, ForeignKey("kubernetes_clusters.id", ondelete="SET NULL"), nullable=True
    )
    host_tmfifo_ip = Column(String(64), nullable=True)
    dpu_tmfifo_ip = Column(String(64), nullable=True)

    # Same cascade pattern as ProjectDpuSettings — defer to DB ON DELETE CASCADE.
    project = relationship(
        "Project",
        backref=backref("dpus", cascade="all, delete-orphan", passive_deletes=True),
    )
    kubernetes_cluster = relationship("KubernetesCluster", foreign_keys=[kubernetes_cluster_id])

    __table_args__ = (
        # A given BMC IP may only be registered once per project, but only
        # enforce uniqueness when bmc_ip is actually set (in-band rows leave
        # it NULL and would otherwise collide with each other).
        Index(
            "idx_dpu_project_bmc_ip",
            "project_id", "bmc_ip",
            unique=True,
            postgresql_where=text("bmc_ip IS NOT NULL"),
        ),
        # In-band DPUs are keyed by (host, pci address) per project.
        Index(
            "idx_dpu_project_inband",
            "project_id", "host_node_ip", "pci_address",
            unique=True,
            postgresql_where=text("access_mode = 'in-band'"),
        ),
        # ADR-424: uniqueness backstop for concurrent IPAM allocation.
        # Ensures (cluster, dpu_tmfifo_ip) is unique when a DPU IP is allocated.
        # Concurrent races in allocate_next_subnet become IntegrityError instead
        # of silent duplicate IP allocation (D-001 bug class).
        Index(
            "ix_dpus_cluster_tmfifo_ip",
            "kubernetes_cluster_id", "dpu_tmfifo_ip",
            unique=True,
            postgresql_where=text("dpu_tmfifo_ip IS NOT NULL"),
        ),
        # Plain index for membership queries, before_delete UPDATE, and reconcile
        # (all of which filter WHERE kubernetes_cluster_id = X over NULL-tmfifo rows
        # that the partial unique index above does not cover).
        Index("ix_dpus_kubernetes_cluster_id", "kubernetes_cluster_id"),
    )
