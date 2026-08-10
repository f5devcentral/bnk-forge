/**
 * Human-readable descriptions for BlueField Redfish attributes and enums.
 *
 * Mirrors backend/services/bare_metal/bluefield/explanations.py so the UI
 * can render each field with context. Any missing key falls back to
 * `describeValue` returning the raw value as a string.
 */

export const BIOS_ATTRIBUTE_DESCRIPTIONS: Record<string, string> = {
  HostPrivilegeLevel:
    "Trust level granted to the x86 host. 'Privileged' (trusted mode) lets the host reconfigure the DPU; 'Restricted' (zero-trust) blocks it. Toggled with `mlxprivhost p/r` or via Bios PATCH; requires DPU cold reset.",
  NicMode:
    "Which CPU owns the data ports. 'DpuMode' — DPU OS owns them. 'NicMode' — host owns them (plain SmartNIC). 'SeparatedHost' / 'ConnectXMode' are variants. Changing requires a flash-level reconfig.",
  InternalCPUModel:
    "'Embedded' — ARM cores active. 'Separated' / 'Disabled' — ARM cores deactivated. Consistent with NicMode.",
  FieldMode: "Manufacturing/field-debug mode. Should be false in production.",
  EnableSMMU: "IOMMU on the ARM cores — isolates DMA between workloads.",
  EnableOPTEE: "OP-TEE TrustZone secure OS enabled.",
  DisablePCIe:
    "When true, severs the PCIe link to the host. Only used in standalone-DPU deployments.",
  BootPartitionProtection: "Anti-rollback protection for the BFB boot image.",
  SPCR_UART: "Host-facing Serial Port Console Redirection table (ACPI).",
  UefiArgs: "Extra UEFI boot arguments passed to the DPU firmware.",
  OsArgs: "Extra kernel cmdline arguments passed to the DPU OS.",
};

export const BIOS_ENUM_VALUES: Record<string, Record<string, string>> = {
  HostPrivilegeLevel: {
    Privileged: 'Host is trusted — can reconfigure the DPU.',
    Restricted: 'Host is zero-trust — blocked from reconfiguration.',
  },
  NicMode: {
    DpuMode: 'DPU OS owns the data ports (full DPU operation).',
    NicMode: 'Host owns the data ports (SmartNIC behaviour).',
    SeparatedHost: 'Data ports split between ARM and host views.',
    ConnectXMode: 'ConnectX compatibility mode — legacy NIC behaviour.',
  },
  InternalCPUModel: {
    Embedded: 'ARM cores active.',
    Separated: 'ARM cores run independently of host lifecycle.',
    Disabled: 'ARM cores disabled.',
  },
  SPCR_UART: {
    Enabled: 'Host sees the DPU UART via ACPI SPCR.',
    Disabled: 'Host does not see the DPU UART via SPCR.',
  },
};

export const LINK_STATUS: Record<string, string> = {
  LinkUp: 'Port is up and passing traffic.',
  LinkDown: 'Port is administratively up but no carrier.',
  NoLink: 'No physical link detected.',
  LinkError: 'Port reports a hardware error.',
};

export const POWER_STATE: Record<string, string> = {
  On: 'System is running.',
  Off: 'System is powered off.',
  PoweringOn: 'System is booting.',
  PoweringOff: 'System is shutting down.',
};

export const HEALTH: Record<string, string> = {
  OK: 'No known issues.',
  Warning: 'Degraded — attention advised.',
  Critical: 'Fault detected — intervention required.',
};

export function describeBiosAttribute(attr: string): string {
  return BIOS_ATTRIBUTE_DESCRIPTIONS[attr] ?? attr;
}

export function describeBiosValue(attr: string, value: unknown): string {
  const values = BIOS_ENUM_VALUES[attr];
  if (values && typeof value === 'string' && value in values) {
    return values[value];
  }
  return String(value);
}
