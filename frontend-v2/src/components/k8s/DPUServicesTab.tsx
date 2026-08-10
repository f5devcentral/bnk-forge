/**
 * DPU Services & Chains Tab
 *
 * Combined read-only view of DPF service-layer resources:
 *   - DPUServices — Helm chart deployments to DPU nodes
 *   - DPUDeployments — coordinated deployment groups (services + DPUSets)
 *   - DPUServiceChains — OVS traffic steering between service functions
 *   - DPUServiceInterfaces — OVS port definitions for service chains
 *   - Realized chains & interfaces — per-DPU instances (lower-level)
 *
 * Data comes from the shared DPF unified data cache.
 */

import { useState, useMemo } from 'react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import {
  CheckCircle2,
  XCircle,
  HelpCircle,
  ChevronDown,
  ChevronRight,
  Package,
  GitBranch,
  Plug,
  Layers,
  Container,
  Network,
} from 'lucide-react';
import type {
  DpfDpuService,
  DpfDpuDeployment,
  DpfDpuServiceChain,
  DpfDpuServiceInterface,
  DpfServiceChain,
  DpfServiceInterface,
  DpfK8sCondition,
  DpfOvsSwitch,
} from '@/types';

// ── Props ─────────────────────────────────────────────────────────────────

interface DPUServicesTabProps {
  services: DpfDpuService[];
  deployments: DpfDpuDeployment[];
  serviceChains: DpfDpuServiceChain[];
  serviceInterfaces: DpfDpuServiceInterface[];
  realizedChains: DpfServiceChain[];
  realizedInterfaces: DpfServiceInterface[];
  isLoading?: boolean;
}

// ── Shared Helpers ────────────────────────────────────────────────────────

type ConditionStatus = 'True' | 'False' | 'Unknown' | undefined;

function conditionIcon(status: ConditionStatus) {
  switch (status) {
    case 'True':  return CheckCircle2;
    case 'False': return XCircle;
    default:      return HelpCircle;
  }
}

function conditionColor(status: ConditionStatus) {
  switch (status) {
    case 'True':  return 'text-success';
    case 'False': return 'text-destructive';
    default:      return 'text-muted-foreground';
  }
}

function isReady(conditions: DpfK8sCondition[] | undefined): boolean {
  return (conditions ?? []).some(
    (c) => c.type === 'Ready' && c.status === 'True'
  );
}

type ReadyBadgeVariant = 'success' | 'warning';

function readyBadgeVariant(ready: boolean): ReadyBadgeVariant {
  return ready ? 'success' : 'warning';
}

// ── Section Header ────────────────────────────────────────────────────────

function SectionHeader({
  icon: Icon,
  title,
  count,
}: {
  icon: typeof Package;
  title: string;
  count: number;
}) {
  return (
    <div className="flex items-center gap-2 mb-3">
      <Icon className="h-4 w-4 text-muted-foreground" />
      <h3 className="text-sm font-semibold text-foreground">
        {title}
      </h3>
      <Badge variant="outline" className="text-[10px] px-1.5 py-0">
        {count}
      </Badge>
    </div>
  );
}

// ── Detail Field ──────────────────────────────────────────────────────────

function DetailField({
  label,
  value,
  mono,
}: {
  label: string;
  value: string | undefined | null;
  mono?: boolean;
}) {
  if (!value) return null;
  return (
    <div>
      <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <p className={cn('mt-0.5 text-foreground/80', mono && 'font-mono text-xs')}>
        {value}
      </p>
    </div>
  );
}

// ── Condition List ────────────────────────────────────────────────────────

function ConditionList({
  conditions,
  compact,
}: {
  conditions: DpfK8sCondition[];
  compact?: boolean;
}) {
  return (
    <div className="space-y-1">
      {!compact && (
        <h4 className="text-xs font-semibold mb-1.5 text-foreground/80">
          Conditions
        </h4>
      )}
      {conditions.map((cond) => {
        const Icon = conditionIcon(cond.status as ConditionStatus);
        return (
          <div
            key={cond.type}
            className="flex items-center gap-2 rounded px-3 py-1.5 text-xs bg-card"
          >
            <Icon className={cn('h-3.5 w-3.5 shrink-0', conditionColor(cond.status as ConditionStatus))} />
            <span className="font-medium text-foreground/80">
              {cond.type}
            </span>
            {cond.reason && (
              <span className="text-muted-foreground">({cond.reason})</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Switch/Port Diagram (service chain visualization) ─────────────────────

function SwitchDiagram({ switches }: { switches: DpfOvsSwitch[] }) {
  if (switches.length === 0) return null;
  return (
    <div className="space-y-2">
      <h5 className="text-[10px] uppercase tracking-wider text-muted-foreground">
        OVS Switches
      </h5>
      <div className="flex flex-wrap gap-3">
        {switches.map((sw, idx) => (
          <div
            key={sw.name ?? idx}
            className="rounded-lg border px-3 py-2 text-xs min-w-[140px] border-border bg-muted/50"
          >
            <div className="font-semibold mb-1 text-foreground/80">
              <Network className="inline h-3 w-3 mr-1 -mt-0.5" />
              {sw.name ?? `switch-${idx}`}
            </div>
            {(sw.ports ?? []).length > 0 ? (
              <div className="space-y-0.5">
                {sw.ports!.map((port, pi) => {
                  const labels = port.serviceInterface?.matchLabels ?? {};
                  const labelStr = Object.entries(labels).map(([k, v]) => `${k}=${v}`).join(', ');
                  return (
                    <div
                      key={pi}
                      className="flex items-center gap-1.5 rounded px-2 py-0.5 bg-card"
                    >
                      <Plug className="h-2.5 w-2.5 shrink-0 text-muted-foreground" />
                      <span className="font-mono text-[10px] truncate text-muted-foreground">
                        {labelStr || `port-${pi}`}
                      </span>
                    </div>
                  );
                })}
              </div>
            ) : (
              <span className="text-[10px] text-muted-foreground">no ports</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

// ── DPUService Card ───────────────────────────────────────────────────────

function DPUServiceCard({ service }: { service: DpfDpuService }) {
  const [expanded, setExpanded] = useState(false);
  const spec = service.spec;
  const conditions = service.status?.conditions ?? [];
  const ready = isReady(conditions);
  const variant = readyBadgeVariant(ready);
  const source = spec?.helmChart?.source;

  return (
    <div className="rounded-lg border border-border bg-card">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center w-full gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/50"
      >
        {expanded
          ? <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
          : <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
        }
        <Package className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="font-medium text-sm text-foreground">
          {service.metadata.name}
        </span>
        {service.metadata.namespace && (
          <span className="text-xs text-muted-foreground">
            {service.metadata.namespace}
          </span>
        )}

        {source?.version && (
          <Badge variant="outline" className="text-[10px] px-1.5 py-0">
            v{source.version}
          </Badge>
        )}

        <div className="flex-1" />

        <Badge variant={variant} className="text-[10px] px-2 py-0">
          {ready ? 'Ready' : 'Not Ready'}
        </Badge>
      </button>

      {expanded && (
        <div className="px-4 pb-4 pl-12 space-y-4 border-t border-border bg-muted/50">
          <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-2 pt-3 text-xs">
            <DetailField label="Repo URL" value={source?.repoURL} mono />
            <DetailField label="Chart" value={source?.chart} />
            <DetailField label="Version" value={source?.version} />
            <DetailField label="Path" value={source?.path} mono />
            <DetailField label="Service ID" value={spec?.serviceID} />
            {(spec?.interfaces ?? []).length > 0 && (
              <DetailField label="Interfaces" value={spec!.interfaces!.join(', ')} />
            )}
          </div>

          {spec?.helmChart?.values && Object.keys(spec.helmChart.values).length > 0 && (
            <div className="pt-1">
              <h5 className="text-[10px] uppercase tracking-wider mb-1 text-muted-foreground">
                Helm Values
              </h5>
              <pre className="rounded px-3 py-2 text-[11px] font-mono whitespace-pre-wrap max-h-40 overflow-auto bg-card text-muted-foreground">
                {JSON.stringify(spec.helmChart.values, null, 2)}
              </pre>
            </div>
          )}

          {conditions.length > 0 && <ConditionList conditions={conditions} />}
        </div>
      )}
    </div>
  );
}

// ── DPUDeployment Card ────────────────────────────────────────────────────

function DPUDeploymentCard({ deployment }: { deployment: DpfDpuDeployment }) {
  const [expanded, setExpanded] = useState(false);
  const spec = deployment.spec;
  const conditions = deployment.status?.conditions ?? [];
  const ready = isReady(conditions);
  const variant = readyBadgeVariant(ready);

  const serviceNames = Object.keys(spec?.services ?? {});
  const dpuSetNames = Object.keys(spec?.dpuSets ?? {});
  const chainNames = Object.keys(spec?.serviceChains ?? {});

  return (
    <div className="rounded-lg border border-border bg-card">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center w-full gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/50"
      >
        {expanded
          ? <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
          : <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
        }
        <Container className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="font-medium text-sm text-foreground">
          {deployment.metadata.name}
        </span>
        {deployment.metadata.namespace && (
          <span className="text-xs text-muted-foreground">
            {deployment.metadata.namespace}
          </span>
        )}

        <div className="flex items-center gap-1.5">
          {serviceNames.length > 0 && (
            <Badge variant="outline" className="text-[10px] px-1.5 py-0">
              {serviceNames.length} svc{serviceNames.length !== 1 ? 's' : ''}
            </Badge>
          )}
          {dpuSetNames.length > 0 && (
            <Badge variant="outline" className="text-[10px] px-1.5 py-0">
              {dpuSetNames.length} set{dpuSetNames.length !== 1 ? 's' : ''}
            </Badge>
          )}
        </div>

        <div className="flex-1" />

        <Badge variant={variant} className="text-[10px] px-2 py-0">
          {ready ? 'Ready' : 'Not Ready'}
        </Badge>
      </button>

      {expanded && (
        <div className="px-4 pb-4 pl-12 space-y-3 border-t border-border bg-muted/50">
          {/* Services within deployment */}
          {serviceNames.length > 0 && (
            <div className="pt-3">
              <h5 className="text-[10px] uppercase tracking-wider mb-1.5 text-muted-foreground">
                Services
              </h5>
              <div className="space-y-1">
                {serviceNames.map((name) => {
                  const svc = spec!.services![name];
                  return (
                    <div
                      key={name}
                      className="flex items-center gap-3 rounded px-3 py-1.5 text-xs bg-card"
                    >
                      <Package className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                      <span className="font-medium text-foreground/80">
                        {name}
                      </span>
                      {svc.helmChart?.source?.repoURL && (
                        <span className="font-mono truncate text-muted-foreground">
                          {svc.helmChart.source.repoURL}
                        </span>
                      )}
                      {svc.helmChart?.source?.version && (
                        <Badge variant="outline" className="text-[10px] px-1.5 py-0">
                          v{svc.helmChart.source.version}
                        </Badge>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* DPUSets within deployment */}
          {dpuSetNames.length > 0 && (
            <div>
              <h5 className="text-[10px] uppercase tracking-wider mb-1.5 text-muted-foreground">
                DPU Sets
              </h5>
              <div className="space-y-1">
                {dpuSetNames.map((name) => {
                  const set = spec!.dpuSets![name];
                  return (
                    <div
                      key={name}
                      className="flex items-center gap-3 rounded px-3 py-1.5 text-xs bg-card"
                    >
                      <Layers className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                      <span className="font-medium text-foreground/80">
                        {name}
                      </span>
                      {set.dpuTemplate?.spec?.bfb?.name && (
                        <span className="text-muted-foreground">
                          BFB: {set.dpuTemplate.spec.bfb.name}
                        </span>
                      )}
                      {set.dpuTemplate?.spec?.dpuFlavor && (
                        <span className="text-muted-foreground">
                          Flavor: {set.dpuTemplate.spec.dpuFlavor}
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Service chains within deployment */}
          {chainNames.length > 0 && (
            <div>
              <h5 className="text-[10px] uppercase tracking-wider mb-1.5 text-muted-foreground">
                Service Chains
              </h5>
              <div className="space-y-2">
                {chainNames.map((name) => {
                  const chain = spec!.serviceChains![name];
                  return (
                    <div key={name}>
                      <div className="text-xs font-medium mb-1 text-foreground/80">
                        {name}
                      </div>
                      <SwitchDiagram switches={chain.switches ?? []} />
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {conditions.length > 0 && <ConditionList conditions={conditions} />}
        </div>
      )}
    </div>
  );
}

// ── DPUServiceChain Card ──────────────────────────────────────────────────

function ServiceChainCard({ chain }: { chain: DpfDpuServiceChain }) {
  const [expanded, setExpanded] = useState(false);
  const conditions = chain.status?.conditions ?? [];
  const ready = isReady(conditions);
  const variant = readyBadgeVariant(ready);
  const switches = chain.spec?.template?.spec?.switches ?? [];

  return (
    <div className="rounded-lg border border-border bg-card">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center w-full gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/50"
      >
        {expanded
          ? <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
          : <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
        }
        <GitBranch className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="font-medium text-sm text-foreground">
          {chain.metadata.name}
        </span>
        {chain.metadata.namespace && (
          <span className="text-xs text-muted-foreground">
            {chain.metadata.namespace}
          </span>
        )}

        {switches.length > 0 && (
          <Badge variant="outline" className="text-[10px] px-1.5 py-0">
            {switches.length} switch{switches.length !== 1 ? 'es' : ''}
          </Badge>
        )}

        <div className="flex-1" />

        <Badge variant={variant} className="text-[10px] px-2 py-0">
          {ready ? 'Ready' : 'Not Ready'}
        </Badge>
      </button>

      {expanded && (
        <div className="px-4 pb-4 pl-12 space-y-3 border-t border-border bg-muted/50">
          {switches.length > 0 && (
            <div className="pt-3">
              <SwitchDiagram switches={switches} />
            </div>
          )}
          {conditions.length > 0 && <ConditionList conditions={conditions} />}
        </div>
      )}
    </div>
  );
}

// ── DPUServiceInterface Card ──────────────────────────────────────────────

function ServiceInterfaceCard({ iface }: { iface: DpfDpuServiceInterface }) {
  const spec = iface.spec?.template?.spec;
  const conditions = iface.status?.conditions ?? [];
  const ready = isReady(conditions);
  const variant = readyBadgeVariant(ready);

  return (
    <div className="rounded-lg border p-4 border-border bg-card">
      <div className="flex items-center gap-3">
        <Plug className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="font-medium text-sm text-foreground">
          {iface.metadata.name}
        </span>
        {iface.metadata.namespace && (
          <span className="text-xs text-muted-foreground">
            {iface.metadata.namespace}
          </span>
        )}
        <div className="flex-1" />
        <Badge variant={variant} className="text-[10px] px-2 py-0">
          {ready ? 'Ready' : 'Not Ready'}
        </Badge>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-1 mt-3 text-xs">
        <DetailField label="Interface Type" value={spec?.interfaceType} />
        <DetailField label="Network" value={spec?.network?.name} />
        <DetailField label="Network NS" value={spec?.network?.namespace} />
        {spec?.vlan?.vlanId != null && (
          <DetailField label="VLAN ID" value={String(spec.vlan.vlanId)} />
        )}
      </div>

      {conditions.length > 0 && (
        <div className="mt-3">
          <ConditionList conditions={conditions} compact />
        </div>
      )}
    </div>
  );
}

// ── Realized Chains Summary ───────────────────────────────────────────────

function RealizedChainsSummary({
  chains,
  interfaces,
}: {
  chains: DpfServiceChain[];
  interfaces: DpfServiceInterface[];
}) {
  // Group by node
  const byNode = useMemo(() => {
    const map = new Map<string, { chains: DpfServiceChain[]; interfaces: DpfServiceInterface[] }>();
    for (const chain of chains) {
      const node = chain.spec?.node ?? 'unknown';
      const entry = map.get(node) ?? { chains: [], interfaces: [] };
      entry.chains.push(chain);
      map.set(node, entry);
    }
    for (const iface of interfaces) {
      const node = iface.spec?.node ?? 'unknown';
      const entry = map.get(node) ?? { chains: [], interfaces: [] };
      entry.interfaces.push(iface);
      map.set(node, entry);
    }
    return map;
  }, [chains, interfaces]);

  const [expandedNode, setExpandedNode] = useState<string | null>(null);

  return (
    <div className="space-y-2">
      {Array.from(byNode.entries()).map(([node, data]) => (
        <div
          key={node}
          className="rounded-lg border border-border bg-card"
        >
          <button
            onClick={() => setExpandedNode(expandedNode === node ? null : node)}
            className="flex items-center w-full gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/50"
          >
            {expandedNode === node
              ? <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
              : <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
            }
            <Network className="h-4 w-4 shrink-0 text-muted-foreground" />
            <span className="font-medium text-sm text-foreground">
              {node}
            </span>
            <div className="flex items-center gap-1.5">
              <Badge variant="outline" className="text-[10px] px-1.5 py-0">
                {data.chains.length} chain{data.chains.length !== 1 ? 's' : ''}
              </Badge>
              <Badge variant="outline" className="text-[10px] px-1.5 py-0">
                {data.interfaces.length} iface{data.interfaces.length !== 1 ? 's' : ''}
              </Badge>
            </div>
          </button>

          {expandedNode === node && (
            <div className="px-4 pb-4 pl-12 space-y-3 border-t border-border bg-muted/50">
              {data.chains.map((chain) => (
                <div key={chain.metadata.uid ?? chain.metadata.name} className="pt-2">
                  <div className="text-xs font-medium mb-1 text-foreground/80">
                    <GitBranch className="inline h-3 w-3 mr-1 -mt-0.5" />
                    {chain.metadata.name}
                  </div>
                  <SwitchDiagram switches={chain.spec?.switches ?? []} />
                </div>
              ))}

              {data.interfaces.length > 0 && (
                <div>
                  <h5 className="text-[10px] uppercase tracking-wider mb-1.5 text-muted-foreground">
                    Interfaces
                  </h5>
                  <div className="space-y-1">
                    {data.interfaces.map((iface) => (
                      <div
                        key={iface.metadata.uid ?? iface.metadata.name}
                        className="flex items-center gap-3 rounded px-3 py-1.5 text-xs bg-card"
                      >
                        <Plug className="h-3 w-3 shrink-0 text-muted-foreground" />
                        <span className="font-medium text-foreground/80">
                          {iface.metadata.name}
                        </span>
                        {iface.spec?.interfaceType && (
                          <Badge variant="outline" className="text-[10px] px-1.5 py-0">
                            {iface.spec.interfaceType}
                          </Badge>
                        )}
                        {iface.spec?.serviceName && (
                          <span className="text-muted-foreground">
                            → {iface.spec.serviceName}
                          </span>
                        )}
                        {iface.status?.interfaceName && (
                          <span className="font-mono text-muted-foreground">
                            ({iface.status.interfaceName})
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ── Empty State ───────────────────────────────────────────────────────────

function EmptyServices() {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-border bg-muted/50 p-10 text-center">
      <Package className="h-10 w-10 mb-3 text-muted-foreground" />
      <h3 className="text-base font-semibold mb-1 text-foreground/80">
        No Service Resources
      </h3>
      <p className="text-sm max-w-md text-muted-foreground">
        No DPU Services, Deployments, or Service Chains found.
        Deploy a DPUService to begin running workloads on DPU nodes.
      </p>
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────────

export function DPUServicesTab({
  services,
  deployments,
  serviceChains,
  serviceInterfaces,
  realizedChains,
  realizedInterfaces,
  isLoading,
}: DPUServicesTabProps) {
  if (isLoading) {
    return (
      <div className="space-y-4">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="h-20 rounded-lg animate-pulse bg-muted/50" />
        ))}
      </div>
    );
  }

  const totalResources =
    services.length +
    deployments.length +
    serviceChains.length +
    serviceInterfaces.length +
    realizedChains.length +
    realizedInterfaces.length;

  if (totalResources === 0) {
    return <EmptyServices />;
  }

  return (
    <div className="space-y-6">
      {/* DPU Services */}
      {services.length > 0 && (
        <div>
          <SectionHeader icon={Package} title="DPU Services" count={services.length} />
          <div className="space-y-3">
            {services.map((svc) => (
              <DPUServiceCard key={svc.metadata.uid ?? svc.metadata.name} service={svc} />
            ))}
          </div>
        </div>
      )}

      {/* DPU Deployments */}
      {deployments.length > 0 && (
        <div>
          <SectionHeader icon={Container} title="DPU Deployments" count={deployments.length} />
          <div className="space-y-3">
            {deployments.map((dep) => (
              <DPUDeploymentCard key={dep.metadata.uid ?? dep.metadata.name} deployment={dep} />
            ))}
          </div>
        </div>
      )}

      {/* DPU Service Chains */}
      {serviceChains.length > 0 && (
        <div>
          <SectionHeader icon={GitBranch} title="Service Chains" count={serviceChains.length} />
          <div className="space-y-3">
            {serviceChains.map((chain) => (
              <ServiceChainCard key={chain.metadata.uid ?? chain.metadata.name} chain={chain} />
            ))}
          </div>
        </div>
      )}

      {/* DPU Service Interfaces */}
      {serviceInterfaces.length > 0 && (
        <div>
          <SectionHeader icon={Plug} title="Service Interfaces" count={serviceInterfaces.length} />
          <div className="space-y-3">
            {serviceInterfaces.map((iface) => (
              <ServiceInterfaceCard key={iface.metadata.uid ?? iface.metadata.name} iface={iface} />
            ))}
          </div>
        </div>
      )}

      {/* Realized per-DPU chains & interfaces */}
      {(realizedChains.length > 0 || realizedInterfaces.length > 0) && (
        <div>
          <SectionHeader
            icon={Network}
            title="Per-DPU Realized Chains"
            count={realizedChains.length + realizedInterfaces.length}
          />
          <RealizedChainsSummary
            chains={realizedChains}
            interfaces={realizedInterfaces}
          />
        </div>
      )}
    </div>
  );
}
