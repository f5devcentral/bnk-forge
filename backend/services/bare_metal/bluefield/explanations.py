"""Human-readable descriptions for BlueField Redfish attributes and enum values.

Used by the expanded per-DPU detail view to render each field with context
rather than raw strings. Any missing key falls back to the raw value.
"""

# Bios attributes — key → description of what the setting controls
BIOS_ATTRIBUTE_DESCRIPTIONS: dict[str, str] = {
    "HostPrivilegeLevel": (
        "Trust level granted to the x86 host. 'Privileged' (aka trusted mode) "
        "lets the host reconfigure the DPU via mlxconfig/mlxreg; 'Restricted' "
        "(zero-trust) blocks it. Toggled with `mlxprivhost p/r` or via Bios PATCH; "
        "requires DPU cold reset."
    ),
    "NicMode": (
        "Which CPU owns the data ports. 'DpuMode' — DPU OS owns them. "
        "'NicMode' — host owns them (plain SmartNIC). 'SeparatedHost' and "
        "'ConnectXMode' are less common variants. Changing NicMode requires a "
        "flash-level reconfig."
    ),
    "InternalCPUModel": (
        "'Embedded' — ARM cores active. 'Separated' / 'Disabled' — ARM cores "
        "deactivated. Consistent with NicMode."
    ),
    "FieldMode": "Manufacturing/field-debug mode. Should be false in production.",
    "EnableSMMU": "IOMMU on the ARM cores — isolates DMA between workloads.",
    "EnableOPTEE": "OP-TEE TrustZone secure OS enabled for secure-world workloads.",
    "DisablePCIe": (
        "When true, severs the PCIe link to the host. Only used in standalone-DPU "
        "deployments where the DPU is not hosted by an x86 system."
    ),
    "BootPartitionProtection": "Anti-rollback protection for the BFB boot image.",
    "SPCR_UART": "Host-facing Serial Port Console Redirection table (ACPI).",
    "UefiArgs": "Extra UEFI boot arguments passed to the DPU firmware.",
    "OsArgs": "Extra kernel cmdline arguments passed to the DPU OS.",
    "DefaultPasswordPolicy": "UEFI default password policy.",
    "EnableDdr5600": "Run DDR at 5600 MT/s (if supported).",
    "Enable2ndeMMC": "Enable the secondary eMMC device.",
    "EmmcWipe": "Wipe eMMC at next boot.",
    "NvmeWipe": "Wipe NVMe at next boot.",
    "ResetEfiVars": "Reset UEFI non-volatile variables.",
    "LegacyPasswordEnable": "Allow legacy UEFI password flow.",
    "DateTime": "UEFI real-time clock date/time.",
    "CeThreshold": "Correctable-error reporting threshold.",
    "DisableHEST": "Disable the ACPI HEST (Hardware Error Source Table).",
    "DisableTMFF": "Disable TMFF feature.",
    "DisableSPMI": "Disable the SPMI table.",
    "DisableI2c1": "Disable the I2C1 bus.",
    "DisableConfigAutoReboot": "Suppress the auto-reboot after config changes.",
    "ForcePxeRetryDisable": "Disable PXE retry logic.",
    "LlcReadAlloc": "LLC read-allocation policy.",
    "L3CachePartitionLevel": "L3 cache partitioning level.",
}

# Enum value → meaning, for Bios attributes whose values are discrete strings
BIOS_ENUM_VALUES: dict[str, dict[str, str]] = {
    "HostPrivilegeLevel": {
        "Privileged": "Host is trusted — can reconfigure the DPU.",
        "Restricted": "Host is zero-trust — blocked from reconfiguration.",
    },
    "NicMode": {
        "DpuMode": "DPU OS owns the data ports (full DPU operation).",
        "NicMode": "Host owns the data ports (SmartNIC behaviour).",
        "SeparatedHost": "Data ports split between ARM and host views.",
        "ConnectXMode": "ConnectX compatibility mode — legacy NIC behaviour.",
    },
    "InternalCPUModel": {
        "Embedded": "ARM cores active.",
        "Separated": "ARM cores run independently of host lifecycle.",
        "Disabled": "ARM cores disabled.",
    },
    "SPCR_UART": {
        "Enabled": "Host sees the DPU UART via ACPI SPCR.",
        "Disabled": "Host does not see the DPU UART via SPCR.",
    },
}

# Redfish common enum explanations
LINK_STATUS: dict[str, str] = {
    "LinkUp": "Port is up and passing traffic.",
    "LinkDown": "Port is administratively up but no carrier.",
    "NoLink": "No physical link detected.",
    "LinkError": "Port reports a hardware error.",
}

POWER_STATE: dict[str, str] = {
    "On": "System is running.",
    "Off": "System is powered off.",
    "PoweringOn": "System is in the middle of booting.",
    "PoweringOff": "System is shutting down.",
}

HEALTH: dict[str, str] = {
    "OK": "No known issues.",
    "Warning": "Degraded — attention advised.",
    "Critical": "Fault detected — intervention required.",
}

IPV6_ADDRESS_ORIGIN: dict[str, str] = {
    "LinkLocal": "Auto-assigned link-local (fe80::/10). No routing.",
    "Static": "Manually configured.",
    "DHCPv6": "Leased via DHCPv6.",
    "SLAAC": "Auto-configured from router advertisement.",
}

IPV4_ADDRESS_ORIGIN: dict[str, str] = {
    "Static": "Manually configured.",
    "DHCP": "Leased via DHCPv4.",
    "BOOTP": "Acquired via BOOTP.",
    "IPv4LinkLocal": "Auto-assigned link-local (169.254.0.0/16).",
}


def describe_bios_attribute(attr_name: str) -> str:
    """Return a human-readable description for a Bios attribute, or its name."""
    return BIOS_ATTRIBUTE_DESCRIPTIONS.get(attr_name, attr_name)


def describe_bios_value(attr_name: str, value: object) -> str:
    """Return a human-readable description for a Bios attribute value, or str(value)."""
    values = BIOS_ENUM_VALUES.get(attr_name)
    if values and isinstance(value, str):
        return values.get(value, str(value))
    return str(value)
