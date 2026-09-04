/**
 * Recovery Panel — BNK platform recovery after cluster reboots.
 *
 * Two recovery workflows:
 * 1. CWC Cert Re-sync — fixes mTLS cert mismatch (licensing/QKView broken)
 * 2. Platform Restart — fixes gRPC sync issues (VLANs Programmed=False)
 *
 * Shows a pre-flight status check, then action buttons for each recovery.
 * Used in F5BNK.tsx > DiagnosticsView > Recovery tab.
 */
import { useState } from 'react';
import { cn } from '@/lib/utils';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { recoveryApi } from '@/lib/api/recovery';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { notify } from '@/lib/notify';
import {
  RefreshCw,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Loader2,
  ShieldCheck,
  Network,
  RotateCcw,
  Cpu,
  Zap,
  ChevronDown,
  ChevronUp,
} from 'lucide-react';
import type {
  RecoveryStep,
  PlatformRestartResult,
} from '@/types';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

interface RecoveryPanelProps {
  clusterId: number;
}

// ---- Step/result display helpers ----

function StepBadge({ status }: { status: RecoveryStep['status'] }) {
  const variantMap: Record<RecoveryStep['status'], 'success' | 'destructive' | 'muted' | 'warning'> = {
    ok: 'success',
    failed: 'destructive',
    skipped: 'muted',
    warning: 'warning',
  };
  const labelMap: Record<RecoveryStep['status'], string> = {
    ok: 'OK',
    failed: 'Failed',
    skipped: 'Skipped',
    warning: 'Warning',
  };
  const variant = variantMap[status] || 'warning';
  const label = labelMap[status] || 'Warning';
  return <Badge variant={variant} className="text-xs">{label}</Badge>;
}

function StepList({ steps }: { steps: RecoveryStep[] }) {
  return (
    <div className="space-y-1.5 mt-3">
      {steps.map((step, i) => (
        <div
          key={i}
          className="flex items-center justify-between text-xs rounded px-2.5 py-1.5 bg-muted/50"
        >
          <span className="text-foreground/80">
            {step.step.replace(/_/g, ' ')}
            {step.detail && <span className="text-muted-foreground ml-1.5">({step.detail})</span>}
          </span>
          <StepBadge status={step.status} />
        </div>
      ))}
    </div>
  );
}

function RestartResultList({ results }: { results: PlatformRestartResult[] }) {
  return (
    <div className="space-y-1.5 mt-3">
      {results.map((r, i) => {
        // FLO is only present on Forge deploy-flow installs — a "not found" here
        // on a direct-helm cluster is expected, not a fault. Render informationally.
        const isFloNotPresent = r.status === 'not_found' && r.message?.includes('direct-helm install');
        return (
        <div key={i} className="rounded px-2.5 py-1.5 bg-muted/50">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-foreground/80">
              {r.component}
            </span>
            <Badge
              variant={r.status === 'restarted' ? 'success' : isFloNotPresent ? 'info' : 'muted'}
              className="text-xs"
            >
              {r.status === 'restarted' ? 'Restarted' : isFloNotPresent ? 'Not Present' : 'Not Found'}
            </Badge>
          </div>
          <p className="text-xs mt-0.5 text-muted-foreground">
            {r.message}
          </p>
          {r.deleted_pods && r.deleted_pods.length > 0 && (
            <p className="text-xs text-muted-foreground mt-0.5 font-mono">
              {r.deleted_pods.join(', ')}
            </p>
          )}
        </div>
        );
      })}
    </div>
  );
}

// ---- Main component ----

export function RecoveryPanel({ clusterId }: RecoveryPanelProps) {
  const queryClient = useQueryClient();

  // Platform restart options
  const [restartController, setRestartController] = useState(true);
  const [restartFlo, setRestartFlo] = useState(false);
  const [restartTmm, setRestartTmm] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  // Pre-flight status check
  const {
    data: status,
    isLoading: statusLoading,
    refetch: refetchStatus,
    isFetching: statusFetching,
  } = useQuery({
    queryKey: ['recovery', 'status', clusterId],
    queryFn: () => recoveryApi.getStatus(clusterId),
    enabled: clusterId > 0,
    staleTime: 60_000,
    placeholderData: (previousData) => previousData,
    retry: 1,
  });

  // CWC cert re-sync mutation
  const cwcResync = useAppMutation({
    mutationFn: () => recoveryApi.resyncCWCCerts(clusterId),
    onSuccess: (data) => {
      if (data.success) {
        notify.success('CWC certs re-synced', data.message, { category: 'security' });
      } else {
        notify.error('CWC cert re-sync failed', data.message, { category: 'security' });
      }
      // Refresh recovery status + health data
      queryClient.invalidateQueries({ queryKey: ['recovery', 'status', clusterId] });
      queryClient.invalidateQueries({ queryKey: ['licensing'] });
      queryClient.invalidateQueries({ queryKey: ['k8s', 'clusters', clusterId] });
    },
    onError: (error: Error) => {
      notify.error('CWC cert re-sync failed', error.message, { category: 'security' });
    },
  });

  // Platform restart mutation
  const platformRestart = useAppMutation({
    mutationFn: () =>
      recoveryApi.platformRestart(clusterId, {
        restart_controller: restartController,
        restart_flo: restartFlo,
        restart_tmm: restartTmm,
      }),
    onSuccess: (data) => {
      if (data.success) {
        notify.success('Platform restart complete', data.message, { category: 'cluster' });
      } else {
        notify.error('Platform restart', data.message, { category: 'cluster' });
      }
      // Refresh recovery status + health data after a delay (pods need time to restart)
      setTimeout(() => {
        queryClient.invalidateQueries({ queryKey: ['recovery', 'status', clusterId] });
        queryClient.invalidateQueries({ queryKey: ['k8s', 'clusters', clusterId] });
        queryClient.invalidateQueries({ queryKey: ['bnk-resources'] });
      }, 5000);
    },
    onError: (error: Error) => {
      notify.error('Platform restart failed', error.message, { category: 'cluster' });
    },
  });

  if (statusLoading && !status) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-primary" />
        <span className="ml-3 text-sm text-muted-foreground">Checking recovery status...</span>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Status Banner */}
      {status && (
        <div
          className={cn(
            'rounded-lg border-l-2 border-y border-r border-y-border border-r-border bg-card p-4 flex items-center justify-between',
            status.platform_healthy ? 'border-l-success' : 'border-l-warning',
          )}
        >
          <div className="flex items-center gap-3">
            {status.platform_healthy ? (
              <CheckCircle2 className="h-5 w-5 text-success" />
            ) : (
              <AlertTriangle className="h-5 w-5 text-warning" />
            )}
            <div>
              <h3 className="font-semibold text-sm">
                {status.platform_healthy
                  ? 'Platform Healthy — No recovery needed'
                  : 'Recovery Actions Available'}
              </h3>
              <p className="text-xs mt-0.5 text-muted-foreground">
                {!status.platform_healthy && (
                  <>
                    {status.cwc_cert_stale && 'CWC certs are stale. '}
                    {status.vlans_failed && 'VLANs have programming failures. '}
                    {'Use the actions below to recover.'}
                  </>
                )}
                {status.platform_healthy && status.cwc_cert_status === 'not_applicable' &&
                  'All components are functioning normally. CWC certs are managed outside Forge on this cluster.'}
                {status.platform_healthy && status.cwc_cert_status !== 'not_applicable' &&
                  'All components are functioning normally.'}
              </p>
            </div>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => refetchStatus()}
            disabled={statusFetching}
          >
            <RefreshCw className={cn('h-3.5 w-3.5', statusFetching && 'animate-spin')} />
          </Button>
        </div>
      )}

      {/* CWC Cert Recovery Card */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <ShieldCheck className="h-5 w-5 text-primary" />
              <CardTitle className="text-base">CWC Certificate Recovery</CardTitle>
            </div>
            {status && (
              <Badge
                variant={
                  status.cwc_cert_status === 'not_applicable'
                    ? 'info'
                    : status.cwc_cert_stale ? 'warning' : 'success'
                }
                className="text-xs"
              >
                {status.cwc_cert_status === 'not_applicable'
                  ? 'Managed outside Forge'
                  : status.cwc_cert_stale ? 'Stale' : 'OK'}
              </Badge>
            )}
          </div>
          <CardDescription>
            Re-sync CWC REST API certificates from cert-manager. Fixes licensing and QKView
            errors caused by the CWC pod reverting to self-signed certs after a cluster reboot.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {status?.cwc_cert_detail && (
            <p className="text-xs mb-3 text-muted-foreground">
              {status.cwc_cert_detail}
            </p>
          )}

          {status?.cwc_cert_status === 'not_applicable' ? (
            <div className="rounded-md p-3 mb-4 text-xs bg-info/10 text-info border border-info/20">
              This cluster&apos;s CWC certs are managed outside Forge (direct-helm / cert-manager
              install) — there&apos;s nothing here for Forge to re-sync. This is expected, not an error.
            </div>
          ) : (
            <div className="rounded-md p-3 mb-4 text-xs space-y-1 bg-muted/50">
              <p className="text-muted-foreground">This will:</p>
              <ul className="list-disc list-inside space-y-0.5 text-muted-foreground">
                <li>Copy cert-manager certs into <code className="font-mono text-[11px]">cwc-license-certs</code></li>
                <li>Restart the CWC pod (~30s downtime for licensing/QKView)</li>
                <li>Clean up stale agent pods</li>
              </ul>
            </div>
          )}

          <Button
            onClick={() => cwcResync.mutate()}
            disabled={cwcResync.isPending || status?.cwc_cert_status === 'not_applicable'}
            variant={status?.cwc_cert_stale ? 'default' : 'outline'}
            size="sm"
          >
            {cwcResync.isPending ? (
              <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
            ) : (
              <RotateCcw className="h-3.5 w-3.5 mr-1.5" />
            )}
            {cwcResync.isPending ? 'Re-syncing...' : 'Re-sync CWC Certs'}
          </Button>

          {cwcResync.data && <StepList steps={cwcResync.data.steps} />}
        </CardContent>
      </Card>

      {/* Platform Restart Card */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Network className="h-5 w-5 text-primary" />
              <CardTitle className="text-base">Platform Recovery</CardTitle>
            </div>
            {status && (
              <Badge variant={status.vlans_failed ? 'warning' : 'success'} className="text-xs">
                {status.vlans_failed ? 'Issues Detected' : 'OK'}
              </Badge>
            )}
          </div>
          <CardDescription>
            Restart BNK platform components to fix gRPC synchronization issues.
            After a cluster reboot, VLANs and routes may fail to program because the
            controller couldn&apos;t reach TMM during startup.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {status?.vlans_detail && (
            <p className="text-xs mb-3 text-muted-foreground">
              {status.vlans_detail}
            </p>
          )}

          {/* Component checkboxes */}
          <div className="space-y-2.5 mb-4">
            <label className="flex items-center gap-3 rounded-md p-2.5 cursor-pointer transition-colors hover:bg-muted/50">
              <input
                type="checkbox"
                checked={restartController}
                onChange={(e) => setRestartController(e.target.checked)}
                className="rounded"
              />
              <div className="flex-1">
                <div className="flex items-center gap-2">
                  <Cpu className="h-3.5 w-3.5 text-primary" />
                  <span className="text-sm font-medium">CNE Controller</span>
                  <Badge variant="outline" className="text-[10px]">Recommended</Badge>
                </div>
                <p className="text-xs mt-0.5 text-muted-foreground">
                  Fixes VLAN, route, and gateway programming failures. Forces a full config re-sync with TMM.
                </p>
              </div>
            </label>

            <button
              onClick={() => setShowAdvanced(!showAdvanced)}
              className="flex items-center gap-1.5 text-xs px-2.5 transition-colors text-muted-foreground hover:text-foreground"
            >
              {showAdvanced ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
              {showAdvanced ? 'Hide advanced options' : 'Show advanced options'}
            </button>

            {showAdvanced && (
              <>
                <label className="flex items-center gap-3 rounded-md p-2.5 cursor-pointer transition-colors hover:bg-muted/50">
                  <input
                    type="checkbox"
                    checked={restartFlo}
                    onChange={(e) => setRestartFlo(e.target.checked)}
                    className="rounded"
                  />
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <Zap className="h-3.5 w-3.5 text-primary" />
                      <span className="text-sm font-medium">FLO Lifecycle Operator</span>
                    </div>
                    <p className="text-xs mt-0.5 text-muted-foreground">
                      Manages pod lifecycle. Restart if pods are stuck in unexpected states.
                    </p>
                  </div>
                </label>

                <label className="flex items-center gap-3 rounded-md p-2.5 cursor-pointer transition-colors border border-destructive/20 hover:bg-destructive/10">
                  <input
                    type="checkbox"
                    checked={restartTmm}
                    onChange={(e) => setRestartTmm(e.target.checked)}
                    className="rounded"
                  />
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <AlertTriangle className="h-3.5 w-3.5 text-destructive" />
                      <span className="text-sm font-medium">TMM (Data Plane)</span>
                      <Badge variant="destructive" className="text-[10px]">
                        Traffic Impact
                      </Badge>
                    </div>
                    <p className="text-xs mt-0.5 text-muted-foreground">
                      Nuclear option — restarts the traffic processing engine. Causes 2-3 minutes of traffic
                      interruption. Only use if controller restart didn&apos;t resolve the issue.
                    </p>
                  </div>
                </label>
              </>
            )}
          </div>

          <Button
            onClick={() => platformRestart.mutate()}
            disabled={platformRestart.isPending || (!restartController && !restartFlo && !restartTmm)}
            variant={status?.vlans_failed ? 'default' : 'outline'}
            size="sm"
          >
            {platformRestart.isPending ? (
              <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
            ) : (
              <RotateCcw className="h-3.5 w-3.5 mr-1.5" />
            )}
            {platformRestart.isPending ? 'Restarting...' : 'Restart Selected Components'}
          </Button>

          {platformRestart.data && <RestartResultList results={platformRestart.data.restarted} />}
        </CardContent>
      </Card>

      {/* Info card */}
      <div className="rounded-lg border border-border bg-muted/50 p-4 text-xs space-y-2">
        <h4 className="font-medium text-foreground/80">
          When do you need recovery?
        </h4>
        <ul className="space-y-1.5 text-muted-foreground">
          <li className="flex items-start gap-2">
            <XCircle className="h-3.5 w-3.5 text-destructive mt-0.5 shrink-0" />
            <span>
              <strong>Licensing/QKView broken</strong> with SSL cert errors — use CWC Cert Recovery.
              After a reboot, the CWC pod reverts to its original self-signed certs.
            </span>
          </li>
          <li className="flex items-start gap-2">
            <XCircle className="h-3.5 w-3.5 text-destructive mt-0.5 shrink-0" />
            <span>
              <strong>VLANs show &quot;Programmed: False&quot;</strong> — use Platform Recovery (Controller restart).
              The controller failed to push config to TMM during startup.
            </span>
          </li>
          <li className="flex items-start gap-2">
            <CheckCircle2 className="h-3.5 w-3.5 text-success mt-0.5 shrink-0" />
            <span>
              These are <strong>safe, idempotent</strong> operations. Pods are deleted and recreated
              by their Deployment/DaemonSet controllers. Running them when everything is healthy has no effect.
            </span>
          </li>
        </ul>
      </div>
    </div>
  );
}
