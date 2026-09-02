/**
 * QKViewPanel — CWC diagnostic tarball collection for F5 BNK clusters.
 *
 * Provides a UI to:
 *   - Complete first-time QKView mTLS setup for CWC, when needed
 *   - Create new QKView collections (with optional filters)
 *   - List existing QKView jobs with status
 *   - Monitor in-progress QKViews
 *   - Download completed QKView tarballs
 *   - Delete/cancel QKViews
 *
 * Docs: https://clouddocs.f5.com/bigip-next-for-kubernetes/latest/troubleshooting/spk-qkview-ihealth.html
 */

import { useState, useCallback } from 'react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card } from '@/components/ui/card';
import { Switch } from '@/components/ui/switch';
import {
  Archive,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  Download,
  ExternalLink,
  FileArchive,
  Loader2,
  Plus,
  RefreshCw,
  Shield,
  ShieldCheck,
  SkipForward,
  Stethoscope,
  Trash2,
  XCircle,
  Ban,
  AlertTriangle,
  Info,
} from 'lucide-react';
import { notify } from '@/lib/notify';
import {
  useQKViewCheck,
  useQKViewList,
  useCreateQKView,
  useDeleteQKView,
  useCancelQKView,
  useDownloadQKView,
  useQKViewStatus,
  useCWCAPISetupStatus,
  useCWCAPISetup,
} from '@/hooks/useQKView';
import type { QKViewItem, QKViewStatusNested } from '@/types';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface QKViewPanelProps {
  clusterId: number;
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

export function QKViewPanel({ clusterId }: QKViewPanelProps) {
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [expandedItem, setExpandedItem] = useState<string | null>(null);

  // Hooks
  const { data: checkData, isLoading: checkLoading } = useQKViewCheck(clusterId);
  const cwcAvailable = checkData?.available === true;

  // Setup status — run concurrently when clusterId is valid
  const { data: setupData, isLoading: setupLoading } = useCWCAPISetupStatus(
    clusterId,
    clusterId > 0,
  );
  const setupComplete = setupData?.setup_complete === true;

  // QKView list — only fetch when CWC is available AND setup is complete
  const { data: listData, isLoading: listLoading, refetch: refetchList } = useQKViewList(
    clusterId,
    cwcAvailable && setupComplete,
  );

  if ((checkLoading && !checkData) || (cwcAvailable && setupLoading && !setupData)) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-primary" />
        <span className="ml-3 text-sm text-muted-foreground">
          {checkLoading ? 'Checking CWC availability...' : 'Checking setup status...'}
        </span>
      </div>
    );
  }

  if (!cwcAvailable) {
    return (
      <div className="rounded-lg border border-border bg-card p-6 text-center space-y-3">
        <div className="flex justify-center">
          <AlertTriangle className="h-8 w-8 text-warning" />
        </div>
        <h3 className="font-semibold text-base">CWC Not Available For QKView</h3>
        <p className="text-sm max-w-md mx-auto text-muted-foreground">
          {checkData?.message
              || 'The Cluster-Wide Controller (CWC) REST API is not reachable on this cluster. QKView diagnostics require a running CWC instance with working mTLS.'}
        </p>
      </div>
    );
  }

  // Show setup flow if cert-manager setup hasn't been done yet
  if (!setupComplete) {
    return (
      <SetupCertManagerFlow
        clusterId={clusterId}
        setupData={setupData}
      />
    );
  }

  const qkviews = listData?.qkviews || [];

  return (
    <div className="space-y-6">
      {/* Header Info */}
      <div className="rounded-lg border border-border bg-muted/50 p-5 flex items-start gap-4">
        <FileArchive className="h-6 w-6 mt-0.5 shrink-0 text-primary" />
        <div className="flex-1">
          <h3 className="font-semibold text-base mb-1">QKView Diagnostics</h3>
          <p className="text-sm text-muted-foreground">
            Collect diagnostic data from F5 BNK pods, containers, and cluster resources into a
            tarball that can be uploaded to{' '}
            <a
              href="https://ihealth.f5.com/qkview-analyzer/"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline inline-flex items-center gap-1"
            >
              F5 iHealth <ExternalLink className="h-3 w-3" />
            </a>{' '}
            for analysis. Licensing and CWC connectivity checks live in the Licensing tab.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => refetchList()}
            disabled={listLoading}
            className="gap-1.5"
          >
            <RefreshCw className={cn('h-3.5 w-3.5', listLoading && 'animate-spin')} />
            Refresh
          </Button>
          <Button
            size="sm"
            onClick={() => setShowCreateForm(!showCreateForm)}
            className="gap-1.5"
          >
            <Plus className="h-3.5 w-3.5" />
            New QKView
          </Button>
        </div>
      </div>

      {/* Create Form */}
      {showCreateForm && (
        <CreateQKViewForm
          clusterId={clusterId}
          onClose={() => setShowCreateForm(false)}
        />
      )}

      {/* List */}
      {listLoading && qkviews.length === 0 ? (
        <div className="flex items-center justify-center py-8">
          <Loader2 className="h-6 w-6 animate-spin text-primary" />
          <span className="ml-2 text-sm text-muted-foreground">Loading QKViews...</span>
        </div>
      ) : qkviews.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border p-8 text-center">
          <Archive className="h-10 w-10 mx-auto mb-3 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">
            No QKView collections yet. Click "New QKView" to create one.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          <h4 className="text-sm font-medium">QKView Collections</h4>
          {qkviews.map((qk, idx) => (
            <QKViewItemCard
              key={qk.id || idx}
              item={qk}
              clusterId={clusterId}
              expanded={expandedItem === (qk.id || String(idx))}
              onToggleExpand={() => setExpandedItem(
                expandedItem === (qk.id || String(idx)) ? null : (qk.id || String(idx))
              )}
            />
          ))}
        </div>
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// First-time Setup Flow — cert-manager integration
// ---------------------------------------------------------------------------

interface SetupFlowProps {
  clusterId: number;
  setupData: { setup_complete: boolean; cert_manager_available: boolean; server_cert_exists: boolean; client_cert_exists: boolean; message: string } | undefined;
}

function SetupCertManagerFlow({ clusterId, setupData }: SetupFlowProps) {
  const [confirmed, setConfirmed] = useState(false);
  const setupMutation = useCWCAPISetup();

  const certManagerAvailable = setupData?.cert_manager_available ?? false;

  const handleRunSetup = useCallback(() => {
    setupMutation.mutate(clusterId, {
      onSuccess: (result) => {
        notify.success(
          'Setup Complete',
          result.message || 'CWC API mTLS setup finished successfully.',
          { category: 'cluster' },
        );
      },
    });
  }, [clusterId, setupMutation]);

  // Setup is running — show progress
  if (setupMutation.isPending) {
    return (
      <div className="rounded-lg border border-border bg-muted/50 p-6 space-y-4">
        <div className="flex items-center gap-3">
          <Loader2 className="h-6 w-6 animate-spin text-primary" />
          <div>
            <h3 className="font-semibold">Setting up QKView certificates...</h3>
            <p className="text-sm mt-1 text-muted-foreground">
              Creating CWC API certificates, updating CWC secrets, and restarting
              the CWC pod. This may take 1-2 minutes.
            </p>
          </div>
        </div>
        <div className="rounded-md bg-muted/50 px-3 py-2 text-xs space-y-1.5">
          <p className="text-muted-foreground">Steps in progress:</p>
          <ul className="space-y-1 ml-4 list-disc text-muted-foreground">
            <li>Create server certificate (cert-manager)</li>
            <li>Update cwc-license-certs secret</li>
            <li>Restart CWC pod</li>
            <li>Create client certificate (cert-manager)</li>
            <li>Clean up stale client pods</li>
          </ul>
        </div>
      </div>
    );
  }

  // Setup failed — show error with retry
  if (setupMutation.isError) {
    const errMsg = setupMutation.error instanceof Error
      ? setupMutation.error.message
      : String(setupMutation.error);

    return (
      <div className="rounded-lg border-l-2 border-destructive border-y border-r border-y-border border-r-border bg-card p-6 space-y-4">
        <div className="flex items-start gap-3">
          <XCircle className="h-6 w-6 text-destructive shrink-0 mt-0.5" />
          <div>
            <h3 className="font-semibold text-destructive">CWC API mTLS Setup Failed</h3>
            <p className="text-sm mt-1 text-muted-foreground">
              {errMsg}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setupMutation.reset()}
            className="gap-1.5"
          >
            Back
          </Button>
          <Button
            size="sm"
            onClick={handleRunSetup}
            className="gap-1.5"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Retry Setup
          </Button>
        </div>
      </div>
    );
  }

  // Setup succeeded — show results (will auto-transition when setupStatus re-fetches)
  if (setupMutation.isSuccess && setupMutation.data) {
    return (
      <div className="rounded-lg border-l-2 border-success border-y border-r border-y-border border-r-border bg-card p-6 space-y-4">
        <div className="flex items-start gap-3">
          <ShieldCheck className="h-6 w-6 text-success shrink-0 mt-0.5" />
          <div>
            <h3 className="font-semibold text-success">Setup Complete</h3>
            <p className="text-sm mt-1 text-muted-foreground">
              {setupMutation.data.message}
            </p>
          </div>
        </div>
        {setupMutation.data.steps && (
          <div className="space-y-1">
            {setupMutation.data.steps.map((step, i: number) => (
              <div key={i} className="flex items-center gap-2 text-xs">
                {step.status === 'ok' ? (
                  <CheckCircle2 className="h-3 w-3 text-success shrink-0" />
                ) : (
                  <SkipForward className="h-3 w-3 text-muted-foreground shrink-0" />
                )}
                <span className="font-mono text-muted-foreground">
                  {step.step.replace(/_/g, ' ')}
                </span>
                {step.detail && (
                  <span className="text-[11px] text-muted-foreground">— {step.detail}</span>
                )}
              </div>
            ))}
          </div>
        )}
        <p className="text-xs text-muted-foreground">
          Loading QKView interface...
        </p>
      </div>
    );
  }

  // Default: show setup prompt
  return (
    <div className="rounded-lg border border-border bg-muted/50 p-6 space-y-5">
      <div className="flex items-start gap-4">
        <Shield className="h-8 w-8 shrink-0 mt-0.5 text-warning" />
        <div className="flex-1 space-y-2">
          <h3 className="font-semibold text-base">Certificate Setup Required</h3>
          <p className="text-sm text-muted-foreground">
            {setupData?.message || 'QKView requires a one-time certificate setup to communicate with the CWC REST API.'}
          </p>
        </div>
      </div>

      {!certManagerAvailable ? (
        <div className="rounded-md bg-destructive/10 text-destructive border border-destructive/20 px-4 py-3 text-sm">
          <p className="font-medium">cert-manager not available</p>
          <p className="text-xs mt-1 text-muted-foreground">
            The <code className="font-mono">bnk-ca-cluster-issuer</code> ClusterIssuer was not found.
            cert-manager must be installed and the ClusterIssuer must exist before setup can proceed.
          </p>
        </div>
      ) : (
        <>
          {/* What will happen */}
          <div className="rounded-md bg-muted/50 px-4 py-3 space-y-2">
            <p className="text-xs font-medium text-muted-foreground">This setup will:</p>
            <ul className="text-xs space-y-1.5 ml-4 text-muted-foreground">
              <li className="flex items-start gap-2">
                <span className="text-primary mt-0.5">1.</span>
                Create a cert-manager <code className="font-mono text-[11px]">Certificate</code> for the CWC
                REST API server (replaces legacy self-signed cert)
              </li>
              <li className="flex items-start gap-2">
                <span className="text-primary mt-0.5">2.</span>
                Update the <code className="font-mono text-[11px]">cwc-license-certs</code> secret with the
                new certificate
              </li>
              <li className="flex items-start gap-2">
                <span className="text-warning mt-0.5">3.</span>
                <span>
                  <strong>Restart the CWC pod</strong> so it picks up the new certs
                  <span className="block text-[11px] mt-0.5 text-muted-foreground">
                    Licensing and telemetry are unaffected. The restart takes ~30 seconds.
                  </span>
                </span>
              </li>
              <li className="flex items-start gap-2">
                <span className="text-primary mt-0.5">4.</span>
                Create a client certificate for BNK-Forge to authenticate with the CWC API
              </li>
            </ul>
          </div>

          {/* Warning */}
          <div className="flex items-start gap-2 rounded-md bg-warning/10 text-warning border border-warning/20 px-3 py-2 text-xs">
            <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
            <span>
              The CWC pod will be restarted during this process. This is a one-time operation
              and takes approximately 1-2 minutes. All certificates will be automatically
              renewed by cert-manager before expiry.
            </span>
          </div>

          {/* Partial status */}
          {(setupData?.server_cert_exists || setupData?.client_cert_exists) && (
            <div className="flex items-center gap-4 text-xs text-muted-foreground">
              <span className="flex items-center gap-1.5">
                {setupData.server_cert_exists ? (
                  <CheckCircle2 className="h-3 w-3 text-success" />
                ) : (
                  <Clock className="h-3 w-3" />
                )}
                Server cert
              </span>
              <span className="flex items-center gap-1.5">
                {setupData.client_cert_exists ? (
                  <CheckCircle2 className="h-3 w-3 text-success" />
                ) : (
                  <Clock className="h-3 w-3" />
                )}
                Client cert
              </span>
            </div>
          )}

          {/* Confirmation + action */}
          <div className="flex items-center justify-between pt-2">
            <label className="flex items-center gap-2 text-xs cursor-pointer text-muted-foreground">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
                className="rounded border-border"
              />
              I understand the CWC pod will be restarted
            </label>
            <Button
              size="sm"
              onClick={handleRunSetup}
              disabled={!confirmed}
              className="gap-1.5"
            >
              <Shield className="h-3.5 w-3.5" />
              Run Setup
            </Button>
          </div>
        </>
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Create Form
// ---------------------------------------------------------------------------

interface CreateFormProps {
  clusterId: number;
  onClose: () => void;
}

function CreateQKViewForm({ clusterId, onClose }: CreateFormProps) {
  const createMutation = useCreateQKView();

  const [filename, setFilename] = useState('');
  const [namespace, setNamespace] = useState('');
  const [timeout, setTimeout] = useState('');
  const [podPatterns, setPodPatterns] = useState('');
  const [includeCoreFiles, setIncludeCoreFiles] = useState(false);
  const [coreFilesMax, setCoreFilesMax] = useState(2);

  const handleCreate = useCallback(() => {
    const data: Parameters<typeof createMutation.mutate>[0] = { cluster_id: clusterId };
    if (filename.trim()) data.filename = filename.trim();
    if (namespace.trim()) data.namespace = namespace.trim();
    if (timeout.trim()) data.timeout = timeout.trim();
    if (podPatterns.trim()) {
      data.pod_patterns = podPatterns.split(',').map(p => p.trim()).filter(Boolean);
    }
    if (includeCoreFiles) {
      data.include_core_files = true;
      data.core_files_max = coreFilesMax;
    }

    createMutation.mutate(data, {
      onSuccess: (result) => {
        const id = result?.id || result?.filename;
        notify.success('QKView Created', id ? `ID: ${id}` : 'Collection started — check the list for status', { category: 'cluster' });
        onClose();
      },
    });
  }, [clusterId, filename, namespace, timeout, podPatterns, includeCoreFiles, coreFilesMax, createMutation, onClose]);

  return (
    <Card className="p-5 space-y-4 bg-card">
      <div className="flex items-center justify-between">
        <h4 className="font-semibold text-sm">Create QKView Collection</h4>
        <Button variant="ghost" size="sm" onClick={onClose} className="h-7 w-7 p-0">
          <XCircle className="h-4 w-4" />
        </Button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="space-y-1.5">
          <Label className="text-xs">Filename (optional)</Label>
          <Input
            placeholder="e.g. troubleshoot-vlan"
            value={filename}
            onChange={(e) => setFilename(e.target.value)}
            className="h-8 text-sm"
          />
          <p className="text-xs text-muted-foreground">
            Alphanumeric, underscores, hyphens only
          </p>
        </div>

        <div className="space-y-1.5">
          <Label className="text-xs">Namespace (optional)</Label>
          <Input
            placeholder="All namespaces"
            value={namespace}
            onChange={(e) => setNamespace(e.target.value)}
            className="h-8 text-sm"
          />
          <p className="text-xs text-muted-foreground">
            Limit collection to a specific namespace
          </p>
        </div>

        <div className="space-y-1.5">
          <Label className="text-xs">Timeout</Label>
          <Input
            placeholder="1h"
            value={timeout}
            onChange={(e) => setTimeout(e.target.value)}
            className="h-8 text-sm"
          />
          <p className="text-xs text-muted-foreground">
            Go duration string (e.g. 30m, 1h, 2h)
          </p>
        </div>

        <div className="space-y-1.5">
          <Label className="text-xs">Pod patterns (optional)</Label>
          <Input
            placeholder="f5-tmm*, f5-coremond*"
            value={podPatterns}
            onChange={(e) => setPodPatterns(e.target.value)}
            className="h-8 text-sm"
          />
          <p className="text-xs text-muted-foreground">
            Comma-separated pod name patterns. Default: all F5 pods
          </p>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <Switch
          checked={includeCoreFiles}
          onCheckedChange={setIncludeCoreFiles}
        />
        <Label className="text-xs cursor-pointer">
          Include core files
        </Label>
        {includeCoreFiles && (
          <div className="flex items-center gap-2 ml-4">
            <Label className="text-xs">Max per node:</Label>
            <Input
              type="number"
              min={1}
              max={10}
              value={coreFilesMax}
              onChange={(e) => setCoreFilesMax(Number(e.target.value))}
              className="h-7 w-16 text-xs"
            />
          </div>
        )}
      </div>

      {includeCoreFiles && (
        <div className="flex items-start gap-2 rounded-md bg-warning/10 text-warning border border-warning/20 px-3 py-2 text-xs">
          <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          <span>
            Core files can be very large. If the tarball exceeds 8 GB it cannot be uploaded to iHealth.
            Consider filtering by specific pods.
          </span>
        </div>
      )}

      <div className="flex items-center justify-end gap-2 pt-2">
        <Button variant="outline" size="sm" onClick={onClose}>
          Cancel
        </Button>
        <Button
          size="sm"
          onClick={handleCreate}
          disabled={createMutation.isPending}
          className="gap-1.5"
        >
          {createMutation.isPending ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Creating...
            </>
          ) : (
            <>
              <Stethoscope className="h-3.5 w-3.5" />
              Collect QKView
            </>
          )}
        </Button>
      </div>
    </Card>
  );
}


// ---------------------------------------------------------------------------
// QKView Item Card
// ---------------------------------------------------------------------------

interface QKViewItemCardProps {
  item: QKViewItem;
  clusterId: number;
  expanded: boolean;
  onToggleExpand: () => void;
}

function QKViewItemCard({
  item,
  clusterId,
  expanded,
  onToggleExpand,
}: QKViewItemCardProps) {
  const deleteMutation = useDeleteQKView();
  const cancelMutation = useCancelQKView();
  const downloadMutation = useDownloadQKView();

  const qkviewId = item.id || '';
  const isInProgress = item.status?.toLowerCase() === 'in_progress' ||
                       item.status?.toLowerCase() === 'running' ||
                       item.status?.toLowerCase() === 'inprogress';
  const isCompleted = item.status?.toLowerCase() === 'completed' ||
                      item.status?.toLowerCase() === 'complete';
  const isFailed = item.status?.toLowerCase() === 'failed' ||
                   item.status?.toLowerCase() === 'error';
  const isWarning = item.status?.toLowerCase() === 'warning';

  // Poll status for in-progress items
  const { data: statusData } = useQKViewStatus(clusterId, qkviewId, isInProgress && !!qkviewId);

  const statusBadge = () => {
    if (isInProgress) return (
      <Badge variant="info">
        <Loader2 className="h-3 w-3 mr-1 animate-spin" />
        In Progress
      </Badge>
    );
    if (isCompleted) return (
      <Badge variant="success">
        <CheckCircle2 className="h-3 w-3 mr-1" />
        Completed
      </Badge>
    );
    if (isFailed) return (
      <Badge variant="destructive">
        <XCircle className="h-3 w-3 mr-1" />
        Failed
      </Badge>
    );
    if (isWarning) return (
      <Badge variant="warning">
        <AlertTriangle className="h-3 w-3 mr-1" />
        Warning
      </Badge>
    );
    return (
      <Badge variant="outline" className="text-xs">
        {item.status || 'Unknown'}
      </Badge>
    );
  };

  return (
    <div className="rounded-lg border border-border bg-card transition-colors">
      {/* Header */}
      <div
        className="flex items-center justify-between p-4 cursor-pointer"
        onClick={onToggleExpand}
      >
        <div className="flex items-center gap-3">
          <div className={cn('p-1.5 rounded-md', isInProgress ? 'bg-info/10' : 'bg-muted')}>
            <FileArchive className={cn('h-4 w-4', isInProgress ? 'text-info' : 'text-muted-foreground')} />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium font-mono">
                {item.filename || qkviewId || 'QKView'}
              </span>
              {statusBadge()}
            </div>
            {qkviewId && item.filename && (
              <span className="text-xs font-mono text-muted-foreground">
                ID: {qkviewId}
              </span>
            )}
          </div>
        </div>

        <div className="flex items-center gap-2">
          {/* Action buttons */}
          {(isCompleted || isWarning) && (
            <Button
              variant="outline"
              size="sm"
              onClick={(e) => {
                e.stopPropagation();
                downloadMutation.mutate({
                  qkviewId,
                  clusterId,
                  filename: item.filename ? `${item.filename}.tar.gz` : undefined,
                });
              }}
              disabled={downloadMutation.isPending}
              className="gap-1 h-7 text-xs"
            >
              {downloadMutation.isPending ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <Download className="h-3 w-3" />
              )}
              Download
            </Button>
          )}

          {isInProgress && (
            <Button
              variant="outline"
              size="sm"
              onClick={(e) => {
                e.stopPropagation();
                cancelMutation.mutate({ qkviewId, clusterId }, {
                  onSuccess: () => notify.success('QKView Cancelled', `Cancelled ${qkviewId}`, { category: 'cluster' }),
                });
              }}
              disabled={cancelMutation.isPending}
              className="gap-1 h-7 text-xs text-warning hover:text-warning"
            >
              <Ban className="h-3 w-3" />
              Cancel
            </Button>
          )}

          {!isInProgress && (
            <Button
              variant="ghost"
              size="sm"
              onClick={(e) => {
                e.stopPropagation();
                deleteMutation.mutate({ qkviewId, clusterId }, {
                  onSuccess: () => notify.success('QKView Deleted', `Deleted ${qkviewId}`, { category: 'cluster' }),
                });
              }}
              disabled={deleteMutation.isPending}
              className="gap-1 h-7 text-xs text-destructive hover:text-destructive"
            >
              <Trash2 className="h-3 w-3" />
            </Button>
          )}

          {expanded ? (
            <ChevronDown className="h-4 w-4 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-4 w-4 text-muted-foreground" />
          )}
        </div>
      </div>

      {/* Expanded Details */}
      {expanded && (
        <div className="border-t border-border px-4 pb-4 pt-3 space-y-3">
          {/* Metadata */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {item.namespace && (
              <DetailKV label="Namespace" value={item.namespace} />
            )}
            {item.timeout && (
              <DetailKV label="Timeout" value={item.timeout} />
            )}
            {item.pod_patterns && item.pod_patterns.length > 0 && (
              <DetailKV
                label="Pod Patterns"
                value={item.pod_patterns.join(', ')}
              />
            )}
          </div>

          {/* Status tree (for in-progress or completed with nested details) */}
          {statusData?.nested && (
            <div className="space-y-2">
              <span className="text-xs font-medium text-muted-foreground">Status Details:</span>
              <StatusTree node={statusData.nested} depth={0} />
            </div>
          )}

          {/* iHealth upload link for completed */}
          {(isCompleted || isWarning) && (
            <div className="flex items-start gap-2 rounded-md bg-info/10 text-info border border-info/20 px-3 py-2 text-xs">
              <Info className="h-3.5 w-3.5 mt-0.5 shrink-0" />
              <span>
                Download the tarball and upload it to{' '}
                <a
                  href="https://ihealth.f5.com/qkview-analyzer/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="underline hover:no-underline"
                >
                  F5 iHealth
                </a>{' '}
                for automated analysis. Max upload size: 8 GB.
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Detail Key-Value
// ---------------------------------------------------------------------------

function DetailKV({ label, value }: {
  label: string;
  value: string;
}) {
  return (
    <div>
      <span className="text-xs block text-muted-foreground">{label}</span>
      <span className="text-xs font-mono text-foreground/80">
        {value}
      </span>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Status Tree — recursive display of nested QKView status
// ---------------------------------------------------------------------------

function StatusTree({ node, depth }: {
  node: Record<string, QKViewStatusNested>;
  depth: number;
}) {
  return (
    <div className={cn('space-y-1', depth > 0 && 'ml-4')}>
      {Object.entries(node).map(([key, value]) => {
        const isOk = value.status?.toLowerCase() === 'completed';
        const isFail = value.status?.toLowerCase() === 'failed';

        return (
          <div key={key}>
            <div className="flex items-center gap-2 text-xs">
              {isOk ? (
                <CheckCircle2 className="h-3 w-3 text-success shrink-0" />
              ) : isFail ? (
                <XCircle className="h-3 w-3 text-destructive shrink-0" />
              ) : (
                <Clock className="h-3 w-3 shrink-0 text-muted-foreground" />
              )}
              <span className="font-mono text-foreground/80">
                {key}
              </span>
              <Badge
                variant={isOk ? 'success' : isFail ? 'destructive' : 'outline'}
                className="text-[10px]"
              >
                {value.status}
              </Badge>
            </div>
            {value.msgs && value.msgs.length > 0 && (
              <div className="ml-5 mt-0.5 space-y-0.5">
                {value.msgs.map((msg, i) => (
                  <p key={i} className={cn('text-[11px]', isFail ? 'text-destructive' : 'text-muted-foreground')}>
                    {msg}
                  </p>
                ))}
              </div>
            )}
            {value.nested && (
              <StatusTree node={value.nested} depth={depth + 1} />
            )}
          </div>
        );
      })}
    </div>
  );
}
