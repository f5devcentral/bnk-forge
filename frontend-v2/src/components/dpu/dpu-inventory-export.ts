/**
 * Markdown / JSON serializers for the per-DPU verbose inventory.
 *
 * The input is whatever `last_discovery_payload` looks like for this
 * DPU — the BMC-Redfish shape (top-level `firmware`, `interfaces`,
 * `bios`, …) or the in-band shape (`raw_lspci`, `mlxconfig_settings`,
 * `dpu_os.*`, …). Both flow through the same renderer; missing keys
 * are silently skipped.
 *
 * Markdown is the human-readable export an operator pastes into a
 * ticket; JSON is the lossless sibling for automation.
 */
import type { Dpu } from '@/lib/api/dpus';

type Inv = Record<string, unknown>;

// Keys we render verbatim as code blocks at the bottom of the doc —
// they're multi-line raw command output.
const RAW_KEYS: Record<string, string> = {
  raw_rshim_misc: '/dev/rshim*/misc',
  raw_lspci: 'lspci -vvv',
  raw_mlxconfig: 'mlxconfig -d <mst> q',
  raw_sysfs: '/sys/bus/pci/devices/<pci>',
};

// Top-level keys handled by purpose-built renderers (so output groups
// nicely instead of dumping a generic key/value table). Anything not
// in here falls through to `_emitGeneric`.
const HANDLED_KEYS = new Set<string>([
  ...Object.keys(RAW_KEYS),
  'firmware', 'bios', 'interfaces', 'manager',
  'mlxconfig_settings', 'dpu_os',
]);


function _esc(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'string') return v.length > 0 ? v : '—';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}


function _isMultiline(s: unknown): boolean {
  return typeof s === 'string' && s.includes('\n');
}


function _humaniseKey(k: string): string {
  // snake_case → Title Case. Keeps short acronyms (BIOS, BMC, FW, OOB)
  // upper-case so the headings read naturally.
  const t = k.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  return t.replace(/\b(Bios|Bmc|Fw|Oob|Lacp|Mtu|Dns|Ntp|Ipv4|Ipv6|Ip|Mac|Pci|Sf|Pf|Vlan|Vf|Sku|Opn|Atf|Uefi|Ofed|Erot)\b/g,
    (m) => m.toUpperCase());
}


function _kvTable(rows: Array<[string, unknown]>): string[] {
  const present = rows.filter(
    ([, v]) => v !== null && v !== undefined && v !== '',
  );
  if (present.length === 0) return [];
  const out = ['| Field | Value |', '| --- | --- |'];
  for (const [k, v] of present) {
    // Pipe characters in values would break the table; escape them.
    const safe = _esc(v).replace(/\|/g, '\\|').replace(/\n/g, ' ');
    out.push(`| ${k} | ${safe} |`);
  }
  return out;
}


function _codeBlock(lang: string, text: string): string[] {
  return ['```' + lang, text.trimEnd(), '```'];
}


function _emitFirmware(out: string[], fw: Inv): void {
  const rows: Array<[string, unknown]> = [
    ['BMC firmware', fw.bmc],
    ['BSP / DOCA', fw.bsp],
    ['DPU OS image', fw.os_image],
    ['UEFI', fw.uefi],
    ['ATF', fw.atf],
    ['NIC firmware', fw.nic],
    ['OFED', fw.ofed],
    ['ERoT', fw.erot],
    ['Board', fw.board],
  ];
  const tbl = _kvTable(rows);
  if (tbl.length === 0) return;
  out.push('## Firmware', '', ...tbl, '');
}


function _emitMlxconfigSettings(out: string[], settings: Record<string, unknown>): void {
  const keys = Object.keys(settings);
  if (keys.length === 0) return;
  out.push(`## mlxconfig settings (${keys.length})`, '');
  out.push('| Setting | Value |', '| --- | --- |');
  for (const k of keys.sort()) {
    out.push(`| \`${k}\` | \`${_esc(settings[k])}\` |`);
  }
  out.push('');
}


function _emitInterfaces(out: string[], interfaces: unknown[]): void {
  if (!Array.isArray(interfaces) || interfaces.length === 0) return;
  out.push('## Network interfaces', '');
  for (const iface of interfaces as Inv[]) {
    out.push(`### ${_esc(iface.name)}`, '');
    out.push(..._kvTable([
      ['MAC', iface.mac],
      ['Link', iface.link_status],
      ['Speed (Mbps)', iface.speed_mbps],
      ['MTU', iface.mtu],
      ['DHCPv4', iface.dhcp_v4_enabled],
      ['DHCPv6 mode', iface.dhcp_v6_mode],
      ['IPv4 default gateway', iface.ipv4_default_gateway],
      ['IPv6 default gateway', iface.ipv6_default_gateway],
    ]));
    if (Array.isArray(iface.ipv4_addresses) && iface.ipv4_addresses.length > 0) {
      out.push('', `**IPv4 addresses**:`);
      for (const a of iface.ipv4_addresses) {
        out.push(`- \`${_esc(a)}\``);
      }
    }
    if (Array.isArray(iface.ipv6_addresses) && iface.ipv6_addresses.length > 0) {
      out.push('', `**IPv6 addresses**:`);
      for (const a of iface.ipv6_addresses) {
        out.push(`- \`${_esc(a)}\``);
      }
    }
    out.push('');
  }
}


function _emitDpuOs(out: string[], dpuOs: Inv): void {
  out.push('## DPU OS probe', '');
  out.push(..._kvTable([
    ['Probed at', dpuOs.probed_at],
    ['DPU OS IP (resolved)', dpuOs.ip],
    ['DPU OS MAC', dpuOs.mac],
    ['Hostname', dpuOs.hostname],
    ['uname', dpuOs.uname],
  ]));
  out.push('');

  for (const [k, label] of [
    ['ip_addr', '`ip -br addr show`'],
    ['ip4_route', '`ip -4 route`'],
    ['ip6_route', '`ip -6 route`'],
  ] as Array<[string, string]>) {
    const v = dpuOs[k];
    if (typeof v === 'string' && v.trim()) {
      out.push(`### ${label}`, '', ..._codeBlock('text', v), '');
    }
  }

  const fw = dpuOs.firmware as Inv | undefined;
  if (fw && Object.keys(fw).length > 0) {
    out.push('### Firmware (DPU OS view)', '');
    out.push(..._kvTable([
      ['DOCA / bf-bundle', fw.doca],
      ['NIC firmware', fw.nic],
      ['BMC firmware', fw.bmc],
    ]));
    out.push('');
  }

  const conn = dpuOs.connectivity as Inv | undefined;
  if (conn) {
    out.push('### Connectivity', '');
    const inet = (conn.internet ?? {}) as Inv;
    const dns = ((conn.dns ?? {}) as Inv).f5_com as Inv | undefined;
    out.push(..._kvTable([
      ['Internet target', inet.target],
      ['Via interface', inet.via_interface],
      ['Next hop', inet.next_hop],
      ['Source IP', inet.src],
      ['Ping ok', inet.ping_ok],
      ['RTT (ms)', inet.rtt_ms],
      ['DNS (f5.com) ok', dns?.ok],
      ['DNS addresses', Array.isArray(dns?.addresses) ? (dns?.addresses as unknown[]).join(', ') : null],
    ]));
    out.push('');
  }

  const lacp = dpuOs.lacp as Inv | undefined;
  if (lacp) {
    out.push('### LACP / link', '');
    if (lacp.bond_present === true) {
      out.push(..._kvTable([
        ['Bond', lacp.bond_name ?? 'bond0'],
        ['Mode', lacp.mode],
        ['LACP rate', lacp.lacp_rate],
      ]));
      const members = (lacp.members ?? []) as Inv[];
      if (members.length > 0) {
        out.push('', '| Member | MII status | Speed | Duplex | Failures |',
          '| --- | --- | --- | --- | --- |');
        for (const m of members) {
          out.push(`| ${_esc(m.name)} | ${_esc(m.mii_status)} | `
            + `${_esc(m.speed)} | ${_esc(m.duplex)} | ${_esc(m.link_failure_count ?? 0)} |`);
        }
      }
    } else {
      const uplinks = (lacp.uplinks ?? []) as Inv[];
      if (uplinks.length > 0) {
        out.push('Bond not present — per-uplink ethtool snapshot:', '');
        out.push('| Uplink | Link | Speed | Duplex |', '| --- | --- | --- | --- |');
        for (const u of uplinks) {
          out.push(`| ${_esc(u.name)} | ${_esc(u.link)} | ${_esc(u.speed)} | ${_esc(u.duplex)} |`);
        }
      }
    }
    out.push('');
  }

  const ipmi = dpuOs.ipmitool as Record<string, string> | undefined;
  if (ipmi && Object.values(ipmi).some((v) => typeof v === 'string' && v.trim())) {
    out.push('### ipmitool', '');
    for (const [section, raw] of Object.entries(ipmi)) {
      if (typeof raw !== 'string' || !raw.trim()) continue;
      out.push(`#### \`${section}\``, '', ..._codeBlock('text', raw), '');
    }
  }
}


function _emitGeneric(out: string[], inv: Inv): void {
  // Catch-all for top-level keys we don't render explicitly. Skips raw
  // blobs (rendered later as code blocks) and the keys handled above.
  const rows: Array<[string, unknown]> = [];
  for (const [k, v] of Object.entries(inv)) {
    if (HANDLED_KEYS.has(k)) continue;
    if (v === null || v === undefined || v === '') continue;
    if (typeof v === 'object' && v !== null) continue;  // arrays/objects handled separately
    if (_isMultiline(v)) continue;  // block out below
    rows.push([_humaniseKey(k), v]);
  }
  if (rows.length === 0) return;
  out.push('## Identity & probe metadata', '', ..._kvTable(rows), '');

  // Multi-line strings under generic keys → code blocks.
  for (const [k, v] of Object.entries(inv)) {
    if (HANDLED_KEYS.has(k)) continue;
    if (typeof v !== 'string' || !v.includes('\n')) continue;
    out.push(`### ${_humaniseKey(k)}`, '', ..._codeBlock('text', v), '');
  }
}


function _emitRawBlocks(out: string[], inv: Inv): void {
  const present = Object.entries(RAW_KEYS).filter(
    ([k]) => typeof inv[k] === 'string' && (inv[k] as string).trim(),
  );
  if (present.length === 0) return;
  out.push('## Raw command output', '');
  for (const [k, label] of present) {
    out.push(`### \`${label}\``, '', ..._codeBlock('text', inv[k] as string), '');
  }
}


export function dpuInventoryToMarkdown(dpu: Dpu, inv: Inv): string {
  const out: string[] = [];
  const ts = new Date().toISOString();
  const name = dpu.host_hostname || dpu.name || `dpu-${dpu.id}`;

  out.push(`# DPU report — ${name}`, '');
  out.push(`_Generated ${ts}_`, '');
  out.push('## DPU registration', '');
  out.push(..._kvTable([
    ['DPU id', dpu.id],
    ['Project id', dpu.project_id],
    ['Name', dpu.name],
    ['Access mode', dpu.access_mode],
    ['BMC IP', dpu.bmc_ip],
    ['Host node IP', dpu.host_node_ip],
    ['Host hostname', dpu.host_hostname],
    ['PCI address', dpu.pci_address],
    ['Serial number', dpu.serial_number],
    ['oob0 MAC', dpu.oob0_mac],
    ['oob0 IPv4', dpu.oob0_ipv4],
    ['DPU OS IP', dpu.dpu_os_ip],
    ['DPU OS reachable', dpu.dpu_os_reachable],
    ['rshim device', dpu.rshim_device],
  ]));
  out.push('');

  _emitGeneric(out, inv);

  const fw = inv.firmware as Inv | undefined;
  if (fw) _emitFirmware(out, fw);

  const interfaces = inv.interfaces;
  if (Array.isArray(interfaces)) _emitInterfaces(out, interfaces);

  const bios = inv.bios as Inv | undefined;
  if (bios && Object.keys(bios).length > 0) {
    out.push('## BIOS', '');
    out.push(..._kvTable(Object.entries(bios).map(
      ([k, v]) => [_humaniseKey(k), v],
    )));
    out.push('');
  }

  const manager = inv.manager as Inv | undefined;
  if (manager && Object.keys(manager).length > 0) {
    out.push('## BMC / Manager', '');
    out.push(..._kvTable(Object.entries(manager).map(
      ([k, v]) => [_humaniseKey(k), v],
    )));
    out.push('');
  }

  const settings = inv.mlxconfig_settings as Record<string, unknown> | undefined;
  if (settings) _emitMlxconfigSettings(out, settings);

  const dpuOs = inv.dpu_os as Inv | undefined;
  if (dpuOs) _emitDpuOs(out, dpuOs);

  _emitRawBlocks(out, inv);

  return out.join('\n');
}


export function dpuInventoryToJson(dpu: Dpu, inv: Inv): string {
  return JSON.stringify(
    {
      generated_at: new Date().toISOString(),
      dpu: {
        id: dpu.id,
        project_id: dpu.project_id,
        name: dpu.name,
        access_mode: dpu.access_mode,
        bmc_ip: dpu.bmc_ip,
        host_node_ip: dpu.host_node_ip,
        host_hostname: dpu.host_hostname,
        pci_address: dpu.pci_address,
        serial_number: dpu.serial_number,
        oob0_mac: dpu.oob0_mac,
        oob0_ipv4: dpu.oob0_ipv4,
        dpu_os_ip: dpu.dpu_os_ip,
        dpu_os_reachable: dpu.dpu_os_reachable,
        rshim_device: dpu.rshim_device,
      },
      inventory: inv,
    },
    null,
    2,
  );
}


export function downloadDpuReport(
  dpu: Dpu,
  inv: Inv,
  format: 'md' | 'json',
): void {
  const name = (dpu.host_hostname || dpu.name || `dpu-${dpu.id}`)
    .replace(/[^a-zA-Z0-9._-]/g, '_');
  const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  const filename = `dpu-${name}-${ts}.${format}`;

  const body = format === 'md'
    ? dpuInventoryToMarkdown(dpu, inv)
    : dpuInventoryToJson(dpu, inv);

  const blob = new Blob(
    [body],
    { type: format === 'md' ? 'text/markdown;charset=utf-8' : 'application/json;charset=utf-8' },
  );
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  // Defer revoke a tick — some browsers race the click handler.
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}
