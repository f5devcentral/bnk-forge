/**
 * Per-DPU diagnostic tables — rendered below the main DPU table on
 * DpuPanel. Data comes inline from each DPU's
 * `dpu_os_connectivity`, `dpu_os_lacp`, and `dpu_os_firmware` fields,
 * populated by the "Probe DPU OS" / "Probe DPU OS All" actions.
 *
 * No new triggers — refresh follows the existing probe flow.
 */
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { CheckCircle2, XCircle, MinusCircle } from 'lucide-react';
import type { Dpu } from '@/lib/api/dpus';
import { dpuRowLabel } from './dpu-label';

interface Props {
  dpus: Dpu[];
}

type ConnPayload = {
  internet?: {
    target?: string;
    via_interface?: string | null;
    next_hop?: string | null;
    src?: string | null;
    ping_ok?: boolean;
    rtt_ms?: number | null;
  };
  dns?: {
    f5_com?: {
      ok?: boolean;
      addresses?: string[];
      error?: string | null;
    };
  };
};

type LacpMember = {
  name?: string;
  mii_status?: string | null;
  speed?: string | null;
  duplex?: string | null;
  link_failure_count?: number | null;
};

type LacpUplink = {
  name?: string;
  speed?: string | null;
  duplex?: string | null;
  link?: string | null;
};

type LacpPayload =
  | {
      bond_present: true;
      bond_name?: string;
      mode?: string | null;
      lacp_rate?: string | null;
      members?: LacpMember[];
    }
  | {
      bond_present: false;
      uplinks?: LacpUplink[];
    };

type FirmwarePayload = {
  doca?: string | null;
  nic?: string | null;
  bmc?: string | null;
};

function OkPill({ ok, label }: { ok: boolean | null | undefined; label: string }) {
  if (ok === true) {
    return (
      <Badge variant="success" className="text-xs">
        <CheckCircle2 className="h-3 w-3 mr-1" />
        {label}
      </Badge>
    );
  }
  if (ok === false) {
    return (
      <Badge variant="destructive" className="text-xs">
        <XCircle className="h-3 w-3 mr-1" />
        {label}
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-muted-foreground text-xs">
      <MinusCircle className="h-3 w-3 mr-1" />
      {label}
    </Badge>
  );
}

export function DpuConnectivityTables({ dpus }: Props) {
  if (dpus.length === 0) return null;

  // Hide the cards entirely when no DPU has been probed yet — keeps the
  // page clean before the operator runs Probe DPU OS at least once.
  const anyProbed = dpus.some(
    (d) =>
      d.dpu_os_connectivity != null
      || d.dpu_os_lacp != null
      || d.dpu_os_firmware != null,
  );
  if (!anyProbed) return null;

  return (
    <>
      <Card className="mt-4">
        <CardHeader>
          <CardTitle>Internet access</CardTitle>
          <CardDescription>
            Chosen uplink + next hop for public IPv4 (8.8.8.8), reachability,
            and DNS resolution against the DPU's configured resolver
            (<span className="font-mono">/etc/resolv.conf</span>) for
            f5.com. Captured during the last DPU OS probe.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th className="py-2 px-3 font-medium">DPU</th>
                  <th className="py-2 px-3 font-medium">Uplink</th>
                  <th className="py-2 px-3 font-medium">Next hop</th>
                  <th className="py-2 px-3 font-medium">Source IP</th>
                  <th className="py-2 px-3 font-medium">Internet (8.8.8.8)</th>
                  <th className="py-2 px-3 font-medium">DNS (f5.com)</th>
                </tr>
              </thead>
              <tbody>
                {dpus.map((dpu) => {
                  const conn = (dpu.dpu_os_connectivity ?? null) as ConnPayload | null;
                  const inet = conn?.internet;
                  const dns = conn?.dns?.f5_com;
                  return (
                    <tr key={dpu.id} className="border-b last:border-0">
                      <td className="py-2 px-3 font-mono text-xs whitespace-nowrap">
                        {dpuRowLabel(dpu)}
                      </td>
                      <td className="py-2 px-3 font-mono text-xs">
                        {inet?.via_interface ?? <span className="text-muted-foreground">—</span>}
                      </td>
                      <td className="py-2 px-3 font-mono text-xs">
                        {inet?.next_hop ?? <span className="text-muted-foreground">—</span>}
                      </td>
                      <td className="py-2 px-3 font-mono text-xs">
                        {inet?.src ?? <span className="text-muted-foreground">—</span>}
                      </td>
                      <td className="py-2 px-3">
                        <OkPill
                          ok={inet?.ping_ok ?? null}
                          label={
                            inet?.ping_ok && typeof inet?.rtt_ms === 'number'
                              ? `${inet.rtt_ms.toFixed(1)} ms`
                              : inet?.ping_ok
                                ? 'reachable'
                                : 'unreachable'
                          }
                        />
                      </td>
                      <td className="py-2 px-3">
                        <OkPill
                          ok={dns?.ok ?? null}
                          label={
                            dns?.ok && dns.addresses && dns.addresses.length > 0
                              ? dns.addresses[0]
                              : 'failed'
                          }
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <Card className="mt-4">
        <CardHeader>
          <CardTitle>Link / LACP status</CardTitle>
          <CardDescription>
            When the LAG bf.conf template is in use, shows bond0 mode +
            negotiated LACP rate + per-member link state. Otherwise shows
            link state and negotiated speed for each uplink port.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th className="py-2 px-3 font-medium">DPU</th>
                  <th className="py-2 px-3 font-medium">Bond / Mode</th>
                  <th className="py-2 px-3 font-medium">LACP rate</th>
                  <th className="py-2 px-3 font-medium">Port</th>
                  <th className="py-2 px-3 font-medium">Link</th>
                  <th className="py-2 px-3 font-medium">Speed / Duplex</th>
                  <th className="py-2 px-3 font-medium">Failures</th>
                </tr>
              </thead>
              <tbody>
                {dpus.flatMap((dpu) => {
                  const lacp = (dpu.dpu_os_lacp ?? null) as LacpPayload | null;
                  if (!lacp) {
                    return [
                      <tr key={`${dpu.id}-empty`} className="border-b last:border-0">
                        <td className="py-2 px-3 font-mono text-xs whitespace-nowrap">{dpuRowLabel(dpu)}</td>
                        <td colSpan={6} className="py-2 px-3 text-muted-foreground italic text-xs">
                          Not yet probed
                        </td>
                      </tr>,
                    ];
                  }
                  if (lacp.bond_present) {
                    const members = lacp.members ?? [];
                    if (members.length === 0) {
                      return [
                        <tr key={`${dpu.id}-bond-only`} className="border-b last:border-0">
                          <td className="py-2 px-3 font-mono text-xs whitespace-nowrap">{dpuRowLabel(dpu)}</td>
                          <td className="py-2 px-3">{lacp.mode ?? '—'}</td>
                          <td className="py-2 px-3">{lacp.lacp_rate ?? '—'}</td>
                          <td className="py-2 px-3 text-muted-foreground italic">no members</td>
                          <td className="py-2 px-3" />
                          <td className="py-2 px-3" />
                          <td className="py-2 px-3" />
                        </tr>,
                      ];
                    }
                    return members.map((m, idx) => (
                      <tr key={`${dpu.id}-${m.name ?? idx}`} className="border-b last:border-0">
                        <td className="py-2 px-3 font-mono text-xs whitespace-nowrap">
                          {idx === 0 ? dpuRowLabel(dpu) : ''}
                        </td>
                        <td className="py-2 px-3 text-xs">
                          {idx === 0 ? (lacp.mode ?? '—') : ''}
                        </td>
                        <td className="py-2 px-3">
                          {idx === 0 ? (
                            <Badge variant="outline" className="text-xs">{lacp.lacp_rate ?? '—'}</Badge>
                          ) : ''}
                        </td>
                        <td className="py-2 px-3 font-mono text-xs">{m.name}</td>
                        <td className="py-2 px-3">
                          <OkPill ok={m.mii_status === 'up'} label={m.mii_status ?? '—'} />
                        </td>
                        <td className="py-2 px-3 font-mono text-xs">
                          {m.speed ?? '—'} {m.duplex ? `· ${m.duplex}` : ''}
                        </td>
                        <td className="py-2 px-3 font-mono text-xs">
                          {m.link_failure_count ?? 0}
                        </td>
                      </tr>
                    ));
                  }
                  // Non-LAG: per-uplink rows.
                  const uplinks = lacp.uplinks ?? [];
                  if (uplinks.length === 0) {
                    return [
                      <tr key={`${dpu.id}-no-uplinks`} className="border-b last:border-0">
                        <td className="py-2 px-3 font-mono text-xs whitespace-nowrap">{dpuRowLabel(dpu)}</td>
                        <td className="py-2 px-3 text-muted-foreground italic">no bond</td>
                        <td className="py-2 px-3 text-muted-foreground">—</td>
                        <td className="py-2 px-3 text-muted-foreground italic">no uplinks</td>
                        <td className="py-2 px-3" />
                        <td className="py-2 px-3" />
                        <td className="py-2 px-3" />
                      </tr>,
                    ];
                  }
                  return uplinks.map((u, idx) => (
                    <tr key={`${dpu.id}-${u.name ?? idx}`} className="border-b last:border-0">
                      <td className="py-2 px-3 font-mono text-xs whitespace-nowrap">
                        {idx === 0 ? dpuRowLabel(dpu) : ''}
                      </td>
                      <td className="py-2 px-3 text-xs">
                        {idx === 0 ? <span className="text-muted-foreground italic">no bond</span> : ''}
                      </td>
                      <td className="py-2 px-3 text-muted-foreground">—</td>
                      <td className="py-2 px-3 font-mono text-xs">{u.name}</td>
                      <td className="py-2 px-3">
                        <OkPill ok={u.link === 'yes'} label={u.link ?? '—'} />
                      </td>
                      <td className="py-2 px-3 font-mono text-xs">
                        {u.speed ?? '—'} {u.duplex ? `· ${u.duplex}` : ''}
                      </td>
                      <td className="py-2 px-3 text-muted-foreground">—</td>
                    </tr>
                  ));
                })}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <Card className="mt-4">
        <CardHeader>
          <CardTitle>DPU firmware versions</CardTitle>
          <CardDescription>
            Captured during the last DPU OS probe — DOCA bundle from
            <span className="font-mono"> /etc/mlnx-release</span>, NIC
            firmware from <span className="font-mono">ethtool -i p0</span>,
            BMC firmware from <span className="font-mono">ipmitool mc info</span>.
            Cells are blank when the source command isn't available
            (fresh BFB without DOCA tools, ethtool absent, IPMI denied).
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th className="py-2 px-3 font-medium">DPU</th>
                  <th className="py-2 px-3 font-medium">DOCA / bf-bundle</th>
                  <th className="py-2 px-3 font-medium">NIC firmware</th>
                  <th className="py-2 px-3 font-medium">BMC firmware</th>
                </tr>
              </thead>
              <tbody>
                {dpus.map((dpu) => {
                  const fw = (dpu.dpu_os_firmware ?? null) as FirmwarePayload | null;
                  return (
                    <tr key={dpu.id} className="border-b last:border-0">
                      <td className="py-2 px-3 font-mono text-xs whitespace-nowrap">
                        {dpuRowLabel(dpu)}
                      </td>
                      <td className="py-2 px-3 font-mono text-xs break-all">
                        {fw?.doca ?? <span className="text-muted-foreground">—</span>}
                      </td>
                      <td className="py-2 px-3 font-mono text-xs break-all">
                        {fw?.nic ?? <span className="text-muted-foreground">—</span>}
                      </td>
                      <td className="py-2 px-3 font-mono text-xs">
                        {fw?.bmc ?? <span className="text-muted-foreground">—</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </>
  );
}
