import type { Dpu } from '@/lib/api/dpus';

/**
 * Label for a DPU row where **no other column already shows the
 * identifier** — e.g. the DPU Uplink Interfaces table, which doesn't
 * otherwise name the DPU. Falls back to "host_ip (pci)" for in-band
 * DPUs that don't have a Redfish serial.
 */
export function dpuRowLabel(dpu: Dpu): string {
  if (dpu.serial_number) return dpu.serial_number;
  if (dpu.access_mode === 'in-band') {
    const host = dpu.host_hostname || dpu.host_node_ip;
    if (host) {
      return dpu.pci_address ? `${host} (${dpu.pci_address})` : host;
    }
  }
  return '—';
}

/**
 * Compact identifier for the main DPU tab. The header reads
 * "Serial # / PCI #", so the role of an in-band row's PCI BDF is
 * already clear from context — no surrounding parens needed.
 */
export function dpuShortLabel(dpu: Dpu): string {
  if (dpu.serial_number) return dpu.serial_number;
  if (dpu.access_mode === 'in-band' && dpu.pci_address) {
    return dpu.pci_address;
  }
  return '—';
}
