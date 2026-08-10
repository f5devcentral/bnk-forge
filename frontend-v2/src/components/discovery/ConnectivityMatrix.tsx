/**
 * Per-group inter-node connectivity matrix display.
 *
 * Rows  = source (node, interface)  — nodes laid out vertically as the user requested.
 * Cols  = target (node, interface).
 * Cells = ✓ / ✗ / — (self), with RTT and target address in the tooltip.
 */
import { useMemo } from 'react';
import { CheckCircle2, ChevronRight, Loader2, MinusCircle, Network, XCircle } from 'lucide-react';
import { Card } from '@/components/ui/card';
import type {
  ConnectivityEndpoint,
  ConnectivityMatrix as ConnectivityMatrixData,
  ConnectivityResult,
} from '@/lib/api/discovery';

interface ConnectivityMatrixProps {
  status: 'skipped' | 'pending' | 'in_progress' | 'completed' | 'failed' | null;
  matrix: ConnectivityMatrixData | null;
}

const GROUP_LABELS: Record<string, { title: string; subtitle: string }> = {
  builtin: {
    title: 'Built-in interfaces',
    subtitle: 'On-board NICs (typically 1G/10G)',
  },
  bluefield: {
    title: 'BlueField-3 interfaces',
    subtitle: 'High-speed BF-3 PFs',
  },
  // DPU↔DPU matrix (rendered from DpuPanel) reuses this component;
  // label keeps the heading distinct from the discovery groups.
  dpu: {
    title: 'DPU↔DPU connectivity',
    subtitle: 'Pairwise ping across each DPU OS — oob_net0 + VLAN data-plane',
  },
};

function endpointKey(ep: ConnectivityEndpoint): string {
  return `${ep.node_id}:${ep.interface}`;
}

function shortHostname(fqdn: string | null): string | null {
  if (!fqdn) return null;
  return fqdn.split('.')[0];
}

function endpointLabel(ep: ConnectivityEndpoint): string {
  return shortHostname(ep.hostname) || ep.node_ip;
}

interface CellMeta {
  reachable: boolean;
  rtt_ms: number | null;
  family: string;
  dst_address: string;
  error: string | null;
}

function indexResults(results: ConnectivityResult[]): Map<string, CellMeta> {
  // Pick the first IPv4 result if present, else IPv6 — keeps the cell deterministic
  // even when both families were tried.
  const map = new Map<string, CellMeta>();
  for (const r of results) {
    const key = `${r.src_node_id}:${r.src_iface}|${r.dst_node_id}:${r.dst_iface}`;
    const existing = map.get(key);
    if (!existing || (existing.family === 'inet6' && r.family === 'inet')) {
      map.set(key, {
        reachable: r.reachable,
        rtt_ms: r.rtt_ms,
        family: r.family,
        dst_address: r.dst_address,
        error: r.error,
      });
    }
  }
  return map;
}

function SummaryMatrix({
  groupKey,
  endpoints,
  cells,
}: {
  groupKey: string;
  endpoints: ConnectivityEndpoint[];
  cells: Map<string, CellMeta>;
}) {
  const labels = GROUP_LABELS[groupKey] ?? { title: groupKey, subtitle: '' };

  // Group endpoints by node, sorted by node id; reference node = the first one.
  const nodes = useMemo(() => {
    const byNode = new Map<number, { node_id: number; label: string; ifaces: ConnectivityEndpoint[] }>();
    for (const ep of endpoints) {
      const entry = byNode.get(ep.node_id) ?? { node_id: ep.node_id, label: endpointLabel(ep), ifaces: [] };
      entry.ifaces.push(ep);
      byNode.set(ep.node_id, entry);
    }
    return [...byNode.values()].sort((a, b) => a.node_id - b.node_id);
  }, [endpoints]);

  if (nodes.length < 2) return null;

  const reference = nodes[0];
  const others = nodes.slice(1);
  const refIfaces = [...reference.ifaces].sort((a, b) => a.interface.localeCompare(b.interface));

  return (
    <Card className="p-4 space-y-2">
      <div>
        <p className="text-sm font-semibold flex items-center gap-2">
          <Network className="h-4 w-4" />
          {labels.title} — summary
        </p>
        <p className="text-xs text-muted-foreground">
          Same-named interface on each target node, sourced from <span className="font-mono">{reference.label}</span>.
        </p>
      </div>

      <div className="overflow-auto">
        <table className="text-xs border-collapse">
          <thead>
            <tr>
              <th className="border p-1 text-left bg-background">{reference.label} interface</th>
              {others.map((node) => (
                <th
                  key={`sum-col-${node.node_id}`}
                  className="border p-1 font-mono text-[10px] whitespace-nowrap"
                  title={`target node ${node.label}`}
                >
                  {node.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {refIfaces.map((src) => (
              <tr key={`sum-row-${src.interface}`}>
                <th className="border p-1 font-mono text-[10px] whitespace-nowrap text-left bg-muted/30">
                  {src.interface}
                </th>
                {others.map((node) => {
                  const dst = node.ifaces.find((ep) => ep.interface === src.interface);
                  if (!dst) {
                    return (
                      <td
                        key={`sum-cell-${src.interface}-${node.node_id}`}
                        className="border p-1 text-center text-muted-foreground"
                        title={`${node.label} has no interface named ${src.interface}`}
                      >
                        n/a
                      </td>
                    );
                  }
                  const key = `${reference.node_id}:${src.interface}|${node.node_id}:${dst.interface}`;
                  const cell = cells.get(key);
                  if (!cell) {
                    return (
                      <td
                        key={`sum-cell-${src.interface}-${node.node_id}`}
                        className="border p-1 text-center text-muted-foreground"
                      >
                        ·
                      </td>
                    );
                  }
                  const tooltip =
                    `${cell.dst_address} (${cell.family})` +
                    (cell.reachable && cell.rtt_ms != null ? ` — ${cell.rtt_ms.toFixed(2)} ms` : '') +
                    (!cell.reachable && cell.error ? ` — ${cell.error}` : '');
                  return (
                    <td
                      key={`sum-cell-${src.interface}-${node.node_id}`}
                      className="border p-1 text-center"
                      title={tooltip}
                    >
                      {cell.reachable ? (
                        <span className="inline-flex items-center gap-1 text-success">
                          <CheckCircle2 className="h-3.5 w-3.5" />
                          {cell.rtt_ms != null && (
                            <span className="text-[10px] tabular-nums">{cell.rtt_ms.toFixed(1)}</span>
                          )}
                        </span>
                      ) : (
                        <XCircle className="h-3.5 w-3.5 inline text-destructive" />
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-[10px] text-muted-foreground">
        Numbers are average ICMP round-trip time in milliseconds. ✓ = reachable, ✗ = unreachable, n/a = no
        same-named interface on that node.
      </p>
    </Card>
  );
}

function GroupMatrix({ groupKey, group }: { groupKey: string; group: { endpoints: ConnectivityEndpoint[]; results: ConnectivityResult[] } }) {
  const cells = useMemo(() => indexResults(group.results), [group.results]);
  const labels = GROUP_LABELS[groupKey] ?? { title: groupKey, subtitle: '' };

  // Sort endpoints stably so rows and columns align: by node id, then interface name.
  const endpoints = useMemo(
    () =>
      [...group.endpoints].sort((a, b) =>
        a.node_id !== b.node_id ? a.node_id - b.node_id : a.interface.localeCompare(b.interface),
      ),
    [group.endpoints],
  );

  // Per-node row spans for the leftmost column.
  const nodeRowCounts = useMemo(() => {
    const counts: Array<{ nodeId: number; label: string; count: number }> = [];
    for (const ep of endpoints) {
      const last = counts[counts.length - 1];
      if (last && last.nodeId === ep.node_id) {
        last.count += 1;
      } else {
        counts.push({ nodeId: ep.node_id, label: endpointLabel(ep), count: 1 });
      }
    }
    return counts;
  }, [endpoints]);

  if (endpoints.length === 0) return null;

  return (
    <div className="space-y-3">
      <SummaryMatrix groupKey={groupKey} endpoints={endpoints} cells={cells} />

      <Card className="p-0 overflow-hidden">
      <details className="group">
        <summary className="cursor-pointer list-none p-3 flex items-center gap-2 hover:bg-muted/50 transition-colors">
          <ChevronRight className="h-4 w-4 text-muted-foreground transition-transform group-open:rotate-90" />
          <span className="text-sm font-semibold flex items-center gap-2">
            <Network className="h-4 w-4" />
            {labels.title} — full matrix
          </span>
          <span className="text-xs text-muted-foreground">({endpoints.length} × {endpoints.length})</span>
        </summary>
        <div className="px-4 pb-4 space-y-3">
          <p className="text-xs text-muted-foreground">{labels.subtitle}</p>

          <div className="overflow-auto">
        <table className="text-xs border-collapse">
          <thead>
            <tr>
              <th className="sticky left-0 bg-background z-10 border p-1 text-left" colSpan={2}>
                src ↓ / dst →
              </th>
              {endpoints.map((ep) => (
                <th
                  key={`col-${endpointKey(ep)}`}
                  className="border p-1 font-mono text-[10px] whitespace-nowrap"
                  title={`${ep.hostname || endpointLabel(ep)} — ${ep.node_ip}`}
                >
                  <div className="text-muted-foreground">{endpointLabel(ep)}</div>
                  <div>{ep.interface}</div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(() => {
              const rows: JSX.Element[] = [];
              let cursor = 0;
              for (const node of nodeRowCounts) {
                for (let i = 0; i < node.count; i++) {
                  const src = endpoints[cursor];
                  rows.push(
                    <tr key={`row-${endpointKey(src)}`}>
                      {i === 0 && (
                        <th
                          rowSpan={node.count}
                          className="sticky left-0 bg-background z-10 border p-1 font-mono text-[10px] whitespace-nowrap align-middle text-left"
                        >
                          {node.label}
                        </th>
                      )}
                      <th className="border p-1 font-mono text-[10px] whitespace-nowrap text-left bg-muted/30">
                        {src.interface}
                      </th>
                      {endpoints.map((dst) => {
                        if (src.node_id === dst.node_id) {
                          return (
                            <td key={`cell-${endpointKey(src)}-${endpointKey(dst)}`} className="border p-1 text-center text-muted-foreground">
                              <MinusCircle className="h-3.5 w-3.5 inline" aria-label="self" />
                            </td>
                          );
                        }
                        const key = `${src.node_id}:${src.interface}|${dst.node_id}:${dst.interface}`;
                        const cell = cells.get(key);
                        if (!cell) {
                          return (
                            <td key={`cell-${endpointKey(src)}-${endpointKey(dst)}`} className="border p-1 text-center text-muted-foreground">
                              ·
                            </td>
                          );
                        }
                        const tooltip =
                          `${cell.dst_address} (${cell.family})` +
                          (cell.reachable && cell.rtt_ms != null ? ` — ${cell.rtt_ms.toFixed(2)} ms` : '') +
                          (!cell.reachable && cell.error ? ` — ${cell.error}` : '');
                        return (
                          <td
                            key={`cell-${endpointKey(src)}-${endpointKey(dst)}`}
                            className="border p-1 text-center"
                            title={tooltip}
                          >
                            {cell.reachable ? (
                              <span className="inline-flex items-center gap-1 text-success">
                                <CheckCircle2 className="h-3.5 w-3.5" />
                                {cell.rtt_ms != null && (
                                  <span className="text-[10px] tabular-nums">{cell.rtt_ms.toFixed(1)}</span>
                                )}
                              </span>
                            ) : (
                              <XCircle className="h-3.5 w-3.5 inline text-destructive" />
                            )}
                          </td>
                        );
                      })}
                    </tr>,
                  );
                  cursor += 1;
                }
              }
              return rows;
            })()}
          </tbody>
        </table>
      </div>
          <p className="text-[10px] text-muted-foreground">
            Numbers are average ICMP round-trip time in milliseconds (
            <span className="font-mono">ping -c 2</span> per cell). ✓ = reachable, ✗ = unreachable, — = self.
          </p>
        </div>
      </details>
    </Card>
    </div>
  );
}

export function ConnectivityMatrix({ status, matrix }: ConnectivityMatrixProps) {
  if (status === 'pending' || status === 'in_progress') {
    return (
      <Card className="p-4 flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Testing inter-node connectivity...
      </Card>
    );
  }

  if (status === 'failed') {
    return (
      <Card className="p-4 border-l-2 border-l-destructive text-sm text-destructive">
        Connectivity sweep failed
        {matrix?.error ? ` — ${matrix.error}` : null}
      </Card>
    );
  }

  if (status !== 'completed' || !matrix?.groups) return null;

  const groupEntries = Object.entries(matrix.groups).filter(
    (entry): entry is [string, NonNullable<typeof entry[1]>] => Boolean(entry[1]),
  );
  if (groupEntries.length === 0) return null;

  return (
    <div className="space-y-3">
      <h4 className="text-base font-semibold">Inter-node connectivity</h4>
      {groupEntries.map(([key, group]) => (
        <GroupMatrix key={key} groupKey={key} group={group} />
      ))}
    </div>
  );
}
