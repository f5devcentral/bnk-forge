/**
 * BNK Health Dashboard
 *
 * Shows at-a-glance health status for all F5 BNK components:
 *   - Platform:    FLO, Controller, CRD Installer, Analyzer
 *   - Data Plane:  TMM pods, container readiness, CNEInstance features
 *   - Networking:  Gateways, VLANs, Listeners, Routes
 *   - Security:    Firewall policies, iRules, Security/Network policies
 *   - AI:          Analyzers, Intelligent LB status
 *
 * UX-009: Each component now has expandable detail cards with:
 *   - WHY explanations (what happens if this is broken)
 *   - WHAT'S WRONG (pod-level issues)
 *   - Action buttons: View Logs, Restart Pod, Describe
 *
 * Polls every 30 seconds. Color-coded severity (green/amber/red).
 * Warning/Critical cards auto-expand. Sorted by severity.
 */

import { useState, useCallback, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { useF5BNKHealth } from '@/hooks/useK8s';
import { useClusterDriftStatus } from '@/hooks/useDrift';
import { LicenseStatusCard } from '@/components/k8s/LicenseStatusCard';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { HealthDetailCard } from '@/components/health/HealthDetailCard';
import { PodLogsViewer } from '@/components/k8s/PodLogsViewer';
import { ResourceDescribeViewer } from '@/components/k8s/ResourceDescribeViewer';
import type { K8sResource } from '@/types/kubernetes';
import {
  RefreshCw,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Loader2,
  GitCompareArrows,
  Wifi,
  Link2,
} from 'lucide-react';
import type {
  HealthSeverity,
  HealthPodDetail,
  HealthRemediationAction,
  ClusterDriftStatus,
  HealthConnectivityStatus,
  HealthIntegrationStatus,
} from '@/types';
import { SEVERITY_CONFIG, getSeverityConfig, compareSeverity } from '@/lib/health-severity';
import { ErrorState } from '@/components/ui/error-state';
import { parseApiError } from '@/lib/error-handler';

interface BNKHealthDashboardProps {
  clusterId: number;
  namespace?: string;
}

// ---- Severity helpers (use shared config from PLAT-REL-001) ----

// Alias for local usage — same shape, backed by shared SEVERITY_CONFIG.
const severityConfig = SEVERITY_CONFIG;

function SeverityDot({ severity }: { severity: HealthSeverity }) {
  const cfg = getSeverityConfig(severity);
  return (
    <span className={cn('inline-block h-2.5 w-2.5 rounded-full', cfg.dot)} />
  );
}

// ---- Stat row ----

function StatRow({
  label,
  value,
  total,
  suffix,
  severity,
}: {
  label: string;
  value: number | string;
  total?: number | string;
  suffix?: string;
  severity?: HealthSeverity;
}) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-sm text-muted-foreground">{label}</span>
      <div className="flex items-center gap-2">
        {severity && <SeverityDot severity={severity} />}
        <span className="text-sm font-medium tabular-nums">
          {value}{total !== undefined ? `/${total}` : ''}{suffix ? ` ${suffix}` : ''}
        </span>
      </div>
    </div>
  );
}

// ---- Feature toggle badge ----

function FeatureBadge({ label, enabled }: { label: string; enabled: boolean }) {
  return (
    <Badge
      variant={enabled ? 'success' : 'muted'}
      className="text-xs gap-1"
    >
      {enabled ? <CheckCircle2 className="h-3 w-3" /> : <XCircle className="h-3 w-3" />}
      {label}
    </Badge>
  );
}

// ---- Connectivity / integration badges ----

function ConnectivityBadge({ connectivity }: { connectivity: HealthConnectivityStatus }) {
  const statusConfig: Record<string, { variant: 'success' | 'warning' | 'destructive' | 'muted'; icon: typeof Wifi; label: string }> = {
    connected: { variant: 'success', icon: CheckCircle2, label: 'Connected' },
    reachable: { variant: 'warning', icon: Wifi, label: 'Reachable' },
    partial: { variant: 'warning', icon: AlertTriangle, label: 'Partial' },
    unreachable: { variant: 'destructive', icon: XCircle, label: 'Unreachable' },
    unknown: { variant: 'muted', icon: Wifi, label: 'Unknown' },
  };
  const cfg = statusConfig[connectivity.status] || statusConfig.unknown;
  const Icon = cfg.icon;
  return (
    <Badge
      variant={cfg.variant}
      className="text-xs gap-1 font-normal"
      title={connectivity.message}
    >
      <Icon className="h-3 w-3" />
      {cfg.label}
    </Badge>
  );
}

function IntegrationBadge({ integration }: { integration: HealthIntegrationStatus }) {
  const severityConfigMap: Record<string, { variant: 'success' | 'warning' | 'destructive' | 'muted'; icon: typeof Link2; label: string }> = {
    healthy: { variant: 'success', icon: CheckCircle2, label: 'Operator Connected' },
    warning: { variant: 'warning', icon: AlertTriangle, label: 'Operator Disconnected' },
    critical: { variant: 'destructive', icon: XCircle, label: 'Integration Failed' },
    unknown: { variant: 'muted', icon: Link2, label: 'Integration Unknown' },
  };
  const cfg = severityConfigMap[integration.status] || severityConfigMap.unknown;
  const Icon = cfg.icon;
  const label = integration.operatorMode === 'kubeconfig'
    ? 'Kubeconfig'
    : cfg.label;
  return (
    <Badge
      variant={cfg.variant}
      className="text-xs gap-1 font-normal"
      title={integration.message}
    >
      <Icon className="h-3 w-3" />
      {label}
    </Badge>
  );
}

// ---- Component card data builder ----

interface ComponentCardData {
  name: string;
  severity: HealthSeverity;
  summary: string;
  explanation: string;
  podDetails: HealthPodDetail[];
  remediationActions: HealthRemediationAction[];
  namespaces: string[];
  zones: string[];
  nodes: string[];
  children?: React.ReactNode;
}

// ---- Main component ----

export function BNKHealthDashboard({ clusterId, namespace }: BNKHealthDashboardProps) {
  const navigate = useNavigate();
  const {
    data: health,
    isLoading,
    isError,
    error,
    refetch,
    isFetching,
    dataUpdatedAt,
  } = useF5BNKHealth(clusterId, namespace ? { namespace } : undefined, {
    pollingEnabled: true,
  });
  const { data: driftStatus } = useClusterDriftStatus(clusterId);

  // Dialog state for View Logs
  const [logsDialogOpen, setLogsDialogOpen] = useState(false);
  const [logsPod, setLogsPod] = useState<{ name: string; namespace: string } | null>(null);

  // Dialog state for Describe
  const [describeDialogOpen, setDescribeDialogOpen] = useState(false);
  const [describePod, setDescribePod] = useState<{ name: string; namespace: string } | null>(null);

  const handleViewLogs = useCallback((podName: string, ns: string) => {
    setLogsPod({ name: podName, namespace: ns });
    setLogsDialogOpen(true);
  }, []);

  const handleDescribe = useCallback((podName: string, ns: string) => {
    setDescribePod({ name: podName, namespace: ns });
    setDescribeDialogOpen(true);
  }, []);

  // Build component card data from health response
  const componentCards: ComponentCardData[] = useMemo(() => {
    if (!health) return [];

    const cards: ComponentCardData[] = [];

    // Platform components — each gets its own card
    const platformComponents: Array<{
      key: 'flo' | 'controller' | 'crdInstaller' | 'analyzer';
      name: string;
      summaryFn: (comp: Record<string, unknown>) => string;
      show: boolean;
    }> = [
      {
        key: 'flo',
        name: 'FLO Operator',
        summaryFn: (c) => `${c.running ?? 0}/${c.total ?? 0} running`,
        // Only show FLO for a confirmed FLO deploy-flow install. Direct-helm
        // installs genuinely have no FLO/lifecycle-operator pod, and an
        // 'unknown' shape (e.g. transient cluster-connectivity issue) should
        // not flash a misleading permanent 0/0 "unknown" card either.
        show: health.installShape === 'flo',
      },
      {
        key: 'controller',
        name: 'CNE Controller',
        summaryFn: (c) => `${c.running ?? 0}/${c.total ?? 0} running`,
        show: true,
      },
      {
        key: 'crdInstaller',
        name: 'CRD Installer',
        summaryFn: (c) => `${c.completed ?? 0}/${c.total ?? 0} done`,
        // One-shot Job; pods are garbage-collected after the CRDs are
        // installed. Steady-state on a working cluster is 0/0 — hide the card
        // then to avoid signalling "Unknown" when there's nothing to inspect.
        // However, if the Job failed (pods GC'd, severity=critical), show the
        // card so operators can see the failure.
        show: (health.platform?.crdInstaller?.total ?? 0) > 0
          || health.platform?.crdInstaller?.severity === 'critical',
      },
      {
        key: 'analyzer',
        name: 'F5 Analyzer',
        summaryFn: (c) => `${c.running ?? 0}/${c.total ?? 0} running`,
        show: (health.platform?.analyzer?.total ?? 0) > 0,
      },
    ];

    for (const pc of platformComponents) {
      if (!pc.show) continue;
      const comp = health.platform?.[pc.key];
      if (!comp) continue;
      cards.push({
        name: pc.name,
        severity: (comp.severity as HealthSeverity) || 'unknown',
        summary: pc.summaryFn(comp as unknown as Record<string, unknown>),
        explanation: (comp.explanation as string) || '',
        podDetails: (comp.podDetails as HealthPodDetail[]) || [],
        remediationActions: (comp.remediationActions as HealthRemediationAction[]) || [],
        namespaces: (comp.namespaces as string[]) || [],
        zones: (comp.zones as string[]) || [],
        nodes: (comp.nodes as string[]) || [],
      });
    }

    // TMM / Data Plane
    const tmm = health.dataPlane?.tmm;
    if (tmm) {
      const cneInstance = health.dataPlane?.cneInstance;
      const hasCne = cneInstance && 'name' in cneInstance && cneInstance.name;
      cards.push({
        name: 'TMM (Data Plane)',
        severity: (tmm.severity as HealthSeverity) || 'unknown',
        summary: `${tmm.running ?? 0}/${tmm.pods ?? 0} pods running · ${tmm.containersReady ?? 0}/${tmm.containersTotal ?? 0} containers · ${tmm.totalRestarts ?? 0} restarts`,
        explanation: (tmm.explanation as string) || '',
        podDetails: (tmm.podDetails as HealthPodDetail[]) || [],
        remediationActions: (tmm.remediationActions as HealthRemediationAction[]) || [],
        namespaces: (tmm.namespaces as string[]) || [],
        zones: (tmm.zones as string[]) || [],
        nodes: (tmm.nodes as string[]) || [],
        children: hasCne ? (
          <div className="pt-2 border-t border-border">
            <p className="text-xs mb-2 text-muted-foreground">
              CNEInstance: {cneInstance.name as string}
            </p>
            <div className="flex flex-wrap gap-1.5">
              <FeatureBadge label="Firewall" enabled={!!cneInstance.firewallACL} />
              <FeatureBadge label="IntelligentLB" enabled={!!cneInstance.intelligentLB} />
              <FeatureBadge label="PseudoCNI" enabled={!!cneInstance.pseudoCNI} />
              <FeatureBadge label="Metrics" enabled={!!cneInstance.metricSubsystem} />
              <FeatureBadge label="Logging" enabled={!!cneInstance.loggingSubsystem} />
            </div>
          </div>
        ) : undefined,
      });
    }

    // Networking — Gateways
    const gateways = health.networking?.gateways;
    if (gateways && (gateways.total ?? 0) > 0) {
      const addresses = (gateways.addresses as string[]) || [];
      cards.push({
        name: 'Gateways',
        severity: (gateways.severity as HealthSeverity) || 'unknown',
        summary: `${gateways.programmed ?? 0}/${gateways.total ?? 0} programmed${addresses.length ? ` · VIPs: ${addresses.join(', ')}` : ''}`,
        explanation: (gateways.explanation as string) || '',
        podDetails: [],
        remediationActions: [],
        namespaces: [],
        zones: [],
        nodes: [],
        children: (
          <>
            <StatRow label="Listeners" value={health.networking?.listeners ?? 0} />
            <StatRow label="HTTP Routes" value={health.networking?.httpRoutes ?? 0} />
          </>
        ),
      });
    }

    // Networking — VLANs
    const vlans = health.networking?.vlans;
    if (vlans && (vlans.total ?? 0) > 0) {
      cards.push({
        name: 'VLANs',
        severity: (vlans.severity as HealthSeverity) || 'unknown',
        summary: `${vlans.programmed ?? 0}/${vlans.total ?? 0} programmed`,
        explanation: (vlans.explanation as string) || '',
        podDetails: [],
        remediationActions: [],
        namespaces: [],
        zones: [],
        nodes: [],
        children: (
          <>
            {(vlans.details as Array<{ name: string; interfaces: string[]; selfIPs: string[] }>)?.map((vlan) => (
              <div key={vlan.name} className="flex items-center justify-between text-xs text-muted-foreground">
                <span>{vlan.name} ({vlan.interfaces?.join(', ')})</span>
                <span className="font-mono">{vlan.selfIPs?.join(', ')}</span>
              </div>
            ))}
          </>
        ),
      });
    }

    // Security — iRules (if any have issues)
    const irules = health.security?.irules;
    if (irules && (irules.total ?? 0) > 0) {
      const errorIrules = (irules.details as Array<{ name: string; error: string | null }>)?.filter(ir => ir.error) || [];
      cards.push({
        name: 'iRules',
        severity: (irules.severity as HealthSeverity) || 'unknown',
        summary: `${irules.accepted ?? 0}/${irules.total ?? 0} accepted`,
        explanation: (irules.explanation as string) || '',
        podDetails: [],
        remediationActions: [],
        namespaces: [],
        zones: [],
        nodes: [],
        children: errorIrules.length > 0 ? (
          <>
            {errorIrules.map((ir) => (
              <div key={ir.name} className="flex items-start gap-2">
                <AlertTriangle className="h-3.5 w-3.5 text-warning mt-0.5 shrink-0" />
                <div>
                  <span className="text-xs font-medium text-warning">{ir.name}</span>
                  <p className="text-xs text-muted-foreground line-clamp-2">{ir.error}</p>
                </div>
              </div>
            ))}
          </>
        ) : undefined,
      });
    }

    // Security — counts summary card (always show if any policies exist)
    const secPolicyCount = (health.security?.firewallPolicies ?? 0) +
      (health.security?.securityPolicies ?? 0) +
      (health.security?.networkPolicies ?? 0);
    if (secPolicyCount > 0) {
      cards.push({
        name: 'Security Policies',
        severity: (health.security?.severity as HealthSeverity) || 'unknown',
        summary: `${secPolicyCount} policies · ${health.security?.addressLists ?? 0} address lists · ${health.security?.portLists ?? 0} port lists`,
        explanation: 'Security policies protect your network functions with firewall rules, network segmentation, and access control.',
        podDetails: [],
        remediationActions: [],
        namespaces: [],
        zones: [],
        nodes: [],
        children: (
          <>
            <StatRow label="Firewall Policies" value={health.security?.firewallPolicies ?? 0} />
            <StatRow label="Security Policies" value={health.security?.securityPolicies ?? 0} />
            <StatRow label="Network Policies" value={health.security?.networkPolicies ?? 0} />
          </>
        ),
      });
    }

    // Sort: worst severity first (unhealthy/critical → degraded/warning → unknown → healthy)
    cards.sort((a, b) => compareSeverity(a.severity, b.severity));

    return cards;
  }, [health]);

  if (isLoading && !health) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
        <span className="ml-3 text-sm text-muted-foreground">Loading BNK health data...</span>
      </div>
    );
  }

  if (isError) {
    const parsedHealthError = parseApiError(error);
    const healthErrorRoute = parsedHealthError.action?.route;
    return (
      <ErrorState
        error={error}
        onRetry={() => refetch()}
        size="sm"
        {...(healthErrorRoute ? {
          secondaryAction: {
            label: parsedHealthError.action!.label,
            onClick: () => navigate(healthErrorRoute),
          },
        } : {})}
      />
    );
  }

  if (!health) return null;

  const lastUpdated = dataUpdatedAt
    ? new Date(dataUpdatedAt).toLocaleTimeString()
    : 'never';

  const overallConfig = severityConfig[(health.overall as HealthSeverity)] || severityConfig.unknown;
  const OverallIcon = overallConfig.icon;

  return (
    <div className="space-y-6">
      {/* Overall Status Banner */}
      <div className={cn(
        'rounded-lg border p-4 flex items-center justify-between',
        overallConfig.bg,
        overallConfig.border,
      )}>
        <div className="flex items-center gap-3">
          <OverallIcon className={cn('h-6 w-6', overallConfig.color)} />
          <div>
            <h2 className="font-semibold text-lg flex items-center gap-2">
              BNK Platform {overallConfig.label}
              {health.installMethod && (
                <Badge variant="outline" className="text-xs font-normal">
                  {health.installMethod}
                </Badge>
              )}
              {health.connectivity && (
                <ConnectivityBadge connectivity={health.connectivity} />
              )}
              {health.integration && (
                <IntegrationBadge integration={health.integration} />
              )}
            </h2>
            <p className="text-sm text-muted-foreground">
              {health.counts?.tmm_containers || '0/0'} TMM containers
              {' · '}
              {health.counts?.gateways || 0} gateway{health.counts?.gateways !== 1 ? 's' : ''}
              {' · '}
              {health.counts?.httpRoutes || 0} routes
              {' · '}
              {health.counts?.vlans || 0} VLANs
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-muted-foreground">
            Updated {lastUpdated}
          </span>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => refetch()}
            disabled={isFetching}
          >
            <RefreshCw className={cn('h-3.5 w-3.5', isFetching && 'animate-spin')} />
          </Button>
        </div>
      </div>

      {/* License Status Card — K8S-UX-010 */}
      <LicenseStatusCard clusterId={clusterId} />

      {/* Drift Status Banner */}
      {driftStatus && driftStatus.total_modules > 0 && (
        <DriftStatusBanner driftStatus={driftStatus} />
      )}

      {/* Health Detail Cards Grid — sorted by severity */}
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {componentCards.map((card) => (
          <HealthDetailCard
            key={card.name}
            name={card.name}
            severity={card.severity}
            summary={card.summary}
            explanation={card.explanation}
            podDetails={card.podDetails}
            remediationActions={card.remediationActions}
            namespaces={card.namespaces}
            zones={card.zones}
            nodes={card.nodes}
            clusterId={clusterId}
            onViewLogs={handleViewLogs}
            onDescribe={handleDescribe}
          >
            {card.children}
          </HealthDetailCard>
        ))}
      </div>

      {/* Pod Logs Viewer Dialog */}
      <PodLogsViewer
        open={logsDialogOpen}
        onOpenChange={setLogsDialogOpen}
        pod={logsPod ? { metadata: { name: logsPod.name, namespace: logsPod.namespace } } as never : null}
        clusterId={clusterId}
        namespace={logsPod?.namespace || ''}
      />

      {/* Resource Describe Viewer Dialog */}
      <ResourceDescribeViewer
        open={describeDialogOpen}
        onOpenChange={setDescribeDialogOpen}
        resource={describePod ? {
          name: describePod.name,
          kind: 'Pod',
          apiVersion: 'v1',
          metadata: { name: describePod.name, namespace: describePod.namespace },
        } as K8sResource : null}
        clusterId={clusterId}
        namespace={describePod?.namespace}
      />
    </div>
  );
}

// ---- Drift Status Banner ----

function DriftStatusBanner({ driftStatus }: { driftStatus: ClusterDriftStatus }) {
  const hasDrift = driftStatus.modules_with_drift > 0;
  const allUnchecked = driftStatus.overall_status === 'unchecked';

  const bannerConfig = hasDrift
    ? { bg: 'bg-warning/10', border: 'border-warning/30', icon: AlertTriangle, color: 'text-warning', badgeVariant: 'warning' as const, label: 'Drift Detected' }
    : allUnchecked
    ? { bg: 'bg-muted', border: 'border-border', icon: GitCompareArrows, color: 'text-muted-foreground', badgeVariant: 'muted' as const, label: 'Not Checked' }
    : { bg: 'bg-success/10', border: 'border-success/30', icon: CheckCircle2, color: 'text-success', badgeVariant: 'success' as const, label: 'No Drift' };

  const Icon = bannerConfig.icon;

  return (
    <div className={cn(
      'rounded-lg border p-4',
      bannerConfig.bg,
      bannerConfig.border,
    )}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Icon className={cn('h-5 w-5', bannerConfig.color)} />
          <div>
            <h3 className="font-semibold text-sm flex items-center gap-2">
              Configuration Drift
              <Badge variant={bannerConfig.badgeVariant} className="text-xs">
                {bannerConfig.label}
              </Badge>
            </h3>
            <p className="text-xs mt-0.5 text-muted-foreground">
              {hasDrift
                ? `${driftStatus.modules_with_drift} of ${driftStatus.total_modules} module(s) drifted from desired state`
                : allUnchecked
                ? `${driftStatus.total_modules} module(s) — enable drift detection in project settings`
                : `${driftStatus.modules_ok} of ${driftStatus.total_modules} module(s) match desired state`}
              {driftStatus.modules_unchecked > 0 && !allUnchecked && (
                <> · {driftStatus.modules_unchecked} unchecked</>
              )}
            </p>
          </div>
        </div>
        {!driftStatus.drift_enabled && (
          <Badge variant="outline" className="text-xs">
            Scheduled checks disabled
          </Badge>
        )}
      </div>

      {/* Show drifted modules */}
      {hasDrift && driftStatus.module_statuses
        .filter(m => m.status === 'drift')
        .map(m => (
          <div key={m.module_id} className="flex items-center justify-between mt-2 pl-8 text-xs text-muted-foreground">
            <div className="flex items-center gap-2">
              <AlertTriangle className="h-3 w-3 text-warning" />
              <span className="font-medium">{m.module_name}</span>
              <Badge variant="outline" className="text-[10px] px-1.5 py-0">
                {m.engine_type === 'kubernetes' ? 'K8s' : m.engine_type === 'container' ? 'Container' : 'OpenTofu'}
              </Badge>
            </div>
            <span className="text-muted-foreground truncate max-w-[300px]">{m.drift_summary}</span>
          </div>
        ))}
    </div>
  );
}
