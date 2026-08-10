/**
 * Licensing Panel — full BNK licensing management for the Diagnostics tab.
 *
 * K8S-UX-010: Shows license status, CWC connectivity, and provides
 * the complete licensing workflow:
 *   1. View current license status + telemetry state
 *   2. Activate/reactivate a license (paste raw JWT)
 *   3. Download telemetry report
 *   4. Submit signed manifest (receipt)
 *
 * Used in F5BNK.tsx > DiagnosticsView > Licensing tab.
 */
import { useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import {
  useLicenseStatus,
  useCWCStatus,
  useLicenseReport,
  useActivateLicense,
  useRenewLicense,
  useValidateJwks,
  usePostLicenseReceipt,
} from '@/hooks/useLicensing';
import { useCWCAPISetup, useCWCAPISetupStatus } from '@/hooks/useQKView';
import { Badge, type BadgeProps } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Textarea } from '@/components/ui/textarea';
import { notify } from '@/lib/notify';
import {
  Shield,
  ShieldCheck,
  ShieldAlert,
  ShieldX,
  ShieldQuestion,
  RefreshCw,
  Key,
  KeyRound,
  Radio,
  Clock,
  Download,
  Upload,
  CheckCircle2,
  XCircle,
  Loader2,
  AlertTriangle,
  Copy,
  FileText,
  RotateCw,
  Zap,
  Wrench,
} from 'lucide-react';
import type { LicenseInfo } from '@/types';

interface LicensingPanelProps {
  clusterId: number;
}

// D-020: token-pure status mapping. Status surfaces as a small badge + icon,
// not a whole-card tint. `iconColor` is a single token class for the icon.
const stateConfig: Record<LicenseInfo['state'], {
  icon: typeof Shield;
  iconColor: string;
  label: string;
  badgeVariant: NonNullable<BadgeProps['variant']>;
}> = {
  active: {
    icon: ShieldCheck,
    iconColor: 'text-success',
    label: 'Active',
    badgeVariant: 'success',
  },
  evaluation: {
    icon: ShieldAlert,
    iconColor: 'text-warning',
    label: 'Evaluation',
    badgeVariant: 'warning',
  },
  expired: {
    icon: ShieldX,
    iconColor: 'text-destructive',
    label: 'Expired',
    badgeVariant: 'destructive',
  },
  unknown: {
    icon: ShieldQuestion,
    iconColor: 'text-muted-foreground',
    label: 'Unknown',
    badgeVariant: 'outline',
  },
  unavailable: {
    icon: ShieldQuestion,
    iconColor: 'text-muted-foreground',
    label: 'Unavailable',
    badgeVariant: 'outline',
  },
};

function StatusDot({ ok, label }: { ok: boolean; label: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className={cn(
        'h-2 w-2 rounded-full',
        ok ? 'bg-success' : 'bg-muted-foreground',
      )} />
      <span className="text-sm">{label}</span>
      {ok ? (
        <CheckCircle2 className="h-3.5 w-3.5 text-success" />
      ) : (
        <XCircle className="h-3.5 w-3.5 text-muted-foreground" />
      )}
    </div>
  );
}

export function LicensingPanel({ clusterId }: LicensingPanelProps) {
  // Data hooks
  const {
    licenseInfo,
    isLoading: statusLoading,
    isError: statusError,
    refetch: refetchStatus,
    isFetching: statusFetching,
  } = useLicenseStatus(clusterId);

  const { data: cwcStatus, isLoading: cwcLoading } = useCWCStatus(clusterId);
  const { data: cwcApiSetupStatus } = useCWCAPISetupStatus(clusterId, clusterId > 0);

  // Mutation hooks
  const activateMutation = useActivateLicense();
  const renewMutation = useRenewLicense();
  const receiptMutation = usePostLicenseReceipt();
  const jwksMutation = useValidateJwks();
  const cwcApiSetupMutation = useCWCAPISetup();

  // Report download
  const {
    data: reportData,
    refetch: fetchReport,
    isFetching: reportFetching,
  } = useLicenseReport(clusterId, false); // disabled by default, trigger manually

  // Local state for forms
  const [jwtInput, setJwtInput] = useState('');
  const [renewJwtInput, setRenewJwtInput] = useState('');
  const [manifestInput, setManifestInput] = useState('');
  const [showActivateForm, setShowActivateForm] = useState(false);
  const [showRenewForm, setShowRenewForm] = useState(false);
  const [showReceiptForm, setShowReceiptForm] = useState(false);
  const [forceRenew, setForceRenew] = useState(false);

  const handleActivate = useCallback(() => {
    if (!jwtInput.trim()) {
      notify.warning('Please paste the JWT license token', undefined, { category: 'security' });
      return;
    }
    activateMutation.mutate(
      { clusterId, data: { jwt: jwtInput.trim() } },
      {
        onSuccess: (data) => {
          if (data?.success === false) {
            notify.error(data.error_message ?? 'License activation failed', undefined, { category: 'security' });
            return;
          }
          const stateMsg = data?.license_state_pre && data?.license_state_post
            ? ` (${data.license_state_pre} → ${data.license_state_post})`
            : '';
          notify.success(`License activated successfully${stateMsg}`, undefined, { category: 'security' });
          setJwtInput('');
          setShowActivateForm(false);
        },
      },
    );
  }, [activateMutation, clusterId, jwtInput]);

  const handleRenew = useCallback(() => {
    if (!renewJwtInput.trim()) {
      notify.warning('Please paste the JWT license token', undefined, { category: 'security' });
      return;
    }
    renewMutation.mutate(
      { clusterId, data: { jwt: renewJwtInput.trim(), force: forceRenew } },
      {
        onSuccess: (data) => {
          if (data?.success === false) {
            notify.error(data.error_message ?? 'License renewal failed', undefined, { category: 'security' });
            return;
          }
          const stateMsg = data?.license_state_pre && data?.license_state_post
            ? ` (${data.license_state_pre} → ${data.license_state_post})`
            : '';
          const base = forceRenew
            ? 'License renewed (force). CWC was restarted with the new license.'
            : 'License switched successfully. No CWC restart needed.';
          notify.success(`${base}${stateMsg}`, undefined, { category: 'security' });
          setRenewJwtInput('');
          setShowRenewForm(false);
          setForceRenew(false);
        },
      },
    );
  }, [renewMutation, clusterId, renewJwtInput, forceRenew]);

  const handleValidateJwks = useCallback(() => {
    jwksMutation.mutate(
      { clusterId },
      {
        onSuccess: (data) => {
          if (data.status === 'patched') {
            notify.success('JWKS fixed — replaced placeholder keys with real F5 public keys.', undefined, { category: 'security' });
          } else if (data.status === 'valid') {
            notify.success('JWKS is already valid — no changes needed.', undefined, { category: 'security' });
          } else {
            notify.warning(data.message || 'JWKS ConfigMap not found on this cluster.', undefined, { category: 'security' });
          }
        },
      },
    );
  }, [jwksMutation, clusterId]);

  const handleSetupCwcCerts = useCallback(() => {
    cwcApiSetupMutation.mutate(clusterId, {
      onSuccess: (result) => {
        notify.success(
          result.message || 'CWC mTLS setup completed successfully.',
          undefined,
          { category: 'security' },
        );
      },
    });
  }, [cwcApiSetupMutation, clusterId]);

  const handleReceipt = useCallback(() => {
    if (!manifestInput.trim()) {
      notify.warning('Please paste the signed manifest', undefined, { category: 'security' });
      return;
    }
    receiptMutation.mutate(
      { clusterId, data: { manifest: manifestInput.trim() } },
      {
        onSuccess: (data) => {
          if (data?.success === false) {
            notify.error(data.error_message ?? 'License receipt submission failed', undefined, { category: 'security' });
            return;
          }
          notify.success('License receipt submitted successfully', undefined, { category: 'security' });
          setManifestInput('');
          setShowReceiptForm(false);
        },
      },
    );
  }, [receiptMutation, clusterId, manifestInput]);

  const handleDownloadReport = useCallback(async () => {
    const result = await fetchReport();
    if (result.data) {
      const raw = result.data.raw || JSON.stringify(result.data, null, 2);
      // Copy to clipboard
      try {
        await navigator.clipboard.writeText(raw);
      } catch {
        // Fallback: download as file
        const blob = new Blob([raw], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `telemetry-report-cluster-${clusterId}.txt`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      }
    }
  }, [fetchReport, clusterId]);

  if (statusLoading || cwcLoading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
        <span className="ml-3 text-sm text-muted-foreground">Loading licensing information...</span>
      </div>
    );
  }

  // When license status fails or is unavailable, show contextual help.
  // With the legacy fallback path, this now means CWC itself isn't reachable
  // (no certs, no CWC service, etc.) — not just "no operator connected".
  if (statusError || licenseInfo.state === 'unavailable') {
    const cwcNotFound = cwcStatus && !cwcStatus.cwc_service_found;
    const certsNotMounted = cwcStatus && !cwcStatus.certs_mounted;

    // If CWC service doesn't exist on this cluster, hide the panel entirely
    if (cwcNotFound) return null;

    return (
      <Card>
        <CardContent className="pt-6">
          <div className="flex items-start gap-3">
            <ShieldQuestion className="h-6 w-6 text-muted-foreground shrink-0 mt-0.5" />
            <div className="space-y-2">
              <p className="text-sm font-medium text-foreground">
                License Status Unavailable
              </p>
              <p className="text-sm text-muted-foreground">
                {certsNotMounted
                  ? 'CWC certificates are not configured. Use the Recovery tab to re-sync certificates, or complete initial QKView mTLS setup from the QKView tab.'
                  : 'Unable to reach the CWC REST API. Ensure the cluster has a CWC service with valid mTLS certificates.'}
              </p>
              {cwcStatus && (
                <div className="flex flex-wrap gap-4 pt-2">
                  <StatusDot ok={cwcStatus.cwc_service_found} label="CWC Service" />
                  <StatusDot ok={cwcStatus.certs_mounted} label="mTLS Certs" />
                  <StatusDot ok={cwcStatus.cwc_reachable} label="API Reachable" />
                  <StatusDot ok={cwcStatus.setup_complete} label="Setup Complete" />
                </div>
              )}
            </div>
          </div>
        </CardContent>
      </Card>
    );
  }

  const config = stateConfig[licenseInfo.state] || stateConfig.unknown;
  const StateIcon = config.icon;

  return (
    <div className="space-y-6">
      {/* CWC Connectivity Status */}
      {cwcStatus && (
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base flex items-center gap-2">
              <Radio className="h-4 w-4" />
              CWC Connectivity
            </CardTitle>
            <CardDescription>
              Connection status to the Cluster Wide Controller REST API
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-6">
              <StatusDot ok={cwcStatus.cwc_service_found} label="CWC Service" />
              <StatusDot ok={cwcStatus.certs_mounted} label="mTLS Certs" />
              <StatusDot ok={cwcStatus.cwc_reachable} label="API Reachable" />
              <StatusDot ok={cwcStatus.setup_complete} label="Setup Complete" />
              <StatusDot ok={cwcStatus.cert_manager_available} label="cert-manager" />
            </div>
            {!cwcStatus.certs_mounted && (
              <div className="mt-4 flex items-start gap-2 rounded-md p-3 text-sm bg-warning/10 text-warning">
                <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
                <span>
                  CWC certificates are not mounted on the operator pod.
                  Complete the initial QKView mTLS setup from the QKView tab, then update the operator
                  deployment with <code className="font-mono text-xs">cwc.enabled: true</code>.
                </span>
              </div>
            )}

            {cwcApiSetupStatus && !cwcApiSetupStatus.setup_complete && (
              <div className="mt-4 rounded-md border border-border bg-muted/50 p-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="space-y-1.5">
                    <p className="text-sm font-medium text-foreground">
                      CWC mTLS Setup Required
                    </p>
                    <p className="text-sm text-muted-foreground">
                      Licensing and QKView both require CWC REST API certificates. This one-time
                      setup creates the cert-manager-backed server and client certs, updates
                      `cwc-license-certs`, and restarts the CWC pod.
                    </p>
                    <div className="flex flex-wrap gap-4 pt-1">
                      <StatusDot ok={cwcApiSetupStatus.cert_manager_available} label="cert-manager" />
                      <StatusDot ok={cwcApiSetupStatus.server_cert_exists} label="Server Cert" />
                      <StatusDot ok={cwcApiSetupStatus.client_cert_exists} label="Client Cert" />
                      <StatusDot ok={cwcApiSetupStatus.setup_complete} label="Setup Complete" />
                    </div>
                  </div>
                  <Button
                    size="sm"
                    onClick={handleSetupCwcCerts}
                    disabled={cwcApiSetupMutation.isPending || !cwcApiSetupStatus.cert_manager_available}
                  >
                    {cwcApiSetupMutation.isPending ? (
                      <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                    ) : (
                      <Wrench className="h-3.5 w-3.5 mr-1.5" />
                    )}
                    Setup CWC mTLS
                  </Button>
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* License Status */}
      <Card>
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between">
            <CardTitle className="text-base flex items-center gap-2">
              <StateIcon className={cn('h-5 w-5', config.iconColor)} />
              License Status
              <Badge variant={config.badgeVariant} className="text-xs">
                {config.label}
              </Badge>
            </CardTitle>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => refetchStatus()}
              disabled={statusFetching}
            >
              <RefreshCw className={cn('h-3.5 w-3.5', statusFetching && 'animate-spin')} />
            </Button>
          </div>
          {statusError && (
            <CardDescription className="text-destructive">
              Failed to refresh license status
            </CardDescription>
          )}
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {licenseInfo.entitlementType && (
                <DetailItem icon={Shield} label="Entitlement" value={licenseInfo.entitlementType} />
              )}
              {licenseInfo.digitalAssetId && (
                <DetailItem icon={Key} label="Digital Asset ID" value={licenseInfo.digitalAssetId} />
              )}
              {licenseInfo.expiryDate && (
                <DetailItem icon={Clock} label="Expiry Date" value={licenseInfo.expiryDate} />
              )}
              {licenseInfo.expiryDays !== undefined && (
                <DetailItem
                  icon={Clock}
                  label="Days Remaining"
                  value={licenseInfo.expiryDays > 0 ? `${licenseInfo.expiryDays} days` : 'Expired'}
                  warn={licenseInfo.expiryDays <= 30}
                />
              )}
              {licenseInfo.telemetryState && (
                <DetailItem icon={Radio} label="Telemetry" value={licenseInfo.telemetryState} />
              )}
              {licenseInfo.telemetryMessage && (
                <DetailItem icon={FileText} label="Telemetry Status" value={licenseInfo.telemetryMessage} />
              )}
            </div>

            {/* Raw CWC state (when not cleanly mapped) */}
            {licenseInfo.rawState && licenseInfo.rawState !== 'Active' && licenseInfo.rawState !== 'Licensed' && (
              <DetailItem icon={Shield} label="CWC State" value={licenseInfo.rawState} />
            )}

            {/* Error / suggested action from CWC */}
            {licenseInfo.error && (
              <div className="col-span-full flex items-start gap-2 rounded-md p-3 text-sm bg-destructive/10 text-destructive">
                <ShieldX className="h-4 w-4 shrink-0 mt-0.5" />
                <div className="space-y-1">
                  <p className="font-medium">License Error</p>
                  <p className="text-xs opacity-80">{licenseInfo.error}</p>
                  {licenseInfo.suggestedAction && (
                    <p className="text-xs opacity-60">{licenseInfo.suggestedAction}</p>
                  )}
                </div>
              </div>
            )}

            {/* Warnings */}
            {licenseInfo.state === 'evaluation' && (
              <div className="flex items-start gap-2 rounded-md p-3 text-sm bg-warning/10 text-warning">
                <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
                <span>
                  This cluster is running an evaluation license.
                  Activate a production license before the evaluation period expires.
                </span>
              </div>
            )}
            {licenseInfo.state === 'expired' && (
              <div className="flex items-start gap-2 rounded-md p-3 text-sm bg-destructive/10 text-destructive">
                <ShieldX className="h-4 w-4 shrink-0 mt-0.5" />
                <span>
                  This license has expired. Activate a new license to restore full functionality.
                </span>
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Licensing Actions */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Licensing Actions</CardTitle>
          <CardDescription>
            Manage license activation, telemetry reports, and receipt submission
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Action buttons row */}
          <div className="flex flex-wrap gap-3">
            <Button
              variant="outline"
              size="sm"
              onClick={() => { setShowActivateForm(!showActivateForm); setShowRenewForm(false); setShowReceiptForm(false); }}
              className={cn(showActivateForm && 'ring-2 ring-primary')}
            >
              <Key className="h-3.5 w-3.5 mr-1.5" />
              Activate License
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => { setShowRenewForm(!showRenewForm); setShowActivateForm(false); setShowReceiptForm(false); }}
              className={cn(showRenewForm && 'ring-2 ring-primary')}
            >
              <RotateCw className="h-3.5 w-3.5 mr-1.5" />
              Renew License
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handleDownloadReport}
              disabled={reportFetching || licenseInfo.telemetryState !== 'Config Report Ready to Download'}
              title={licenseInfo.telemetryState !== 'Config Report Ready to Download'
                ? `Telemetry report is only available when state shows 'Config Report Ready to Download'. Current state: ${licenseInfo.telemetryState ?? 'unknown'}`
                : undefined}
            >
              {reportFetching ? (
                <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
              ) : (
                <Download className="h-3.5 w-3.5 mr-1.5" />
              )}
              Download Telemetry Report
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => { setShowReceiptForm(!showReceiptForm); setShowActivateForm(false); setShowRenewForm(false); }}
              className={cn(showReceiptForm && 'ring-2 ring-primary')}
            >
              <Upload className="h-3.5 w-3.5 mr-1.5" />
              Submit Receipt
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handleValidateJwks}
              disabled={jwksMutation.isPending}
            >
              {jwksMutation.isPending ? (
                <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
              ) : (
                <KeyRound className="h-3.5 w-3.5 mr-1.5" />
              )}
              Validate JWKS
            </Button>
          </div>

          {/* Activate License Form */}
          {showActivateForm && (
            <div className="rounded-lg border border-border bg-muted/50 p-4 space-y-3">
              <div>
                <h4 className="text-sm font-medium mb-1">Activate / Reactivate License</h4>
                <p className="text-xs text-muted-foreground">
                  Paste the raw JWT object from F5. This is used to activate an evaluation license,
                  switch from evaluation to production, or update an existing license.
                </p>
              </div>
              <Textarea
                placeholder="Paste raw JWT license token here..."
                value={jwtInput}
                onChange={(e) => setJwtInput(e.target.value)}
                rows={4}
                className="font-mono text-xs"
              />
              <div className="flex gap-2">
                <Button
                  size="sm"
                  onClick={handleActivate}
                  disabled={activateMutation.isPending || !jwtInput.trim()}
                >
                  {activateMutation.isPending ? (
                    <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                  ) : (
                    <Key className="h-3.5 w-3.5 mr-1.5" />
                  )}
                  Activate
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => { setShowActivateForm(false); setJwtInput(''); }}
                >
                  Cancel
                </Button>
              </div>
            </div>
          )}

          {/* Renew / Switch License Form */}
          {showRenewForm && (
            <div className={cn(
              'rounded-lg border p-4 space-y-3',
              forceRenew
                ? 'border-l-2 border-l-warning border-border bg-muted/50'
                : 'border-border bg-muted/50',
            )}>
              <div>
                <h4 className="text-sm font-medium mb-1">
                  {forceRenew ? 'Force Renew License' : 'Renew / Switch License'}
                </h4>
                {forceRenew ? (
                  <div className="space-y-1.5">
                    <p className="text-xs text-muted-foreground">
                      Nuclear renewal for when CWC is unresponsive or in a broken state. This will:
                    </p>
                    <ol className="text-xs list-decimal list-inside space-y-0.5 text-muted-foreground">
                      <li>Validate and fix JWKS ConfigMap if needed</li>
                      <li>Patch JWT into cpcl-config-cm for persistence</li>
                      <li>Delete all CPCL state secrets (resets licensing state)</li>
                      <li>Restart the CWC pod (~2 min downtime)</li>
                      <li>Activate the new license with your JWT</li>
                    </ol>
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">
                    Clean license switch via the CWC API. No CWC restart, no downtime, telemetry
                    history preserved. Validates JWKS and patches ConfigMaps automatically.
                  </p>
                )}
              </div>

              {/* Force mode warning */}
              {forceRenew && (
                <div className="flex items-start gap-2 rounded-md p-2.5 text-xs bg-warning/10 text-warning">
                  <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                  <span>
                    CWC will be restarted and telemetry history will be lost. Traffic processing is
                    unaffected but licensing will be unavailable for ~2 minutes.
                  </span>
                </div>
              )}

              <Textarea
                placeholder="Paste raw JWT license token here..."
                value={renewJwtInput}
                onChange={(e) => setRenewJwtInput(e.target.value)}
                rows={4}
                className="font-mono text-xs"
              />
              <div className="flex items-center gap-3">
                <Button
                  size="sm"
                  variant={forceRenew ? 'destructive' : 'default'}
                  onClick={handleRenew}
                  disabled={renewMutation.isPending || !renewJwtInput.trim()}
                >
                  {renewMutation.isPending ? (
                    <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                  ) : forceRenew ? (
                    <Zap className="h-3.5 w-3.5 mr-1.5" />
                  ) : (
                    <RotateCw className="h-3.5 w-3.5 mr-1.5" />
                  )}
                  {renewMutation.isPending
                    ? 'Processing...'
                    : forceRenew ? 'Force Renew' : 'Switch License'}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => { setShowRenewForm(false); setRenewJwtInput(''); setForceRenew(false); }}
                >
                  Cancel
                </Button>

                {/* Force toggle */}
                <label className="ml-auto flex items-center gap-2 text-xs cursor-pointer select-none text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={forceRenew}
                    onChange={(e) => setForceRenew(e.target.checked)}
                    className="rounded border-border"
                  />
                  <span className={cn(forceRenew && 'text-warning font-medium')}>
                    Force (restart CWC)
                  </span>
                </label>
              </div>
            </div>
          )}

          {/* Submit Receipt Form */}
          {showReceiptForm && (
            <div className="rounded-lg border border-border bg-muted/50 p-4 space-y-3">
              <div>
                <h4 className="text-sm font-medium mb-1">Submit Signed Manifest</h4>
                <p className="text-xs text-muted-foreground">
                  After downloading the telemetry report and submitting it to the F5 licensing server,
                  paste the signed manifest you receive back here.
                </p>
              </div>
              <Textarea
                placeholder="Paste signed manifest from F5 licensing server..."
                value={manifestInput}
                onChange={(e) => setManifestInput(e.target.value)}
                rows={4}
                className="font-mono text-xs"
              />
              <div className="flex gap-2">
                <Button
                  size="sm"
                  onClick={handleReceipt}
                  disabled={receiptMutation.isPending || !manifestInput.trim()}
                >
                  {receiptMutation.isPending ? (
                    <Loader2 className="h-3.5 w-3.5 mr-1.5 animate-spin" />
                  ) : (
                    <Upload className="h-3.5 w-3.5 mr-1.5" />
                  )}
                  Submit
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => { setShowReceiptForm(false); setManifestInput(''); }}
                >
                  Cancel
                </Button>
              </div>
            </div>
          )}

          {/* Telemetry Report Preview (when fetched) */}
          {reportData?.raw && (
            <div className="rounded-lg border border-border bg-muted/50 p-4 space-y-2">
              <div className="flex items-center justify-between">
                <h4 className="text-sm font-medium">Telemetry Report</h4>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(reportData.raw || '');
                    } catch {
                      notify.error('Failed to copy to clipboard', undefined, { category: 'security' });
                    }
                  }}
                >
                  <Copy className="h-3.5 w-3.5 mr-1.5" />
                  Copy
                </Button>
              </div>
              <pre className="text-xs font-mono p-3 rounded overflow-x-auto max-h-40 bg-card text-foreground/80">
                {reportData.raw.length > 500
                  ? `${reportData.raw.substring(0, 500)}...`
                  : reportData.raw}
              </pre>
            </div>
          )}

          {/* Licensing workflow help */}
          <div className="text-xs space-y-1 pt-2 text-muted-foreground">
            <p className="font-medium">F5 BNK Licensing:</p>
            <ul className="space-y-1 ml-1">
              <li><strong>Activate License</strong> — First-time activation using a JWT from MyF5</li>
              <li><strong>Renew License</strong> — Switch or renew a license via the CWC API (no downtime)</li>
              <li><strong>Force Renew</strong> — Nuclear option: resets CPCL state, restarts CWC. Use when CWC is stuck.</li>
              <li><strong>Validate JWKS</strong> — Check and fix the JWKS ConfigMap (cpcl-key-cm) if it has placeholder keys</li>
              <li><strong>Telemetry</strong> — In connected mode, CWC handles reporting automatically via the internet</li>
            </ul>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function DetailItem({
  icon: Icon,
  label,
  value,
  warn = false,
}: {
  icon: typeof Shield;
  label: string;
  value: string;
  warn?: boolean;
}) {
  return (
    <div className="flex items-start gap-2.5">
      <Icon className={cn(
        'h-4 w-4 shrink-0 mt-0.5',
        warn ? 'text-warning' : 'text-muted-foreground',
      )} />
      <div>
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className={cn(
          'text-sm font-medium',
          warn ? 'text-warning' : 'text-foreground',
        )}>
          {value}
        </p>
      </div>
    </div>
  );
}
