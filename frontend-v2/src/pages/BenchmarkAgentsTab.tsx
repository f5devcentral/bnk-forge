/**
 * BenchmarkAgentsTab — D-020: agent list, built-in agent badge, docs.
 * Slice 1: adds "Register remote agent host" form above the Advanced accordion.
 * Slice 2: wires Scan button → POST .../scan + polls GET .../hosts/{id} for readiness.
 * Slice 5: host/jumphost picker — select from project's existing hosts instead of typing.
 *
 * The built-in "Forge local agent" (name=forge-local OR tags.builtin=true) is
 * surfaced at the top of the list with a "Built-in" badge and a note that it
 * is for control/demo only — real load runs on a registered remote host.
 *
 * Managed remote hosts are listed with a separate "Remote Hosts" card showing
 * provision status, Scan button, and a readiness card once scanned.
 */
import { useState, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { TimeAgo } from '@/components/ui/TimeAgo';
import { SectionCard } from '@/components/ui/section-card';
import {
  Collapsible,
  CollapsibleTrigger,
  CollapsibleContent,
} from '@/components/ui/collapsible';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import {
  Server,
  Wifi,
  WifiOff,
  Loader2,
  Trash2,
  Copy,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Terminal,
  Download,
  Play,
  AlertCircle,
  Cpu,
  Plus,
  ScanLine,
  Wrench,
  XCircle,
  CheckCircle,
  MemoryStick,
  List,
  PenLine,
} from 'lucide-react';
import {
  useAgentHostCandidates,
  useBenchmarkAgents,
  useBenchmarkAgentHosts,
  useCreateBenchmarkAgentHost,
  useDeleteBenchmarkAgent,
  useDeleteBenchmarkAgentHost,
  useImportAwsJumphost,
  useProvisionBenchmarkAgentHost,
  useScanBenchmarkAgentHost,
} from '@/hooks/useBenchmarks';
import { useProjects } from '@/hooks/useProjects';
import { sshCredentialsApi } from '@/lib/api/ssh-credentials';
import type {
  AgentHostCandidate,
  AgentHostCandidateSource,
  AgentHostReadiness,
  BenchmarkAgent,
  BenchmarkAgentHost,
  BenchmarkAgentHostCreate,
} from '@/types/benchmarks';

// ============================================================================
// Helpers
// ============================================================================

function isBuiltinAgent(agent: BenchmarkAgent): boolean {
  const tags = agent.tags as Record<string, unknown> | null;
  return (
    (tags?.builtin === true || tags?.builtin === 'true') ||
    agent.name === 'forge-local'
  );
}

// ============================================================================
// Shared Sub-Components
// ============================================================================

/** Copyable code block — terminal convention keeps dark mono surface */
function CodeBlock({ children, className }: { children: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = useCallback(() => {
    navigator.clipboard.writeText(children);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [children]);

  return (
    <div className={cn('relative group', className)}>
      <pre className="bg-muted/60 border border-border text-foreground/90 rounded-md px-4 py-3 text-sm font-mono overflow-x-auto leading-relaxed">
        {children}
      </pre>
      <Button
        size="icon"
        variant="ghost"
        className="absolute top-2 right-2 h-7 w-7 opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-foreground"
        onClick={copy}
        title="Copy to clipboard"
      >
        {copied ? <CheckCircle2 className="h-3.5 w-3.5 text-success" /> : <Copy className="h-3.5 w-3.5" />}
      </Button>
    </div>
  );
}

/** Collapsible section for how-to docs */
function DocSection({ title, icon: Icon, children, defaultOpen = false }: {
  title: string;
  icon: typeof Terminal;
  children: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="rounded-md border border-border bg-card">
      <button
        className="w-full flex items-center gap-2 px-4 py-3 text-sm font-medium text-left text-foreground"
        onClick={() => setOpen(o => !o)}
      >
        <Icon className="h-4 w-4 text-info shrink-0" />
        <span>{title}</span>
        <span className="ml-auto">
          {open ? <ChevronDown className="h-4 w-4 text-muted-foreground" /> : <ChevronRight className="h-4 w-4 text-muted-foreground" />}
        </span>
      </button>
      {open && <div className="px-4 pb-4 space-y-3">{children}</div>}
    </div>
  );
}

// ============================================================================
// Register Remote Host Form
// ============================================================================

const SOURCE_LABELS: Record<AgentHostCandidateSource, string> = {
  aws_jumphost: 'AWS jumphosts',
  bare_metal: 'Bare-metal hosts',
  cluster_bastion: 'Cluster bastions',
  ssh_credential: 'SSH credentials',
};

const SOURCE_ORDER: AgentHostCandidateSource[] = [
  'aws_jumphost',
  'cluster_bastion',
  'bare_metal',
  'ssh_credential',
];

interface RegisterHostFormProps {
  open: boolean;
  onClose: () => void;
}

function RegisterRemoteHostDialog({ open, onClose }: RegisterHostFormProps) {
  const { data: projects } = useProjects();
  const { data: sshCreds } = useQuery({
    queryKey: ['ssh-credentials', 'list'],
    queryFn: sshCredentialsApi.listSSHCredentials,
    enabled: open,
  });
  const createHost = useCreateBenchmarkAgentHost();
  const importJumphost = useImportAwsJumphost();

  const [form, setForm] = useState<{
    name: string;
    project_id: string;
    host_ip: string;
    ssh_credential_id: string;
    ssh_port: string;
  }>({
    name: '',
    project_id: '',
    host_ip: '',
    ssh_credential_id: '',
    ssh_port: '22',
  });

  // Picker mode: true = show candidate list, false = manual fields
  const [pickerMode, setPickerMode] = useState(true);
  const projectIdNum = form.project_id ? parseInt(form.project_id, 10) : undefined;

  const { data: candidatesResp, isLoading: candidatesLoading } = useAgentHostCandidates(
    open ? projectIdNum : undefined,
  );
  const candidates = candidatesResp?.candidates ?? [];

  // Group candidates by source, preserving display order
  const grouped = SOURCE_ORDER.reduce<Record<string, AgentHostCandidate[]>>((acc, src) => {
    const group = candidates.filter(c => c.source === src);
    if (group.length > 0) acc[src] = group;
    return acc;
  }, {});

  function applyCandidate(candidate: AgentHostCandidate) {
    setForm(f => ({
      ...f,
      host_ip: candidate.host_ip,
      ssh_credential_id: candidate.ssh_credential_id != null ? String(candidate.ssh_credential_id) : '',
      ssh_port: String(candidate.ssh_port ?? 22),
      // Suggest a name from the candidate label (strip IP suffix)
      name: f.name || candidate.label.split(' (')[0].replace(/^.* — /, '').trim().toLowerCase().replace(/\s+/g, '-'),
    }));
    setPickerMode(false);
  }

  function handleImportJumphost(candidate: AgentHostCandidate) {
    if (!candidate.module_id || !projectIdNum) return;
    importJumphost.mutate(
      { projectId: projectIdNum, moduleId: candidate.module_id },
      {
        onSuccess: (result) => {
          setForm(f => ({
            ...f,
            ssh_credential_id: String(result.ssh_credential_id),
          }));
        },
      },
    );
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.name || !form.project_id || !form.host_ip || !form.ssh_credential_id) return;

    const payload: BenchmarkAgentHostCreate = {
      name: form.name.trim(),
      project_id: parseInt(form.project_id, 10),
      host_ip: form.host_ip.trim(),
      ssh_credential_id: parseInt(form.ssh_credential_id, 10),
      ssh_port: parseInt(form.ssh_port, 10) || 22,
    };

    createHost.mutate(payload, {
      onSuccess: () => {
        setForm({ name: '', project_id: '', host_ip: '', ssh_credential_id: '', ssh_port: '22' });
        setPickerMode(true);
        onClose();
      },
    });
  };

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) onClose(); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Register remote agent host</DialogTitle>
          <DialogDescription>
            Register a remote server as a Forge-managed benchmark agent host.
            Forge will SSH in and provision aiperf in a later step.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Name — always visible */}
          <div className="space-y-1.5">
            <Label htmlFor="host-name">Name</Label>
            <Input
              id="host-name"
              placeholder="loadgen-01"
              value={form.name}
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
              required
            />
          </div>

          {/* Project picker */}
          <div className="space-y-1.5">
            <Label htmlFor="host-project">Project</Label>
            <Select value={form.project_id} onValueChange={v => {
              setForm(f => ({ ...f, project_id: v, host_ip: '', ssh_credential_id: '' }));
              setPickerMode(true);
            }}>
              <SelectTrigger id="host-project">
                <SelectValue placeholder="Select a project…" />
              </SelectTrigger>
              <SelectContent>
                {(projects ?? []).map(p => (
                  <SelectItem key={p.id} value={String(p.id)}>{p.name}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Host selection — picker or manual toggle */}
          {form.project_id && (
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-foreground">Host</span>
                <div className="ml-auto flex items-center gap-1">
                  <Button
                    type="button"
                    size="sm"
                    variant={pickerMode ? 'secondary' : 'ghost'}
                    className="h-6 px-2 text-xs"
                    onClick={() => setPickerMode(true)}
                  >
                    <List className="h-3 w-3 mr-1" />
                    Pick from project
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant={!pickerMode ? 'secondary' : 'ghost'}
                    className="h-6 px-2 text-xs"
                    onClick={() => setPickerMode(false)}
                  >
                    <PenLine className="h-3 w-3 mr-1" />
                    Enter manually
                  </Button>
                </div>
              </div>

              {pickerMode ? (
                <div className="rounded-md border border-border bg-muted/20 max-h-56 overflow-y-auto">
                  {candidatesLoading ? (
                    <div className="flex items-center gap-2 p-3 text-xs text-muted-foreground">
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      Loading project hosts…
                    </div>
                  ) : candidates.length === 0 ? (
                    <p className="p-3 text-xs text-muted-foreground">
                      No hosts found for this project. Use "Enter manually" to type an IP.
                    </p>
                  ) : (
                    <div className="divide-y divide-border">
                      {SOURCE_ORDER.filter(src => grouped[src]).map(src => (
                        <div key={src}>
                          <p className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground bg-muted/40">
                            {SOURCE_LABELS[src]}
                          </p>
                          {grouped[src].map((c, i) => (
                            <div
                              key={i}
                              className="flex items-center gap-2 px-3 py-2 hover:bg-accent/50 cursor-pointer group"
                              onClick={() => !c.needs_credential_import && applyCandidate(c)}
                            >
                              <div className="flex-1 min-w-0">
                                <p className="text-xs font-medium text-foreground truncate">{c.label}</p>
                                <p className="text-[10px] text-muted-foreground font-mono">{c.host_ip}</p>
                                {c.last_test_status && (
                                  <Badge
                                    variant={c.last_test_status === 'ok' ? 'success' : 'destructive'}
                                    className="text-[9px] px-1 py-0 h-3.5 mt-0.5"
                                  >
                                    {c.last_test_status}
                                  </Badge>
                                )}
                              </div>
                              {c.needs_credential_import ? (
                                <Button
                                  type="button"
                                  size="sm"
                                  variant="outline"
                                  className="h-6 px-2 text-xs shrink-0"
                                  disabled={importJumphost.isPending}
                                  onClick={(e) => { e.stopPropagation(); handleImportJumphost(c); }}
                                >
                                  {importJumphost.isPending ? (
                                    <Loader2 className="h-3 w-3 animate-spin" />
                                  ) : (
                                    'Import credential'
                                  )}
                                </Button>
                              ) : (
                                <Button
                                  type="button"
                                  size="sm"
                                  variant="ghost"
                                  className="h-6 px-2 text-xs shrink-0 opacity-0 group-hover:opacity-100"
                                  onClick={() => applyCandidate(c)}
                                >
                                  Select
                                </Button>
                              )}
                            </div>
                          ))}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ) : (
                /* Manual fields — shown when picker is dismissed or no candidates */
                <div className="space-y-3">
                  <div className="space-y-1.5">
                    <Label htmlFor="host-ip">Host IP</Label>
                    <Input
                      id="host-ip"
                      placeholder="10.0.1.100"
                      value={form.host_ip}
                      onChange={e => setForm(f => ({ ...f, host_ip: e.target.value }))}
                      required
                    />
                    <p className="text-xs text-muted-foreground">IP address Forge will SSH to when provisioning</p>
                  </div>

                  <div className="space-y-1.5">
                    <Label htmlFor="host-ssh-cred">SSH credential</Label>
                    <Select value={form.ssh_credential_id} onValueChange={v => setForm(f => ({ ...f, ssh_credential_id: v }))}>
                      <SelectTrigger id="host-ssh-cred">
                        <SelectValue placeholder="Select SSH credential…" />
                      </SelectTrigger>
                      <SelectContent>
                        {(sshCreds ?? []).map(c => (
                          <SelectItem key={c.id} value={String(c.id)}>{c.name} ({c.username}@{c.host})</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    {(!sshCreds || sshCreds.length === 0) && (
                      <p className="text-xs text-warning">No SSH credentials found. Create one in Settings → SSH Credentials first.</p>
                    )}
                  </div>

                  <div className="space-y-1.5">
                    <Label htmlFor="host-ssh-port">SSH port</Label>
                    <Input
                      id="host-ssh-port"
                      type="number"
                      min={1}
                      max={65535}
                      value={form.ssh_port}
                      onChange={e => setForm(f => ({ ...f, ssh_port: e.target.value }))}
                    />
                  </div>
                </div>
              )}

              {/* Pre-filled summary — shown once a candidate is selected */}
              {!pickerMode && form.host_ip && (
                <div className="rounded-md border border-border bg-muted/20 p-2.5 text-xs space-y-1">
                  <p className="text-muted-foreground font-medium">Pre-filled from selection:</p>
                  <div className="font-mono text-foreground/80 space-y-0.5">
                    <div><span className="text-muted-foreground">IP:</span> {form.host_ip}</div>
                    <div><span className="text-muted-foreground">Port:</span> {form.ssh_port}</div>
                    {form.ssh_credential_id && (
                      <div><span className="text-muted-foreground">Cred ID:</span> {form.ssh_credential_id}</div>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
            <Button
              type="submit"
              disabled={
                createHost.isPending ||
                !form.name || !form.project_id || !form.host_ip || !form.ssh_credential_id
              }
            >
              {createHost.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
              Register host
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ============================================================================
// Readiness Card — shown after a scan completes
// ============================================================================

const VERDICT_LABELS: Record<string, { label: string; variant: 'success' | 'warning' | 'destructive' | 'muted' }> = {
  ready: { label: 'Ready', variant: 'success' },
  needs_provision: { label: 'Needs provisioning', variant: 'warning' },
  unreachable_to_targets: { label: 'Targets unreachable', variant: 'destructive' },
  ssh_unreachable: { label: 'SSH unreachable', variant: 'destructive' },
};

function ReadinessCard({ readiness }: { readiness: AgentHostReadiness }) {
  const verdict = VERDICT_LABELS[readiness.verdict] ?? { label: readiness.verdict, variant: 'muted' as const };
  const tools = readiness.tools;

  return (
    <div className="rounded-md border border-border bg-muted/20 p-3 mt-2 space-y-2 text-xs">
      <div className="flex items-center gap-2">
        <span className="text-muted-foreground font-medium">Readiness:</span>
        <Badge variant={verdict.variant}>{verdict.label}</Badge>
      </div>

      {/* OS / HW summary */}
      {(readiness.os.os_pretty_name || readiness.cpu || readiness.mem_gb) && (
        <div className="flex flex-wrap gap-3 text-muted-foreground">
          {readiness.os.os_pretty_name && (
            <span className="flex items-center gap-1">
              <Server className="h-3 w-3" />
              {readiness.os.os_pretty_name}
              {readiness.os.architecture && ` (${readiness.os.architecture})`}
            </span>
          )}
          {readiness.cpu != null && (
            <span className="flex items-center gap-1">
              <Cpu className="h-3 w-3" />
              {readiness.cpu} CPU
            </span>
          )}
          {readiness.mem_gb != null && (
            <span className="flex items-center gap-1">
              <MemoryStick className="h-3 w-3" />
              {readiness.mem_gb} GB
            </span>
          )}
        </div>
      )}

      {/* Tool presence */}
      <div className="flex flex-wrap gap-2">
        {(['python3', 'pip', 'aiperf', 'systemctl'] as const).map((tool) => {
          const present = (tools as unknown as Record<string, boolean>)[tool];
          return (
            <span key={tool} className="flex items-center gap-1">
              {present
                ? <CheckCircle className="h-3 w-3 text-success" />
                : <XCircle className="h-3 w-3 text-destructive" />}
              <span className={`font-mono ${present ? 'text-foreground/80' : 'text-muted-foreground line-through'}`}>
                {tool}
              </span>
            </span>
          );
        })}
        {tools.python3_version && (
          <span className="text-muted-foreground">({tools.python3_version})</span>
        )}
      </div>

      {/* Target reachability */}
      {readiness.reachable_targets.length > 0 && (
        <div>
          <p className="text-muted-foreground font-medium mb-1">Target reachability from host:</p>
          <div className="space-y-0.5">
            {readiness.reachable_targets.map((t) => (
              <div key={t.target_id} className="flex items-center gap-2">
                {t.ok
                  ? <CheckCircle className="h-3 w-3 text-success shrink-0" />
                  : <XCircle className="h-3 w-3 text-destructive shrink-0" />}
                <span className={`${t.ok ? 'text-foreground/80' : 'text-muted-foreground'}`}>{t.name}</span>
                <span className="font-mono text-muted-foreground text-[10px]">{t.llm_base_url}</span>
                {t.http_code != null && (
                  <span className="text-muted-foreground text-[10px]">HTTP {t.http_code}</span>
                )}
                {t.error && (
                  <span className="text-destructive text-[10px] truncate max-w-[200px]">{t.error}</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Remote Hosts List
// ============================================================================

function RemoteHostRow({ host }: { host: BenchmarkAgentHost }) {
  const scanHost = useScanBenchmarkAgentHost();
  const provisionHost = useProvisionBenchmarkAgentHost();
  const deleteHost = useDeleteBenchmarkAgentHost();
  const isScanning = host.provision_status === 'scanning' || scanHost.isPending;
  const isProvisioning = host.provision_status === 'provisioning' || provisionHost.isPending;

  const readiness = host.readiness as AgentHostReadiness | null;
  const [showReadiness, setShowReadiness] = useState(false);
  const [showProvisionLog, setShowProvisionLog] = useState(false);

  // Provision button is enabled when readiness verdict is ready or needs_provision
  const readinessVerdict = readiness?.verdict;
  const canProvision =
    !isScanning &&
    !isProvisioning &&
    host.provision_status !== 'provisioned' &&
    (readinessVerdict === 'ready' || readinessVerdict === 'needs_provision');

  // Auto-show provision log while provisioning
  const showLog = isProvisioning || (showProvisionLog && !!host.provision_message);

  return (
    <>
      <TableRow key={host.id}>
        <TableCell>
          <div className="flex items-center gap-2">
            <Server className="h-4 w-4 text-muted-foreground shrink-0" />
            <span className="font-medium text-foreground">{host.name}</span>
            <Badge variant="info" className="text-[10px] px-1.5 py-0">
              Managed remote
            </Badge>
          </div>
        </TableCell>
        <TableCell className="font-mono text-xs text-muted-foreground">
          {host.host_ip || '—'}
        </TableCell>
        <TableCell>
          <Badge variant={
            host.status === 'connected' ? 'success'
            : host.status === 'running' ? 'info'
            : 'muted'
          }>
            {host.status}
          </Badge>
        </TableCell>
        <TableCell>
          <Badge variant={
            host.provision_status === 'provisioned' ? 'success'
            : host.provision_status === 'provisioning' ? 'info'
            : host.provision_status === 'scanning' ? 'info'
            : host.provision_status === 'failed' ? 'destructive'
            : 'muted'
          }>
            {host.provision_status ?? 'unprovisioned'}
          </Badge>
        </TableCell>
        <TableCell className="text-right">
          <div className="flex items-center justify-end gap-1">
            {/* Readiness toggle — only visible after a scan */}
            {readiness && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 text-muted-foreground"
                    title={showReadiness ? 'Hide readiness details' : 'Show readiness details'}
                    onClick={() => setShowReadiness((v) => !v)}
                  >
                    <CheckCircle2 className="h-3.5 w-3.5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>{showReadiness ? 'Hide' : 'Show'} readiness details</TooltipContent>
              </Tooltip>
            )}
            {/* Provision log toggle — visible when there's a message */}
            {host.provision_message && !isProvisioning && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7 text-muted-foreground"
                    title={showProvisionLog ? 'Hide provision log' : 'Show provision log'}
                    onClick={() => setShowProvisionLog((v) => !v)}
                  >
                    <Terminal className="h-3.5 w-3.5" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>{showProvisionLog ? 'Hide' : 'Show'} provision log</TooltipContent>
              </Tooltip>
            )}
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 text-muted-foreground"
                  disabled={isScanning}
                  title={isScanning ? 'Scanning…' : 'Run SSH suitability scan'}
                  onClick={() => scanHost.mutate({ hostId: host.id })}
                >
                  {isScanning
                    ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    : <ScanLine className="h-3.5 w-3.5" />}
                </Button>
              </TooltipTrigger>
              <TooltipContent>{isScanning ? 'Scanning…' : 'Run SSH suitability scan'}</TooltipContent>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger asChild>
                <span>
                  <Button
                    variant="ghost"
                    size="icon"
                    className={cn(
                      'h-7 w-7',
                      canProvision
                        ? 'text-muted-foreground hover:text-primary'
                        : 'text-muted-foreground/40',
                    )}
                    disabled={!canProvision}
                    title={
                      isProvisioning
                        ? 'Provisioning in progress…'
                        : canProvision
                          ? 'SSH-provision host (install aiperf + forge_agent)'
                          : host.provision_status === 'provisioned'
                            ? 'Already provisioned'
                            : 'Run a scan first to check host readiness'
                    }
                    onClick={() => {
                      if (canProvision) {
                        setShowProvisionLog(true);
                        provisionHost.mutate(host.id);
                      }
                    }}
                  >
                    {isProvisioning
                      ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      : <Wrench className="h-3.5 w-3.5" />}
                  </Button>
                </span>
              </TooltipTrigger>
              <TooltipContent>
                {isProvisioning
                  ? 'Provisioning in progress…'
                  : canProvision
                    ? 'SSH-provision: install aiperf + forge_agent + systemd'
                    : host.provision_status === 'provisioned'
                      ? 'Already provisioned'
                      : 'Scan the host first (readiness must be "ready" or "needs_provision")'}
              </TooltipContent>
            </Tooltip>
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 text-muted-foreground hover:text-destructive"
              onClick={() => {
                if (confirm(`Remove remote host "${host.name}"?`)) {
                  deleteHost.mutate(host.id);
                }
              }}
              title="Remove host"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          </div>
        </TableCell>
      </TableRow>
      {/* Provision live install log — shown while provisioning or when toggled */}
      {showLog && host.provision_message && (
        <TableRow>
          <TableCell colSpan={5} className="pb-3 pt-0">
            <div className="rounded-md border border-border bg-muted/30 p-2 mt-1">
              <div className="flex items-center gap-1.5 mb-1.5">
                {isProvisioning && <Loader2 className="h-3 w-3 animate-spin text-info" />}
                <span className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide">
                  {isProvisioning ? 'Installing…' : 'Provision log'}
                </span>
              </div>
              <pre className="text-xs font-mono text-foreground/80 whitespace-pre-wrap leading-relaxed">
                {host.provision_message}
              </pre>
            </div>
          </TableCell>
        </TableRow>
      )}
      {/* Readiness expandable row */}
      {readiness && showReadiness && (
        <TableRow>
          <TableCell colSpan={5} className="pb-3 pt-0">
            <ReadinessCard readiness={readiness} />
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

function RemoteHostsCard() {
  const { data: hosts, isLoading } = useBenchmarkAgentHosts();

  if (isLoading) {
    return (
      <SectionCard title="Remote agent hosts" compact>
        <div className="space-y-2">{[1, 2].map(i => <Skeleton key={i} className="h-10 w-full" />)}</div>
      </SectionCard>
    );
  }

  if (!hosts || hosts.length === 0) {
    return null;
  }

  return (
    <SectionCard
      title={`${hosts.length} remote ${hosts.length === 1 ? 'host' : 'hosts'}`}
      compact
    >
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Host IP</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Provision</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {hosts.map(host => (
              <RemoteHostRow key={host.id} host={host} />
            ))}
          </TableBody>
        </Table>
      </div>
    </SectionCard>
  );
}

// ============================================================================
// Main Component
// ============================================================================

export function BenchmarkAgentsTab() {
  const { data: agents, isLoading } = useBenchmarkAgents();
  const deleteAgent = useDeleteBenchmarkAgent();
  const forgeUrl = typeof window !== 'undefined' ? window.location.origin : 'https://forge';
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [registerOpen, setRegisterOpen] = useState(false);

  // Sort so builtin agents appear first
  const sortedAgents = agents
    ? [...agents].sort((a, b) => {
        const aBuiltin = isBuiltinAgent(a) ? 0 : 1;
        const bBuiltin = isBuiltinAgent(b) ? 0 : 1;
        return aBuiltin - bBuiltin;
      })
    : [];

  return (
    <div className="space-y-6">
      {/* Primary action — Register a remote host */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-foreground">Remote agent hosts</h3>
          <p className="text-xs text-muted-foreground mt-0.5">
            Register bare-metal or VM servers as Forge-managed benchmark agents.
            Real load testing runs on these — the built-in Docker agent is for control/demo only.
          </p>
        </div>
        <Button size="sm" onClick={() => setRegisterOpen(true)}>
          <Plus className="h-4 w-4 mr-1.5" />
          Register remote host
        </Button>
      </div>

      <RegisterRemoteHostDialog open={registerOpen} onClose={() => setRegisterOpen(false)} />

      {/* Remote managed hosts list */}
      <RemoteHostsCard />

      {/* Agent list (built-in + self-registered) */}
      {isLoading ? (
        <SectionCard title="Registered test clients" compact>
          <div className="space-y-2">{[1, 2, 3].map(i => <Skeleton key={i} className="h-12 w-full" />)}</div>
        </SectionCard>
      ) : !agents || agents.length === 0 ? (
        <SectionCard title="Registered test clients">
          <div className="flex items-start gap-3">
            <Server className="h-5 w-5 text-muted-foreground mt-0.5 shrink-0" />
            <div className="flex-1">
              <p className="text-sm font-medium text-foreground mb-1">No test clients registered</p>
              <p className="text-xs text-muted-foreground">
                The built-in agent registers automatically on boot — for control and demo use only.
                Register a remote host above for production load testing.
              </p>
            </div>
          </div>
        </SectionCard>
      ) : (
        <SectionCard
          title={`${agents.length} test ${agents.length === 1 ? 'client' : 'clients'}`}
          compact
        >
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Hostname</TableHead>
                  <TableHead>IP</TableHead>
                  <TableHead>Last heartbeat</TableHead>
                  <TableHead>Capabilities</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sortedAgents.map(agent => {
                  const builtin = isBuiltinAgent(agent);
                  return (
                    <TableRow key={agent.id}>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          {builtin ? (
                            <Cpu className="h-4 w-4 text-info shrink-0" />
                          ) : agent.status === 'connected' ? (
                            <Wifi className="h-4 w-4 text-success" />
                          ) : agent.status === 'running' ? (
                            <Loader2 className="h-4 w-4 text-info animate-spin" />
                          ) : (
                            <WifiOff className="h-4 w-4 text-muted-foreground" />
                          )}
                          <span className="font-medium text-foreground">{agent.name}</span>
                          {builtin && (
                            <Badge variant="info" className="text-[10px] px-1.5 py-0">
                              Built-in • control/demo only
                            </Badge>
                          )}
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant={
                            agent.status === 'connected'
                              ? 'success'
                              : agent.status === 'running'
                                ? 'info'
                                : 'muted'
                          }
                        >
                          {agent.status}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-mono text-xs text-muted-foreground">
                        {agent.hostname || '—'}
                      </TableCell>
                      <TableCell className="font-mono text-xs text-muted-foreground">
                        {agent.ip_address || '—'}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {agent.last_heartbeat ? <TimeAgo dateStr={agent.last_heartbeat} /> : '—'}
                      </TableCell>
                      <TableCell>
                        {agent.capabilities ? (
                          <div className="flex flex-wrap gap-1 max-w-[200px]">
                            {Object.entries(agent.capabilities as Record<string, unknown>).map(([k, v]) => (
                              <span
                                key={k}
                                className="px-1.5 py-0.5 bg-muted/60 border border-border rounded text-muted-foreground text-[10px]"
                              >
                                {k}: {String(v)}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <span className="text-xs text-muted-foreground">—</span>
                        )}
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7 text-muted-foreground hover:text-destructive"
                          disabled={builtin}
                          onClick={() => {
                            if (!builtin && confirm(`Deregister agent "${agent.name}"?`)) {
                              deleteAgent.mutate(agent.id);
                            }
                          }}
                          title={builtin ? 'Built-in agent cannot be deregistered' : 'Deregister agent'}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        </SectionCard>
      )}

      {/* Advanced — register an external agent (collapsed by default) */}
      <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
        <div className="rounded-xl border border-border bg-card">
          <CollapsibleTrigger asChild>
            <button
              className="w-full flex items-center gap-2 px-6 py-4 text-sm font-semibold text-left text-foreground"
              aria-expanded={advancedOpen}
              data-testid="advanced-agent-accordion"
            >
              <Server className="h-4 w-4 text-muted-foreground shrink-0" />
              Advanced — register an external agent
              <span className="text-xs text-muted-foreground font-normal ml-2">
                Install aiperf on a remote machine and connect it to Forge
              </span>
              <span className="ml-auto">
                {advancedOpen
                  ? <ChevronDown className="h-4 w-4 text-muted-foreground" />
                  : <ChevronRight className="h-4 w-4 text-muted-foreground" />}
              </span>
            </button>
          </CollapsibleTrigger>

          <CollapsibleContent>
            <div className="px-6 pb-6 space-y-3">
              {/* Step 0: Get Bearer Token */}
              <DocSection title="Step 0 — Get Your Bearer Token" icon={AlertCircle}>
                <p className="text-xs text-muted-foreground">
                  All API calls require a bearer token. Log in via the API, then copy the token from the response.
                </p>
                <CodeBlock>{`# Login to get a bearer token\ncurl -s -X POST ${forgeUrl}/api/auth/login \\\n  -H "Content-Type: application/json" \\\n  -d '{"username": "admin", "password": "your-password"}' \\\n  | python3 -m json.tool\n\n# Copy the "token" field from the response.\n# Use it in all subsequent requests as:\n#   -H "Authorization: Bearer <paste-token-here>"`}</CodeBlock>
                <p className="text-xs text-muted-foreground mt-2">
                  <span className="font-medium text-warning">Tip:</span> Save the token to a variable for convenience:
                </p>
                <CodeBlock>{`export FORGE_TOKEN=$(curl -s -X POST ${forgeUrl}/api/auth/login \\\n  -H "Content-Type: application/json" \\\n  -d '{"username": "admin", "password": "your-password"}' \\\n  | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")\n\n# Then use: -H "Authorization: Bearer $FORGE_TOKEN"`}</CodeBlock>
                <p className="text-xs text-muted-foreground mt-2">
                  <span className="font-medium text-foreground/80">Or from the browser:</span> Open DevTools (F12) {'→'} Console {'→'} type <code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">localStorage.getItem('auth_token')</code> to copy the token from your current session.
                </p>
              </DocSection>

              {/* Step 1: Register an Agent */}
              <DocSection title="Step 1 — Register an Agent" icon={Server}>
                <p className="text-xs text-muted-foreground">
                  Register a test client machine so benchmark results can be associated with it. Optional — you can push results without an agent.
                </p>
                <CodeBlock>{`curl -X POST ${forgeUrl}/api/benchmarks/agents \\\n  -H "Content-Type: application/json" \\\n  -H "Authorization: Bearer $FORGE_TOKEN" \\\n  -d '{\n    "name": "loadgen-01",\n    "hostname": "'$(hostname)'",\n    "ip_address": "'$(hostname -I | awk "{print \\$1}")'"\n  }'`}</CodeBlock>
                <p className="text-xs text-muted-foreground mt-2">
                  The <code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">name</code> field must be unique. POSTing the same name again updates the existing agent (upsert).
                </p>
              </DocSection>

              {/* Step 2: Install aiperf */}
              <DocSection title="Step 2 — Install aiperf" icon={Download}>
                <p className="text-xs text-muted-foreground mb-2">
                  AIPerf is an NVIDIA benchmarking tool for generative AI inference. Requires <span className="font-semibold text-warning">Python 3.10+</span>.
                  See <a href="https://github.com/ai-dynamo/aiperf" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">ai-dynamo/aiperf</a> docs.
                </p>
                <CodeBlock>{`# Ensure Python 3.10+\npython3 --version\n\n# Create venv and install\npython3 -m venv ~/venv-aiperf\nsource ~/venv-aiperf/bin/activate\npip install --upgrade pip\npip install aiperf\n\n# Verify\naiperf --help`}</CodeBlock>
                <p className="text-xs text-muted-foreground mt-2">
                  <span className="font-medium text-warning">macOS:</span> Use <code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">brew install python@3.12</code> then create venv with <code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">$(brew --prefix python@3.12)/bin/python3.12 -m venv ~/venv-aiperf</code>
                </p>
              </DocSection>

              {/* Step 3: Run Benchmarks */}
              <DocSection title="Step 3 — Run aiperf profile" icon={Play}>
                <p className="text-xs text-muted-foreground mb-2">
                  Benchmarks an LLM endpoint. Reports TTFT, ITL, throughput, and latency percentiles. Config keys in the Configs tab map to CLI flags.
                </p>
                <CodeBlock>{`# Quick test (10 requests)\naiperf profile \\\n    --model "your-model-name" \\\n    --url http://YOUR_LLM_ENDPOINT:8000 \\\n    --endpoint-type chat \\\n    --streaming \\\n    --request-count 10 \\\n    --concurrency 2 \\\n    --ui simple`}</CodeBlock>
                <CodeBlock>{`# Standard benchmark (100 requests, warmup)\naiperf profile \\\n    --model "your-model-name" \\\n    --url http://YOUR_PROXY_OR_LLM:8000 \\\n    --endpoint-type chat \\\n    --streaming \\\n    --request-count 100 \\\n    --concurrency 10 \\\n    --warmup-request-count 5 \\\n    --isl 550 --osl 150`}</CodeBlock>
                <p className="text-xs text-muted-foreground mt-2">
                  <span className="font-medium text-foreground/80">Tip:</span> Check model name first: <code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">curl -s http://YOUR_ENDPOINT:8000/v1/models | python3 -m json.tool</code>.
                  If the model path differs from HuggingFace repo, add <code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">--tokenizer HF_ORG/MODEL_NAME</code>.
                </p>
              </DocSection>

              {/* Step 4: Push Results */}
              <DocSection title="Step 4 — Push Results to Forge" icon={Terminal}>
                <p className="text-xs text-muted-foreground mb-2">
                  After <code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">aiperf profile</code> completes, push the JSON output to Forge.
                  Results appear in the Runs tab automatically.
                </p>
                <CodeBlock>{`# Find latest result\nls -t artifacts/*/profile_export_aiperf.json | head -1\n\n# Push to Forge\ncurl -X POST "${forgeUrl}/api/benchmarks/results/aiperf?proxy=nodeport" \\\n  -H "Content-Type: application/json" \\\n  -H "Authorization: Bearer $FORGE_TOKEN" \\\n  -d @artifacts/LATEST_RUN/profile_export_aiperf.json`}</CodeBlock>
                <p className="text-xs text-muted-foreground mt-2">
                  <span className="font-medium text-foreground/80">Query params:</span>{' '}
                  <code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">proxy</code> (envoy/nginx/haproxy/f5-bnk/nodeport),{' '}
                  <code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">model</code>,{' '}
                  <code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">run_label</code>,{' '}
                  <code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">agent_name</code>
                </p>
              </DocSection>

              {/* Network Requirements */}
              <DocSection title="Network Requirements" icon={Wifi}>
                <div className="text-xs text-muted-foreground space-y-2">
                  <p>
                    <span className="font-medium text-warning">aiperf must reach the LLM endpoint.</span>{' '}
                    ClusterIPs and BNK Gateway VIPs are typically only reachable from within the cluster.
                  </p>
                  <ul className="list-disc list-inside space-y-1 ml-2">
                    <li><span className="font-medium text-foreground/80">NodePort</span> — cluster node IP + allocated port</li>
                    <li><span className="font-medium text-foreground/80">SSH tunnel</span> — <code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">ssh -L 8000:VIP:80 user@node</code></li>
                    <li><span className="font-medium text-foreground/80">Run on cluster node</span> — direct access to all endpoints</li>
                    <li><span className="font-medium text-foreground/80">LoadBalancer</span> — external LB IP if available</li>
                  </ul>
                </div>
              </DocSection>
            </div>
          </CollapsibleContent>
        </div>
      </Collapsible>
    </div>
  );
}
