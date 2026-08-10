import { useState, useEffect } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useCreateCluster, useUpdateCluster } from '@/hooks/useK8s';
import type { K8sCluster, K8sClusterCreateRequest, K8sClusterUpdateRequest, SSHCredential } from '@/types';
import { Switch } from '@/components/ui/switch';
import { Loader2, Upload, FileText, Plus, Server, Search, CheckCircle2 } from 'lucide-react';
import { SectionCard } from '@/components/ui/section-card';
import { notify } from '@/lib/notify';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';
import { Badge } from '@/components/ui/badge';

interface PreFilledContext {
  name: string;
  cluster: string;
  api_server: string;
  is_current: boolean;
  kubeconfig_b64: string;
}

interface ClusterConfigDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: number;
  cluster?: K8sCluster | null;
  /** If set, auto-enable SSH tunnel and pre-select this credential for new clusters */
  projectSshCredentialId?: number;
  /** Pre-filled kubeconfig probe result from a discovered node */
  preFilledProbe?: { contexts: PreFilledContext[]; host: string; node_ip: string } | null;
}

/** Extract hostname and port from a K8s API server URL (e.g. https://10.0.0.5:6443). */
function parseApiServer(apiServer: string | undefined | null): [string, number] {
  if (!apiServer) return ['localhost', 6443];
  try {
    const url = new URL(apiServer);
    return [url.hostname || 'localhost', parseInt(url.port) || 6443];
  } catch {
    return ['localhost', 6443];
  }
}

export function ClusterConfigDialog({ open, onOpenChange, projectId, cluster, projectSshCredentialId, preFilledProbe }: ClusterConfigDialogProps) {
  // silent=true: we handle all error display in handleSubmit to support
  // the KUBECONFIG_UNPORTABLE inline-field path vs. toast for other errors.
  const createMutation = useCreateCluster({ silent: true });
  const updateMutation = useUpdateCluster({ silent: true });
  const isEditing = !!cluster;

  // Fetch SSH connections for tunnel configuration
  const { data: sshCredentials } = useQuery({
    queryKey: queryKeys.sshCredentials.list(),
    queryFn: () => api.listSSHCredentials(),
    enabled: open,
  });

  const [formData, setFormData] = useState({
    name: '',
    kubeconfig: '',
    cloud_provider: '',
    region: '',
    context: '',
    default_namespace: 'default',
    ssh_tunnel_enabled: false,
    ssh_remote_k8s_host: 'localhost',
    ssh_remote_k8s_port: 6443,
    ssh_credential_id: null as number | null,
    ssh_host_override: '' as string,
  });
  const [kubeconfigFile, setKubeconfigFile] = useState<File | null>(null);
  const [kubeconfigError, setKubeconfigError] = useState<string | null>(null);
  const [isProbing, setIsProbing] = useState(false);
  const [probeContexts, setProbeContexts] = useState<Array<{
    name: string;
    cluster: string;
    user: string;
    api_server: string;
    is_current: boolean;
  }> | null>(null);
  const [probeKubeconfig, setProbeKubeconfig] = useState<string | null>(null);
  // Per-context kubeconfig map (for kind clusters where each cluster has its own kubeconfig)
  const [contextKubeconfigMap, setContextKubeconfigMap] = useState<Map<string, string>>(new Map());
  const [autoProbed, setAutoProbed] = useState(false);
  // Node IP hint: when set, the tunnel must SSH to this IP (not the project jumphost)
  const [tunnelNodeIpHint, setTunnelNodeIpHint] = useState<string | null>(null);

  // Load cluster data when editing
  useEffect(() => {
    if (cluster) {
      setFormData({
        name: cluster.name,
        kubeconfig: '', // Don't show existing kubeconfig for security
        cloud_provider: cluster.cloud_provider || '',
        region: cluster.region || '',
        context: cluster.context || '',
        default_namespace: cluster.default_namespace || 'default',
        ssh_tunnel_enabled: cluster.ssh_tunnel_enabled || false,
        ssh_remote_k8s_host: cluster.ssh_remote_k8s_host || 'localhost',
        ssh_remote_k8s_port: cluster.ssh_remote_k8s_port || 6443,
        ssh_credential_id: cluster.ssh_credential_id ?? null,
        ssh_host_override: cluster.ssh_host_override || '',
      });
    } else {
      // Reset form when creating new
      // Auto-enable SSH tunnel if project has SSH credentials
      const autoSsh = !!projectSshCredentialId;
      setFormData({
        name: '',
        kubeconfig: '',
        cloud_provider: autoSsh ? 'on-prem' : '',
        region: '',
        context: '',
        default_namespace: 'default',
        ssh_tunnel_enabled: autoSsh,
        ssh_remote_k8s_host: 'localhost',
        ssh_remote_k8s_port: 6443,
        ssh_credential_id: autoSsh ? projectSshCredentialId : null,
        ssh_host_override: '',
      });
      setKubeconfigFile(null);
      setProbeContexts(null);
      setProbeKubeconfig(null);
      setContextKubeconfigMap(new Map());
      setAutoProbed(false);
      setTunnelNodeIpHint(null);
    }
  }, [cluster, open, projectSshCredentialId]);

  // Populate from pre-filled probe result (e.g. from discovered node registration)
  useEffect(() => {
    if (!open || cluster || !preFilledProbe || preFilledProbe.contexts.length === 0) return;

    const mapped = preFilledProbe.contexts.map(ctx => ({
      name: ctx.name,
      cluster: ctx.cluster,
      user: '',
      api_server: ctx.api_server,
      is_current: ctx.is_current,
    }));
    setProbeContexts(mapped);
    setAutoProbed(true); // Prevent the SSH auto-probe from overwriting this

    const ctxMap = new Map(preFilledProbe.contexts.map(ctx => [ctx.name, ctx.kubeconfig_b64]));
    setContextKubeconfigMap(ctxMap);

    const current = preFilledProbe.contexts.find(c => c.is_current) ?? preFilledProbe.contexts[0];
    const sharedKubeconfig = current.kubeconfig_b64;
    setProbeKubeconfig(sharedKubeconfig);

    const [remoteHost, remotePort] = parseApiServer(current.api_server);

    // If the K8s API is bound to localhost (typical for kind), the SSH tunnel must connect
    // directly to the node (node_ip), not to the project jumphost. Clear the pre-selected
    // jumphost credential so the user explicitly picks a credential for the node.
    const apiIsLocalhost = remoteHost === '127.0.0.1' || remoteHost === 'localhost';
    const needsNodeCredential = apiIsLocalhost && !!preFilledProbe.node_ip;
    setTunnelNodeIpHint(needsNodeCredential ? preFilledProbe.node_ip : null);

    setFormData(prev => ({
      ...prev,
      kubeconfig: sharedKubeconfig,
      context: current.name,
      name: prev.name || current.cluster || current.name,
      cloud_provider: 'on-prem',
      ssh_tunnel_enabled: true,
      // When API is localhost-bound: keep the jumphost credential (for its key) but
      // override the SSH endpoint host to connect directly to the node instead.
      ssh_credential_id: projectSshCredentialId ?? null,
      ssh_host_override: needsNodeCredential ? preFilledProbe.node_ip : '',
      ssh_remote_k8s_host: remoteHost,
      ssh_remote_k8s_port: remotePort,
    }));
  }, [open, cluster, preFilledProbe]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-probe when dialog opens for a new cluster with SSH pre-selected
  useEffect(() => {
    if (!open || cluster || !projectSshCredentialId || autoProbed) return;
    setAutoProbed(true);
    const timer = setTimeout(async () => {
      // Inline probe to avoid dependency on handleProbe (not yet defined at hook level)
      try {
        setIsProbing(true);
        const result = await api.probeKubeconfig(projectSshCredentialId);
        if (result.success && result.contexts && result.contexts.length > 0) {
          setProbeContexts(result.contexts);
          setProbeKubeconfig(result.kubeconfig || null);
          const current = result.contexts.find(c => c.is_current) || result.contexts[0];
          const [remoteHost, remotePort] = parseApiServer(current.api_server);
          setFormData(prev => ({
            ...prev,
            kubeconfig: result.kubeconfig || '',
            context: current.name,
            name: prev.name || current.cluster || current.name,
            ssh_remote_k8s_host: remoteHost,
            ssh_remote_k8s_port: remotePort,
          }));
          notify.success(`Found ${result.contexts.length} context(s) on ${result.host}`, undefined, { category: 'credentials' });
        } else {
          notify.error(result.error || 'No kubeconfig found on remote host', undefined, { category: 'credentials' });
        }
      } catch {
        // Silent — user can manually probe later
      } finally {
        setIsProbing(false);
      }
    }, 400);
    return () => clearTimeout(timer);
  }, [open, cluster, projectSshCredentialId, autoProbed]);

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    try {
      const text = await file.text();
      // Base64 encode the kubeconfig
      const base64 = btoa(text);
      setFormData(prev => ({ ...prev, kubeconfig: base64 }));
      setKubeconfigFile(file);
      setKubeconfigError(null);
    } catch (_error) {
      notify.error('Failed to read kubeconfig file', undefined, { category: 'credentials' });
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!isEditing && !formData.kubeconfig) {
      notify.error('Please upload a kubeconfig file', undefined, { category: 'credentials' });
      return;
    }

    // Validate: if SSH tunnel is enabled, require an SSH connection
    if (formData.ssh_tunnel_enabled && !formData.ssh_credential_id) {
      notify.error('Please select an SSH connection for the tunnel', undefined, { category: 'credentials' });
      return;
    }

    // Error handler: KUBECONFIG_UNPORTABLE → inline field message; all others → toast.
    // silent=true on the mutations means we own all error display here.
    const handleMutationError = (err: unknown) => {
      const axiosErr = err as { response?: { data?: { error?: { code?: string; details?: { user_message?: string } } } } };
      const apiError = axiosErr?.response?.data?.error;
      if (apiError?.code === 'KUBECONFIG_UNPORTABLE') {
        setKubeconfigError(
          apiError.details?.user_message ??
          'Kubeconfig is not portable. Please upload a flattened kubeconfig.'
        );
      } else {
        notify.error('Failed to save cluster configuration', undefined, { category: 'credentials' });
      }
    };

    try {
      if (isEditing && cluster) {
        // Build update request (only include fields that are set)
        const updateData: K8sClusterUpdateRequest = {
          name: formData.name !== cluster.name ? formData.name : undefined,
          cloud_provider: formData.cloud_provider || undefined,
          region: formData.region || undefined,
          context: formData.context || undefined,
          default_namespace: formData.default_namespace || undefined,
          ssh_tunnel_enabled: formData.ssh_tunnel_enabled,
          ssh_credential_id: formData.ssh_tunnel_enabled
            ? formData.ssh_credential_id
            : null,
        };

        // Only include kubeconfig if a new one was uploaded
        if (formData.kubeconfig) {
          updateData.kubeconfig = formData.kubeconfig;
        }

        // SSH tunnel endpoint fields (only when tunnel is enabled)
        if (formData.ssh_tunnel_enabled) {
          updateData.ssh_remote_k8s_host = formData.ssh_remote_k8s_host;
          updateData.ssh_remote_k8s_port = formData.ssh_remote_k8s_port;
          updateData.ssh_host_override = formData.ssh_host_override || null;
        }

        await updateMutation.mutateAsync(
          { clusterId: cluster.id, data: updateData },
          { onError: handleMutationError }
        );
      } else {
        // Create new cluster
        const createData: K8sClusterCreateRequest = {
          name: formData.name,
          kubeconfig: formData.kubeconfig,
          cloud_provider: formData.cloud_provider || undefined,
          region: formData.region || undefined,
          context: formData.context || undefined,
          default_namespace: formData.default_namespace || undefined,
          ssh_tunnel_enabled: formData.ssh_tunnel_enabled,
          ssh_credential_id: formData.ssh_tunnel_enabled
            ? formData.ssh_credential_id
            : null,
        };

        // SSH tunnel endpoint fields (only when tunnel is enabled)
        if (formData.ssh_tunnel_enabled) {
          createData.ssh_remote_k8s_host = formData.ssh_remote_k8s_host;
          createData.ssh_remote_k8s_port = formData.ssh_remote_k8s_port;
          createData.ssh_host_override = formData.ssh_host_override || null;
        }

        await createMutation.mutateAsync(
          { projectId, data: createData },
          { onError: handleMutationError }
        );
      }

      onOpenChange(false);
    } catch (_err) {
      // Non-KUBECONFIG_UNPORTABLE errors already handled by mutation's onError (toast)
    }
  };

  const handleChange = (field: string, value: string) => {
    setFormData(prev => ({ ...prev, [field]: value }));
  };

  // Probe remote host for kubeconfig via SSH
  const handleProbe = async () => {
    if (!formData.ssh_credential_id) {
      notify.error('Select an SSH credential first', undefined, { category: 'credentials' });
      return;
    }

    setIsProbing(true);
    setProbeContexts(null);
    setProbeKubeconfig(null);

    try {
      const result = await api.probeKubeconfig(formData.ssh_credential_id);

      if (!result.success) {
        notify.error(result.error || 'Probe failed', undefined, { category: 'credentials' });
        return;
      }

      if (!result.contexts || result.contexts.length === 0) {
        notify.error('No Kubernetes contexts found on remote host', undefined, { category: 'credentials' });
        return;
      }

      setProbeContexts(result.contexts);
      setProbeKubeconfig(result.kubeconfig || null);
      setKubeconfigError(null);

      // Auto-select the current context and populate form
      const current = result.contexts.find(c => c.is_current) || result.contexts[0];
      const [remoteHost, remotePort] = parseApiServer(current.api_server);
      setFormData(prev => ({
        ...prev,
        kubeconfig: result.kubeconfig || '',
        context: current.name,
        name: prev.name || current.cluster || current.name,
        ssh_remote_k8s_host: remoteHost,
        ssh_remote_k8s_port: remotePort,
      }));
      setKubeconfigFile(null); // Clear any uploaded file

      notify.success(`Found ${result.contexts.length} context(s) on ${result.host}`, undefined, { category: 'credentials' });
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : 'Probe failed';
      notify.error(msg, undefined, { category: 'credentials' });
    } finally {
      setIsProbing(false);
    }
  };

  // Select a discovered context
  const handleSelectContext = (ctx: { name: string; cluster: string; api_server: string }) => {
    const [remoteHost, remotePort] = parseApiServer(ctx.api_server);
    // Use per-context kubeconfig (kind clusters) or the shared probe kubeconfig
    const ctxKubeconfig = contextKubeconfigMap.get(ctx.name) ?? probeKubeconfig ?? undefined;
    setFormData(prev => ({
      ...prev,
      context: ctx.name,
      name: prev.name || ctx.cluster || ctx.name,
      kubeconfig: ctxKubeconfig || prev.kubeconfig,
      ssh_remote_k8s_host: remoteHost,
      ssh_remote_k8s_port: remotePort,
    }));
  };

  // Find the selected SSH credential for display
  const selectedSshCredential = sshCredentials?.find(
    (c: SSHCredential) => c.id === formData.ssh_credential_id
  );

  const isLoading = createMutation.isPending || updateMutation.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[600px] max-h-[85vh] overflow-y-auto">
        <form onSubmit={handleSubmit}>
          <DialogHeader>
            <DialogTitle className="text-xl font-bold">{isEditing ? 'Edit Cluster' : 'Add Kubernetes Cluster'}</DialogTitle>
            <DialogDescription>
              {isEditing
                ? 'Update cluster configuration and credentials'
                : 'Connect a Kubernetes cluster to this project (EKS, AKS, GKE, on-prem, etc.)'}
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-6 py-4">
            <SectionCard title="Cluster Identity" compact>
              <div className="grid gap-4">
            {/* Cluster Name */}
            <div className="grid gap-2">
              <Label htmlFor="name">Cluster Name *</Label>
              <Input
                id="name"
                value={formData.name}
                onChange={(e) => handleChange('name', e.target.value)}
                placeholder="my-cluster"
                required
              />
            </div>

            {/* Kubeconfig — Probe via SSH or Manual Upload */}
            <div className="grid gap-2">
              <Label htmlFor="kubeconfig">
                Kubeconfig {isEditing ? '(optional)' : '*'}
              </Label>

              {/* Probe via SSH — available when SSH tunnel is enabled and credential is selected */}
              {formData.ssh_tunnel_enabled && formData.ssh_credential_id && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={handleProbe}
                  disabled={isProbing}
                  className="w-full"
                >
                  {isProbing ? (
                    <>
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      Probing remote host...
                    </>
                  ) : probeContexts ? (
                    <>
                      <CheckCircle2 className="mr-2 h-4 w-4" />
                      Re-probe via SSH
                    </>
                  ) : (
                    <>
                      <Search className="mr-2 h-4 w-4" />
                      Probe via SSH — auto-detect kubeconfig
                    </>
                  )}
                </Button>
              )}

              {/* Discovered contexts from probe */}
              {probeContexts && probeContexts.length > 0 && (
                <div className="rounded-lg border p-3 space-y-2">
                  <p className="text-xs font-medium text-muted-foreground">
                    Discovered {probeContexts.length} context(s):
                  </p>
                  <div className="space-y-1.5">
                    {probeContexts.map((ctx) => (
                      <button
                        key={ctx.name}
                        type="button"
                        onClick={() => handleSelectContext(ctx)}
                        className={`w-full text-left rounded-md border p-2 text-sm transition-colors ${
                          formData.context === ctx.name
                            ? 'border-primary bg-primary/5 ring-1 ring-primary'
                            : 'border-border hover:border-primary/50 hover:bg-muted/50'
                        }`}
                      >
                        <div className="flex items-center gap-2">
                          <span className="font-medium">{ctx.name}</span>
                          {ctx.is_current && (
                            <Badge variant="secondary" className="text-[10px] px-1 py-0">current</Badge>
                          )}
                        </div>
                        <div className="text-xs text-muted-foreground mt-0.5">
                          {ctx.api_server && <span>{ctx.api_server}</span>}
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {/* Manual upload — shown when no probe, or as fallback */}
              {!probeContexts && (
                <>
                  <div className="flex items-center gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => document.getElementById('kubeconfig-file')?.click()}
                      className="w-full"
                    >
                      {kubeconfigFile ? (
                        <>
                          <FileText className="mr-2 h-4 w-4" />
                          {kubeconfigFile.name}
                        </>
                      ) : (
                        <>
                          <Upload className="mr-2 h-4 w-4" />
                          Upload Kubeconfig
                        </>
                      )}
                    </Button>
                    <input
                      id="kubeconfig-file"
                      type="file"
                      accept="*/*"
                      onChange={handleFileUpload}
                      className="hidden"
                    />
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {formData.ssh_tunnel_enabled && formData.ssh_credential_id
                      ? 'Or upload a kubeconfig file manually'
                      : 'Upload your kubeconfig file (usually from ~/.kube/config or cloud provider)'}
                  </p>
                </>
              )}
            </div>

            {/* Kubeconfig portability error — shown inline under the field */}
            {kubeconfigError && (
              <div className="rounded-md border border-destructive/50 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                {kubeconfigError}
              </div>
            )}

            {/* Context (optional) */}
            <div className="grid gap-2">
              <Label htmlFor="context">Context (optional)</Label>
              <Input
                id="context"
                value={formData.context}
                onChange={(e) => handleChange('context', e.target.value)}
                placeholder="Auto-detected from kubeconfig"
              />
              <p className="text-xs text-muted-foreground">
                Leave empty to use the current context from kubeconfig
              </p>
            </div>

            {/* Cloud Provider */}
            <div className="grid gap-2">
              <Label htmlFor="cloud_provider">Cloud Provider (optional)</Label>
              <Select
                value={formData.cloud_provider}
                onValueChange={(value) => handleChange('cloud_provider', value)}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select cloud provider" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="aws">AWS (EKS)</SelectItem>
                  <SelectItem value="azure">Azure (AKS)</SelectItem>
                  <SelectItem value="gcp">GCP (GKE)</SelectItem>
                  <SelectItem value="ibm">IBM Cloud (ROKS)</SelectItem>
                  <SelectItem value="on-prem">On-Premises</SelectItem>
                  <SelectItem value="other">Other</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* Region */}
            <div className="grid gap-2">
              <Label htmlFor="region">Region (optional)</Label>
              <Input
                id="region"
                value={formData.region}
                onChange={(e) => handleChange('region', e.target.value)}
                placeholder="e.g., us-west-2 or eastus"
              />
            </div>

            {/* Default Namespace */}
            <div className="grid gap-2">
              <Label htmlFor="default_namespace">Default Namespace</Label>
              <Input
                id="default_namespace"
                value={formData.default_namespace}
                onChange={(e) => handleChange('default_namespace', e.target.value)}
                placeholder="default"
              />
              <p className="text-xs text-muted-foreground">
                Default namespace for querying resources
              </p>
            </div>
              </div>
            </SectionCard>

            <SectionCard title="SSH Tunnel" compact>
              <div className="grid gap-4">
            {/* ── SSH Tunnel Section ── always visible */}
            <div className="flex items-center justify-between">
              <div className="space-y-0.5">
                <Label htmlFor="ssh_tunnel">Enable Tunnel</Label>
                <p className="text-xs text-muted-foreground">
                  Route K8s API traffic through an SSH tunnel to reach on-prem or private clusters
                </p>
              </div>
              <Switch
                checked={formData.ssh_tunnel_enabled}
                onCheckedChange={(checked) => setFormData(prev => ({ ...prev, ssh_tunnel_enabled: checked }))}
              />
            </div>

            {/* SSH tunnel configuration — shown when toggle is on */}
            {formData.ssh_tunnel_enabled && (
              <>
                {/* SSH Credential picker */}
                <div className="grid gap-2">
                  <Label>SSH Connection *</Label>
                  {sshCredentials && sshCredentials.length > 0 ? (
                    <Select
                      value={formData.ssh_credential_id?.toString() || ''}
                      onValueChange={(value) => setFormData(prev => ({
                        ...prev,
                        ssh_credential_id: value ? parseInt(value) : null,
                      }))}
                    >
                      <SelectTrigger>
                        <SelectValue placeholder="Select SSH connection" />
                      </SelectTrigger>
                      <SelectContent>
                        {sshCredentials.map((cred: SSHCredential) => (
                          <SelectItem key={cred.id} value={cred.id.toString()}>
                            <div className="flex items-center gap-2">
                              <Server className="h-3 w-3 text-muted-foreground" />
                              <span>{cred.name}</span>
                              <span className="text-muted-foreground text-xs">
                                ({cred.username}@{cred.host}:{cred.port})
                              </span>
                            </div>
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  ) : (
                    <div className="rounded-lg border border-dashed border-border p-3 text-center">
                      <p className="text-sm text-muted-foreground mb-2">
                        No SSH connections configured
                      </p>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => {
                          window.open('/settings?tab=ssh', '_blank');
                        }}
                      >
                        <Plus className="mr-2 h-3 w-3" />
                        Add SSH Connection
                      </Button>
                    </div>
                  )}
                  {selectedSshCredential && (
                    <p className="text-xs text-muted-foreground">
                      Will tunnel through {selectedSshCredential.username}@{selectedSshCredential.host}:{selectedSshCredential.port}
                      {' '}({selectedSshCredential.auth_type === 'key' ? 'SSH key' : 'password'} auth)
                    </p>
                  )}
                </div>

                {/* SSH Host Override — shown when set, or when auto-detected from node registration */}
                {(formData.ssh_host_override || tunnelNodeIpHint) && (
                  <div className="grid gap-2">
                    <Label htmlFor="ssh_host_override">SSH Target Host Override</Label>
                    <Input
                      id="ssh_host_override"
                      value={formData.ssh_host_override}
                      onChange={(e) => handleChange('ssh_host_override', e.target.value)}
                      placeholder="e.g. 192.168.68.113"
                    />
                    <p className="text-xs text-muted-foreground">
                      SSH to this host instead of the credential&apos;s host. The credential&apos;s key is reused.
                      Useful when the K8s API is on localhost of a directly-reachable node that shares the same SSH key.
                    </p>
                  </div>
                )}

                {/* Info banner */}
                <div className="bg-info/10 border border-info/20 rounded-lg p-3 text-xs text-info">
                  BNK Forge opens an SSH tunnel through the selected connection and rewrites the kubeconfig server URL automatically.
                  Upload your <strong>original</strong> kubeconfig — no manual tunnel setup needed.
                </div>

                {/* Remote K8s endpoint */}
                <div className="grid grid-cols-2 gap-4">
                  <div className="grid gap-2">
                    <Label htmlFor="ssh_remote_k8s_host">K8s API Host (from SSH host)</Label>
                    <Input
                      id="ssh_remote_k8s_host"
                      value={formData.ssh_remote_k8s_host}
                      onChange={(e) => handleChange('ssh_remote_k8s_host', e.target.value)}
                      placeholder="localhost"
                    />
                  </div>
                  <div className="grid gap-2">
                    <Label htmlFor="ssh_remote_k8s_port">K8s API Port</Label>
                    <Input
                      id="ssh_remote_k8s_port"
                      type="number"
                      value={formData.ssh_remote_k8s_port}
                      onChange={(e) => setFormData(prev => ({ ...prev, ssh_remote_k8s_port: parseInt(e.target.value) || 6443 }))}
                      placeholder="6443"
                    />
                  </div>
                </div>
                <p className="text-xs text-muted-foreground -mt-2">
                  K8s API address as reachable from the SSH host. Auto-filled from probe; edit if the cluster API is on a different host.
                </p>
              </>
            )}
              </div>
            </SectionCard>
          </div>

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={isLoading}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={isLoading}>
              {isLoading ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  {isEditing ? 'Updating...' : 'Adding...'}
                </>
              ) : (
                <>{isEditing ? 'Update Cluster' : 'Add Cluster'}</>
              )}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
