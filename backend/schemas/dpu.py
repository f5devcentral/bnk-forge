"""Pydantic schemas for DPU Provisioning (Blueprint 1).

Credential fields are plaintext on write models only. Response models never
return passwords. Presence of a credential is surfaced via boolean flags
(e.g. `has_bmc_password`).
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ─── Bluefield Software Images (admin) ──────────────────────────────────────

class BluefieldSoftwareImageCreate(BaseModel):
    image_filename: str | None = None
    base_url: str | None = None
    doca_version: str
    doca_package: str | None = "doca-all"
    host_os: str
    host_os_version: str | None = None
    host_arch: str
    doca_host_url: str | None = None
    is_default: bool = False
    notes: str | None = None


class BluefieldSoftwareImageUpdate(BaseModel):
    image_filename: str | None = None
    base_url: str | None = None
    doca_version: str | None = None
    doca_package: str | None = None
    host_os: str | None = None
    host_os_version: str | None = None
    host_arch: str | None = None
    doca_host_url: str | None = None
    is_default: bool | None = None
    notes: str | None = None


class BluefieldSoftwareImageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    image_filename: str | None
    base_url: str | None
    doca_version: str | None
    doca_package: str | None
    host_os: str | None
    host_os_version: str | None
    host_arch: str | None
    doca_host_url: str | None
    is_default: bool
    notes: str | None
    created_at: datetime
    updated_at: datetime


class BluefieldSoftwareImageListResponse(BaseModel):
    images: list[BluefieldSoftwareImageResponse]


# ─── bf.conf templates (admin-managed) ──────────────────────────────────────

class BfConfTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    content: str
    matching_bnk_version: str | None
    # Server-classified uplink mode used by the DPU Project Settings UI to
    # filter the Uplink port dropdown. "lag" templates only allow `bond0`;
    # "p0p1" templates only allow `p0` / `p1`. "unknown" → UI shows all
    # choices (custom operator template).
    uplink_mode: str | None = None
    created_at: datetime
    updated_at: datetime


class BfConfTemplateSummary(BaseModel):
    """Lightweight shape used in dropdowns — omits the full template content."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    matching_bnk_version: str | None
    # See BfConfTemplateResponse.uplink_mode.
    uplink_mode: str | None = None


class BfConfTemplateListResponse(BaseModel):
    templates: list[BfConfTemplateSummary]


class BfConfTemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    content: str = Field(..., min_length=1)
    matching_bnk_version: str | None = None


class BfConfTemplateUpdate(BaseModel):
    """Partial update — send only the fields you want to change."""

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    content: str | None = Field(None, min_length=1)
    matching_bnk_version: str | None = None


# ─── Project DPU Settings (per-project defaults) ────────────────────────────

DEFAULT_DNS_SERVERS: list[str] = ["1.1.1.1", "8.8.8.8"]
DEFAULT_NTP_SERVERS: list[str] = ["ntp.ubuntu.com"]

_ALLOWED_UPLINKS: tuple[str, ...] = ("bond0", "p0", "p1")

# Names of the two fixed, locked rows. Extras can be anything else —
# but not these, so user-added rows can't pretend to be external/internal.
FIXED_VLAN_NAMES: frozenset[str] = frozenset({"external", "internal"})


def _validate_uplink(value: str | None) -> str | None:
    if value is None:
        return value
    v = value.strip()
    if v == "":
        return None
    if v not in _ALLOWED_UPLINKS:
        raise ValueError(
            f"Uplink must be one of {_ALLOWED_UPLINKS}; got {value!r}"
        )
    return v


def _check_uplink_consistency(uplinks: list[str | None]) -> None:
    """Either every non-null uplink is 'bond0', or every non-null uplink is
    'p0' / 'p1'. Mixing bond0 with p0/p1 is invalid."""
    chosen = [u for u in uplinks if u]
    if not chosen:
        return
    has_bond = any(u == "bond0" for u in chosen)
    has_phys = any(u in ("p0", "p1") for u in chosen)
    if has_bond and has_phys:
        raise ValueError(
            "VLAN uplink ports must all be 'bond0' (LAG path) "
            "or all be 'p0'/'p1' (non-LAG path); mixing the two is not allowed."
        )


class ExtraVlanInterfaceDef(BaseModel):
    """One editable project-level VLAN row (beyond the fixed external/internal).

    Shape mirrors the old `storage_vlan_*` tuple. `name` is a short token
    the operator picks (e.g. "storage", "vdi", "bgp") — it shows up both
    in the UI and as the interface prefix in the rendered bf.conf.
    """

    name: str = Field(..., min_length=1, max_length=32)
    vlan_id: int | None = None
    ipv4_subnet: str | None = None
    ipv6_subnet: str | None = None
    start_host: int | None = None
    uplink: str | None = None

    @field_validator("name")
    @classmethod
    def _name_not_reserved(cls, value: str) -> str:
        v = value.strip().lower()
        if not v:
            raise ValueError("name cannot be empty")
        if v in FIXED_VLAN_NAMES:
            raise ValueError(
                f"'{value}' is reserved — external/internal are fixed rows, "
                "not extras"
            )
        return v

    @field_validator("uplink", mode="before")
    @classmethod
    def _uplink_choice(cls, value: str | None) -> str | None:
        return _validate_uplink(value)


class ExtraVlanInterfaceAssignment(BaseModel):
    """Per-DPU assignment for one extra VLAN. `name` matches the project
    entry — the renderer joins the two to get the full (subnet, uplink, IP)
    picture for each row.
    """

    name: str = Field(..., min_length=1, max_length=32)
    vlan_id: int | None = None
    ipv4: str | None = None
    ipv6: str | None = None

    @field_validator("name")
    @classmethod
    def _name_clean(cls, value: str) -> str:
        v = value.strip().lower()
        if not v:
            raise ValueError("name cannot be empty")
        return v


class ProjectDpuSettingsUpdate(BaseModel):
    """All fields optional — send only the ones you want to change."""

    bluefield_image_id: int | None = None
    bf_template_id: int | None = None
    high_speed_mtu: int | None = None
    dns_servers: list[str] | None = None
    ntp_servers: list[str] | None = None
    default_oob_username: str | None = None
    default_oob_password: str | None = None  # plaintext; encrypted on write
    default_os_username: str | None = None
    default_os_password: str | None = None
    default_os_ssh_credential_id: int | None = None

    # Fixed VLAN rows — external and internal are always present.
    external_vlan_id: int | None = None
    external_vlan_ipv4_subnet: str | None = None
    external_vlan_ipv6_subnet: str | None = None
    external_vlan_start_host: int | None = None
    external_vlan_uplink: str | None = None
    internal_vlan_id: int | None = None
    internal_vlan_ipv4_subnet: str | None = None
    internal_vlan_ipv6_subnet: str | None = None
    internal_vlan_start_host: int | None = None
    internal_vlan_uplink: str | None = None

    # Add/remove rows via this list. Each entry's name must be unique and
    # cannot collide with the fixed "external" / "internal" names.
    extra_vlan_interfaces: list[ExtraVlanInterfaceDef] | None = None

    @field_validator(
        "external_vlan_uplink", "internal_vlan_uplink",
        mode="before",
    )
    @classmethod
    def _uplink_choice(cls, value: str | None) -> str | None:
        return _validate_uplink(value)

    @field_validator("extra_vlan_interfaces")
    @classmethod
    def _unique_names(
        cls, value: list[ExtraVlanInterfaceDef] | None
    ) -> list[ExtraVlanInterfaceDef] | None:
        if value is None:
            return value
        seen: set[str] = set()
        for entry in value:
            key = entry.name  # already lowercased by the nested validator
            if key in seen:
                raise ValueError(f"duplicate VLAN interface name '{key}'")
            seen.add(key)
        return value

    @model_validator(mode="after")
    def _uplink_consistency(self) -> "ProjectDpuSettingsUpdate":
        # Only validate consistency when at least one uplink is explicitly
        # set in this partial update — otherwise a single-field PATCH of
        # e.g. dns_servers would fail against a stored row.
        touched_fields = {"external_vlan_uplink", "internal_vlan_uplink", "extra_vlan_interfaces"}
        if touched_fields & self.model_fields_set:
            uplinks: list[str | None] = [
                self.external_vlan_uplink,
                self.internal_vlan_uplink,
            ]
            if self.extra_vlan_interfaces is not None:
                uplinks.extend(e.uplink for e in self.extra_vlan_interfaces)
            _check_uplink_consistency(uplinks)
        return self


class ProjectDpuSettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    bluefield_image_id: int | None
    bf_template_id: int | None = None
    high_speed_mtu: int | None
    dns_servers: list[str] = Field(default_factory=lambda: list(DEFAULT_DNS_SERVERS))
    ntp_servers: list[str] = Field(default_factory=lambda: list(DEFAULT_NTP_SERVERS))
    default_oob_username: str | None
    default_os_username: str | None
    has_default_oob_password: bool
    has_default_os_password: bool
    default_os_ssh_credential_id: int | None = None
    external_vlan_id: int | None = None
    external_vlan_ipv4_subnet: str | None = None
    external_vlan_ipv6_subnet: str | None = None
    external_vlan_start_host: int | None = None
    external_vlan_uplink: str | None = None
    internal_vlan_id: int | None = None
    internal_vlan_ipv4_subnet: str | None = None
    internal_vlan_ipv6_subnet: str | None = None
    internal_vlan_start_host: int | None = None
    internal_vlan_uplink: str | None = None
    extra_vlan_interfaces: list[ExtraVlanInterfaceDef] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


# ─── DPU (per-device) ────────────────────────────────────────────────────────

class DpuCreate(BaseModel):
    name: str | None = None
    access_mode: str = "bmc"  # "bmc" | "in-band"
    # BMC-mode fields
    bmc_ip: str | None = None
    bmc_username: str | None = None  # plaintext
    bmc_password: str | None = None  # plaintext
    # In-band-mode fields — set when access_mode="in-band".
    host_node_ip: str | None = None
    host_ssh_credential_id: int | None = None
    host_ssh_username: str | None = None
    host_ssh_port: int | None = None
    host_ssh_auth_type: str | None = None  # "password" | "key"
    host_ssh_password: str | None = None  # plaintext
    host_ssh_private_key: str | None = None  # plaintext PEM
    host_ssh_key_passphrase: str | None = None  # plaintext
    pci_address: str | None = None
    # Deprecated — kept for API back-compat, never read. UI has dropped it.
    oob0_ipv4: str = "dhcp"
    # VLAN plan — all optional at create time
    external_vlan_id: int | None = None
    external_vlan_ipv4: str | None = None
    external_vlan_ipv6: str | None = None
    internal_vlan_id: int | None = None
    internal_vlan_ipv4: str | None = None
    internal_vlan_ipv6: str | None = None
    # Extras — auto-populated at create time from project subnets. Clients
    # can also pass explicit assignments here to override.
    extra_vlan_interfaces: list[ExtraVlanInterfaceAssignment] | None = None


class DpuUpdate(BaseModel):
    """All fields optional — partial update."""

    name: str | None = None
    bmc_ip: str | None = None
    bmc_username: str | None = None
    bmc_password: str | None = None
    # In-band credentials — any subset editable.
    host_node_ip: str | None = None
    host_ssh_credential_id: int | None = None
    host_ssh_username: str | None = None
    host_ssh_port: int | None = None
    host_ssh_auth_type: str | None = None
    host_ssh_password: str | None = None
    host_ssh_private_key: str | None = None
    host_ssh_key_passphrase: str | None = None
    external_vlan_id: int | None = None
    external_vlan_ipv4: str | None = None
    external_vlan_ipv6: str | None = None
    internal_vlan_id: int | None = None
    internal_vlan_ipv4: str | None = None
    internal_vlan_ipv6: str | None = None
    extra_vlan_interfaces: list[ExtraVlanInterfaceAssignment] | None = None


class DpuResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    name: str | None
    access_mode: str  # "bmc" | "in-band"
    bmc_ip: str | None
    bmc_username: str | None
    has_bmc_password: bool
    host_node_ip: str | None = None
    host_hostname: str | None = None
    host_ssh_credential_id: int | None = None
    host_ssh_username: str | None = None
    host_ssh_port: int | None = None
    host_ssh_auth_type: str | None = None
    has_host_ssh_password: bool = False
    has_host_ssh_private_key: bool = False
    pci_address: str | None = None
    serial_number: str | None
    oob0_mac: str | None
    oob0_ipv4: str

    external_vlan_id: int | None
    external_vlan_ipv4: str | None
    external_vlan_ipv6: str | None
    internal_vlan_id: int | None
    internal_vlan_ipv4: str | None
    internal_vlan_ipv6: str | None
    extra_vlan_interfaces: list[ExtraVlanInterfaceAssignment] = Field(default_factory=list)

    last_discovery_at: datetime | None
    last_discovery_status: str | None
    last_discovery_error: str | None

    # Derived from last_discovery_payload — surfaced in list view so the UI
    # doesn't need a per-row inventory fetch.
    nic_mode: str | None = None
    host_privilege_level: str | None = None

    dpu_os_ip: str | None
    dpu_os_reachable: bool | None
    dpu_os_status: str | None = None
    # Surfaced from last_discovery_payload.dpu_os.error so the list view can
    # show the actual probe failure reason (auth, unreachable, etc.) instead
    # of a generic "probe error" badge.
    dpu_os_error: str | None = None
    # Phase 3: per-DPU connectivity + LACP/link diagnostics derived from
    # last_discovery_payload.dpu_os.{connectivity,lacp}. Surfaced inline
    # so the DPU page can render the bottom-of-page tables without a
    # separate inventory fetch per row.
    dpu_os_connectivity: dict[str, Any] | None = None
    dpu_os_lacp: dict[str, Any] | None = None
    # DOCA / NIC FW / BMC FW captured by the DPU OS probe. Empty values
    # indicate the source command was unavailable (fresh BFB before
    # DOCA tools install; ipmitool denied; etc.) — the UI hides those
    # cells instead of showing "unknown" placeholders.
    dpu_os_firmware: dict[str, str | None] | None = None
    # Names of every kernel-visible interface on the DPU at probe time
    # (`["lo", "oob_net0", "p0", "p1", "bond0", "external0",
    # "internal100", …]`). Surfaced so the e2e harness can wait for
    # bf.conf-driven VLAN sub-interfaces to come up post-flash before
    # the connectivity matrix runs — `dpu_os_status=reachable` means
    # SSH works, not that netplan finished.
    dpu_os_interfaces: list[str] | None = None

    # Phase 4 — flash lifecycle (null when never flashed by Forge).
    flash_status: str | None = None
    flash_stage_detail: str | None = None
    flash_started_at: datetime | None = None
    flash_completed_at: datetime | None = None
    flash_error: str | None = None
    last_rendered_bf_conf_at: datetime | None = None

    # rshim readiness on the reachable side (in-band only today).
    # "ready" | "inactive" | "unreachable" | null (never checked).
    rshim_state: str | None = None
    rshim_state_detail: str | None = None
    rshim_checked_at: datetime | None = None
    # /dev/rshimN device this DPU is mapped to on the host (e.g. "rshim0",
    # "rshim1"). Populated by the in-band probe; null until first probe.
    # Multi-DPU hosts expose one rshim per DPU; the UI surfaces this so an
    # operator can tell which device serves which DPU.
    rshim_device: str | None = None
    # DOCA host package readiness on the host side.
    doca_status: dict | None = None
    # Aggregate port link mode derived from the mlxconfig snapshot:
    #   "eth"     — both ports configured for Ethernet
    #   "ib"      — both ports configured for InfiniBand
    #   "mixed"   — P1 and P2 differ
    #   null      — unknown (not probed yet, or BMC-only DPU pre-PhaseN)
    # The UI shows a compact `IB` chip beside the DPU name when this is
    # `ib` / `mixed`; default Ethernet stays unbadged to keep the table
    # uncluttered.
    link_mode: str | None = None
    # Hostname this DPU's bf.conf will render into the DPU OS at flash
    # time. Single-DPU hosts get `<host>-dpu`; multi-DPU hosts get
    # `<host>-dpu1`, `<host>-dpu2`, … BMC-mode DPUs return the serial
    # number (when known). Computed server-side so the UI shows exactly
    # what gets flashed without duplicating the rules client-side.
    bfb_hostname: str | None = None

    created_at: datetime
    updated_at: datetime


class DpuListResponse(BaseModel):
    dpus: list[DpuResponse]


class DpuResetRequest(BaseModel):
    """Body for POST /dpus/{id}/reset — which kind of reset to trigger."""

    reset_type: str  # one of: GracefulRestart, ForceRestart, PowerCycle


class DpuResetToDefaultsRequest(BaseModel):
    """Body for POST /dpus/{id}/reset-to-defaults — layered factory reset.

    All three default to True; the caller disables what they don't want.
    `nic=True` requires DPU OS reachable (mlxconfig + mlxprivhost live on
    the DPU side); the service skips the step gracefully otherwise.
    `bmc_deep=True` (only meaningful when bmc=True) swaps the Redfish
    Manager.ResetToDefaults for the Nvidia OEM variant that clears more
    state.
    """

    bios: bool = True
    bmc: bool = True
    nic: bool = True
    bmc_deep: bool = False


class DpuDiscoveryDetail(BaseModel):
    """Expanded per-DPU detail view — full inventory payload for the UI."""

    model_config = ConfigDict(from_attributes=True)

    dpu: DpuResponse
    inventory: dict[str, Any] | None  # Serialized DpuInventory (from last_discovery_payload)
