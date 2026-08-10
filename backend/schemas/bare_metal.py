"""Pydantic schemas for bare-metal DPU deployment API."""

from datetime import datetime
from typing import Self

from pydantic import BaseModel, Field, model_validator

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
    dpu_credential_id: int | None = None
    version_profile_id: int | None = None
    deploy_dpu_pci_address: str | None = None
    rshim_source: str | None = None   # "host" | "bmc"
    bond_mode: str | None = None      # "independent" | "lag"
    net_rshim_mac_base: str | None = None  # host-level tmfifo MAC base override


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
    dpu_credential_id: int | None = None
    version_profile_id: int | None = None
    deploy_dpu_pci_address: str | None = None
    rshim_source: str | None = None
    bond_mode: str | None = None
    net_rshim_mac_base: str | None = None  # host-level tmfifo MAC base override


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
    uses_project_jumphost: bool = Field(
        default=False,
        description="True if host inherits jumphost from project (no host-level jumphost_chain)"
    )
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
    dpu_credential_id: int | None
    version_profile_id: int | None
    os_info: dict | None
    dpu_info: list[dict] | None
    k8s_info: dict | None
    last_discovery_at: datetime | None
    last_discovery_status: str | None = None
    last_discovery_error: str | None = None
    # NEW — promoted discovery facts:
    nic_mode: str | None = None
    mst_device: str | None = None
    rshim_present: bool | None = None
    default_route_iface: str | None = None
    phase_c_deposited: bool | None = None
    phase_c_completed: bool | None = None
    pf_interfaces: list[dict] | None = None
    vf_count: int | None = None
    hugepages_host_gb: int | None = None
    deploy_dpu_pci_address: str | None = None
    deploy_dpu_index: int | None = None
    rshim_source: str | None = None
    bond_mode: str | None = None
    net_rshim_mac_base: str | None = None  # host-level tmfifo MAC base override
    has_discovery_result: bool = False  # Flag, NOT the full blob
    kubernetes_cluster_id: int | None
    created_at: datetime
    updated_at: datetime


class BareMetalHostListResponse(BaseModel):
    hosts: list[BareMetalHostResponse]


# --- Deployment Schemas ---

class BareMetalDeploymentCreate(BaseModel):
    host_id: int | None = None
    control_plane_host_id: int | None = None
    # Widens the active-deployment conflict check in the orchestrator only.
    # Does NOT trigger worker deployments — multi-host orchestration lands in Phase 2.
    worker_host_ids: list[int] | None = None
    resume_from_step: int | None = None  # Optional: resume from this step index
    skip_discovery: bool = False          # Skip pre-flight discovery
    selected_phases: list[str] | None = None   # None = all phases
    selected_steps: list[str] | None = None    # None = all steps in selected phases
    deployable_release_id: int | None = None   # None = catalog default (is_default=True)

    @model_validator(mode="after")
    def _validate_host_ids(self) -> Self:
        if self.host_id is None and self.control_plane_host_id is None:
            raise ValueError("Either host_id or control_plane_host_id must be provided")
        if self.host_id is None and self.control_plane_host_id is not None:
            self.host_id = self.control_plane_host_id
        return self


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
    selected_phases: list[str] | None = None
    selected_steps: list[str] | None = None
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


class DeploymentStepPlanItem(BaseModel):
    """A step in the proposed deployment plan (pre-creation preview)."""
    step_index: int
    phase: str
    name: str
    description: str
    connectivity_risk: bool
    estimated_duration_seconds: int
    prerequisites: list[str]
    idempotent: bool
    selected: bool  # Whether this step is included in the selection


class DeploymentPlanPreview(BaseModel):
    """Preview of the deployment plan before creation."""
    topology: str
    total_steps: int
    selected_steps: int
    estimated_total_duration_seconds: int
    steps: list[DeploymentStepPlanItem]
    prerequisite_warnings: list[str]  # Unmet prerequisites for selected phases


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
    actionable_assessment: list[dict] | None = None  # [{topic, severity, title, body, commands}]
    discovered_at: datetime


# --- Deployable Release Schemas ---

class DeployableReleaseResponse(BaseModel):
    id: int
    name: str
    display_name: str
    description: str | None
    is_default: bool
    is_active: bool
    source_type: str
    bnk_release_id: int | None
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
    # ADR-494 provenance fields — optional so existing callers need no changes
    source_id: int | None = None
    last_synced: datetime | None = None
    created_at: datetime


class DeployableReleaseListResponse(BaseModel):
    releases: list[DeployableReleaseResponse]

