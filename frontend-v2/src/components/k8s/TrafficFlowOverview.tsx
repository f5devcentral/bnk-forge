/**
 * TrafficFlowOverview — TOPO-004 (tuned)
 *
 * Left-to-right traffic flow visualization showing how BNK resources connect:
 * Gateways → Listeners → Routes → Backends
 *
 * Design goals (TOPO-004-TUNE):
 * - Clean first impression: compact summary chips at top, no wall of text
 * - Visually distinct from Gateway Topology: pipeline-style colored stages
 * - Clickable resource names that open the detail panel
 * - Collapsible infrastructure section (secondary information)
 * - "+N more" is interactive — expands the list
 *
 * Data source: useBnkData() — no new backend endpoints needed.
 */

import { useMemo, useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import { Badge, type BadgeProps } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useBnkData } from '@/hooks/k8s/useBnk';

import type {
  TopologyGateway,
  TopologyDataPlane,
  TopologyCounts,
  TopologyEgress,
  BnkBackendEntry,
  BnkTrafficStatsResponse,
} from '@/types/f5bnk';
import {
  Globe,
  Server,
  Route,
  Shield,
  Code,
  Activity,
  Network,
  Cpu,
  ChevronDown,
  ChevronRight,
  RefreshCw,
  AlertTriangle,
  Loader2,
  Wifi,
  Layers,
  ArrowRightLeft,
  Boxes,
} from 'lucide-react';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface TrafficFlowOverviewProps {
  clusterId: number;
  namespace?: string;
  onSelectResource?: (selection: { kind: string; name: string; namespace: string }) => void;
  onNavigateView?: (viewKey: string) => void;
}

interface GatewayFlowData {
  gateway: TopologyGateway;
  listenerCount: number;
  routeCount: number;
  backendNames: Set<string>;
  securityPolicyCount: number;
  firewallRuleCount: number;
  networkPolicyCount: number;
  iRuleCount: number;
  analyzerCount: number;
  analyzedRouteNames: string[];
}

// ---------------------------------------------------------------------------
// Data helpers — pure functions
// ---------------------------------------------------------------------------

function buildGatewayFlowData(topology: TopologyGateway[]): GatewayFlowData[] {
  return topology.map(gw => {
    const backendNames = new Set<string>();
    let routeCount = 0;
    let networkPolicyCount = 0;
    let iRuleCount = 0;
    let analyzerCount = 0;
    const analyzedRouteNames: string[] = [];

    for (const listener of gw.listeners) {
      routeCount += listener.routes.length;
      networkPolicyCount += listener.networkPolicies.length;
      for (const np of listener.networkPolicies) {
        iRuleCount += np.extensions.filter(e => e.kind === 'F5BigCneIrule').length;
      }
      for (const route of listener.routes) {
        for (const backend of route.backends) {
          backendNames.add(backend.namespace ? `${backend.namespace}/${backend.name}` : backend.name);
        }
        if (route.analyzers.length > 0) {
          analyzerCount += route.analyzers.length;
          analyzedRouteNames.push(route.name);
        }
      }
    }

    let firewallRuleCount = 0;
    for (const sp of gw.securityPolicies) {
      for (const fw of sp.firewallPolicies) {
        firewallRuleCount += fw.rules.length;
      }
    }

    return {
      gateway: gw,
      listenerCount: gw.listeners.length,
      routeCount,
      backendNames,
      securityPolicyCount: gw.securityPolicies.length,
      firewallRuleCount,
      networkPolicyCount,
      iRuleCount,
      analyzerCount,
      analyzedRouteNames,
    };
  });
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

/** Compact stat chip for the summary bar — uses token-pure Badge variants */
type StatChipVariant = Extract<BadgeProps['variant'], 'info' | 'success' | 'warning' | 'secondary' | 'destructive' | 'muted'>;

function StatChip({
  icon: Icon,
  value,
  label,
  variant = 'muted',
}: {
  icon: typeof Globe;
  value: number;
  label: string;
  variant?: StatChipVariant;
}) {
  return (
    <Badge variant={variant} className="gap-1.5 px-2.5 py-1 rounded-md font-medium">
      <Icon className="h-3.5 w-3.5" />
      <span className="tabular-nums">{value}</span>
      <span className="opacity-70">{label}</span>
    </Badge>
  );
}

/** Section header for a flow stage — token-pure, distinguished by icon + label, not color */
function StageHeader({
  title,
  icon: Icon,
}: {
  title: string;
  icon: typeof Globe;
}) {
  return (
    <div className="flex items-center gap-1.5 px-2 py-1 rounded-md mb-2 bg-muted/50">
      <Icon className="h-3.5 w-3.5 text-muted-foreground" />
      <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        {title}
      </span>
    </div>
  );
}

/** Flow connector between stages — solid chrome line with arrow */
function FlowConnector() {
  return (
    <div className="flex items-center justify-center px-1.5 shrink-0 self-stretch">
      <div className="h-full w-px relative bg-border">
        <div className="absolute top-1/2 -translate-y-1/2 -right-1 w-0 h-0 border-t-[4px] border-t-transparent border-b-[4px] border-b-transparent border-l-[5px] border-l-border" />
      </div>
    </div>
  );
}

function AttachmentBadge({
  label,
  count,
  icon: Icon,
  variant = 'default',
}: {
  label: string;
  count: number;
  icon: typeof Shield;
  variant?: 'default' | 'empty';
}) {
  if (variant === 'empty') {
    return (
      <Badge variant="muted" className="gap-1 text-[10px] px-1.5 py-0.5 rounded font-normal">
        <Icon className="h-2.5 w-2.5" />
        none
      </Badge>
    );
  }
  return (
    <Badge variant="info" className="gap-1 text-[10px] px-1.5 py-0.5 rounded font-medium">
      <Icon className="h-2.5 w-2.5" />
      {count} {label}
    </Badge>
  );
}

function ClickableName({
  name,
  kind,
  namespace,
  onSelect,
  className: extraClass,
}: {
  name: string;
  kind: string;
  namespace: string;
  onSelect?: (sel: { kind: string; name: string; namespace: string }) => void;
  className?: string;
}) {
  if (!onSelect) {
    return <span className={cn('font-medium text-xs', extraClass)}>{name}</span>;
  }
  return (
    <button
      type="button"
      onClick={() => onSelect({ kind, name, namespace })}
      className={cn(
        'font-medium text-xs hover:underline text-left truncate text-primary hover:text-primary/80',
        extraClass,
      )}
    >
      {name}
    </button>
  );
}

/** "+N more" that expands the list when clicked */
function ShowMoreButton({
  remaining,
  onClick,
}: {
  remaining: number;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="text-[10px] px-2 py-0.5 rounded hover:underline cursor-pointer text-primary hover:text-primary/80"
    >
      +{remaining} more
    </button>
  );
}

// ---------------------------------------------------------------------------
// GatewayFlowRow — one horizontal pipeline per gateway
// ---------------------------------------------------------------------------

const INITIAL_ROUTE_LIMIT = 5;
const INITIAL_BACKEND_LIMIT = 4;

function GatewayFlowRow({
  flow,
  onSelectResource,
  gatewayStatsMap,
}: {
  flow: GatewayFlowData;
  onSelectResource?: (sel: { kind: string; name: string; namespace: string }) => void;
  gatewayStatsMap: Map<string, { totalConns: number; curConns: number }>;
}) {
  const { gateway } = flow;
  const gatewayStats = gatewayStatsMap.get(`${gateway.namespace}/${gateway.name}`);
  const [expandedListeners, setExpandedListeners] = useState<Set<string>>(() =>
    new Set(gateway.listeners.slice(0, 3).map(l => l.name)),
  );
  const [showAllRoutes, setShowAllRoutes] = useState(false);
  const [showAllBackends, setShowAllBackends] = useState(false);

  const toggleListener = useCallback((name: string) => {
    setExpandedListeners(prev => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }, []);

  const allRoutes = useMemo(
    () => gateway.listeners.flatMap(l => l.routes),
    [gateway.listeners],
  );
  const allBackends = useMemo(
    () => Array.from(flow.backendNames),
    [flow.backendNames],
  );
  const visibleRoutes = showAllRoutes ? allRoutes : allRoutes.slice(0, INITIAL_ROUTE_LIMIT);
  const visibleBackends = showAllBackends ? allBackends : allBackends.slice(0, INITIAL_BACKEND_LIMIT);

  return (
    <div className="rounded-lg border overflow-hidden bg-card border-border">
      {/* Gateway header bar */}
      <div className="flex items-center gap-3 px-4 py-3 border-b bg-muted/50 border-border">
        <div className="h-8 w-8 rounded-full flex items-center justify-center shrink-0 bg-primary/10">
          <Globe className="h-4 w-4 text-primary" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <ClickableName
              name={gateway.name}
              kind="Gateway"
              namespace={gateway.namespace}
              onSelect={onSelectResource}
              className="text-sm"
            />
            {gateway.gatewayClassName && (
              <Badge variant="outline" className="text-[10px] py-0">
                {gateway.gatewayClassName}
              </Badge>
            )}
            {gateway.addresses.length > 0 && (
              <Badge variant="secondary" className="text-[10px] py-0 font-mono">
                {gateway.addresses.join(', ')}
              </Badge>
            )}
          </div>
          <div className="text-[11px] mt-0.5 text-muted-foreground">
            {gateway.namespace}
            {flow.securityPolicyCount > 0 || flow.networkPolicyCount > 0 || flow.analyzerCount > 0 ? (
              <span className="ml-2">
                {'· '}
                {flow.securityPolicyCount > 0 && <span>{flow.securityPolicyCount} fw {flow.securityPolicyCount !== 1 ? 'policies' : 'policy'}</span>}
                {flow.securityPolicyCount > 0 && flow.networkPolicyCount > 0 && ', '}
                {flow.networkPolicyCount > 0 && <span>{flow.networkPolicyCount} net {flow.networkPolicyCount !== 1 ? 'policies' : 'policy'}</span>}
                {(flow.securityPolicyCount > 0 || flow.networkPolicyCount > 0) && flow.analyzerCount > 0 && ', '}
                {flow.analyzerCount > 0 && <span>{flow.analyzerCount} AI {flow.analyzerCount !== 1 ? 'analyzers' : 'analyzer'}</span>}
              </span>
            ) : null}
          </div>
        </div>
        {gatewayStats && gatewayStats.totalConns > 0 && (
          <Badge variant="info" className="text-xs gap-1">
            <Activity className="h-3 w-3" />
            {gatewayStats.totalConns} conn{gatewayStats.totalConns !== 1 ? 's' : ''}
            {gatewayStats.curConns > 0 && <span className="opacity-70">({gatewayStats.curConns} active)</span>}
          </Badge>
        )}
      </div>

      {/* Flow pipeline */}
      <div className="flex items-stretch p-4 gap-0 min-h-[120px]">
        {/* Listeners */}
        <div className="w-[25%] min-w-0">
          <StageHeader title="Listeners" icon={Layers} />
          <div className="space-y-1">
            {gateway.listeners.map(listener => (
              <div key={listener.name} className="rounded px-2 py-1.5 text-xs bg-muted/50">
                <button
                  type="button"
                  className="flex items-center gap-1.5 w-full text-left"
                  onClick={() => toggleListener(listener.name)}
                >
                  {expandedListeners.has(listener.name)
                    ? <ChevronDown className="h-3 w-3 shrink-0" />
                    : <ChevronRight className="h-3 w-3 shrink-0" />
                  }
                  <span className="font-medium truncate">{listener.name}</span>
                  <Badge variant="outline" className="text-[9px] py-0 ml-auto shrink-0">
                    {listener.protocol}:{listener.port}
                  </Badge>
                </button>
                {expandedListeners.has(listener.name) && (
                  <div className="mt-1 pl-5 space-y-0.5 text-[11px] text-muted-foreground">
                    <div>{listener.routes.length} route{listener.routes.length !== 1 ? 's' : ''}</div>
                    {listener.networkPolicies.length > 0 && (
                      <div className="flex gap-1 flex-wrap">
                        <AttachmentBadge label="net policy" count={listener.networkPolicies.length} icon={Network} />
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
            {gateway.listeners.length === 0 && (
              <div className="text-xs px-2 py-1 text-muted-foreground">
                no listeners
              </div>
            )}
          </div>
        </div>

        <FlowConnector />

        {/* Routes */}
        <div className="w-[35%] min-w-0">
          <StageHeader title="Routes" icon={Route} />
          <div className="space-y-1">
            {visibleRoutes.map(route => (
              <div key={`${route.namespace}/${route.name}`} className="rounded px-2 py-1.5 text-xs bg-muted/50">
                <div className="flex items-center gap-1.5">
                  <Badge variant="outline" className="text-[9px] py-0 shrink-0">
                    {route.kind.replace('Route', '')}
                  </Badge>
                  <ClickableName
                    name={route.name}
                    kind={route.kind}
                    namespace={route.namespace}
                    onSelect={onSelectResource}
                  />
                </div>
                {route.hostnames.length > 0 && (
                  <div className="text-[10px] mt-0.5 pl-0.5 truncate text-muted-foreground">
                    {route.hostnames.join(', ')}
                  </div>
                )}
                {route.analyzers.length > 0 && (
                  <div className="mt-1">
                    <AttachmentBadge label="AI analyzer" count={route.analyzers.length} icon={Activity} />
                  </div>
                )}
              </div>
            ))}
            {!showAllRoutes && allRoutes.length > INITIAL_ROUTE_LIMIT && (
              <ShowMoreButton
                remaining={allRoutes.length - INITIAL_ROUTE_LIMIT}
                onClick={() => setShowAllRoutes(true)}
              />
            )}
            {allRoutes.length === 0 && (
              <div className="text-xs px-2 py-1 text-muted-foreground">
                no routes
              </div>
            )}
          </div>
        </div>

        <FlowConnector />

        {/* Backends */}
        <div className="w-[25%] min-w-0">
          <StageHeader title="Backends" icon={Server} />
          <div className="space-y-1">
            {visibleBackends.map(name => {
              const parts = name.split('/');
              const svcName = parts.length > 1 ? parts[1] : parts[0];
              const svcNs = parts.length > 1 ? parts[0] : '';
              return (
                <div key={name} className="rounded px-2 py-1.5 text-xs bg-muted/50">
                  <ClickableName
                    name={svcName}
                    kind="Service"
                    namespace={svcNs}
                    onSelect={onSelectResource}
                  />
                  {svcNs && (
                    <div className="text-[10px] truncate text-muted-foreground">
                      {svcNs}
                    </div>
                  )}
                </div>
              );
            })}
            {!showAllBackends && allBackends.length > INITIAL_BACKEND_LIMIT && (
              <ShowMoreButton
                remaining={allBackends.length - INITIAL_BACKEND_LIMIT}
                onClick={() => setShowAllBackends(true)}
              />
            )}
            {allBackends.length === 0 && (
              <div className="text-xs px-2 py-1 text-muted-foreground">
                no backends
              </div>
            )}
          </div>
        </div>

        {/* Security summary — compact sidebar */}
        <div className="w-[15%] ml-3 pl-3 border-l shrink-0 border-border">
          <StageHeader title="Security" icon={Shield} />
          <div className="space-y-1.5">
            {flow.securityPolicyCount > 0
              ? <AttachmentBadge label={`fw polic${flow.securityPolicyCount !== 1 ? 'ies' : 'y'}`} count={flow.securityPolicyCount} icon={Shield} />
              : <AttachmentBadge label="" count={0} icon={Shield} variant="empty" />
            }
            {flow.firewallRuleCount > 0 && (
              <AttachmentBadge label={`rule${flow.firewallRuleCount !== 1 ? 's' : ''}`} count={flow.firewallRuleCount} icon={Shield} />
            )}
            {flow.networkPolicyCount > 0
              ? <AttachmentBadge label={`net polic${flow.networkPolicyCount !== 1 ? 'ies' : 'y'}`} count={flow.networkPolicyCount} icon={Network} />
              : <AttachmentBadge label="" count={0} icon={Network} variant="empty" />
            }
            {flow.iRuleCount > 0 && (
              <AttachmentBadge label={`iRule${flow.iRuleCount !== 1 ? 's' : ''}`} count={flow.iRuleCount} icon={Code} />
            )}
            {flow.analyzerCount > 0 && (
              <AttachmentBadge label="AI analyzer" count={flow.analyzerCount} icon={Activity} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// EgressSection — captured namespaces → TMM SNAT → egress VLAN
// ---------------------------------------------------------------------------

function EgressFlowRow({
  egress,
  onSelectResource,
}: {
  egress: TopologyEgress;
  onSelectResource?: (sel: { kind: string; name: string; namespace: string }) => void;
}) {
  return (
    <div className="rounded-lg border overflow-hidden bg-card border-border">
      <div className="flex items-center gap-3 px-4 py-3 border-b bg-muted/50 border-border">
        <div className="h-8 w-8 rounded-full flex items-center justify-center shrink-0 bg-primary/10">
          <ArrowRightLeft className="h-4 w-4 text-primary" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <ClickableName
              name={egress.name}
              kind="F5SPKEgress"
              namespace={egress.namespace}
              onSelect={onSelectResource}
              className="text-sm"
            />
            {!egress.ready && (
              <Badge variant="warning" className="text-[10px] py-0">
                not ready
              </Badge>
            )}
          </div>
          <div className="text-[11px] mt-0.5 text-muted-foreground">
            {egress.namespace}
          </div>
        </div>
      </div>

      <div className="flex items-stretch p-4 gap-0 min-h-[100px]">
        {/* Captured Namespaces */}
        <div className="w-[35%] min-w-0">
          <StageHeader title="Captured Namespaces" icon={Boxes} />
          <div className="flex flex-wrap gap-1">
            {egress.capturedNamespaces.length > 0 ? (
              egress.capturedNamespaces.map(ns => (
                <Badge key={ns} variant="secondary" className="text-[10px] py-0 font-mono">
                  {ns}
                </Badge>
              ))
            ) : (
              <div className="text-xs px-2 py-1 text-muted-foreground">
                no captured namespaces
              </div>
            )}
          </div>
        </div>

        <FlowConnector />

        {/* TMM SNAT */}
        <div className="w-[35%] min-w-0">
          <StageHeader title={`TMM SNAT: ${egress.snatType || 'unknown'}`} icon={ArrowRightLeft} />
          <div className="flex flex-wrap gap-1">
            {egress.firewallEnforcedPolicy && (
              <Badge variant="info" className="gap-1 text-[10px] px-1.5 py-0.5 rounded font-medium">
                <Shield className="h-2.5 w-2.5" />
                fw: {egress.firewallEnforcedPolicy}
              </Badge>
            )}
            {egress.logProfile && (
              <Badge variant="secondary" className="gap-1 text-[10px] px-1.5 py-0.5 rounded font-medium">
                <Code className="h-2.5 w-2.5" />
                log: {egress.logProfile}
              </Badge>
            )}
            {!egress.firewallEnforcedPolicy && !egress.logProfile && (
              <div className="text-xs px-2 py-1 text-muted-foreground">
                no firewall or log policy attached
              </div>
            )}
          </div>
        </div>

        <FlowConnector />

        {/* Egress VLAN */}
        <div className="w-[30%] min-w-0">
          <StageHeader title="Egress VLAN" icon={Globe} />
          {egress.vxlan?.tmmInterfaceName ? (
            <div className="flex items-center gap-1.5 rounded px-2 py-1.5 text-xs bg-muted/50">
              <Network className="h-3 w-3 shrink-0" />
              <span className="font-mono truncate">{egress.vxlan.tmmInterfaceName}</span>
              <span className="text-muted-foreground shrink-0">{'→'}</span>
              <Globe className="h-3 w-3 shrink-0 text-success" />
              <span className="text-muted-foreground shrink-0">Internet</span>
            </div>
          ) : (
            <div className="text-xs px-2 py-1 text-muted-foreground">
              no VXLAN configured
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function EgressSection({
  egresses,
  onSelectResource,
}: {
  egresses: TopologyEgress[];
  onSelectResource?: (sel: { kind: string; name: string; namespace: string }) => void;
}) {
  if (egresses.length === 0) return null;

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 px-1">
        <ArrowRightLeft className="h-4 w-4 text-success" />
        <span className="text-sm font-medium text-foreground/80">
          Egress (outbound)
        </span>
        <span className="text-xs text-muted-foreground">
          ({egresses.length} configured)
        </span>
      </div>
      {egresses.map(egress => (
        <EgressFlowRow
          key={`${egress.namespace}/${egress.name}`}
          egress={egress}
          onSelectResource={onSelectResource}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// InfrastructureCard — collapsible data plane summary
// ---------------------------------------------------------------------------

function InfrastructureCard({
  dataPlane,
  onSelectResource,
}: {
  dataPlane: TopologyDataPlane | undefined;
  onSelectResource?: (sel: { kind: string; name: string; namespace: string }) => void;
}) {
  const [expanded, setExpanded] = useState(false);

  if (!dataPlane) return null;

  const hasContent = dataPlane.vlans.length > 0 || dataPlane.cneInstances.length > 0 ||
    dataPlane.snatPools.length > 0 || dataPlane.staticRoutes.length > 0;

  if (!hasContent) return null;

  const itemCount = dataPlane.cneInstances.length + dataPlane.vlans.length +
    dataPlane.snatPools.length + dataPlane.staticRoutes.length;

  return (
    <div className="rounded-lg border bg-card border-border">
      <button
        type="button"
        className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-muted/50"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2">
          <Cpu className="h-4 w-4 text-success" />
          <span className="text-sm font-medium text-foreground/80">
            Infrastructure & Data Plane
          </span>
          <span className="text-xs text-muted-foreground">
            ({itemCount} resource{itemCount !== 1 ? 's' : ''})
          </span>
        </div>
        {expanded
          ? <ChevronDown className="h-4 w-4 text-muted-foreground" />
          : <ChevronRight className="h-4 w-4 text-muted-foreground" />
        }
      </button>

      {expanded && (
        <div className="px-4 pb-4">
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-2">
            {dataPlane.cneInstances.map(cne => (
              <div key={cne.name} className="rounded px-2 py-1.5 text-xs bg-muted/50">
                <div className="flex items-center gap-1.5">
                  <Cpu className="h-3 w-3 shrink-0" />
                  <ClickableName name={cne.name} kind="CNEInstance" namespace={cne.namespace} onSelect={onSelectResource} />
                </div>
                <div className="text-[10px] mt-0.5 text-muted-foreground">
                  {cne.phase} · {Object.entries(cne.features).filter(([, v]) => v).length} features
                </div>
              </div>
            ))}
            {dataPlane.vlans.map(vlan => (
              <div key={vlan.name} className="rounded px-2 py-1.5 text-xs bg-muted/50">
                <div className="flex items-center gap-1.5">
                  <Wifi className="h-3 w-3 shrink-0" />
                  <ClickableName name={vlan.name} kind="F5SPKVlan" namespace={vlan.namespace} onSelect={onSelectResource} />
                </div>
                <div className="text-[10px] mt-0.5 text-muted-foreground">
                  {vlan.selfipV4s.join(', ') || 'no self-IPs'} · {vlan.ready ? 'ready' : 'pending'}
                </div>
              </div>
            ))}
            {dataPlane.snatPools.map(sp => (
              <div key={sp.name} className="rounded px-2 py-1.5 text-xs bg-muted/50">
                <div className="flex items-center gap-1.5">
                  <Network className="h-3 w-3 shrink-0" />
                  <ClickableName name={sp.name} kind="F5SPKSnatPool" namespace={sp.namespace} onSelect={onSelectResource} />
                </div>
                <div className="text-[10px] mt-0.5 text-muted-foreground">
                  SNAT · {sp.addresses.length} addr{sp.addresses.length !== 1 ? 's' : ''}
                </div>
              </div>
            ))}
            {dataPlane.staticRoutes.map(sr => (
              <div key={sr.name} className="rounded px-2 py-1.5 text-xs bg-muted/50">
                <div className="flex items-center gap-1.5">
                  <Route className="h-3 w-3 shrink-0" />
                  <ClickableName name={sr.name} kind="F5SPKStaticRoute" namespace={sr.namespace} onSelect={onSelectResource} />
                </div>
                <div className="text-[10px] mt-0.5 text-muted-foreground">
                  {sr.destination} → {sr.gateway}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Unmapped Services — only shown when there are orphan services
// ---------------------------------------------------------------------------

function UnmappedServicesCard({
  backends,
  onSelectResource,
  onNavigateView,
}: {
  backends: BnkBackendEntry[] | undefined;
  onSelectResource?: (sel: { kind: string; name: string; namespace: string }) => void;
  onNavigateView?: (viewKey: string) => void;
}) {
  const unmapped = useMemo(() => backends?.filter(b => !b.mapped) ?? [], [backends]);
  const [expanded, setExpanded] = useState(false);

  if (unmapped.length === 0) return null;

  return (
    <div className="rounded-lg border bg-card border-border border-l-2 border-l-warning">
      <button
        type="button"
        className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-muted/50"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2">
          <AlertTriangle className="h-4 w-4 text-warning" />
          <span className="text-sm font-medium text-foreground/80">
            Unmapped Services
          </span>
          <span className="text-xs text-muted-foreground">
            ({unmapped.length} service{unmapped.length !== 1 ? 's' : ''} not referenced by any route)
          </span>
        </div>
        {expanded
          ? <ChevronDown className="h-4 w-4 text-muted-foreground" />
          : <ChevronRight className="h-4 w-4 text-muted-foreground" />
        }
      </button>
      {expanded && (
        <div className="px-4 pb-3 space-y-2">
          <div className="flex flex-wrap gap-1.5">
            {unmapped.map(b => (
              <span
                key={`${b.namespace}/${b.name}`}
                className="inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded bg-muted/50"
              >
                <ClickableName
                  name={b.name}
                  kind="Service"
                  namespace={b.namespace}
                  onSelect={onSelectResource}
                />
                <span className="opacity-50 text-muted-foreground">
                  {b.namespace} · {b.type}
                </span>
              </span>
            ))}
          </div>
          {onNavigateView && (
            <div className="text-[11px] flex items-center gap-1 text-muted-foreground">
              <span>See full details in</span>
              <button
                type="button"
                onClick={() => onNavigateView('view-backends')}
                className="hover:underline font-medium text-primary hover:text-primary/80"
              >
                Backends
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

export function TrafficFlowOverview({ clusterId, namespace, onSelectResource, onNavigateView }: TrafficFlowOverviewProps) {
  const { data, isLoading, error, refetch, isFetching } = useBnkData(
    clusterId,
    namespace ? { namespace } : undefined,
    { pollingEnabled: false, enabled: !!clusterId },
  );

  const topology = useMemo(() => (data?.topology as TopologyGateway[]) ?? [], [data?.topology]);
  const dataPlane = data?.dataPlane as TopologyDataPlane | undefined;
  const counts = data?.topologyCounts as TopologyCounts | undefined;
  const backends = data?.backends as BnkBackendEntry[] | undefined;
  const trafficStats = data?.trafficStats as BnkTrafficStatsResponse | undefined;

  const flowRows = useMemo(() => buildGatewayFlowData(topology), [topology]);

  const gatewayStatsMap = useMemo(() => {
    const map = new Map<string, { totalConns: number; curConns: number }>();
    if (!trafficStats?.available) return map;
    for (const listener of trafficStats.listeners || []) {
      const key = `${listener.gatewayNamespace}/${listener.gatewayName}`;
      const existing = map.get(key) || { totalConns: 0, curConns: 0 };
      existing.totalConns += listener.clientsideTotConns || 0;
      existing.curConns += listener.clientsideCurConns || 0;
      map.set(key, existing);
    }
    return map;
  }, [trafficStats]);

  // Loading
  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-primary mr-3" />
        <span className="text-sm text-muted-foreground">
          Loading traffic flow…
        </span>
      </div>
    );
  }

  // Error
  if (error) {
    return (
      <div className="rounded-lg border p-6 text-center bg-card border-border border-l-2 border-l-destructive">
        <AlertTriangle className="h-8 w-8 text-destructive mx-auto mb-3" />
        <p className="text-sm font-medium text-destructive">
          Failed to load traffic flow data
        </p>
        <p className="text-xs mt-1 text-muted-foreground">
          {(error as Error).message}
        </p>
        <Button variant="outline" size="sm" className="mt-4" onClick={() => refetch()}>
          <RefreshCw className="h-3.5 w-3.5 mr-1.5" />
          Retry
        </Button>
      </div>
    );
  }

  // Compute summary stats
  const totalRoutes = counts?.totalRoutes ?? flowRows.reduce((n, r) => n + r.routeCount, 0);
  const totalBackends = flowRows.reduce((n, r) => n + r.backendNames.size, 0);
  const totalListeners = counts?.listeners ?? flowRows.reduce((n, r) => n + r.listenerCount, 0);
  const totalPolicies = (counts?.firewallPolicies ?? 0) + (counts?.securityPolicies ?? 0) + (counts?.networkPolicies ?? 0);
  const totalConnections = Array.from(gatewayStatsMap.values()).reduce((n, s) => n + s.totalConns, 0);

  // Empty state — no gateways and no meaningful infrastructure
  const hasInfra = dataPlane && (
    dataPlane.vlans.length > 0 || dataPlane.cneInstances.length > 0 ||
    dataPlane.snatPools.length > 0 || dataPlane.egresses.length > 0 ||
    dataPlane.staticRoutes.length > 0
  );
  if (flowRows.length === 0 && !hasInfra) {
    return (
      <div className="space-y-4">
        {/* Summary bar */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 flex-wrap">
            <StatChip icon={Globe} value={0} label="gateways" variant="info" />
            <StatChip icon={Route} value={0} label="routes" variant="muted" />
          </div>
          <Button variant="ghost" size="sm" onClick={() => refetch()} disabled={isFetching} className="h-7">
            <RefreshCw className={cn('h-3.5 w-3.5 mr-1.5', isFetching && 'animate-spin')} />
            Refresh
          </Button>
        </div>
        <div className="rounded-lg border p-8 text-center bg-card border-border">
          <Globe className="h-10 w-10 mx-auto mb-3 text-muted-foreground" />
          <p className="text-sm font-medium text-foreground/80">
            No BNK gateways configured
          </p>
          <p className="text-xs mt-1 text-muted-foreground">
            Deploy gateways and routes to see the traffic flow visualization
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Summary bar with stat chips */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 flex-wrap">
          <StatChip icon={Globe} value={flowRows.length} label={flowRows.length !== 1 ? 'gateways' : 'gateway'} variant="info" />
          <StatChip icon={Layers} value={totalListeners} label={totalListeners !== 1 ? 'listeners' : 'listener'} variant="muted" />
          <StatChip icon={Route} value={totalRoutes} label={totalRoutes !== 1 ? 'routes' : 'route'} variant="secondary" />
          <StatChip icon={Server} value={totalBackends} label={totalBackends !== 1 ? 'backends' : 'backend'} variant="success" />
          {totalConnections > 0 && (
            <StatChip icon={Activity} value={totalConnections} label={totalConnections !== 1 ? 'connections' : 'connection'} variant="info" />
          )}
          {totalPolicies > 0 && (
            <StatChip icon={Shield} value={totalPolicies} label={totalPolicies !== 1 ? 'policies' : 'policy'} variant="warning" />
          )}
        </div>
        <Button variant="ghost" size="sm" onClick={() => refetch()} disabled={isFetching} className="h-7">
          <RefreshCw className={cn('h-3.5 w-3.5 mr-1.5', isFetching && 'animate-spin')} />
          Refresh
        </Button>
      </div>

      {/* Gateway flow rows — the main visualization */}
      {flowRows.map(flow => (
        <GatewayFlowRow
          key={`${flow.gateway.namespace}/${flow.gateway.name}`}
          flow={flow}
          onSelectResource={onSelectResource}
          gatewayStatsMap={gatewayStatsMap}
        />
      ))}

      {/* Egress (outbound) — only when the cluster has F5SPKEgress resources */}
      {dataPlane && (
        <EgressSection
          egresses={dataPlane.egresses}
          onSelectResource={onSelectResource}
        />
      )}

      {/* Unmapped services — only if there are orphans */}
      <UnmappedServicesCard
        backends={backends}
        onSelectResource={onSelectResource}
        onNavigateView={onNavigateView}
      />

      {/* Infrastructure — collapsible, secondary info */}
      <InfrastructureCard
        dataPlane={dataPlane}
        onSelectResource={onSelectResource}
      />
    </div>
  );
}
