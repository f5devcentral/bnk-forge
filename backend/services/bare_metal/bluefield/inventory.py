"""Typed inventory returned by the BlueField Redfish probe."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DpuFirmware:
    bmc: str | None = None
    bsp: str | None = None
    os_image: str | None = None
    uefi: str | None = None
    atf: str | None = None
    nic: str | None = None
    ofed: str | None = None
    erot: str | None = None
    board: str | None = None
    redfish_spec: str | None = None


@dataclass
class DpuInterface:
    name: str
    mac: str | None = None
    link_status: str | None = None
    speed_mbps: int | None = None
    mtu: int | None = None
    interface_enabled: bool | None = None
    state: str | None = None
    dhcp_v4_enabled: bool | None = None
    dhcp_v6_mode: str | None = None
    ipv4_addresses: list[dict[str, Any]] = field(default_factory=list)
    ipv4_static_addresses: list[dict[str, Any]] = field(default_factory=list)
    ipv6_addresses: list[dict[str, Any]] = field(default_factory=list)
    ipv6_default_gateway: str | None = None


@dataclass
class DpuBiosSettings:
    host_privilege_level: str | None = None
    nic_mode: str | None = None
    internal_cpu_model: str | None = None
    field_mode: bool | None = None
    enable_smmu: bool | None = None
    enable_optee: bool | None = None
    disable_pcie: bool | None = None
    boot_partition_protection: bool | None = None
    spcr_uart: str | None = None
    uefi_args: str | None = None
    os_args: str | None = None
    all_attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class DpuManagerInfo:
    firmware_version: str | None = None
    manager_type: str | None = None
    model: str | None = None
    health: str | None = None
    datetime: str | None = None
    otp_provisioned: bool | None = None


@dataclass
class DpuInventory:
    manufacturer: str | None = None
    model: str | None = None
    serial_number: str | None = None
    part_number: str | None = None
    uuid: str | None = None
    power_state: str | None = None
    system_health: str | None = None
    redfish_version: str | None = None
    redfish_product: str | None = None
    interfaces: list[DpuInterface] = field(default_factory=list)
    firmware: DpuFirmware = field(default_factory=DpuFirmware)
    bios: DpuBiosSettings = field(default_factory=DpuBiosSettings)
    manager: DpuManagerInfo = field(default_factory=DpuManagerInfo)
    raw: dict[str, Any] = field(default_factory=dict)
