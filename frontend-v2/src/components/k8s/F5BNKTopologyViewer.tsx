/**
 * F5 BNK Gateway Topology Viewer
 *
 * Visualizes the complete BNK object graph:
 * Gateway → Listeners → HTTPRoutes → BNKSecPolicy → FW Rules → Address/Port Lists,
 * BNKNetPolicy → iRules (with event handlers), F5BigAnalyzer → monitored routes.
 *
 * Shows how all BNK resources connect together so the user can understand
 * at a glance how the demo stack is wired up.
 */

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { useF5GatewayTopology } from '@/hooks/useK8s';
import {
  Globe,
  Radio,
  Route,
  Shield,
  ShieldAlert,
  Code,
  Activity,
  Network,
  ChevronDown,
  ChevronRight,
  RefreshCw,
  AlertTriangle,
  Server,
  List,
  Loader2,
  Cpu,
  Wifi,
  FileText,
  ArrowRightLeft,
  CheckCircle2,
  XCircle,
} from 'lucide-react';
import { useState } from 'react';

// ─── Types (matching backend response) ─────────────────────────────────

interface TopologyAnalyzer {
  name: string;
  schedule: string;
  scriptType: string;
  dataSources: string[];
  parameters: Record<string, string>;
}

interface TopologyBackend {
  name: string;
  namespace?: string | null;
  port: number | null;
  weight: number | null;
  kind?: string;
  group?: string;
}

interface TopologyRoute {
  name: string;
  namespace: string;
  kind: string;
  hostnames: string[];
  backends: TopologyBackend[];
  analyzers: TopologyAnalyzer[];
}

interface TopologyExtension {
  kind: string;
  name: string;
  group: string;
  lineCount?: number;
  eventHandlers?: string[];
}

interface TopologyNetPolicy {
  name: string;
  namespace: string;
  extensions: TopologyExtension[];
  resolvedCount: number;
  totalExtensions: number;
}

interface TopologyFwRule {
  name: string;
  action: string;
  ipProtocol: string;
  logging: boolean;
}

interface TopologyAddressList {
  name: string;
  addresses: string[];
}

interface TopologyPortList {
  name: string;
  ports: (string | number)[];
}

interface TopologyFwPolicy {
  name: string;
  rules: TopologyFwRule[];
  addressLists: TopologyAddressList[];
  portLists: TopologyPortList[];
}

interface TopologySecPolicy {
  name: string;
  namespace: string;
  targetListener: string;
  firewallPolicies: TopologyFwPolicy[];
}

interface TopologyListener {
  name: string;
  protocol: string;
  port: number | null;
  routes: TopologyRoute[];
  networkPolicies: TopologyNetPolicy[];
}

interface TopologyGateway {
  name: string;
  namespace: string;
  gatewayClassName: string;
  addresses: string[];
  listeners: TopologyListener[];
  securityPolicies: TopologySecPolicy[];
}

interface TopologyCounts {
  gateways: number;
  listeners: number;
  httpRoutes: number;
  grpcRoutes: number;
  tcpRoutes: number;
  udpRoutes: number;
  tlsRoutes: number;
  l4Routes: number;
  totalRoutes: number;
  referenceGrants: number;
  securityPolicies: number;
  networkPolicies: number;
  firewallPolicies: number;
  iRules: number;
  analyzers: number;
  vlans: number;
  cneInstances: number;
  staticRoutes: number;
  snatPools: number;
  egresses: number;
  hslPublishers: number;
  logProfiles: number;
}

// ─── Data Plane Types ──────────────────────────────────────────────────

interface DataPlaneVlan {
  name: string;
  namespace: string;
  interfaces: string[];
  selfipV4s: string[];
  prefixLen: number | null;
  mtu: number | null;
  internal: boolean;
  autoLasthop: string;
  ready: boolean;
}

interface DataPlaneCNEInstance {
  name: string;
  namespace: string;
  features: Record<string, boolean>;
  networkAttachments: string[];
  containerPlatform: string;
  phase: string;
}

interface DataPlaneStaticRoute {
  name: string;
  namespace: string;
  destination: string;
  gateway: string;
}

interface DataPlaneSnatPool {
  name: string;
  namespace: string;
  addresses: string[];
}

interface DataPlaneEgress {
  name: string;
  namespace: string;
  sourceTranslation: Record<string, unknown>;
}

interface DataPlaneHslPublisher {
  name: string;
  namespace: string;
  pool: Record<string, unknown>;
  protocol: string;
}

interface DataPlaneLogProfile {
  name: string;
  namespace: string;
  publishers: string[];
}

interface DataPlane {
  vlans: DataPlaneVlan[];
  cneInstances: DataPlaneCNEInstance[];
  staticRoutes: DataPlaneStaticRoute[];
  snatPools: DataPlaneSnatPool[];
  egresses: DataPlaneEgress[];
  logging: {
    hslPublishers: DataPlaneHslPublisher[];
    logProfiles: DataPlaneLogProfile[];
  };
}

interface TopologyResponse {
  topology: TopologyGateway[];
  dataPlane: DataPlane;
  counts: TopologyCounts;
  cluster_id: number;
  namespace: string | null;
}

// ─── Resource Selection ────────────────────────────────────────────────

/** Emitted when a user clicks a route or backend in the topology tree */
export interface TopologyResourceSelection {
  kind: string;       // "HTTPRoute", "L4Route", "Gateway", "TCPRoute", etc.
  name: string;
  namespace: string;
}

// ─── Props ─────────────────────────────────────────────────────────────

interface F5BNKTopologyViewerProps {
  clusterId: number;
  namespace?: string;
  /** Called when the user clicks a route or backend in the tree */
  onSelectResource?: (selection: TopologyResourceSelection) => void;
}

// ─── Collapsible Section ───────────────────────────────────────────────

function CollapsibleSection({
  title,
  icon: Icon,
  badge,
  badgeVariant = 'secondary',
  defaultOpen = true,
  children,
  indent = 0,
  onClickTitle,
}: {
  title: string;
  icon: React.ComponentType<{ className?: string }>;
  badge?: string;
  badgeVariant?: 'default' | 'secondary' | 'destructive' | 'outline';
  defaultOpen?: boolean;
  children: React.ReactNode;
  indent?: number;
  /** If provided, clicking the title text (not the chevron) triggers this instead of toggling */
  onClickTitle?: () => void;
}) {
  const [isOpen, setIsOpen] = useState(defaultOpen);
  const isClickable = !!onClickTitle;

  return (
    <div style={{ marginLeft: indent * 16 }}>
      <div className="flex items-center gap-2 w-full py-1.5 px-2 rounded-md text-sm font-medium transition-colors text-left hover:bg-muted/50">
        <button
          onClick={() => setIsOpen(!isOpen)}
          className="flex-shrink-0 p-0.5 -m-0.5 rounded hover:bg-muted transition-colors"
          aria-label={isOpen ? 'Collapse' : 'Expand'}
        >
          {isOpen ? (
            <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
          )}
        </button>
        <Icon className="h-4 w-4 flex-shrink-0" />
        {isClickable ? (
          <button
            onClick={(e) => { e.stopPropagation(); onClickTitle(); }}
            className="truncate text-left transition-colors hover:text-primary hover:underline cursor-pointer"
          >
            {title}
          </button>
        ) : (
          <button
            onClick={() => setIsOpen(!isOpen)}
            className="truncate text-left"
          >
            {title}
          </button>
        )}
        {badge && (
          <Badge variant={badgeVariant} className="ml-auto text-xs">
            {badge}
          </Badge>
        )}
      </div>
      {isOpen && <div className="ml-4 mt-1">{children}</div>}
    </div>
  );
}

// ─── Tree Leaf Node ────────────────────────────────────────────────────

function TreeLeaf({
  icon: Icon,
  label,
  detail,
  badges,
  indent = 0,
  onClick,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  detail?: string;
  badges?: Array<{ text: string; variant?: 'default' | 'secondary' | 'destructive' | 'outline' | 'success' | 'warning' | 'info' | 'muted'; className?: string }>;
  indent?: number;
  onClick?: () => void;
}) {
  const isClickable = !!onClick;

  return (
    <div
      className={cn(
        'flex items-center gap-2 py-1 px-2 text-sm rounded-md text-foreground/80',
        isClickable && 'hover:bg-muted/50 cursor-pointer',
      )}
      style={{ marginLeft: indent * 16 }}
      onClick={onClick}
      role={isClickable ? 'button' : undefined}
      tabIndex={isClickable ? 0 : undefined}
      onKeyDown={isClickable ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick(); } } : undefined}
    >
      <div className="w-3.5 flex-shrink-0" /> {/* Spacer to align with collapsible chevrons */}
      <Icon className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground" />
      <span className={cn(
        'truncate font-medium',
        isClickable && 'hover:text-primary',
      )}>
        {label}
      </span>
      {detail && (
        <span className="text-xs truncate text-muted-foreground">
          {detail}
        </span>
      )}
      {badges && badges.length > 0 && (
        <span className="flex items-center gap-1 ml-auto">
          {badges.map((b, i) => (
            <Badge key={i} variant={b.variant || 'secondary'} className={cn('text-xs', b.className)}>
              {b.text}
            </Badge>
          ))}
        </span>
      )}
    </div>
  );
}

// ─── Summary Cards ─────────────────────────────────────────────────────

function CountCard({
  label,
  count,
  icon: Icon,
}: {
  label: string;
  count: number;
  icon: React.ComponentType<{ className?: string }>;
}) {
  return (
    <div className="flex items-center gap-3 px-4 py-3 rounded-lg border bg-card border-border">
      <div className="h-9 w-9 rounded-lg flex items-center justify-center bg-primary/10">
        <Icon className="h-4 w-4 text-primary" />
      </div>
      <div>
        <div className="text-2xl font-bold">{count}</div>
        <div className="text-xs text-muted-foreground">{label}</div>
      </div>
    </div>
  );
}

// ─── Firewall Action Badge ─────────────────────────────────────────────

function ActionBadge({ action }: { action: string }) {
  const lower = action?.toLowerCase() || '';
  if (lower === 'accept' || lower === 'allow') {
    return <Badge variant="success" className="text-xs">Accept</Badge>;
  }
  if (lower === 'drop' || lower === 'reject' || lower === 'deny') {
    return <Badge variant="destructive" className="text-xs">Drop</Badge>;
  }
  return <Badge variant="secondary" className="text-xs">{action}</Badge>;
}

// ─── Main Component ────────────────────────────────────────────────────

export function F5BNKTopologyViewer({ clusterId, namespace, onSelectResource }: F5BNKTopologyViewerProps) {
  const { data, isLoading, error, refetch, isFetching } = useF5GatewayTopology(
    clusterId,
    namespace ? { namespace } : undefined,
    { pollingEnabled: false, enabled: !!clusterId }
  );

  const topology = (data as TopologyResponse)?.topology || [];
  const dataPlane = (data as TopologyResponse)?.dataPlane;
  const counts = (data as TopologyResponse)?.counts;

  // ── Loading State ──
  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-primary mr-3" />
        <span className="text-sm text-muted-foreground">
          Loading topology…
        </span>
      </div>
    );
  }

  // ── Error State ──
  if (error) {
    return (
      <div className="rounded-lg border-l-2 border-l-destructive border border-border bg-card p-6 text-center">
        <AlertTriangle className="h-8 w-8 text-destructive mx-auto mb-3" />
        <p className="text-sm font-medium text-foreground">
          Failed to load topology
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

  const hasDataPlane = dataPlane && (
    dataPlane.vlans.length > 0 || dataPlane.cneInstances.length > 0 ||
    dataPlane.staticRoutes.length > 0 || dataPlane.snatPools.length > 0 ||
    dataPlane.egresses.length > 0 ||
    dataPlane.logging.hslPublishers.length > 0 || dataPlane.logging.logProfiles.length > 0
  );

  // ── Empty State ──
  if (!topology.length && !hasDataPlane) {
    return (
      <div className="rounded-lg border bg-muted/50 border-border p-8 text-center">
        <Globe className="h-10 w-10 text-muted-foreground mx-auto mb-3" />
        <p className="text-sm font-medium text-foreground/80">
          No BNK Resources Found
        </p>
        <p className="text-xs mt-1 text-muted-foreground">
          Deploy F5 BNK to see the topology tree and data plane configuration.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* ── Summary Stats ── */}
      {counts && (
        <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
          <CountCard label="Gateways" count={counts.gateways} icon={Globe} />
          <CountCard label="Routes" count={counts.totalRoutes} icon={Route} />
          <CountCard label="Policies" count={counts.securityPolicies + counts.networkPolicies} icon={Shield} />
          <CountCard label="iRules" count={counts.iRules} icon={Code} />
          <CountCard label="VLANs" count={counts.vlans} icon={Wifi} />
          <CountCard label="CNE Instances" count={counts.cneInstances} icon={Cpu} />
        </div>
      )}

      {/* ── Refresh Bar ── */}
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">
          Showing complete object graph for {topology.length} gateway{topology.length !== 1 ? 's' : ''}
        </p>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => refetch()}
          disabled={isFetching}
          className="text-xs"
        >
          <RefreshCw className={cn('h-3.5 w-3.5 mr-1.5', isFetching && 'animate-spin')} />
          Refresh
        </Button>
      </div>

      {/* ── Topology Tree ── */}
      {topology.map((gw) => (
        <div
          key={`${gw.namespace}/${gw.name}`}
          className="rounded-lg border overflow-hidden bg-card border-border"
        >
          {/* Gateway Header */}
          <div className="px-4 py-3 border-b flex items-center gap-3 bg-muted/50 border-border">
            <div className="h-8 w-8 rounded-lg flex items-center justify-center bg-primary/10">
              <Globe className="h-4 w-4 text-primary" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                {onSelectResource ? (
                  <button
                    onClick={() => onSelectResource({ kind: 'Gateway', name: gw.name, namespace: gw.namespace })}
                    className="font-semibold text-sm truncate transition-colors hover:text-primary hover:underline cursor-pointer"
                  >
                    {gw.name}
                  </button>
                ) : (
                  <span className="font-semibold text-sm truncate">{gw.name}</span>
                )}
                <Badge variant="outline" className="text-xs">
                  {gw.gatewayClassName}
                </Badge>
              </div>
              <div className="text-xs text-muted-foreground">
                {gw.namespace}
                {gw.addresses.length > 0 && (
                  <span className="ml-2 font-mono">VIP: {gw.addresses.join(', ')}</span>
                )}
              </div>
            </div>
            <div className="flex items-center gap-1.5">
              <Badge variant="secondary" className="text-xs">
                {gw.listeners.length} listener{gw.listeners.length !== 1 ? 's' : ''}
              </Badge>
              <Badge variant="secondary" className="text-xs">
                {gw.listeners.reduce((sum, l) => sum + l.routes.length, 0)} route{gw.listeners.reduce((sum, l) => sum + l.routes.length, 0) !== 1 ? 's' : ''}
              </Badge>
            </div>
          </div>

          {/* Topology Tree */}
          <div className="p-3 space-y-0.5">
            {/* ── Listeners ── */}
            {gw.listeners.map((listener) => (
              <CollapsibleSection
                key={listener.name}
                title={listener.name}
                icon={Radio}
                badge={`${listener.protocol} :${listener.port}`}
              >
                {/* ── Routes (HTTP, GRPC, TCP, UDP, TLS, L4) ── */}
                {listener.routes.map((route) => (
                  <CollapsibleSection
                    key={`${route.namespace}/${route.name}`}
                    title={route.name}
                    icon={Route}
                    badge={route.kind !== 'HTTPRoute' ? route.kind : undefined}
                    indent={1}
                    defaultOpen={route.analyzers.length > 0}
                    onClickTitle={onSelectResource ? () => onSelectResource({ kind: route.kind, name: route.name, namespace: route.namespace }) : undefined}
                  >
                    {/* Route namespace (shown when different from gateway) */}
                    {route.namespace && (
                      <div
                        className="flex items-center gap-1.5 px-2 py-0.5 text-xs text-muted-foreground"
                        style={{ marginLeft: 32 }}
                      >
                        ns: <span className="font-mono">{route.namespace}</span>
                        {route.hostnames.length > 0 && (
                          <span className="ml-2">hosts: {route.hostnames.join(', ')}</span>
                        )}
                      </div>
                    )}

                    {/* Backends */}
                    {route.backends.map((be, i) => {
                      const isCrossNs = be.namespace && be.namespace !== route.namespace;
                      const isCustomKind = be.kind && be.kind !== 'Service';
                      const badges: Array<{ text: string; variant?: 'default' | 'secondary' | 'destructive' | 'outline' | 'success' | 'warning' | 'info' | 'muted'; className?: string }> = [];
                      if (isCrossNs) {
                        badges.push({ text: `ns:${be.namespace}`, variant: 'warning' });
                      }
                      if (isCustomKind) {
                        badges.push({ text: be.kind!, variant: 'outline' });
                      }
                      if (be.weight) {
                        badges.push({ text: `weight ${be.weight}` });
                      }
                      return (
                        <TreeLeaf
                          key={i}
                          icon={Server}
                          label={be.name}
                          detail={be.port ? `:${be.port}` : undefined}
                          badges={badges.length > 0 ? badges : undefined}
                          indent={2}
                        />
                      );
                    })}

                    {/* AI Analyzers attached to this route */}
                    {route.analyzers.map((az) => (
                      <CollapsibleSection
                        key={az.name}
                        title={az.name}
                        icon={Activity}
                        badge="AI Analyzer"
                        badgeVariant="default"
                        indent={2}
                        defaultOpen={false}
                      >
                        <TreeLeaf
                          icon={Activity}
                          label="Schedule"
                          detail={az.schedule || 'N/A'}
                          indent={3}
                        />
                        {az.dataSources.map((ds, i) => (
                          <TreeLeaf
                            key={i}
                            icon={Activity}
                            label="DataSource"
                            detail={ds}
                            indent={3}
                          />
                        ))}
                        {Object.entries(az.parameters).map(([key, val]) => (
                          <TreeLeaf
                            key={key}
                            icon={Activity}
                            label={key}
                            detail={String(val)}
                            indent={3}
                          />
                        ))}
                      </CollapsibleSection>
                    ))}
                  </CollapsibleSection>
                ))}

                {listener.routes.length === 0 && (
                  <TreeLeaf
                    icon={Route}
                    label="No routes"
                    detail="No routes targeting this listener"
                    indent={1}
                  />
                )}

                {/* ── Network Policies (per-listener) ── */}
                {listener.networkPolicies.map((np) => (
                  <CollapsibleSection
                    key={np.name}
                    title={np.name}
                    icon={Network}
                    badge="NetPolicy"
                    indent={1}
                    defaultOpen={true}
                    onClickTitle={onSelectResource ? () => onSelectResource({ kind: 'BNKNetPolicy', name: np.name, namespace: np.namespace }) : undefined}
                  >
                    {np.extensions.map((ext, i) => {
                      if (ext.kind === 'F5BigCneIrule') {
                        return (
                          <TreeLeaf
                            key={i}
                            icon={Code}
                            label={ext.name}
                            detail={ext.lineCount ? `${ext.lineCount} lines` : undefined}
                            badges={[
                              ...(ext.eventHandlers || []).map((eh) => ({
                                text: eh,
                                variant: (eh.includes('REQUEST')
                                  ? 'info'
                                  : eh.includes('RESPONSE')
                                    ? 'secondary'
                                    : 'secondary') as 'info' | 'secondary',
                              })),
                            ]}
                            indent={2}
                            onClick={onSelectResource ? () => onSelectResource({ kind: ext.kind, name: ext.name, namespace: np.namespace }) : undefined}
                          />
                        );
                      }
                      return (
                        <TreeLeaf
                          key={i}
                          icon={Code}
                          label={ext.name}
                          detail={ext.kind}
                          indent={2}
                          onClick={onSelectResource ? () => onSelectResource({ kind: ext.kind, name: ext.name, namespace: np.namespace }) : undefined}
                        />
                      );
                    })}
                    {np.extensions.length === 0 && (
                      <TreeLeaf
                        icon={Code}
                        label="No extensions"
                        indent={2}
                      />
                    )}
                    {np.totalExtensions > 0 && (
                      <div
                        className="text-xs px-2 py-0.5 ml-8 text-muted-foreground"
                        style={{ marginLeft: 48 }}
                      >
                        {np.resolvedCount}/{np.totalExtensions} resolved
                      </div>
                    )}
                  </CollapsibleSection>
                ))}
              </CollapsibleSection>
            ))}

            {/* ── Security Policies (gateway-level) ── */}
            {gw.securityPolicies.length > 0 && (
              <div className="mt-2 pt-2 border-t border-border">
                {gw.securityPolicies.map((sp) => (
                  <CollapsibleSection
                    key={sp.name}
                    title={sp.name}
                    icon={ShieldAlert}
                    badge={sp.targetListener ? `→ ${sp.targetListener}` : 'All Listeners'}
                    onClickTitle={onSelectResource ? () => onSelectResource({ kind: 'BNKSecPolicy', name: sp.name, namespace: sp.namespace }) : undefined}
                  >
                    {sp.firewallPolicies.map((fw) => (
                      <CollapsibleSection
                        key={fw.name}
                        title={fw.name}
                        icon={Shield}
                        badge={`${fw.rules.length} rule${fw.rules.length !== 1 ? 's' : ''}`}
                        indent={1}
                      >
                        {/* Firewall Rules */}
                        {fw.rules.map((rule, i) => (
                          <div
                            key={i}
                            className="flex items-center gap-2 py-1 px-2 text-sm rounded-md text-foreground/80"
                            style={{ marginLeft: 32 }}
                          >
                            <div className="w-3.5 flex-shrink-0" />
                            <Shield className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground" />
                            <span className="font-medium truncate">{rule.name}</span>
                            <ActionBadge action={rule.action} />
                            {rule.ipProtocol && (
                              <Badge variant="outline" className="text-xs">
                                {rule.ipProtocol}
                              </Badge>
                            )}
                            {rule.logging && (
                              <Badge variant="secondary" className="text-xs">
                                Log
                              </Badge>
                            )}
                          </div>
                        ))}

                        {/* Address Lists */}
                        {fw.addressLists.length > 0 && (
                          <div className="mt-1">
                            {fw.addressLists.map((al) => (
                              <CollapsibleSection
                                key={al.name}
                                title={al.name}
                                icon={List}
                                badge={`${al.addresses.length} addr`}
                                indent={2}
                                defaultOpen={false}
                              >
                                <div className="text-xs font-mono px-2 py-1 rounded ml-12 text-muted-foreground bg-muted/50">
                                  {al.addresses.join(', ')}
                                </div>
                              </CollapsibleSection>
                            ))}
                          </div>
                        )}

                        {/* Port Lists */}
                        {fw.portLists.length > 0 && (
                          <div className="mt-1">
                            {fw.portLists.map((pl) => (
                              <CollapsibleSection
                                key={pl.name}
                                title={pl.name}
                                icon={List}
                                badge={`${pl.ports.length} port${pl.ports.length !== 1 ? 's' : ''}`}
                                indent={2}
                                defaultOpen={false}
                              >
                                <div className="text-xs font-mono px-2 py-1 rounded ml-12 text-muted-foreground bg-muted/50">
                                  {pl.ports.join(', ')}
                                </div>
                              </CollapsibleSection>
                            ))}
                          </div>
                        )}
                      </CollapsibleSection>
                    ))}

                    {sp.firewallPolicies.length === 0 && (
                      <TreeLeaf
                        icon={Shield}
                        label="No firewall policies"
                        detail="Policy ref not resolved"
                        indent={1}
                      />
                    )}
                  </CollapsibleSection>
                ))}
              </div>
            )}
          </div>
        </div>
      ))}

      {/* ── Data Plane Section ── */}
      {hasDataPlane && (
        <div className="rounded-lg border overflow-hidden bg-card border-border">
          {/* Data Plane Header */}
          <div className="px-4 py-3 border-b flex items-center gap-3 bg-muted/50 border-border">
            <div className="h-8 w-8 rounded-lg flex items-center justify-center bg-success/10">
              <Cpu className="h-4 w-4 text-success" />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="font-semibold text-sm">Data Plane</span>
                <Badge variant="outline" className="text-xs">TMM Networking</Badge>
              </div>
              <div className="text-xs text-muted-foreground">
                VLANs, interfaces, self-IPs, and CNE instance configuration
              </div>
            </div>
            <div className="flex items-center gap-1.5">
              {counts && counts.vlans > 0 && (
                <Badge variant="secondary" className="text-xs">
                  {counts.vlans} VLAN{counts.vlans !== 1 ? 's' : ''}
                </Badge>
              )}
              {counts && counts.cneInstances > 0 && (
                <Badge variant="secondary" className="text-xs">
                  {counts.cneInstances} CNE
                </Badge>
              )}
            </div>
          </div>

          <div className="p-3 space-y-0.5">
            {/* ── CNE Instances ── */}
            {dataPlane!.cneInstances.map((cne) => (
              <CollapsibleSection
                key={cne.name}
                title={cne.name}
                icon={Cpu}
                badge={cne.phase || 'Unknown'}
                onClickTitle={onSelectResource ? () => onSelectResource({ kind: 'CNEInstance', name: cne.name, namespace: cne.namespace }) : undefined}
              >
                {/* Network Attachments */}
                {cne.networkAttachments.length > 0 && (
                  <CollapsibleSection
                    title="Network Attachments"
                    icon={Network}
                    badge={`${cne.networkAttachments.length}`}
                    indent={1}
                    defaultOpen={true}
                  >
                    {cne.networkAttachments.map((na, i) => (
                      <TreeLeaf
                        key={i}
                        icon={Network}
                        label={na}
                        detail={`→ interface 1.${i + 1}`}
                        indent={2}
                      />
                    ))}
                  </CollapsibleSection>
                )}

                {/* Feature Toggles */}
                <CollapsibleSection
                  title="Feature Toggles"
                  icon={Server}
                  badge={`${Object.values(cne.features).filter(Boolean).length}/${Object.keys(cne.features).length} enabled`}
                  indent={1}
                  defaultOpen={false}
                >
                  {Object.entries(cne.features).map(([key, enabled]) => (
                    <div
                      key={key}
                      className="flex items-center gap-2 py-0.5 px-2 text-sm text-foreground/80"
                      style={{ marginLeft: 32 }}
                    >
                      <div className="w-3.5 flex-shrink-0" />
                      {enabled ? (
                        <CheckCircle2 className="h-3.5 w-3.5 flex-shrink-0 text-success" />
                      ) : (
                        <XCircle className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground" />
                      )}
                      <span className={cn('font-medium', !enabled && 'opacity-50')}>{key}</span>
                      <Badge
                        variant={enabled ? 'success' : 'secondary'}
                        className={cn('text-xs ml-auto', !enabled && 'opacity-50')}
                      >
                        {enabled ? 'Enabled' : 'Disabled'}
                      </Badge>
                    </div>
                  ))}
                </CollapsibleSection>

                {cne.containerPlatform && (
                  <TreeLeaf
                    icon={Server}
                    label="Platform"
                    detail={cne.containerPlatform}
                    indent={1}
                  />
                )}
              </CollapsibleSection>
            ))}

            {/* ── VLANs ── */}
            {dataPlane!.vlans.length > 0 && (
              <div className="mt-2 pt-2 border-t border-border">
                {dataPlane!.vlans.map((vlan) => (
                  <CollapsibleSection
                    key={`${vlan.namespace}/${vlan.name}`}
                    title={`${vlan.name}${vlan.namespace ? ` (${vlan.namespace})` : ''}`}
                    icon={Wifi}
                    badge={vlan.internal ? 'Internal' : 'External'}
                    onClickTitle={onSelectResource ? () => onSelectResource({ kind: 'F5SPKVlan', name: vlan.name, namespace: vlan.namespace }) : undefined}
                  >
                    {/* Self-IPs */}
                    {vlan.selfipV4s.map((ip, i) => (
                      <TreeLeaf
                        key={i}
                        icon={Globe}
                        label={`${ip}${vlan.prefixLen ? `/${vlan.prefixLen}` : ''}`}
                        detail="Self-IP"
                        badges={[{
                          text: vlan.ready ? 'Programmed' : 'Pending',
                          variant: vlan.ready ? 'success' : 'warning',
                        }]}
                        indent={1}
                      />
                    ))}

                    {/* Interfaces */}
                    {vlan.interfaces.map((iface, i) => (
                      <TreeLeaf
                        key={i}
                        icon={Network}
                        label={`Interface ${iface}`}
                        indent={1}
                      />
                    ))}

                    {/* MTU */}
                    {vlan.mtu && (
                      <TreeLeaf
                        icon={ArrowRightLeft}
                        label="MTU"
                        detail={String(vlan.mtu)}
                        indent={1}
                      />
                    )}

                    {/* Auto Lasthop */}
                    {vlan.autoLasthop && (
                      <TreeLeaf
                        icon={ArrowRightLeft}
                        label="Auto Lasthop"
                        detail={vlan.autoLasthop}
                        indent={1}
                      />
                    )}
                  </CollapsibleSection>
                ))}
              </div>
            )}

            {/* ── Static Routes ── */}
            {dataPlane!.staticRoutes.length > 0 && (
              <div className="mt-2 pt-2 border-t border-border">
                <CollapsibleSection
                  title="Static Routes"
                  icon={ArrowRightLeft}
                  badge={`${dataPlane!.staticRoutes.length}`}
                  defaultOpen={false}
                >
                  {dataPlane!.staticRoutes.map((sr) => (
                    <TreeLeaf
                      key={sr.name}
                      icon={ArrowRightLeft}
                      label={sr.destination || sr.name}
                      detail={sr.gateway ? `via ${sr.gateway}` : undefined}
                      indent={1}
                    />
                  ))}
                </CollapsibleSection>
              </div>
            )}

            {/* ── SNAT Pools ── */}
            {dataPlane!.snatPools.length > 0 && (
              <div className="mt-2 pt-2 border-t border-border">
                <CollapsibleSection
                  title="SNAT Pools"
                  icon={Server}
                  badge={`${dataPlane!.snatPools.length}`}
                  defaultOpen={false}
                >
                  {dataPlane!.snatPools.map((sp) => (
                    <TreeLeaf
                      key={sp.name}
                      icon={Server}
                      label={sp.name}
                      detail={sp.addresses.length > 0 ? sp.addresses.join(', ') : 'No members'}
                      indent={1}
                    />
                  ))}
                </CollapsibleSection>
              </div>
            )}

            {/* ── Egress ── */}
            {dataPlane!.egresses.length > 0 && (
              <div className="mt-2 pt-2 border-t border-border">
                <CollapsibleSection
                  title="Egress"
                  icon={ArrowRightLeft}
                  badge={`${dataPlane!.egresses.length}`}
                  defaultOpen={false}
                >
                  {dataPlane!.egresses.map((eg) => (
                    <TreeLeaf
                      key={eg.name}
                      icon={ArrowRightLeft}
                      label={eg.name}
                      indent={1}
                    />
                  ))}
                </CollapsibleSection>
              </div>
            )}

            {/* ── Logging ── */}
            {(dataPlane!.logging.hslPublishers.length > 0 || dataPlane!.logging.logProfiles.length > 0) && (
              <div className="mt-2 pt-2 border-t border-border">
                <CollapsibleSection
                  title="Logging & Telemetry"
                  icon={FileText}
                  badge={`${dataPlane!.logging.hslPublishers.length + dataPlane!.logging.logProfiles.length}`}
                  defaultOpen={false}
                >
                  {dataPlane!.logging.hslPublishers.map((hsl) => (
                    <TreeLeaf
                      key={hsl.name}
                      icon={FileText}
                      label={hsl.name}
                      detail={`HSL Publisher${hsl.protocol ? ` (${hsl.protocol})` : ''}`}
                      indent={1}
                    />
                  ))}
                  {dataPlane!.logging.logProfiles.map((lp) => (
                    <TreeLeaf
                      key={lp.name}
                      icon={FileText}
                      label={lp.name}
                      detail="Log Profile"
                      indent={1}
                    />
                  ))}
                </CollapsibleSection>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
