/**
 * Expanded per-DPU detail view — full BlueField Redfish inventory.
 *
 * Driven from the /dpus/{id}/inventory endpoint. Every BIOS attribute
 * and key interface field is rendered with a human-readable description
 * beside the raw value.
 */
import { Download, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useDpuInventory } from '@/hooks/useDpus';
import type { Dpu, DpuInventoryPayload } from '@/lib/api/dpus';
import { DocaStatusCard, type DocaStatus } from '../bare-metal/DocaStatusCard';
import {
  describeBiosAttribute,
  describeBiosValue,
  HEALTH,
  LINK_STATUS,
  POWER_STATE,
} from './bluefield-explanations';
import { cn } from '@/lib/utils';
import { downloadDpuReport } from './dpu-inventory-export';


function ExportButtons({
  dpu,
  inv,
}: {
  dpu: Dpu;
  inv: Record<string, unknown>;
}) {
  // Two compact buttons, top-right of the panel. Markdown is the
  // operator-friendly default (paste into a ticket); JSON is the
  // lossless sibling for automation / `jq` filtering.
  return (
    <div className="flex items-center gap-2">
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="h-7 text-xs gap-1"
        title="Download this DPU's verbose data as Markdown — operator-readable, paste-friendly"
        onClick={(e) => {
          e.stopPropagation();
          downloadDpuReport(dpu, inv, 'md');
        }}
      >
        <Download className="h-3 w-3" />
        MD
      </Button>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="h-7 text-xs gap-1"
        title="Download this DPU's verbose data as JSON — lossless, machine-readable"
        onClick={(e) => {
          e.stopPropagation();
          downloadDpuReport(dpu, inv, 'json');
        }}
      >
        <Download className="h-3 w-3" />
        JSON
      </Button>
    </div>
  );
}

interface DpuInventoryDetailProps {
  projectId: number;
  dpu: Dpu;
}

export function DpuInventoryDetail({ projectId, dpu }: DpuInventoryDetailProps) {
  const { data, isLoading, error } = useDpuInventory(projectId, dpu.id);

  if (isLoading) {
    return (
      <div className="flex justify-center py-4">
        <Loader2 className="h-5 w-5 animate-spin text-primary" />
      </div>
    );
  }
  if (error) {
    return <div className="text-destructive py-2">Failed to load inventory.</div>;
  }
  if (!data || !data.inventory) {
    return (
      <div className="py-2 text-muted-foreground">
        No inventory yet. Run “Discover” to probe this DPU.
      </div>
    );
  }

  const inv = data.inventory;

  // In-band DPUs write a completely different shape into last_discovery_payload
  // (InbandInventory dataclass — rshim + lspci + mlxconfig). Render a
  // separate view so the BMC-only sections below don't crash on missing
  // `inv.interfaces` / `inv.firmware` / BIOS fields.
  if ((inv as unknown as { rshim_device_present?: boolean }).rshim_device_present !== undefined) {
    return (
      <InbandInventoryView
        dpu={dpu}
        inv={inv as unknown as InbandInventoryPayload}
      />
    );
  }

  const dpuOs = (inv as unknown as { dpu_os?: Record<string, unknown> }).dpu_os;

  return (
    <div className="p-4 rounded-md space-y-6 bg-muted/50">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Inventory</h3>
        <ExportButtons dpu={dpu} inv={inv as unknown as Record<string, unknown>} />
      </div>
      <ProbedFirmwareSection payload={inv as unknown as { dpu_os?: { firmware?: { doca?: string | null; nic?: string | null; bmc?: string | null } | null } | null }} />
      <IdentitySection inv={inv} />
      <FirmwareSection inv={inv} />
      <InterfacesSection inv={inv} />
      <BiosSection inv={inv} />
      <ManagerSection inv={inv} />
      {dpuOs && <DpuOsSection dpuOs={dpuOs} />}
    </div>
  );
}

interface InbandInventoryPayload {
  rshim_device_present: boolean;
  raw_rshim_misc?: string;
  raw_lspci?: string;
  raw_mlxconfig?: string;
  raw_sysfs?: string;
  opn?: string | null;
  dev_info?: string | null;
  bf_mode?: string | null;
  boot_mode?: string | null;
  up_time_seconds?: number | null;
  uuid?: string | null;
  mac?: string | null;
  serial?: string | null;
  psid?: string | null;
  fw_version?: string | null;
  part_number?: string | null;
  pci_description?: string | null;
  sku?: string | null;
  description?: string | null;
  base_mac?: string | null;
  subsystem_id?: string | null;
  pci_id?: string | null;
  mlxconfig_device_type?: string | null;
  mlxconfig_pci_device?: string | null;
  mlxconfig_settings?: Record<string, string>;
}

function InbandInventoryView({
  dpu,
  inv,
}: {
  dpu: Dpu;
  inv: InbandInventoryPayload;
}) {
  // Identity first — these are the fields an operator actually wants to
  // see at a glance. Rendered full-width so long SKU / description lines
  // wrap instead of getting truncated.
  const identity: Array<[string, string | number | null | undefined]> = [
    ['SKU / OPN', inv.sku],
    ['Description', inv.description],
    ['Device type', inv.mlxconfig_device_type],
    ['Serial number', inv.serial],
    ['Part number (VPD)', inv.part_number],
    ['MST device', inv.mlxconfig_pci_device],
    ['PCI ID', inv.pci_id],
    ['Subsystem ID', inv.subsystem_id],
    ['PCIe description', inv.pci_description],
  ];
  const secondary: Array<[string, string | number | null | undefined]> = [
    ['Device info', inv.dev_info],
    ['OPN (rshim)', inv.opn],
    ['UUID', inv.uuid],
    ['BF mode', inv.bf_mode],
    ['Boot mode', inv.boot_mode],
    ['Up time', inv.up_time_seconds != null ? `${inv.up_time_seconds}s` : null],
    ['MAC (tmfifo)', inv.mac],
  ];
  const presentIdentity = identity.filter(([, v]) => v != null && v !== '');
  const presentSecondary = secondary.filter(([, v]) => v != null && v !== '');

  const renderRow = (k: string, v: string | number, opts: { wide?: boolean } = {}) => (
    <div
      key={k}
      className={cn(
        'flex gap-4 border-b border-dashed border-border py-1',
        opts.wide ? 'flex-col' : 'justify-between items-baseline',
      )}
    >
      <span className="text-muted-foreground text-xs">{k}</span>
      <span
        className={cn(
          'font-mono text-xs break-all',
          opts.wide ? '' : 'text-right',
        )}
      >
        {v}
      </span>
    </div>
  );

  const access: Array<[string, string | number | null | undefined]> = [
    ['DPU host hostname', dpu.host_hostname],
    ['DPU host IP', dpu.host_node_ip],
    ['DPU PCI address', dpu.pci_address],
    ['rshim device', dpu.rshim_device ? `/dev/${dpu.rshim_device}` : null],
  ];
  const presentAccess = access.filter(([, v]) => v != null && v !== '');

  return (
    <div className="p-4 rounded-md space-y-4 bg-muted/50">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Inventory (in-band)</h3>
        <ExportButtons dpu={dpu} inv={inv as unknown as Record<string, unknown>} />
      </div>
      <ProbedFirmwareSection
        payload={inv as unknown as { dpu_os?: { firmware?: { doca?: string | null; nic?: string | null; bmc?: string | null } | null } | null }}
      />
      {dpu.doca_status && (
        <DocaStatusCard doca={dpu.doca_status as unknown as DocaStatus} />
      )}
      {presentAccess.length > 0 && (
        <div>
          <h4 className="text-sm font-semibold mb-2">In-band access</h4>
          <div className="space-y-0.5 text-sm">
            {presentAccess.map(([k, v]) => renderRow(k, String(v)))}
          </div>
        </div>
      )}
      {presentIdentity.length > 0 && (
        <div>
          <h4 className="text-sm font-semibold mb-2">DPU identity (via rshim + lspci)</h4>
          <div className="space-y-0.5 text-sm">
            {presentIdentity.map(([k, v]) => {
              const wide = k === 'Description' || k === 'PCIe description';
              return renderRow(k, String(v), { wide });
            })}
          </div>
        </div>
      )}
      {presentSecondary.length > 0 && (
        <div>
          <h4 className="text-sm font-semibold mb-2">Runtime / firmware</h4>
          <div className="grid grid-cols-2 gap-x-6 gap-y-0.5 text-sm">
            {presentSecondary.map(([k, v]) => renderRow(k, String(v)))}
          </div>
        </div>
      )}
      {presentIdentity.length === 0 && presentSecondary.length === 0 && (
        <p className="text-xs text-muted-foreground">
          No identity fields parsed — raw command output is below.
        </p>
      )}
      {inv.mlxconfig_settings && Object.keys(inv.mlxconfig_settings).length > 0 && (
        <div>
          <h4 className="text-sm font-semibold mb-2">
            mlxconfig settings ({Object.keys(inv.mlxconfig_settings).length})
          </h4>
          <div className="rounded border max-h-96 overflow-auto">
            <table className="w-full text-xs">
              <tbody>
                {Object.entries(inv.mlxconfig_settings).map(([k, v]) => (
                  <tr key={k} className="border-b border-dashed border-border">
                    <td className="py-1 pl-2 pr-4 text-muted-foreground font-mono whitespace-nowrap align-top">
                      {k}
                    </td>
                    <td className="py-1 pr-2 font-mono break-all">{v}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {inv.raw_rshim_misc && (
        <RawBlock
          title={`/dev/${dpu.rshim_device || 'rshim0'}/misc (DISPLAY_LEVEL 2)`}
          text={inv.raw_rshim_misc}
        />
      )}
      {inv.raw_lspci && <RawBlock title="lspci -vvv" text={inv.raw_lspci} />}
      {/* `raw_mlxconfig` is intentionally NOT rendered — every field
          shown there is already in the structured `mlxconfig settings`
          table above. Keeping it in the persisted payload for
          forensic export but not in the panel. */}
      {inv.raw_sysfs && <RawBlock title="/sys/bus/pci/devices/<pci>" text={inv.raw_sysfs} />}
      {(() => {
        // DPU OS probe payload is merged into last_discovery_payload by
        // dpu_os_probe_service regardless of access mode, so render it
        // here too (in-band DPUs go through `Probe DPU OS` after flash).
        const dpuOs = (inv as unknown as { dpu_os?: Record<string, unknown> }).dpu_os;
        return dpuOs ? <DpuOsSection dpuOs={dpuOs} /> : null;
      })()}
    </div>
  );
}

function RawBlock({ title, text }: { title: string; text: string }) {
  return (
    <details className="rounded border">
      <summary className="cursor-pointer px-3 py-2 text-xs font-medium bg-muted/40 hover:bg-muted/60">
        {title}
      </summary>
      <pre className="max-h-80 overflow-auto p-3 text-[11px] font-mono whitespace-pre-wrap">
        {text}
      </pre>
    </details>
  );
}

function DpuOsSection({ dpuOs }: { dpuOs: Record<string, unknown> }) {
  const rows: Array<[string, React.ReactNode, string?]> = [];
  if (typeof dpuOs.error === 'string') {
    rows.push(['Error', dpuOs.error]);
  } else {
    if (typeof dpuOs.ip === 'string') rows.push(['IP', dpuOs.ip]);
    if (typeof dpuOs.mac === 'string') rows.push(['MAC', dpuOs.mac]);
    if (typeof dpuOs.hostname === 'string' && dpuOs.hostname) {
      rows.push(['hostname', dpuOs.hostname]);
    }
    if (typeof dpuOs.uname === 'string') rows.push(['uname', dpuOs.uname]);
    if (typeof dpuOs.ip_addr === 'string') {
      rows.push([
        'ip addr',
        <pre className="whitespace-pre-wrap">{dpuOs.ip_addr}</pre>,
      ]);
    }
    if (typeof dpuOs.ip4_route === 'string' && dpuOs.ip4_route) {
      rows.push([
        'ip -4 route',
        <pre className="whitespace-pre-wrap">{dpuOs.ip4_route}</pre>,
      ]);
    }
    if (typeof dpuOs.ip6_route === 'string' && dpuOs.ip6_route) {
      rows.push([
        'ip -6 route',
        <pre className="whitespace-pre-wrap">{dpuOs.ip6_route}</pre>,
      ]);
    }
  }
  if (typeof dpuOs.probed_at === 'string') {
    rows.push(['Probed at', dpuOs.probed_at]);
  }
  // The ipmitool blob is a dict with one entry per sub-command; render
  // the sub-section only when at least one command produced output, so
  // legacy probes (no ipmitool field) don't show an empty header.
  const ipmitool =
    dpuOs.ipmitool && typeof dpuOs.ipmitool === 'object'
      ? (dpuOs.ipmitool as Record<string, unknown>)
      : null;
  return (
    <Section title="DPU OS (via BMC ARP sweep + SSH)">
      <KeyValueTable rows={rows} />
      {ipmitool && <IpmitoolSubsection ipmitool={ipmitool} />}
    </Section>
  );
}


// Order intentional: the temperature sensors are the most useful at-a-
// glance signal for the operator, so they go first. The matching label
// is what we show in the <summary>; `key` indexes the dict written by
// `dpu_os_probe_service._parse_dpu_os_probe_output`.
const IPMITOOL_SUBSECTIONS: Array<{ key: string; label: string }> = [
  { key: 'sdr_type_temperature', label: 'ipmitool sdr type Temperature' },
  { key: 'mc_info', label: 'ipmitool mc info' },
  { key: 'fru_0', label: 'ipmitool fru print 0' },
  { key: 'fru_1', label: 'ipmitool fru print 1' },
  { key: 'sensor_list', label: 'ipmitool sensor list' },
  { key: 'sdr_elist', label: 'ipmitool sdr elist' },
  { key: 'lan_print', label: 'ipmitool lan print' },
];

function IpmitoolSubsection({
  ipmitool,
}: {
  ipmitool: Record<string, unknown>;
}) {
  const present = IPMITOOL_SUBSECTIONS.filter(({ key }) => {
    const v = ipmitool[key];
    return typeof v === 'string' && v.trim().length > 0;
  });
  if (present.length === 0) return null;
  return (
    <div className="mt-4 space-y-1">
      <h5 className="text-xs font-semibold uppercase tracking-wide opacity-70">
        DPU BMC ipmitool info
      </h5>
      {present.map(({ key, label }) => (
        <details key={key} className="rounded border">
          <summary className="cursor-pointer px-3 py-1.5 text-xs font-medium bg-muted/40 hover:bg-muted/60 font-mono">
            {label}
          </summary>
          <pre className="max-h-80 overflow-auto p-3 text-[11px] font-mono whitespace-pre-wrap">
            {String(ipmitool[key])}
          </pre>
        </details>
      ))}
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h4 className="text-sm font-semibold mb-2 uppercase tracking-wide opacity-70">
        {title}
      </h4>
      {children}
    </div>
  );
}

function KeyValueTable({ rows }: { rows: Array<[string, React.ReactNode, string?]> }) {
  return (
    <table className="w-full text-xs border-collapse">
      <tbody>
        {rows.map(([k, v, hint], i) => (
          <tr key={i} className="align-top">
            <td
              className={cn(
                'py-1.5 pr-3 font-medium whitespace-nowrap w-48',
                i > 0 && 'border-t border-border',
              )}
            >
              {k}
            </td>
            <td
              className={cn(
                'py-1.5',
                i > 0 && 'border-t border-border',
              )}
            >
              <span className="font-mono">
                {v ?? <span className="opacity-50">—</span>}
              </span>
              {hint && (
                <span className="ml-3 opacity-60 font-normal italic">{hint}</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function IdentitySection({ inv }: { inv: DpuInventoryPayload }) {
  return (
    <Section title="Identity">
      <KeyValueTable
        rows={[
          ['Manufacturer', inv.manufacturer],
          ['Model', inv.model],
          ['Serial Number', inv.serial_number],
          ['Part Number', inv.part_number],
          ['UUID', inv.uuid],
          ['Power State', inv.power_state, inv.power_state ? POWER_STATE[inv.power_state] : ''],
          ['Health', inv.system_health, inv.system_health ? HEALTH[inv.system_health] : ''],
          ['Redfish Version', inv.redfish_version],
        ]}
      />
    </Section>
  );
}

// Compact "DOCA / NIC / BMC" trio captured by `Probe DPU OS` from
// inside the DPU OS itself. Rendered as the FIRST block in the
// expanded inventory so operators see the three versions they care
// about most without scrolling. Source: last_discovery_payload.dpu_os.firmware.
// Returns null when the probe hasn't run yet — falls back to the empty
// state hint at the call site.
function ProbedFirmwareSection({
  payload,
}: {
  payload: { dpu_os?: { firmware?: { doca?: string | null; nic?: string | null; bmc?: string | null } | null } | null } | null | undefined;
}) {
  const fw = payload?.dpu_os?.firmware;
  if (!fw) return null;
  const rows: Array<[string, string | null | undefined]> = [
    ['DOCA / bf-bundle', fw.doca],
    ['NIC firmware', fw.nic],
    ['BMC firmware', fw.bmc],
  ];
  if (rows.every(([, v]) => !v)) return null;
  return (
    <Section title="Firmware (DOCA · NIC · BMC)">
      <KeyValueTable rows={rows} />
    </Section>
  );
}


function FirmwareSection({ inv }: { inv: DpuInventoryPayload }) {
  const fw = inv.firmware;
  return (
    <Section title="Firmware (BMC Redfish view)">
      <KeyValueTable
        rows={[
          ['BMC (OpenBMC)', fw.bmc],
          ['BSP (DOCA)', fw.bsp],
          ['DPU OS image', fw.os_image],
          ['UEFI', fw.uefi],
          ['ATF', fw.atf],
          ['NIC firmware', fw.nic],
          ['OFED', fw.ofed],
          ['ERoT', fw.erot],
          ['Board', fw.board],
        ]}
      />
    </Section>
  );
}

function InterfacesSection({ inv }: { inv: DpuInventoryPayload }) {
  return (
    <Section title="Ethernet interfaces">
      <div className="space-y-3">
        {inv.interfaces.map((iface) => (
          <div key={iface.name} className="rounded border border-border p-2">
            <div className="font-mono text-xs font-semibold mb-1">{iface.name}</div>
            <KeyValueTable
              rows={[
                ['MAC', iface.mac],
                [
                  'Link',
                  iface.link_status,
                  iface.link_status ? LINK_STATUS[iface.link_status] : '',
                ],
                ['Speed', iface.speed_mbps != null ? `${iface.speed_mbps} Mbps` : null],
                ['MTU', iface.mtu],
                [
                  'DHCPv4',
                  iface.dhcp_v4_enabled == null ? null : iface.dhcp_v4_enabled ? 'enabled' : 'disabled',
                ],
                [
                  'IPv4 addresses',
                  iface.ipv4_addresses.length
                    ? JSON.stringify(iface.ipv4_addresses)
                    : 'none (BSP may not expose DHCP leases)',
                ],
                [
                  'IPv6 addresses',
                  iface.ipv6_addresses.length ? JSON.stringify(iface.ipv6_addresses) : 'none',
                ],
              ]}
            />
          </div>
        ))}
      </div>
    </Section>
  );
}

function BiosSection({ inv }: { inv: DpuInventoryPayload }) {
  const b = inv.bios;
  const rows: Array<[string, React.ReactNode, string]> = [];
  const addRow = (attrName: string, value: unknown) => {
    if (value == null) return;
    rows.push([
      attrName,
      describeBiosValue(attrName, value),
      describeBiosAttribute(attrName),
    ]);
  };
  addRow('HostPrivilegeLevel', b.host_privilege_level);
  addRow('NicMode', b.nic_mode);
  addRow('InternalCPUModel', b.internal_cpu_model);
  addRow('FieldMode', b.field_mode);
  addRow('EnableSMMU', b.enable_smmu);
  addRow('EnableOPTEE', b.enable_optee);
  addRow('DisablePCIe', b.disable_pcie);
  addRow('BootPartitionProtection', b.boot_partition_protection);
  addRow('SPCR_UART', b.spcr_uart);
  if (b.uefi_args) addRow('UefiArgs', b.uefi_args);
  if (b.os_args) addRow('OsArgs', b.os_args);
  return (
    <Section title="BIOS / mode">
      <KeyValueTable rows={rows} />
    </Section>
  );
}

function ManagerSection({ inv }: { inv: DpuInventoryPayload }) {
  const m = inv.manager;
  return (
    <Section title="Manager (BMC)">
      <KeyValueTable
        rows={[
          ['Firmware version', m.firmware_version],
          ['Model', m.model],
          ['Type', m.manager_type],
          ['Health', m.health, m.health ? HEALTH[m.health] : ''],
          ['Date/time', m.datetime],
          [
            'OTP provisioned',
            m.otp_provisioned == null ? null : m.otp_provisioned ? 'true' : 'false',
            'Secure-boot root-of-trust fuses burned. False is normal for lab hardware.',
          ],
        ]}
      />
    </Section>
  );
}
