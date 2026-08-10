/**
 * BareMetalPanel — Host management, discovery, and deployment for bare-metal DPU projects.
 *
 * Visual template: NodeDiscoveryPanel (discovery tab) — expandable rows with status badges.
 */
import { useState, useCallback, useEffect, useRef } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircuitBoard,
  Copy,
  Cpu,
  Info,
  Key,
  Link,
  Loader2,
  Network,
  Plus,
  Rocket,
  Search,
  Server,
  Save,
  Trash2,
  XCircle,
} from 'lucide-react';
import { api } from '@/lib/api';
import { bareMetalDeploymentsApi } from '@/lib/api/bare-metal';
import { sshCredentialsApi } from '@/lib/api/ssh-credentials';
import { queryKeys } from '@/lib/queryKeys';
import { useProject } from '@/hooks/useProjects';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import {
  useBareMetalHostsWithPolling,
  useCreateBareMetalHost,
  useUpdateBareMetalHost,
  useDeleteBareMetalHost,
  useTriggerBareMetalDiscovery,
  useBareMetalDiscoveryResult,
  useBareMetalDeployments,
  useCreateBareMetalDeployment,
  useBareMetalDeployment,
  useCancelBareMetalDeployment,
  useBnkVersionProfiles,
} from '@/hooks/useBareMetal';
import { notify, notifyError } from '@/lib/notify';
import { cn } from '@/lib/utils';
import {
  TOPOLOGY_LABELS,
  DEPLOYMENT_PHASE_LABELS,
} from '@/types/bare-metal';
import type {
  ActionableAssessmentSection,
  BareMetalHost,
  BareMetalHostCreate,
  BareMetalHostUpdate,
  BareMetalDiscoveryResponse,
  BareMetalDeployment,
  DeploymentStep,
  DeploymentPlanPreview,
} from '@/types/bare-metal';
import { DocaStatusCard } from './DocaStatusCard';

interface BareMetalPanelProps {
  projectId: number;
  isOwner: boolean;
}

export function BareMetalPanel({ projectId, isOwner }: BareMetalPanelProps) {
  const queryClient = useQueryClient();
  const [showAddForm, setShowAddForm] = useState(false);
  const [selectedHostId, setSelectedHostId] = useState<number | null>(null);
  const [activeDeploymentId, setActiveDeploymentId] = useState<number | null>(null);
  const [deployPreview, setDeployPreview] = useState<{ hostId: number; plan: DeploymentPlanPreview } | null>(null);
  const [isLoadingPreview, setIsLoadingPreview] = useState(false);

  const { data: project } = useProject(projectId);
  const projectSshCredentialId = project?.ssh_credential_id;

  const { data: hosts, isLoading: hostsLoading } = useBareMetalHostsWithPolling(projectId);
  const { data: profiles } = useBnkVersionProfiles();

  // Track previous discovery statuses to detect transitions
  const prevStatusRef = useRef<Record<number, string | null>>({});
  useEffect(() => {
    if (!hosts) return;
    const prev = prevStatusRef.current;
    for (const host of hosts) {
      const prevStatus = prev[host.id];
      const curStatus = host.last_discovery_status;
      // Detect completion transition
      if (
        prevStatus &&
        prevStatus !== 'completed' &&
        prevStatus !== 'failed' &&
        curStatus === 'completed'
      ) {
        notify.success('Discovery complete', `${host.name} (${host.host_ip})`, { category: 'deployment' });
        // Invalidate discovery result so it reloads
        queryClient.invalidateQueries({
          queryKey: queryKeys.bareMetal.hosts.discoveryResult(projectId, host.id),
        });
      }
      // Detect failure transition
      if (
        prevStatus &&
        prevStatus !== 'completed' &&
        prevStatus !== 'failed' &&
        curStatus === 'failed'
      ) {
        notifyError(
          host.last_discovery_error || 'Unknown error',
          `Discovery failed: ${host.name}`,
        );
      }
      prev[host.id] = curStatus;
    }
    prevStatusRef.current = prev;
  }, [hosts, projectId, queryClient]);

  const createHost = useCreateBareMetalHost(projectId);
  const deleteHost = useDeleteBareMetalHost(projectId);
  const triggerDiscovery = useTriggerBareMetalDiscovery(projectId);
  const { data: deployments } = useBareMetalDeployments(projectId);
  const createDeployment = useCreateBareMetalDeployment(projectId);

  // Load persisted discovery result for the selected host
  const { data: persistedDiscovery } = useBareMetalDiscoveryResult(projectId, selectedHostId);
  const effectiveDiscoveryResult = persistedDiscovery ?? null;

  // Fetch SSH credentials for the dropdown
  const { data: sshCredentials } = useQuery({
    queryKey: queryKeys.sshCredentials.list(),
    queryFn: () => api.listSSHCredentials(),
  });

  // --- Add Host Form ---
  const [newHost, setNewHost] = useState<Partial<BareMetalHostCreate>>({
    name: '',
    host_ip: '',
    ssh_port: 22,
  });

  // Inline SSH credential creation state
  const [hostCredMode, setHostCredMode] = useState<'existing' | 'new'>('existing');
  const [newHostCred, setNewHostCred] = useState({ name: '', username: 'root', auth_type: 'key' as 'key' | 'password', password: '', private_key: '' });

  const [jumpCredMode, setJumpCredMode] = useState<'existing' | 'new'>('existing');
  const [newJumpCred, setNewJumpCred] = useState({ name: '', host: '', username: '', auth_type: 'password' as 'key' | 'password', password: '', private_key: '' });
  const [dpuCred, setDpuCred] = useState({ username: 'ubuntu', password: 'password' });

  const handleCreateHost = useCallback(async () => {
    if (!newHost.name || !newHost.host_ip) return;
    try {
      const hostData = { ...newHost } as BareMetalHostCreate;

      // Step 1: Create inline SSH credential for host if needed
      if (hostCredMode === 'new' && newHostCred.username) {
        const credName = newHostCred.name || `${newHost.name}-ssh`;
        const cred = await sshCredentialsApi.createSSHCredential({
          name: credName,
          host: newHost.host_ip || '',
          port: newHost.ssh_port || 22,
          username: newHostCred.username,
          auth_type: newHostCred.auth_type,
          password: newHostCred.auth_type === 'password' ? newHostCred.password : undefined,
          private_key: newHostCred.auth_type === 'key' ? newHostCred.private_key : undefined,
        });
        hostData.ssh_credential_id = cred.id;
        queryClient.invalidateQueries({ queryKey: queryKeys.sshCredentials.all });
      }

      // Step 2: Create inline SSH credential for jumphost if needed
      if (jumpCredMode === 'new' && newJumpCred.username) {
        const credName = newJumpCred.name || `${newHost.name}-jumphost`;
        const jumpCred = await sshCredentialsApi.createSSHCredential({
          name: credName,
          host: newJumpCred.host || 'jumphost',
          port: 22,
          username: newJumpCred.username,
          auth_type: newJumpCred.auth_type,
          password: newJumpCred.auth_type === 'password' ? newJumpCred.password : undefined,
          private_key: newJumpCred.auth_type === 'key' ? newJumpCred.private_key : undefined,
        });
        hostData.jumphost_chain = [{ ssh_credential_id: jumpCred.id }];
        queryClient.invalidateQueries({ queryKey: queryKeys.sshCredentials.all });
      }

      // Step 2.5: Create DPU SSH credential if username/password provided
      if (dpuCred.username && dpuCred.password) {
        const credName = `${newHost.name || 'host'}-dpu`;
        const dpuCredResult = await sshCredentialsApi.createSSHCredential({
          name: credName,
          host: newHost.dpu_mgmt_ip || '192.168.100.2',
          port: 22,
          username: dpuCred.username,
          auth_type: 'password',
          password: dpuCred.password,
        });
        hostData.dpu_credential_id = dpuCredResult.id;
        queryClient.invalidateQueries({ queryKey: queryKeys.sshCredentials.all });
      }

      // Step 3: Create the host
      const host = await createHost.mutateAsync(hostData);
      setShowAddForm(false);
      setNewHost({ name: '', host_ip: '', ssh_port: 22, ssh_credential_id: undefined, jumphost_chain: undefined, bmc_ip: '', bmc_username: '', bmc_password: '', dpu_credential_id: undefined, dpu_mgmt_ip: '' });
      setHostCredMode('existing');
      setNewHostCred({ name: '', username: 'root', auth_type: 'key', password: '', private_key: '' });
      setJumpCredMode('existing');
      setNewJumpCred({ name: '', host: '', username: '', auth_type: 'password', password: '', private_key: '' });
      setDpuCred({ username: 'ubuntu', password: 'password' });
      setSelectedHostId(host.id);
    } catch (err) {
      notifyError(err, 'Failed to register host');
    }
  }, [newHost, hostCredMode, newHostCred, jumpCredMode, newJumpCred, dpuCred, createHost, queryClient]);

  const handleDeleteHost = useCallback(async (hostId: number, hostName: string) => {
    if (!confirm(`Delete host "${hostName}"? This removes all discovery data and deployments.`)) return;
    try {
      await deleteHost.mutateAsync(hostId);
      notify.success('Host deleted', hostName, { category: 'fleet' });
      if (selectedHostId === hostId) {
        setSelectedHostId(null);
      }
    } catch (err) {
      notifyError(err, 'Failed to delete host');
    }
  }, [deleteHost, selectedHostId]);

  const handleDiscover = useCallback(async (hostId: number, hostName: string) => {
    try {
      await triggerDiscovery.mutateAsync({
        hostId,
        data: { probe_bmc: true, probe_ipmi: true, probe_dpu: true, probe_vlan: false },
      });
      notify.success('Discovery queued', `${hostName} — polling for progress…`, { category: 'deployment' });
    } catch (err) {
      notifyError(err, 'Failed to trigger discovery');
    }
  }, [triggerDiscovery]);

  const handleDeploy = useCallback(async (hostId: number) => {
    try {
      setIsLoadingPreview(true);
      const plan = await bareMetalDeploymentsApi.previewDeployment(projectId, { host_id: hostId });
      setDeployPreview({ hostId, plan });
    } catch (err) {
      notifyError(err, 'Failed to load deployment plan');
    } finally {
      setIsLoadingPreview(false);
    }
  }, [projectId]);

  const handleConfirmDeploy = useCallback(async () => {
    if (!deployPreview) return;
    try {
      const deployment = await createDeployment.mutateAsync({ host_id: deployPreview.hostId });
      setActiveDeploymentId(deployment.id);
      setDeployPreview(null);
      notify.success('Deployment started', `Deployment #${deployment.id}`, { category: 'deployment' });
    } catch (err) {
      notifyError(err, 'Failed to start deployment');
    }
  }, [deployPreview, createDeployment]);

  // If previewing a deployment plan, show confirmation
  if (deployPreview) {
    const { plan, hostId } = deployPreview;
    const host = hosts?.find(h => h.id === hostId);
    const formatDuration = (s: number) => s >= 60 ? `${Math.round(s / 60)}m` : `${s}s`;

    return (
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-semibold flex items-center gap-2">
              <Rocket className="h-5 w-5" />
              Deployment Plan
            </h2>
            <p className="text-sm text-muted-foreground mt-1">
              {host?.name} ({host?.host_ip}) — topology: <strong>{plan.topology}</strong>
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={() => setDeployPreview(null)}>
            Back
          </Button>
        </div>

        {/* Summary */}
        <Card className="p-4">
          <div className="grid grid-cols-3 gap-4 text-sm">
            <div>
              <span className="text-muted-foreground">Steps:</span>{' '}
              <strong>{plan.selected_steps}</strong> of {plan.total_steps}
            </div>
            <div>
              <span className="text-muted-foreground">Estimated time:</span>{' '}
              <strong>{formatDuration(plan.estimated_total_duration_seconds)}</strong>
            </div>
            <div>
              <span className="text-muted-foreground">Connectivity risk steps:</span>{' '}
              <strong>{plan.steps.filter(s => s.selected && s.connectivity_risk).length}</strong>
            </div>
          </div>
        </Card>

        {/* Prerequisite warnings */}
        {plan.prerequisite_warnings.length > 0 && (
          <div className="space-y-2">
            {plan.prerequisite_warnings.map((w, i) => (
              <div key={i} className="flex items-start gap-2 px-3 py-2 rounded-md bg-warning/10 border border-warning/20 text-sm text-warning">
                <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
                <span>{w}</span>
              </div>
            ))}
          </div>
        )}

        {/* Step list */}
        <div className="space-y-1">
          {plan.steps.map((step) => (
            <div
              key={step.step_index}
              className={cn(
                "flex items-center gap-3 px-3 py-2 rounded-md border text-sm",
                step.selected
                  ? "bg-background border-border"
                  : "bg-muted/30 border-transparent opacity-50",
              )}
            >
              <span className="text-muted-foreground w-6 text-right shrink-0">
                {step.step_index + 1}.
              </span>
              <div className="flex-1 min-w-0">
                <div className="font-medium truncate">{step.name}</div>
                <div className="text-xs text-muted-foreground truncate">{step.description}</div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {step.connectivity_risk && (
                  <Badge variant="outline" className="bg-destructive/10 text-destructive border-destructive/20 text-xs">
                    <AlertTriangle className="h-3 w-3 mr-1" />connectivity risk
                  </Badge>
                )}
                <span className="text-xs text-muted-foreground">
                  ~{formatDuration(step.estimated_duration_seconds)}
                </span>
                <Badge variant="outline" className="text-xs">
                  {DEPLOYMENT_PHASE_LABELS[step.phase] || step.phase}
                </Badge>
              </div>
            </div>
          ))}
        </div>

        {/* Confirm / Cancel */}
        <div className="flex gap-3 justify-end">
          <Button variant="outline" onClick={() => setDeployPreview(null)}>
            Cancel
          </Button>
          <Button
            onClick={handleConfirmDeploy}
            disabled={createDeployment.isPending}
          >
            {createDeployment.isPending ? (
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
            ) : (
              <Rocket className="h-4 w-4 mr-2" />
            )}
            Start Deployment ({plan.selected_steps} steps)
          </Button>
        </div>
      </div>
    );
  }

  // If viewing a deployment, show the progress view
  if (activeDeploymentId) {
    return (
      <DeploymentProgressView
        projectId={projectId}
        deploymentId={activeDeploymentId}
        onBack={() => setActiveDeploymentId(null)}
        isOwner={isOwner}
      />
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold flex items-center gap-2">
            <CircuitBoard className="h-5 w-5" />
            Bare Metal Hosts
          </h2>
          <p className="text-sm text-muted-foreground mt-1">
            Register servers, run discovery, and deploy BNK with DPU acceleration
          </p>
        </div>
        {isOwner && (
          <Button onClick={() => setShowAddForm(!showAddForm)} size="sm">
            <Plus className="h-4 w-4 mr-1" />Add Host
          </Button>
        )}
      </div>

      {/* Add Host Form */}
      {showAddForm && (
        <Card className="p-4 space-y-4">
          {/* Section 1: Basic Info */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label htmlFor="host-name">Host Name *</Label>
              <Input
                id="host-name"
                value={newHost.name || ''}
                onChange={(e) => setNewHost(prev => ({ ...prev, name: e.target.value }))}
                placeholder="dpu-server-01"
              />
            </div>
            <div>
              <Label htmlFor="host-ip">Host IP *</Label>
              <Input
                id="host-ip"
                value={newHost.host_ip || ''}
                onChange={(e) => setNewHost(prev => ({ ...prev, host_ip: e.target.value }))}
                placeholder="10.176.11.91"
              />
            </div>
          </div>

          {/* Section 2: SSH Access */}
          <div className="rounded-lg border p-3 space-y-3 bg-muted/30">
            <div className="flex items-center gap-2 text-sm font-medium">
              <Key className="h-4 w-4" />
              SSH Access
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label htmlFor="ssh-credential">SSH Credential</Label>
                {hostCredMode === 'existing' ? (
                  <>
                    <select
                      id="ssh-credential"
                      className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm"
                      value={newHost.ssh_credential_id || ''}
                      onChange={(e) => {
                        if (e.target.value === '__new__') {
                          setHostCredMode('new');
                          setNewHost(prev => ({ ...prev, ssh_credential_id: undefined }));
                        } else {
                          setNewHost(prev => ({ ...prev, ssh_credential_id: e.target.value ? parseInt(e.target.value) : undefined }));
                        }
                      }}
                    >
                      <option value="">None (use ssh-agent)</option>
                      {sshCredentials?.map(cred => (
                        <option key={cred.id} value={cred.id}>
                          {cred.name} ({cred.username}@{cred.host}:{cred.port})
                        </option>
                      ))}
                      <option value="__new__">+ Create new credential...</option>
                    </select>
                    <p className="text-xs text-muted-foreground mt-1">
                      Select existing or create a new credential inline
                    </p>
                  </>
                ) : (
                  <div className="space-y-2 rounded border p-2 bg-background">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-medium text-primary">New credential for host</span>
                      <button
                        type="button"
                        className="text-xs text-muted-foreground hover:text-foreground underline"
                        onClick={() => { setHostCredMode('existing'); }}
                      >
                        Cancel
                      </button>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      <div>
                        <Label className="text-xs">Username *</Label>
                        <Input
                          value={newHostCred.username}
                          onChange={(e) => setNewHostCred(prev => ({ ...prev, username: e.target.value }))}
                          placeholder="root"
                          className="h-8 text-sm"
                        />
                      </div>
                      <div>
                        <Label className="text-xs">Credential Name</Label>
                        <Input
                          value={newHostCred.name}
                          onChange={(e) => setNewHostCred(prev => ({ ...prev, name: e.target.value }))}
                          placeholder={`${newHost.name || 'host'}-ssh`}
                          className="h-8 text-sm"
                        />
                      </div>
                    </div>
                    <div>
                      <div className="flex gap-2 mb-1">
                        <button
                          type="button"
                          className={cn('text-xs px-2 py-0.5 rounded', newHostCred.auth_type === 'key' ? 'bg-primary text-primary-foreground' : 'bg-muted')}
                          onClick={() => setNewHostCred(prev => ({ ...prev, auth_type: 'key' }))}
                        >
                          SSH Key
                        </button>
                        <button
                          type="button"
                          className={cn('text-xs px-2 py-0.5 rounded', newHostCred.auth_type === 'password' ? 'bg-primary text-primary-foreground' : 'bg-muted')}
                          onClick={() => setNewHostCred(prev => ({ ...prev, auth_type: 'password' }))}
                        >
                          Password
                        </button>
                      </div>
                      {newHostCred.auth_type === 'password' ? (
                        <Input
                          type="password"
                          value={newHostCred.password}
                          onChange={(e) => setNewHostCred(prev => ({ ...prev, password: e.target.value }))}
                          placeholder="Password"
                          className="h-8 text-sm"
                        />
                      ) : (
                        <textarea
                          value={newHostCred.private_key}
                          onChange={(e) => setNewHostCred(prev => ({ ...prev, private_key: e.target.value }))}
                          placeholder="Paste private key (PEM format)"
                          className="flex w-full rounded-md border border-input bg-transparent px-3 py-1 text-xs font-mono h-20 resize-none"
                        />
                      )}
                    </div>
                  </div>
                )}
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <Label htmlFor="jumphost-credential">
                    {projectSshCredentialId
                      ? 'Jumphost (optional — project default will be used)'
                      : 'Jumphost / Bastion'}
                  </Label>
                  {projectSshCredentialId && (
                    <Badge variant="outline" className="text-xs bg-info/10 text-info border-info/20">
                      <Link className="h-3 w-3 mr-1" />
                      Project jumphost configured
                    </Badge>
                  )}
                </div>
                {jumpCredMode === 'existing' ? (
                  <>
                    <select
                      id="jumphost-credential"
                      className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm"
                      value={
                        newHost.jumphost_chain && newHost.jumphost_chain.length > 0
                          ? String((newHost.jumphost_chain[0] as Record<string, unknown>).ssh_credential_id || '')
                          : ''
                      }
                      onChange={(e) => {
                        if (e.target.value === '__new__') {
                          setJumpCredMode('new');
                          setNewHost(prev => ({ ...prev, jumphost_chain: undefined }));
                        } else {
                          const credId = e.target.value ? parseInt(e.target.value) : null;
                          setNewHost(prev => ({
                            ...prev,
                            jumphost_chain: credId ? [{ ssh_credential_id: credId }] : undefined,
                          }));
                        }
                      }}
                    >
                      <option value="">
                        {projectSshCredentialId ? 'Use project default' : 'None (direct connection)'}
                      </option>
                      {sshCredentials?.map(cred => (
                        <option key={cred.id} value={cred.id}>
                          {cred.name} ({cred.username}@{cred.host}:{cred.port})
                        </option>
                      ))}
                      <option value="__new__">+ Create new jumphost credential...</option>
                    </select>
                    <p className="text-xs text-muted-foreground mt-1">
                      {projectSshCredentialId
                        ? 'Leave empty to inherit the project jumphost, or select one to override'
                        : 'SSH through a bastion/jump host to reach the target'}
                    </p>
                  </>
                ) : (
                  <div className="space-y-2 rounded border p-2 bg-background">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-medium text-primary">New jumphost credential</span>
                      <button
                        type="button"
                        className="text-xs text-muted-foreground hover:text-foreground underline"
                        onClick={() => { setJumpCredMode('existing'); }}
                      >
                        Cancel
                      </button>
                    </div>
                    <div className="grid grid-cols-3 gap-2">
                      <div>
                        <Label className="text-xs">Jumphost IP *</Label>
                        <Input
                          value={newJumpCred.host}
                          onChange={(e) => setNewJumpCred(prev => ({ ...prev, host: e.target.value }))}
                          placeholder="10.0.0.1"
                          className="h-8 text-sm"
                        />
                      </div>
                      <div>
                        <Label className="text-xs">Username *</Label>
                        <Input
                          value={newJumpCred.username}
                          onChange={(e) => setNewJumpCred(prev => ({ ...prev, username: e.target.value }))}
                          placeholder="jumpuser"
                          className="h-8 text-sm"
                        />
                      </div>
                      <div>
                        <Label className="text-xs">Credential Name</Label>
                        <Input
                          value={newJumpCred.name}
                          onChange={(e) => setNewJumpCred(prev => ({ ...prev, name: e.target.value }))}
                          placeholder={`${newHost.name || 'host'}-jumphost`}
                          className="h-8 text-sm"
                        />
                      </div>
                    </div>
                    <div>
                      <div className="flex gap-2 mb-1">
                        <button
                          type="button"
                          className={cn('text-xs px-2 py-0.5 rounded', newJumpCred.auth_type === 'password' ? 'bg-primary text-primary-foreground' : 'bg-muted')}
                          onClick={() => setNewJumpCred(prev => ({ ...prev, auth_type: 'password' }))}
                        >
                          Password
                        </button>
                        <button
                          type="button"
                          className={cn('text-xs px-2 py-0.5 rounded', newJumpCred.auth_type === 'key' ? 'bg-primary text-primary-foreground' : 'bg-muted')}
                          onClick={() => setNewJumpCred(prev => ({ ...prev, auth_type: 'key' }))}
                        >
                          SSH Key
                        </button>
                      </div>
                      {newJumpCred.auth_type === 'password' ? (
                        <Input
                          type="password"
                          value={newJumpCred.password}
                          onChange={(e) => setNewJumpCred(prev => ({ ...prev, password: e.target.value }))}
                          placeholder="Password"
                          className="h-8 text-sm"
                        />
                      ) : (
                        <textarea
                          value={newJumpCred.private_key}
                          onChange={(e) => setNewJumpCred(prev => ({ ...prev, private_key: e.target.value }))}
                          placeholder="Paste private key (PEM format)"
                          className="flex w-full rounded-md border border-input bg-transparent px-3 py-1 text-xs font-mono h-20 resize-none"
                        />
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <Label htmlFor="ssh-port">SSH Port</Label>
                <Input
                  id="ssh-port"
                  type="number"
                  value={newHost.ssh_port || 22}
                  onChange={(e) => setNewHost(prev => ({ ...prev, ssh_port: parseInt(e.target.value) || 22 }))}
                />
              </div>
            </div>
          </div>

          {/* Section 3: Hardware Access (BMC/IPMI + Version) */}
          <div className="rounded-lg border p-3 space-y-3 bg-muted/30">
            <div className="flex items-center gap-2 text-sm font-medium">
              <Network className="h-4 w-4" />
              BMC / IPMI Access
              <span className="text-xs text-muted-foreground font-normal">(optional)</span>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <Label htmlFor="bmc-ip">BMC/IPMI IP</Label>
                <Input
                  id="bmc-ip"
                  value={newHost.bmc_ip || ''}
                  onChange={(e) => setNewHost(prev => ({ ...prev, bmc_ip: e.target.value }))}
                  placeholder="10.176.11.92"
                />
              </div>
              <div>
                <Label htmlFor="bmc-username">BMC Username</Label>
                <Input
                  id="bmc-username"
                  value={newHost.bmc_username || ''}
                  onChange={(e) => setNewHost(prev => ({ ...prev, bmc_username: e.target.value }))}
                  placeholder="admin"
                />
              </div>
              <div>
                <Label htmlFor="bmc-password">BMC Password</Label>
                <Input
                  id="bmc-password"
                  type="password"
                  value={newHost.bmc_password || ''}
                  onChange={(e) => setNewHost(prev => ({ ...prev, bmc_password: e.target.value }))}
                  placeholder="••••••••"
                />
              </div>
            </div>
          </div>

          {/* Section 4: DPU Access */}
          <div className="rounded-lg border p-3 space-y-3 bg-muted/30">
            <div className="flex items-center gap-2 text-sm font-medium">
              <Cpu className="h-4 w-4" />
              DPU Access
              <span className="text-xs text-muted-foreground font-normal">(rshim SSH — default BFB: ubuntu / password)</span>
            </div>
            <div className="grid grid-cols-4 gap-3">
              <div>
                <Label htmlFor="dpu-username">DPU Username</Label>
                <Input
                  id="dpu-username"
                  value={dpuCred.username}
                  onChange={(e) => setDpuCred(prev => ({ ...prev, username: e.target.value }))}
                  placeholder="ubuntu"
                  className="h-9"
                />
              </div>
              <div>
                <Label htmlFor="dpu-password">DPU Password</Label>
                <Input
                  id="dpu-password"
                  type="password"
                  value={dpuCred.password}
                  onChange={(e) => setDpuCred(prev => ({ ...prev, password: e.target.value }))}
                  placeholder="password"
                  className="h-9"
                />
              </div>
              <div>
                <Label htmlFor="dpu-mgmt-ip">DPU IP</Label>
                <Input
                  id="dpu-mgmt-ip"
                  value={newHost.dpu_mgmt_ip || ''}
                  onChange={(e) => setNewHost(prev => ({ ...prev, dpu_mgmt_ip: e.target.value }))}
                  placeholder="192.168.100.2"
                  className="h-9"
                />
              </div>
              <div>
                <Label htmlFor="dpu-port">DPU Port</Label>
                <Input
                  id="dpu-port"
                  type="number"
                  value={22}
                  disabled
                  className="h-9"
                />
              </div>
            </div>
          </div>

          {/* BNK Version Profile */}
          <div className="grid grid-cols-3 gap-3">
            <div>
              <Label htmlFor="version-profile">BNK Version</Label>
              <select
                id="version-profile"
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm"
                value={newHost.version_profile_id || ''}
                onChange={(e) => setNewHost(prev => ({ ...prev, version_profile_id: e.target.value ? parseInt(e.target.value) : undefined }))}
              >
                <option value="">Auto (default)</option>
                {profiles?.map(p => (
                  <option key={p.id} value={p.id}>{p.display_name}</option>
                ))}
              </select>
            </div>
          </div>

          {/* Actions */}
          <div className="flex gap-2 justify-end">
            <Button variant="outline" size="sm" onClick={() => {
              setShowAddForm(false);
              setHostCredMode('existing');
              setJumpCredMode('existing');
              setDpuCred({ username: 'ubuntu', password: 'password' });
            }}>Cancel</Button>
            <Button
              size="sm"
              onClick={handleCreateHost}
              disabled={!newHost.name || !newHost.host_ip || createHost.isPending}
            >
              {createHost.isPending ? <Loader2 className="h-4 w-4 mr-1 animate-spin" /> : <Plus className="h-4 w-4 mr-1" />}
              Register Host
            </Button>
          </div>
        </Card>
      )}

      {/* Host List */}
      {hostsLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-20 w-full" />
        </div>
      ) : hosts && hosts.length > 0 ? (
        <div className="space-y-2">
          {hosts.map(host => (
            <HostRow
              key={host.id}
              host={host}
              projectId={projectId}
              isSelected={selectedHostId === host.id}
              onSelect={() => setSelectedHostId(selectedHostId === host.id ? null : host.id)}
              onDiscover={() => handleDiscover(host.id, host.name)}
              onDeploy={() => handleDeploy(host.id)}
              onDelete={() => handleDeleteHost(host.id, host.name)}
              isOwner={isOwner}
              isDiscovering={triggerDiscovery.isPending}
              isLoadingPreview={isLoadingPreview}
              sshCredentials={sshCredentials}
              projectSshCredentialId={projectSshCredentialId}
              discoveryResult={selectedHostId === host.id ? effectiveDiscoveryResult : null}
              deployments={deployments?.filter(d => d.host_id === host.id)}
              onViewDeployment={(id) => setActiveDeploymentId(id)}
            />
          ))}
        </div>
      ) : (
        <Card className="p-8 text-center">
          <Server className="h-12 w-12 mx-auto text-muted-foreground mb-4" />
          <h3 className="text-lg font-medium mb-2">No hosts registered</h3>
          <p className="text-sm text-muted-foreground mb-4">
            Register a bare-metal server to start. You'll need the server's IP address and SSH access.
          </p>
          {isOwner && (
            <Button onClick={() => setShowAddForm(true)}>
              <Plus className="h-4 w-4 mr-1" />Register First Host
            </Button>
          )}
        </Card>
      )}
    </div>
  );
}


// ============================================================================
// DiscoveryStatusBadge — shows current discovery stage with spinner
// ============================================================================

function DiscoveryStatusBadge({ host }: { host: BareMetalHost }) {
  const status = host.last_discovery_status;

  if (!status) {
    if (host.last_discovery_at) {
      return (
        <Badge variant="outline" className="bg-success/10 text-success border-success/20 text-xs">
          <CheckCircle2 className="h-3 w-3 mr-1" />Discovered
        </Badge>
      );
    }
    return (
      <Badge variant="outline" className="bg-warning/10 text-warning border-warning/20 text-xs">
        <AlertCircle className="h-3 w-3 mr-1" />Registered
      </Badge>
    );
  }

  if (status === 'completed') {
    return (
      <Badge variant="outline" className="bg-success/10 text-success border-success/20 text-xs">
        <CheckCircle2 className="h-3 w-3 mr-1" />Discovered
      </Badge>
    );
  }

  if (status === 'failed') {
    return (
      <Badge variant="outline" className="bg-destructive/10 text-destructive border-destructive/20 text-xs">
        <XCircle className="h-3 w-3 mr-1" />Failed
      </Badge>
    );
  }

  // Active discovery — show spinner with stage label
  const stageLabels: Record<string, string> = {
    pending: 'Queued…',
    ssh_probe: 'SSH Probe…',
    bmc_probe: 'BMC Probe…',
    dpu_probe: 'DPU Probe…',
    assessment: 'Assessment…',
  };

  return (
    <Badge variant="outline" className="bg-info/10 text-info border-info/20 text-xs">
      <Loader2 className="h-3 w-3 mr-1 animate-spin" />
      {stageLabels[status] || status}
    </Badge>
  );
}


// ============================================================================
// Host Row — expandable card showing host info + discovery results
// ============================================================================

function HostRow({
  host,
  projectId,
  isSelected,
  onSelect,
  onDiscover,
  onDeploy: _onDeploy,
  onDelete,
  isOwner,
  isDiscovering,
  isLoadingPreview: _isLoadingPreview,
  sshCredentials,
  projectSshCredentialId,
  discoveryResult,
  deployments,
  onViewDeployment,
}: {
  host: BareMetalHost;
  projectId: number;
  isSelected: boolean;
  onSelect: () => void;
  onDiscover: () => void;
  onDeploy: () => void;
  onDelete: () => void;
  isOwner: boolean;
  isDiscovering: boolean;
  isLoadingPreview?: boolean;
  sshCredentials?: Array<{ id: number; name: string; username: string; host: string; port: number }>;
  projectSshCredentialId?: number | null;
  discoveryResult: BareMetalDiscoveryResponse | null;
  deployments?: BareMetalDeployment[];
  onViewDeployment: (id: number) => void;
}) {
  return (
    <div className="border rounded-lg">
      {/* Header row */}
      <div
        className="flex items-center gap-3 p-3 cursor-pointer hover:bg-muted/50 transition-colors"
        onClick={onSelect}
      >
        <button className="text-muted-foreground" aria-label={`toggle-host-${host.id}`}>
          {isSelected ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </button>

        <Server className="h-4 w-4 text-muted-foreground flex-shrink-0" />
        <span className="font-medium text-sm">{host.name}</span>
        <span className="font-mono text-sm text-muted-foreground">{host.host_ip}</span>

        <div className="flex items-center gap-2 ml-auto flex-shrink-0">
          {/* Topology badge */}
          {host.topology && (
            <Badge variant="outline" className="text-xs">
              {TOPOLOGY_LABELS[host.topology] || host.topology}
            </Badge>
          )}

          {/* OS info */}
          {host.os_info && (
            <Badge variant="outline" className="text-xs">
              {(host.os_info as Record<string, string>).os_type} {(host.os_info as Record<string, string>).os_version}
            </Badge>
          )}

          {/* DPU info */}
          {host.dpu_info && host.dpu_info.length > 0 && (
            <Badge variant="outline" className="bg-info/10 text-info border-info/20 text-xs">
              <Cpu className="h-3 w-3 mr-1" />{host.dpu_info.length > 1 ? `${host.dpu_info.length} DPUs` : 'DPU'}
            </Badge>
          )}

          {/* rshim source for dual_dpu_obmc */}
          {host.topology === 'dual_dpu_obmc' && host.rshim_source === 'bmc' && (
            <Badge variant="outline" className="bg-warning/10 text-warning border-warning/20 text-xs">
              rshim via OpenBMC
            </Badge>
          )}

          {/* BMC tier */}
          {host.bmc_access_tier && host.bmc_access_tier !== 'none' && (
            <Badge variant="outline" className="bg-info/10 text-info border-info/20 text-xs">
              <Network className="h-3 w-3 mr-1" />
              {host.bmc_access_tier.toUpperCase()}
            </Badge>
          )}

          {/* Jumphost badges */}
          {host.uses_project_jumphost && !host.has_jumphost_chain && (
            <Badge variant="outline" className="bg-info/10 text-info border-info/20 text-xs">
              <Link className="h-3 w-3 mr-1" />Using project jumphost
            </Badge>
          )}

          {/* Host discovery status */}
          <DiscoveryStatusBadge host={host} />
        </div>
      </div>

      {/* Expanded detail */}
      {isSelected && (
        <div className="border-t p-4 space-y-4">
          {/* Action buttons */}
          {isOwner && (
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={(e) => { e.stopPropagation(); onDiscover(); }}
                disabled={isDiscovering || (host.last_discovery_status !== null && host.last_discovery_status !== 'completed' && host.last_discovery_status !== 'failed')}
              >
                {(host.last_discovery_status !== null && host.last_discovery_status !== 'completed' && host.last_discovery_status !== 'failed') ? (
                  <Loader2 className="h-4 w-4 mr-1 animate-spin" />
                ) : (
                  <Search className="h-4 w-4 mr-1" />
                )}
                {(host.last_discovery_status !== null && host.last_discovery_status !== 'completed' && host.last_discovery_status !== 'failed') ? 'Discovering…' : 'Run Discovery'}
              </Button>

              {host.topology && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled
                  title="Use a Blueprint from the Stacks tab to deploy bare-metal infrastructure"
                >
                  <Rocket className="h-4 w-4 mr-1" />
                  Deploy via Blueprint
                </Button>
              )}

              <div className="ml-auto">
                <Button
                  size="sm"
                  variant="ghost"
                  className="text-destructive hover:text-destructive"
                  onClick={(e) => { e.stopPropagation(); onDelete(); }}
                >
                  <Trash2 className="h-4 w-4 mr-1" />Delete
                </Button>
              </div>
            </div>
          )}

          {/* SSH access info */}
          {(host.ssh_credential_id || host.has_jumphost_chain || (host.uses_project_jumphost && projectSshCredentialId)) && (
            <div className="flex items-center gap-3 text-sm text-muted-foreground">
              <Key className="h-3.5 w-3.5" />
              {host.ssh_credential_id && (() => {
                const cred = sshCredentials?.find(c => c.id === host.ssh_credential_id);
                return <span>{cred ? `${cred.name} (${cred.username}@${cred.host})` : `SSH credential #${host.ssh_credential_id}`}</span>;
              })()}
              {host.has_jumphost_chain && (
                <Badge variant="outline" className="text-xs">
                  via jumphost
                </Badge>
              )}
              {host.uses_project_jumphost && !host.has_jumphost_chain && projectSshCredentialId && (
                <Badge variant="outline" className="text-xs bg-info/10 text-info border-info/20">
                  <Link className="h-3 w-3 mr-1" />
                  using project jumphost
                </Badge>
              )}
            </div>
          )}

          {/* dual_dpu_obmc topology settings */}
          {host.topology === 'dual_dpu_obmc' && (
            <DualDpuObmcSettings host={host} projectId={projectId} isOwner={isOwner} />
          )}

          {/* Discovery results */}
          {(() => {
            // Check if discovery data is stale (a deployment happened after last discovery)
            const latestDeployment = deployments?.length
              ? deployments.reduce((latest, d) =>
                  (d.started_at && (!latest.started_at || d.started_at > latest.started_at)) ? d : latest
                )
              : null;
            const isStale = !!(
              host.last_discovery_at &&
              latestDeployment?.started_at &&
              new Date(latestDeployment.started_at) > new Date(host.last_discovery_at)
            );

            return (
              <>
                {isStale && (
                  <div className="flex items-center gap-2 px-3 py-2 rounded-md bg-warning/10 border border-warning/20 text-sm text-warning">
                    <AlertTriangle className="h-4 w-4 shrink-0" />
                    <span>Discovery data is from before the last deployment — re-run discovery to see current state</span>
                  </div>
                )}
                {discoveryResult && (
                  <div className={isStale ? 'opacity-50' : ''}>
                    <DiscoveryResults result={discoveryResult} />
                  </div>
                )}

                {/* Host details (from cached discovery) */}
                {!discoveryResult && host.last_discovery_at && (
                  <div className={cn("grid grid-cols-2 gap-4 text-sm", isStale && "opacity-50")}>
                    {host.os_info && (
                      <div>
                        <span className="text-muted-foreground">OS:</span>{' '}
                        {(host.os_info as Record<string, string>).os_pretty_name || 'Unknown'}
                      </div>
                    )}
                    {host.os_info && (
                      <div>
                        <span className="text-muted-foreground">Kernel:</span>{' '}
                        {(host.os_info as Record<string, string>).kernel || 'Unknown'}
                      </div>
                    )}
                    {host.k8s_info && (
                      <div>
                        <span className="text-muted-foreground">K8s:</span>{' '}
                        {(host.k8s_info as Record<string, string>).running === 'true' || (host.k8s_info as Record<string, boolean>).running === true
                          ? `Running (${(host.k8s_info as Record<string, string>).version})`
                          : 'Not running'}
                      </div>
                    )}
                    <div>
                      <span className="text-muted-foreground">Last discovered:</span>{' '}
                      {new Date(host.last_discovery_at).toLocaleString()}
                    </div>
                  </div>
                )}
              </>
            );
          })()}

          {/* Past deployments for this host */}
          {deployments && deployments.length > 0 && (
            <div className="space-y-2">
              <h4 className="text-sm font-medium">Deployments</h4>
              {deployments.slice(0, 3).map(d => (
                <div
                  key={d.id}
                  className="flex items-center gap-3 p-2 border rounded text-sm cursor-pointer hover:bg-muted/50"
                  onClick={() => onViewDeployment(d.id)}
                >
                  <DeploymentStatusBadge status={d.status} />
                  <span className="text-muted-foreground">#{d.id}</span>
                  <span>{d.topology}</span>
                  <span className="text-muted-foreground ml-auto text-xs">
                    {new Date(d.created_at).toLocaleString()}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}


// ============================================================================
// DualDpuObmcSettings — topology-specific config for dual_dpu_obmc hosts
// ============================================================================

function DualDpuObmcSettings({
  host,
  projectId,
  isOwner,
}: {
  host: BareMetalHost;
  projectId: number;
  isOwner: boolean;
}) {
  const updateHost = useUpdateBareMetalHost(projectId);
  const [editPci, setEditPci] = useState<string | null>(null);
  const [editBondMode, setEditBondMode] = useState<string | null>(null);
  const [editBmcIp, setEditBmcIp] = useState<string | null>(null);
  const [editBmcUsername, setEditBmcUsername] = useState<string | null>(null);
  const [editBmcPassword, setEditBmcPassword] = useState<string | null>(null);

  const isDirty =
    (editPci !== null && editPci !== (host.deploy_dpu_pci_address ?? '')) ||
    (editBondMode !== null && editBondMode !== (host.bond_mode ?? 'independent')) ||
    (editBmcIp !== null && editBmcIp !== (host.bmc_ip ?? '')) ||
    (editBmcUsername !== null && editBmcUsername !== '') ||
    (editBmcPassword !== null && editBmcPassword !== '');

  const handleSave = async () => {
    const data: BareMetalHostUpdate = {};
    if (editPci !== null) data.deploy_dpu_pci_address = editPci;
    if (editBondMode !== null) data.bond_mode = editBondMode;
    if (editBmcIp !== null) data.bmc_ip = editBmcIp;
    if (editBmcUsername !== null && editBmcUsername !== '') data.bmc_username = editBmcUsername;
    if (editBmcPassword !== null && editBmcPassword !== '') data.bmc_password = editBmcPassword;
    try {
      await updateHost.mutateAsync({ hostId: host.id, data });
      setEditPci(null);
      setEditBondMode(null);
      setEditBmcIp(null);
      setEditBmcUsername(null);
      setEditBmcPassword(null);
    } catch (err) {
      notifyError(err, 'Failed to update host');
    }
  };

  const pciValue = editPci ?? host.deploy_dpu_pci_address ?? '';
  const bondValue = editBondMode ?? host.bond_mode ?? 'independent';
  const missingBmc = !(editBmcIp ?? host.bmc_ip);
  const missingPci = !pciValue;

  return (
    <div className="rounded-lg border p-3 space-y-3 bg-muted/30">
      <div className="flex items-center gap-2 text-sm font-medium">
        <Cpu className="h-4 w-4" />
        Dual DPU (OpenBMC) Settings
        {host.rshim_source === 'bmc' && (
          <Badge variant="outline" className="bg-warning/10 text-warning border-warning/20 text-xs">
            rshim via DPU OpenBMC
          </Badge>
        )}
      </div>

      {/* Validation warnings */}
      {missingBmc && (
        <div className="flex items-start gap-2 px-3 py-2 rounded-md bg-warning/10 border border-warning/20 text-sm text-warning">
          <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
          <div>
            <span className="font-medium">BMC IP needed for deployment</span>
            <span className="text-warning"> — DPU OpenBMC rshim access needs a BMC IP address. Enter it below.</span>
          </div>
        </div>
      )}
      {missingPci && (
        <div className="flex items-start gap-2 px-3 py-2 rounded-md bg-warning/10 border border-warning/20 text-sm text-warning">
          <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
          <span>Deployment DPU PCI address not set. Run discovery to auto-detect, or enter manually below.</span>
        </div>
      )}

      <div className="grid grid-cols-2 gap-4">
        {/* Deployment DPU */}
        <div className="space-y-1.5">
          <Label className="text-xs">Deployment DPU PCI Address</Label>
          <div className="flex items-center gap-2">
            <Input
              value={pciValue}
              onChange={(e) => setEditPci(e.target.value)}
              placeholder="0000:03:00.0"
              className="h-8 text-sm font-mono"
              disabled={!isOwner}
            />
            {host.deploy_dpu_index != null && (
              <Badge variant="outline" className="text-xs shrink-0">
                DPU #{host.deploy_dpu_index}
              </Badge>
            )}
          </div>
          <p className="text-xs text-muted-foreground">
            {host.deploy_dpu_pci_address
              ? 'Auto-detected by discovery. Override if needed.'
              : 'Will be set by discovery, or enter manually.'}
          </p>
        </div>

        {/* Bond Mode */}
        <div className="space-y-1.5">
          <Label className="text-xs">Bond Mode</Label>
          <select
            className="flex h-8 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm"
            value={bondValue}
            onChange={(e) => setEditBondMode(e.target.value)}
            disabled={!isOwner}
          >
            <option value="independent">Independent (SF-based networking)</option>
            <option value="lag">LAG (802.3ad bonded interfaces)</option>
          </select>
          <p className="text-xs text-muted-foreground">
            Controls how DPU physical functions are bonded for data plane traffic.
          </p>
        </div>
      </div>

      {/* BMC IP — editable */}
      <div className="space-y-1.5">
        <Label className="text-xs flex items-center gap-1.5">
          <Network className="h-3 w-3" />BMC IP
        </Label>
        <Input
          value={editBmcIp ?? host.bmc_ip ?? ''}
          onChange={(e) => setEditBmcIp(e.target.value)}
          placeholder="192.168.1.100"
          className="h-8 text-sm font-mono"
          disabled={!isOwner}
        />
      </div>

      {/* BMC credentials — only shown when a BMC IP is configured or being entered */}
      {(editBmcIp ?? host.bmc_ip) && (
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <Label className="text-xs">BMC Username</Label>
            <Input
              value={editBmcUsername ?? ''}
              onChange={(e) => setEditBmcUsername(e.target.value)}
              placeholder={host.has_bmc_credentials ? '(unchanged)' : 'root'}
              className="h-8 text-sm"
              disabled={!isOwner}
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs">BMC Password</Label>
            <Input
              type="password"
              value={editBmcPassword ?? ''}
              onChange={(e) => setEditBmcPassword(e.target.value)}
              placeholder={host.has_bmc_credentials ? '(unchanged)' : ''}
              className="h-8 text-sm"
              disabled={!isOwner}
            />
          </div>
        </div>
      )}

      {/* BMC credential status badge */}
      {host.bmc_ip && (
        <div className="flex items-center gap-2">
          {host.has_bmc_credentials ? (
            <Badge variant="outline" className="bg-success/10 text-success border-success/20 text-xs">
              <CheckCircle2 className="h-3 w-3 mr-1" />Credentials set
            </Badge>
          ) : (
            <Badge variant="outline" className="bg-warning/10 text-warning border-warning/20 text-xs">
              <AlertTriangle className="h-3 w-3 mr-1" />No credentials
            </Badge>
          )}
        </div>
      )}

      {/* Save button */}
      {isOwner && isDirty && (
        <div className="flex justify-end">
          <Button
            size="sm"
            onClick={handleSave}
            disabled={updateHost.isPending}
          >
            {updateHost.isPending ? (
              <Loader2 className="h-4 w-4 mr-1 animate-spin" />
            ) : (
              <Save className="h-4 w-4 mr-1" />
            )}
            Save Changes
          </Button>
        </div>
      )}
    </div>
  );
}


// ============================================================================
// Discovery Results — assessment checks with pass/warn/fail
// ============================================================================

function DiscoveryResults({ result }: { result: BareMetalDiscoveryResponse }) {
  const host = result.probes?.host as Record<string, unknown> | undefined;
  const dpu = result.probes?.dpu as Record<string, unknown> | undefined;
  const bmc = result.probes?.bmc as Record<string, unknown> | undefined;

  return (
    <div className="space-y-4">
      {/* Topology recommendation */}
      {result.topology_recommendation && (
        <div className="flex items-center gap-2 p-2 rounded bg-info/10 border border-info/20">
          <CircuitBoard className="h-4 w-4 text-info" />
          <span className="text-sm font-medium">
            Recommended topology: {TOPOLOGY_LABELS[result.topology_recommendation] || result.topology_recommendation}
          </span>
        </div>
      )}

      {/* Host Probe Details */}
      {host && (
        <div className="space-y-2">
          <h4 className="text-sm font-semibold flex items-center gap-1.5">
            <Server className="h-3.5 w-3.5" />Host
          </h4>
          <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm pl-5">
            {!!host.os_pretty_name && (
              <ProbeRow label="OS" value={host.os_pretty_name as string} />
            )}
            {!!host.kernel_version && (
              <ProbeRow label="Kernel" value={host.kernel_version as string} />
            )}
            {!!host.architecture && (
              <ProbeRow label="Architecture" value={host.architecture as string} />
            )}
            {!!host.nic_mode && (
              <ProbeRow
                label="NIC Mode"
                value={host.nic_mode as string}
                badge={
                  (host.nic_mode as string).includes('EMBEDDED') ? 'SuperNIC' :
                  (host.nic_mode as string).includes('SEPARATED') ? 'DPU' : undefined
                }
                badgeColor={
                  (host.nic_mode as string).includes('EMBEDDED') ? 'bg-warning/10 text-warning border-warning/20' :
                  'bg-success/10 text-success border-success/20'
                }
              />
            )}
            {!!host.default_route && (
              <ProbeRow
                label="Default route"
                value={host.default_route as string}
              />
            )}
            {(host.pf_interfaces as unknown[])?.length > 0 && (
              <ProbeRow
                label="Interfaces"
                value={(host.pf_interfaces as Array<Record<string, string>>).map(pf => pf.name).join(', ')}
              />
            )}
            <ProbeRow label="VF Count" value={String(host.vf_count ?? 0)} />
            <ProbeRow label="Hugepages" value={`${host.hugepages_gb ?? 0} GB`} />
            <ProbeRow
              label="rshim"
              value={host.rshim_present ? 'Available (BF3 DPU detected)' : 'Not found'}
              status={host.rshim_present ? 'pass' : 'warn'}
            />
            {host.k8s_installed != null && (
              <ProbeRow
                label="K8s"
                value={
                  host.k8s_installed
                    ? `Installed${host.k8s_version ? ` (${host.k8s_version})` : ''}${host.k8s_running ? ' — cluster running' : ' — not running'}`
                    : 'Not installed'
                }
                status={host.k8s_running ? 'pass' : host.k8s_installed ? 'warn' : undefined}
              />
            )}
          </div>
        </div>
      )}

      {/* DPU Probe Details */}
      {dpu && (
        <div className="space-y-2">
          <h4 className="text-sm font-semibold flex items-center gap-1.5">
            <Cpu className="h-3.5 w-3.5" />DPU
          </h4>
          <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm pl-5">
            <ProbeRow
              label="SSH"
              value={dpu.ssh_reachable ? 'Reachable' : `Unreachable${dpu.error ? `: ${dpu.error}` : ''}`}
              status={dpu.ssh_reachable ? 'pass' : 'fail'}
            />
            {!!dpu.ssh_reachable && (
              <>
                {!!dpu.doca_version && <ProbeRow label="DOCA" value={dpu.doca_version as string} />}
                {!!dpu.containerd_version && <ProbeRow label="containerd" value={dpu.containerd_version as string} />}
                {!!dpu.runc_version && <ProbeRow label="runc" value={dpu.runc_version as string} />}
                {!!dpu.k8s_version && <ProbeRow label="K8s" value={dpu.k8s_version as string} />}
                <ProbeRow
                  label="OVS"
                  value={dpu.ovs_installed ? `Installed${(dpu.ovs_bridge_ports as string[])?.length ? ` (bridge ports: ${(dpu.ovs_bridge_ports as string[]).join(', ')})` : ''}` : 'Not installed'}
                />
                {(dpu.vf_representors as string[])?.length > 0 && (
                  <ProbeRow label="VF Representors" value={(dpu.vf_representors as string[]).join(', ')} />
                )}
                <ProbeRow
                  label="Hugepages"
                  value={`${dpu.hugepages_gb ?? 0} GB`}
                  status={(dpu.hugepages_gb as number) >= 16 ? 'pass' : 'warn'}
                />
              </>
            )}
          </div>
        </div>
      )}

      {/* BMC Probe Details */}
      {bmc && (
        <div className="space-y-2">
          <h4 className="text-sm font-semibold flex items-center gap-1.5">
            <Network className="h-3.5 w-3.5" />BMC / IPMI
          </h4>
          <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-sm pl-5">
            <ProbeRow
              label="Redfish"
              value={bmc.redfish_reachable ? `Reachable${bmc.vendor ? ` (${bmc.vendor})` : ''}` : 'Not reachable'}
              status={bmc.redfish_reachable ? 'pass' : undefined}
            />
            <ProbeRow
              label="IPMI"
              value={bmc.ipmi_reachable ? 'Reachable' : 'Not reachable'}
              status={bmc.ipmi_reachable ? 'pass' : undefined}
            />
            {!!bmc.power_state && <ProbeRow label="Power" value={bmc.power_state as string} />}
            {!!bmc.system_manufacturer && <ProbeRow label="Manufacturer" value={bmc.system_manufacturer as string} />}
            {!!bmc.system_model && <ProbeRow label="Model" value={bmc.system_model as string} />}
          </div>
        </div>
      )}

      {/* DOCA Host Package Status */}
      {result.probes.doca && (
        <DocaStatusCard doca={result.probes.doca as unknown as import('./DocaStatusCard').DocaStatus} />
      )}

      {/* Assessment checks */}
      <div className="space-y-1">
        <h4 className="text-sm font-semibold mb-2">Assessment</h4>
        {result.assessment.map((check, i) => (
          <div key={i} className="flex items-center gap-2 text-sm py-1">
            {check.status === 'pass' && <CheckCircle2 className="h-4 w-4 text-success flex-shrink-0" />}
            {check.status === 'warn' && <AlertTriangle className="h-4 w-4 text-warning flex-shrink-0" />}
            {check.status === 'fail' && <XCircle className="h-4 w-4 text-destructive flex-shrink-0" />}
            <span>{check.message}</span>
          </div>
        ))}
      </div>

      {/* Version drift */}
      {result.version_drift && result.version_drift.length > 0 && (
        <div className="space-y-1">
          <h4 className="text-sm font-semibold mb-2 text-warning">Version Drift</h4>
          {result.version_drift.map((drift, i) => (
            <div key={i} className="flex items-center gap-2 text-sm py-1">
              <AlertTriangle className="h-4 w-4 text-warning flex-shrink-0" />
              <span>
                <span className="font-medium">{drift.component}</span>: installed {drift.installed}, expected {drift.expected}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Recommendations */}
      {result.recommendations.length > 0 && (
        <div className="space-y-1">
          <h4 className="text-sm font-semibold mb-2">Recommendations</h4>
          {result.recommendations.map((rec, i) => (
            <p key={i} className="text-sm text-muted-foreground">• {rec}</p>
          ))}
        </div>
      )}

      {/* Actionable Assessment */}
      {result.actionable_assessment && result.actionable_assessment.length > 0 && (
        <ActionableAssessment sections={result.actionable_assessment} />
      )}
    </div>
  );
}

// ============================================================================
// ActionableAssessment — Collapsible sections with commands and copy buttons
// ============================================================================

function ActionableAssessment({ sections }: { sections: ActionableAssessmentSection[] }) {
  const [openSections, setOpenSections] = useState<Set<string>>(new Set());

  const toggleSection = (topic: string) => {
    setOpenSections(prev => {
      const next = new Set(prev);
      if (next.has(topic)) {
        next.delete(topic);
      } else {
        next.add(topic);
      }
      return next;
    });
  };

  const copyToClipboard = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      notify({ title: 'Copied to clipboard', severity: 'success' });
    } catch (err) {
      notifyError(err, 'Failed to copy to clipboard');
    }
  };

  const getSeverityIcon = (severity: string) => {
    switch (severity) {
      case 'info':
        return <Info className="h-4 w-4 text-info" />;
      case 'warning':
        return <AlertTriangle className="h-4 w-4 text-warning" />;
      case 'action_required':
        return <AlertCircle className="h-4 w-4 text-destructive" />;
      default:
        return <Info className="h-4 w-4 text-muted-foreground" />;
    }
  };

  const getSeverityBgClass = (severity: string) => {
    switch (severity) {
      case 'info':
        return 'bg-info/5 border-info/20';
      case 'warning':
        return 'bg-warning/5 border-warning/20';
      case 'action_required':
        return 'bg-destructive/5 border-destructive/20';
      default:
        return 'bg-muted/30 border-border';
    }
  };

  return (
    <div className="space-y-2 mt-4">
      <h4 className="text-sm font-semibold mb-2">Next Steps</h4>
      {sections.map((section) => {
        const isOpen = openSections.has(section.topic);
        const hasCommands = section.commands && section.commands.length > 0;
        
        return (
          <Collapsible
            key={section.topic}
            open={isOpen}
            onOpenChange={() => toggleSection(section.topic)}
          >
            <div className={cn('border rounded-lg', getSeverityBgClass(section.severity))}>
              <CollapsibleTrigger asChild>
                <button
                  type="button"
                  className="w-full flex items-center justify-between p-3 text-left hover:opacity-80 transition-opacity"
                >
                  <div className="flex items-center gap-2">
                    {getSeverityIcon(section.severity)}
                    <span className="text-sm font-medium">{section.title}</span>
                  </div>
                  {isOpen ? (
                    <ChevronDown className="h-4 w-4 text-muted-foreground" />
                  ) : (
                    <ChevronRight className="h-4 w-4 text-muted-foreground" />
                  )}
                </button>
              </CollapsibleTrigger>

              <CollapsibleContent>
                <div className="px-3 pb-3 space-y-3">
                  {/* Body text — preserve markdown-ish formatting */}
                  <div className="text-sm text-foreground whitespace-pre-wrap leading-relaxed">
                    {section.body.split('\n').map((line, i) => {
                      // Bold text handling: **text**
                      const boldRegex = /\*\*([^*]+)\*\*/g;
                      const parts: (string | JSX.Element)[] = [];
                      let lastIndex = 0;
                      let match;

                      while ((match = boldRegex.exec(line)) !== null) {
                        if (match.index > lastIndex) {
                          parts.push(line.substring(lastIndex, match.index));
                        }
                        parts.push(<strong key={match.index}>{match[1]}</strong>);
                        lastIndex = match.index + match[0].length;
                      }
                      if (lastIndex < line.length) {
                        parts.push(line.substring(lastIndex));
                      }

                      return (
                        <span key={i}>
                          {parts.length > 0 ? parts : line}
                          {i < section.body.split('\n').length - 1 && <br />}
                        </span>
                      );
                    })}
                  </div>

                  {/* Command blocks */}
                  {hasCommands && (
                    <div className="space-y-2">
                      {section.commands.map((cmd, idx) => (
                        <div
                          key={idx}
                          className="relative group bg-card border border-border text-success rounded px-3 py-2 font-mono text-xs overflow-x-auto"
                        >
                          <pre className="whitespace-pre-wrap break-all">{cmd}</pre>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="absolute top-1 right-1 h-6 px-2 opacity-0 group-hover:opacity-100 transition-opacity bg-card hover:bg-muted text-foreground border border-border"
                            onClick={(e) => {
                              e.stopPropagation();
                              void copyToClipboard(cmd);
                            }}
                          >
                            <Copy className="h-3 w-3 mr-1" />
                            Copy
                          </Button>
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Warning banner for connectivity risk */}
                  {section.severity === 'action_required' && section.body.includes('connectivity loss') && (
                    <div className="flex items-start gap-2 p-2 rounded bg-destructive/5 border border-destructive/20 text-xs">
                      <AlertTriangle className="h-3.5 w-3.5 text-destructive mt-0.5 flex-shrink-0" />
                      <span className="text-destructive">
                        <strong>Connectivity Risk:</strong> This operation may cause network disruption. Ensure out-of-band access before proceeding.
                      </span>
                    </div>
                  )}
                </div>
              </CollapsibleContent>
            </div>
          </Collapsible>
        );
      })}
    </div>
  );
}


// ============================================================================
// ProbeRow — single key/value row with optional status indicator and badge
// ============================================================================

function ProbeRow({
  label,
  value,
  status,
  badge,
  badgeColor,
}: {
  label: string;
  value: string;
  status?: 'pass' | 'warn' | 'fail';
  badge?: string;
  badgeColor?: string;
}) {
  return (
    <div className="flex items-center gap-2 py-0.5">
      {status === 'pass' && <CheckCircle2 className="h-3 w-3 text-success flex-shrink-0" />}
      {status === 'warn' && <AlertTriangle className="h-3 w-3 text-warning flex-shrink-0" />}
      {status === 'fail' && <XCircle className="h-3 w-3 text-destructive flex-shrink-0" />}
      {!status && <div className="w-3 flex-shrink-0" />}
      <span className="text-muted-foreground w-24 flex-shrink-0">{label}:</span>
      <span className="font-mono text-xs">{value}</span>
      {badge && (
        <Badge variant="outline" className={cn('text-[10px] px-1.5 py-0 h-4', badgeColor)}>
          {badge}
        </Badge>
      )}
    </div>
  );
}


// ============================================================================
// Deployment Progress View — step-by-step with phase grouping
// ============================================================================

function DeploymentProgressView({
  projectId,
  deploymentId,
  onBack,
  isOwner,
}: {
  projectId: number;
  deploymentId: number;
  onBack: () => void;
  isOwner: boolean;
}) {
  const { data: deployment, isLoading } = useBareMetalDeployment(projectId, deploymentId);
  const cancelDeployment = useCancelBareMetalDeployment(projectId);
  const [expandedStep, setExpandedStep] = useState<number | null>(null);

  if (isLoading || !deployment) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  const isActive = ['pending', 'discovering', 'planning', 'in_progress'].includes(deployment.status);
  const isFailed = deployment.status === 'failed';

  // Group steps by phase
  const phases = new Map<string, DeploymentStep[]>();
  for (const step of deployment.steps) {
    const existing = phases.get(step.phase) || [];
    existing.push(step);
    phases.set(step.phase, existing);
  }

  const completedSteps = deployment.steps.filter(s => s.status === 'completed' || s.status === 'skipped').length;
  const totalSteps = deployment.steps.length;
  const progressPct = totalSteps > 0 ? Math.round((completedSteps / totalSteps) * 100) : 0;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="sm" onClick={onBack}>
          ← Back
        </Button>
        <h2 className="text-xl font-semibold">Deployment #{deployment.id}</h2>
        <DeploymentStatusBadge status={deployment.status} />
        {isActive && isOwner && (
          <Button
            variant="outline"
            size="sm"
            className="ml-auto text-destructive"
            onClick={() => cancelDeployment.mutate(deploymentId)}
            disabled={cancelDeployment.isPending}
          >
            Cancel
          </Button>
        )}
      </div>

      {/* Progress bar */}
      <div className="space-y-1">
        <div className="flex justify-between text-sm text-muted-foreground">
          <span>{completedSteps} of {totalSteps} steps</span>
          <span>{progressPct}%</span>
        </div>
        <div className="h-2 bg-muted rounded-full overflow-hidden">
          <div
            className={cn(
              'h-full rounded-full transition-all duration-500',
              isFailed ? 'bg-destructive' : deployment.status === 'completed' ? 'bg-success' : 'bg-primary'
            )}
            style={{ width: `${progressPct}%` }}
          />
        </div>
      </div>

      {/* Error message */}
      {deployment.error_message && (
        <div className="p-3 rounded border border-destructive/30 bg-destructive/5 text-sm">
          <div className="flex items-center gap-2 text-destructive font-medium">
            <XCircle className="h-4 w-4" />
            Failed at step {deployment.error_step_index}: {deployment.error_message}
          </div>
        </div>
      )}

      {/* Steps grouped by phase */}
      <div className="space-y-4">
        {Array.from(phases.entries()).map(([phase, steps]) => (
          <div key={phase} className="space-y-1">
            <h3 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">
              {DEPLOYMENT_PHASE_LABELS[phase] || phase}
            </h3>
            <div className="space-y-0.5">
              {steps.map(step => (
                <StepRow
                  key={step.id}
                  step={step}
                  isExpanded={expandedStep === step.step_index}
                  onToggle={() => setExpandedStep(expandedStep === step.step_index ? null : step.step_index)}
                  projectId={projectId}
                  deploymentId={deploymentId}
                />
              ))}
            </div>
          </div>
        ))}
      </div>

      {/* Duration */}
      {deployment.duration_seconds && (
        <p className="text-sm text-muted-foreground">
          Total duration: {formatDuration(deployment.duration_seconds)}
        </p>
      )}
    </div>
  );
}


// ============================================================================
// Step Row — individual step with status icon + expandable log
// ============================================================================

function StepRow({
  step,
  isExpanded,
  onToggle,
  projectId,
  deploymentId,
}: {
  step: DeploymentStep;
  isExpanded: boolean;
  onToggle: () => void;
  projectId: number;
  deploymentId: number;
}) {
  // Lazy-load logs only when expanded
  const { data: logs } = useQuery({
    queryKey: ['bare-metal', 'step-logs', deploymentId, step.step_index],
    queryFn: async () => {
      const { bareMetalDeploymentsApi } = await import('@/lib/api/bare-metal');
      return bareMetalDeploymentsApi.getStepLogs(projectId, deploymentId, step.step_index);
    },
    enabled: isExpanded && (step.status === 'completed' || step.status === 'failed'),
  });

  return (
    <div className="border rounded">
      <div
        className="flex items-center gap-2 px-3 py-2 cursor-pointer hover:bg-muted/30 text-sm"
        onClick={onToggle}
      >
        <StepStatusIcon status={step.status} />
        <span className={cn('flex-1', step.status === 'skipped' && 'text-muted-foreground line-through')}>
          {step.description || step.name}
        </span>
        {step.connectivity_risk && (
          <Badge variant="warning" className="text-xs">⚡ Risk</Badge>
        )}
        {step.duration_seconds != null && (
          <span className="text-xs text-muted-foreground">{formatDuration(step.duration_seconds)}</span>
        )}
        {(step.status === 'completed' || step.status === 'failed') && (
          <ChevronDown className={cn('h-3 w-3 text-muted-foreground transition-transform', !isExpanded && '-rotate-90')} />
        )}
      </div>
      {isExpanded && logs && (logs.output_log || logs.error_message) && (
        <div className="border-t px-3 py-2 bg-muted/20">
          {logs.output_log && (
            <pre className="text-xs font-mono whitespace-pre-wrap max-h-48 overflow-y-auto">{logs.output_log}</pre>
          )}
          {logs.error_message && (
            <p className="text-xs text-destructive mt-1">{logs.error_message}</p>
          )}
        </div>
      )}
    </div>
  );
}


// ============================================================================
// Shared helpers
// ============================================================================

function StepStatusIcon({ status }: { status: string }) {
  switch (status) {
    case 'completed': return <CheckCircle2 className="h-4 w-4 text-success flex-shrink-0" />;
    case 'failed': return <XCircle className="h-4 w-4 text-destructive flex-shrink-0" />;
    case 'running': return <Loader2 className="h-4 w-4 text-primary animate-spin flex-shrink-0" />;
    case 'skipped': return <CheckCircle2 className="h-4 w-4 text-muted-foreground flex-shrink-0" />;
    default: return <div className="h-4 w-4 rounded-full border-2 border-border flex-shrink-0" />;
  }
}

function DeploymentStatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    pending: 'bg-muted text-muted-foreground border-border',
    in_progress: 'bg-primary/10 text-primary border-primary/20',
    completed: 'bg-success/10 text-success border-success/20',
    failed: 'bg-destructive/10 text-destructive border-destructive/20',
    cancelled: 'bg-muted text-muted-foreground border-border',
    paused: 'bg-warning/10 text-warning border-warning/20',
  };

  return (
    <Badge variant="outline" className={cn('text-xs', colors[status] || '')}>
      {status === 'in_progress' && <Loader2 className="h-3 w-3 mr-1 animate-spin" />}
      {status === 'completed' && <CheckCircle2 className="h-3 w-3 mr-1" />}
      {status === 'failed' && <XCircle className="h-3 w-3 mr-1" />}
      {status.replace(/_/g, ' ')}
    </Badge>
  );
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}
