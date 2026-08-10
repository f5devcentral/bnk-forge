/**
 * TMMDebugPanel — GUI access to TMM debug sidecar diagnostic commands.
 *
 * Provides pre-built command cards for common tmctl/bdt_cli/configview commands,
 * a raw command input for advanced users, and parsed table output rendering.
 *
 * Architecture: Exec directly into the `debug` container of f5-tmm-* pods
 * via kubeconfig. Does NOT use the bnk-forge-agent pod (Decision D4).
 *
 * F5 Docs: https://clouddocs.f5.com/bigip-next-for-kubernetes/latest/overviews/spk-tmm-debug.html
 */

import { useState, useCallback, useRef, useEffect } from 'react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card } from '@/components/ui/card';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Activity,
  AlertTriangle,
  ArrowDownUp,
  BarChart3,
  Check,
  ChevronDown,
  Clock,
  Copy,
  Cpu,
  Globe,
  Loader2,
  MonitorDot,
  Network,
  Play,
  Radar,
  RefreshCw,
  Server,
  Settings,
  Shield,
  Terminal,
  Wifi,
  Zap,
} from 'lucide-react';
import {
  useTMMDebugPods,
  useTMMDebugExec,
  useTMMDebugTmctl,
  useTMMDebugBdt,
  useTMMDebugConfigview,
  useTMMDebugConfigviewUuids,
} from '@/hooks/useTMMDebug';
import type {
  TMMDebugPod,
  TMMDebugTmctlResponse,
  TMMDebugExecResponse,
  TMMDebugConfigviewUuidsResponse,
  CommandCard,
  CommandCategory,
} from '@/types';


// ---------------------------------------------------------------------------
// Pre-built Command Card Definitions
// ---------------------------------------------------------------------------

const TMCTL_COMMANDS: CommandCard[] = [
  {
    id: 'virtual_server_stat',
    name: 'Virtual Server Stats',
    description: 'Traffic bytes in/out for virtual servers',
    category: 'tmctl',
  },
  {
    id: 'pool_member_stat',
    name: 'Pool Member Stats',
    description: 'Connection counts per pool member',
    category: 'tmctl',
  },
  {
    id: 'tmm_stat',
    name: 'TMM Stats',
    description: 'CPU, memory, and connection stats',
    category: 'tmctl',
  },
  {
    id: 'fw_rule_stat',
    name: 'Firewall Rules',
    description: 'Firewall rule hit counts',
    category: 'tmctl',
  },
  {
    id: 'rule_stat',
    name: 'iRule Stats',
    description: 'iRule execution statistics',
    category: 'tmctl',
  },
  {
    id: 'rst_cause_stat',
    name: 'RST Causes',
    description: 'TCP reset cause analysis',
    category: 'tmctl',
  },
  {
    id: 'doca_flow_entries',
    name: 'DOCA Flow Entries',
    description: 'DPU flow offloading entries',
    category: 'tmctl',
  },
  {
    id: 'doca_flow_fwd',
    name: 'DOCA Flow Forward',
    description: 'DPU forward flow stats',
    category: 'tmctl',
  },
  {
    id: 'tmm/flow_redir_stats',
    name: 'Flow Redirect',
    description: 'Flow redirect statistics',
    category: 'tmctl',
  },
  {
    id: 'profile_udp_statexit',
    name: 'UDP Profile Stats',
    description: 'UDP profile state/exit statistics',
    category: 'tmctl',
  },
  {
    id: 'protocol_inspection_stats',
    name: 'Protocol Inspection',
    description: 'Inspection hit counts by name',
    category: 'tmctl',
  },
  {
    id: 'dns_cache_resolver_stat',
    name: 'DNS Cache',
    description: 'DNS cache resolver hits/misses',
    category: 'tmctl',
  },
  {
    id: 'dos_stat',
    name: 'DoS Stats',
    description: 'DoS attack counts and drops',
    category: 'tmctl',
  },
];

const BDT_COMMANDS: CommandCard[] = [
  {
    id: 'arp',
    name: 'ARP Table',
    description: 'Layer 2 ARP entries',
    category: 'bdt_cli',
  },
  {
    id: 'route',
    name: 'Route Table',
    description: 'IP routing table entries',
    category: 'bdt_cli',
  },
  {
    id: 'connection list',
    name: 'Connection List',
    description: 'Active connections',
    category: 'bdt_cli',
  },
  {
    id: 'l2forward',
    name: 'L2 Forwarding',
    description: 'Layer 2 forwarding entries',
    category: 'bdt_cli',
  },
  {
    id: 'check',
    name: 'TMM Health Check',
    description: 'gRPC connection check to TMM',
    category: 'bdt_cli',
  },
];

/** Human labels for the category selector */
const CATEGORY_LABELS: Record<CommandCategory, string> = {
  tmctl: 'Traffic Statistics (tmctl)',
  bdt_cli: 'Networking (bdt_cli)',
  configview: 'Configuration (configview)',
  netkvest: 'Connectivity (netkvest)',
  raw: 'Advanced — Raw Command',
};

/** Map tmctl card IDs to their query parameters */
const TMCTL_PARAMS: Record<string, { table: string; columns?: string[]; width?: number; directory?: string }> = {
  virtual_server_stat: {
    table: 'virtual_server_stat',
    columns: ['name', 'clientside.bytes_in', 'clientside.bytes_out', 'serverside.bytes_in', 'serverside.bytes_out'],
    width: 200,
  },
  pool_member_stat: {
    table: 'pool_member_stat',
    columns: ['pool_name', 'serverside.tot_conns'],
  },
  tmm_stat: {
    table: 'tmm_stat',
    columns: ['cpu_usage_5secs', 'memory_total', 'memory_used', 'client_side_traffic.cur_conns', 'client_side_traffic.tot_conns'],
  },
  fw_rule_stat: {
    table: 'fw_rule_stat',
    width: 200,
  },
  rule_stat: {
    table: 'rule_stat',
    width: 999,
  },
  rst_cause_stat: {
    table: 'rst_cause_stat',
  },
  doca_flow_entries: {
    table: 'doca_flow_entries',
    directory: '/var/tmstat/blade/',
    width: 150,
  },
  doca_flow_fwd: {
    table: 'doca_flow_fwd',
  },
  'tmm/flow_redir_stats': {
    table: 'tmm/flow_redir_stats',
  },
  profile_udp_statexit: {
    table: 'profile_udp_statexit',
    width: 400,
  },
  protocol_inspection_stats: {
    table: 'protocol_inspection_stats',
    columns: ['insp_name', 'hit_count', 'last_hit_time'],
  },
  dns_cache_resolver_stat: {
    table: 'dns_cache_resolver_stat',
    directory: '/var/tmstat/blade',
    columns: ['name', 'queries', 'msg.hits', 'msg.misses'],
  },
  dos_stat: {
    table: 'dos_stat',
    directory: '/var/tmstat/blade',
    columns: ['vector_name', 'attack_count', 'stats', 'drops', 'stats_1', 'status'],
  },
};


// ---------------------------------------------------------------------------
// Icon helper
// ---------------------------------------------------------------------------

function getCardIcon(card: CommandCard) {
  switch (card.id) {
    case 'virtual_server_stat': return <Server className="w-4 h-4" />;
    case 'pool_member_stat': return <Activity className="w-4 h-4" />;
    case 'tmm_stat': return <BarChart3 className="w-4 h-4" />;
    case 'fw_rule_stat': return <Shield className="w-4 h-4" />;
    case 'rule_stat': return <Zap className="w-4 h-4" />;
    case 'rst_cause_stat': return <ArrowDownUp className="w-4 h-4" />;
    case 'doca_flow_entries': return <Cpu className="w-4 h-4" />;
    case 'doca_flow_fwd': return <Cpu className="w-4 h-4" />;
    case 'tmm/flow_redir_stats': return <ArrowDownUp className="w-4 h-4" />;
    case 'profile_udp_statexit': return <MonitorDot className="w-4 h-4" />;
    case 'protocol_inspection_stats': return <Radar className="w-4 h-4" />;
    case 'dns_cache_resolver_stat': return <Globe className="w-4 h-4" />;
    case 'dos_stat': return <Shield className="w-4 h-4" />;
    case 'arp': return <Network className="w-4 h-4" />;
    case 'route': return <Globe className="w-4 h-4" />;
    case 'connection list': return <Activity className="w-4 h-4" />;
    case 'l2forward': return <Network className="w-4 h-4" />;
    case 'check': return <Wifi className="w-4 h-4" />;
    case 'configview': return <Settings className="w-4 h-4" />;
    case 'netkvest': return <Radar className="w-4 h-4" />;
    default: return <Terminal className="w-4 h-4" />;
  }
}


// ---------------------------------------------------------------------------
// Output Type
// ---------------------------------------------------------------------------

type OutputResult =
  | { type: 'table'; data: TMMDebugTmctlResponse }
  | { type: 'raw'; data: TMMDebugExecResponse }
  | { type: 'uuids'; data: TMMDebugConfigviewUuidsResponse }
  | null;


// ---------------------------------------------------------------------------
// Sub-Components
// ---------------------------------------------------------------------------

/** Pod selector dropdown */
function PodSelector({
  pods,
  selectedPod,
  onSelect,
  isLoading,
  onRefresh,
}: {
  pods: TMMDebugPod[];
  selectedPod: TMMDebugPod | null;
  onSelect: (pod: TMMDebugPod) => void;
  isLoading: boolean;
  onRefresh: () => void;
}) {
  const [open, setOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Close on outside click
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  return (
    <div className="flex items-center gap-2">
      <span className="text-sm font-medium text-foreground/80">
        TMM Pod:
      </span>
      <div className="relative" ref={dropdownRef}>
        <button
          onClick={() => setOpen(!open)}
          className="flex items-center gap-2 px-3 py-1.5 rounded-md border border-border bg-card text-sm text-foreground min-w-[240px] justify-between hover:border-border/70"
          disabled={isLoading || pods.length === 0}
        >
          {isLoading ? (
            <span className="flex items-center gap-2">
              <Loader2 className="w-3 h-3 animate-spin" /> Loading...
            </span>
          ) : selectedPod ? (
            <span className="flex items-center gap-2">
              <span className={cn(
                'w-2 h-2 rounded-full',
                selectedPod.has_debug ? 'bg-success' : 'bg-warning',
              )} />
              {selectedPod.name}
              <span className="text-xs text-muted-foreground">
                ({selectedPod.namespace})
              </span>
            </span>
          ) : (
            <span className="text-muted-foreground">
              No TMM pods found
            </span>
          )}
          <ChevronDown className="w-3.5 h-3.5 flex-shrink-0" />
        </button>

        {open && pods.length > 0 && (
          <div className="absolute z-50 mt-1 w-full rounded-md border border-border bg-card shadow-lg">
            {pods.map((pod) => (
              <button
                key={pod.name}
                onClick={() => { onSelect(pod); setOpen(false); }}
                className={cn(
                  'w-full px-3 py-2 text-left text-sm flex items-center gap-2 hover:bg-muted/50',
                  selectedPod?.name === pod.name && 'bg-muted/50',
                )}
              >
                <span className={cn(
                  'w-2 h-2 rounded-full flex-shrink-0',
                  pod.has_debug ? 'bg-success' : 'bg-warning',
                )} />
                <span>{pod.name}</span>
                <span className="text-xs ml-auto text-muted-foreground">
                  {pod.namespace}
                </span>
                {!pod.has_debug && (
                  <Badge variant="warning" className="text-[10px] ml-1">
                    No debug
                  </Badge>
                )}
              </button>
            ))}
          </div>
        )}
      </div>

      <Button
        variant="ghost"
        size="icon"
        className="h-8 w-8"
        onClick={onRefresh}
        disabled={isLoading}
      >
        <RefreshCw className={cn('w-3.5 h-3.5', isLoading && 'animate-spin')} />
      </Button>

      {selectedPod && (
        <Badge variant={selectedPod.has_debug ? 'success' : 'warning'} className="text-xs">
          {selectedPod.has_debug ? 'Debug sidecar available' : 'Debug sidecar missing'}
        </Badge>
      )}
    </div>
  );
}


/** Rendered table output from tmctl */
function TmctlTable({ data }: { data: TMMDebugTmctlResponse }) {
  if (!data.columns.length || !data.rows.length) {
    return (
      <div className="text-sm py-4 text-center text-muted-foreground">
        No data returned
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs font-mono">
        <thead>
          <tr className="border-b border-border">
            {data.columns.map((col, i) => (
              <th
                key={i}
                className="px-3 py-2 text-left font-semibold whitespace-nowrap text-foreground/80"
              >
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.rows.map((row, ri) => (
            <tr
              key={ri}
              className="border-b border-border hover:bg-muted/50"
            >
              {row.map((cell, ci) => (
                <td
                  key={ci}
                  className="px-3 py-1.5 whitespace-nowrap text-foreground/80"
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}


/** Raw output display with copy button */
function RawOutput({
  output,
  stderr,
  exitCode,
  durationMs,
  command,
}: {
  output: string;
  stderr: string;
  exitCode: number;
  durationMs: number;
  command: string;
}) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(output || stderr);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [output, stderr]);

  return (
    <div>
      {/* Command header */}
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-border text-xs text-muted-foreground">
        <span className="font-mono">$ {command}</span>
        <span className="flex items-center gap-3">
          <span className="flex items-center gap-1">
            <Clock className="w-3 h-3" />
            {durationMs}ms
          </span>
          <span className={cn(
            'flex items-center gap-1',
            exitCode === 0 ? 'text-success' : 'text-destructive',
          )}>
            exit {exitCode}
          </span>
          <button
            onClick={handleCopy}
            className={cn(
              'flex items-center gap-1 hover:text-foreground transition-colors',
              copied && 'text-success',
            )}
          >
            {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
            {copied ? 'Copied' : 'Copy'}
          </button>
        </span>
      </div>

      {/* stderr (if any) */}
      {stderr && (
        <div className="px-3 py-2 bg-destructive/10 border-b border-destructive/20">
          <pre className="text-xs font-mono text-destructive whitespace-pre-wrap">{stderr}</pre>
        </div>
      )}

      {/* stdout */}
      <div className="px-3 py-2 overflow-x-auto max-h-[500px] overflow-y-auto">
        <pre className="text-xs font-mono whitespace-pre text-foreground/80">
          {output || (exitCode === 0 ? '(no output)' : '(command failed — check stderr above)')}
        </pre>
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

export function TMMDebugPanel({ clusterId }: { clusterId: number }) {
  // State
  const [selectedPod, setSelectedPod] = useState<TMMDebugPod | null>(null);
  const [output, setOutput] = useState<OutputResult>(null);
  const [activeCardId, setActiveCardId] = useState<string | null>(null);
  const [rawCommand, setRawCommand] = useState('');
  const [configviewUuids, setConfigviewUuids] = useState<string[]>([]);
  const [configviewUuid, setConfigviewUuid] = useState('');
  const [showConfigviewUuids, setShowConfigviewUuids] = useState(false);

  // Netkvest state
  const [netkvestSnatPool, setNetkvestSnatPool] = useState('');
  const [netkvestDestination, setNetkvestDestination] = useState('');
  const [netkvestUtility, setNetkvestUtility] = useState<'ping' | 'traceroute'>('ping');

  // Compact action-bar selection
  const [category, setCategory] = useState<CommandCategory>('tmctl');
  const [selectedCommandId, setSelectedCommandId] = useState<string>(TMCTL_COMMANDS[0].id);
  const outputRef = useRef<HTMLDivElement>(null);

  // Hooks
  const podsQuery = useTMMDebugPods(clusterId);
  const execMutation = useTMMDebugExec(clusterId);
  const tmctlMutation = useTMMDebugTmctl(clusterId);
  const bdtMutation = useTMMDebugBdt(clusterId);
  const configviewMutation = useTMMDebugConfigview(clusterId);
  const configviewUuidsMutation = useTMMDebugConfigviewUuids(clusterId);

  // Auto-select first pod with debug sidecar
  useEffect(() => {
    if (podsQuery.data?.pods && !selectedPod) {
      const debugPod = podsQuery.data.pods.find((p) => p.has_debug);
      if (debugPod) setSelectedPod(debugPod);
      else if (podsQuery.data.pods.length > 0) setSelectedPod(podsQuery.data.pods[0]);
    }
  }, [podsQuery.data, selectedPod]);

  const isAnyLoading = execMutation.isPending || tmctlMutation.isPending
    || bdtMutation.isPending || configviewMutation.isPending || configviewUuidsMutation.isPending;

  // Handlers
  const handleTmctlCard = useCallback((cardId: string) => {
    if (!selectedPod?.has_debug) return;
    const params = TMCTL_PARAMS[cardId];
    if (!params) return;

    setActiveCardId(cardId);
    setShowConfigviewUuids(false);
    tmctlMutation.mutate(
      {
        pod_name: selectedPod.name,
        namespace: selectedPod.namespace,
        table: params.table,
        columns: params.columns,
        width: params.width ?? 200,
        directory: params.directory ?? 'blade',
      },
      {
        onSuccess: (data) => {
          setOutput({ type: 'table', data });
          setActiveCardId(null);
        },
        onSettled: () => setActiveCardId(null),
      },
    );
  }, [selectedPod, tmctlMutation]);

  const handleBdtCard = useCallback((subcommand: string) => {
    if (!selectedPod?.has_debug) return;

    setActiveCardId(subcommand);
    setShowConfigviewUuids(false);
    bdtMutation.mutate(
      {
        pod_name: selectedPod.name,
        namespace: selectedPod.namespace,
        subcommand,
      },
      {
        onSuccess: (data) => {
          setOutput({ type: 'raw', data });
          setActiveCardId(null);
        },
        onSettled: () => setActiveCardId(null),
      },
    );
  }, [selectedPod, bdtMutation]);

  const handleConfigviewUuids = useCallback(() => {
    if (!selectedPod?.has_debug) return;

    setActiveCardId('configview');
    configviewUuidsMutation.mutate(
      {
        pod_name: selectedPod.name,
        namespace: selectedPod.namespace,
      },
      {
        onSuccess: (data) => {
          setConfigviewUuids(data.uuids);
          setShowConfigviewUuids(true);
          setOutput({ type: 'uuids', data });
          setActiveCardId(null);
        },
        onSettled: () => setActiveCardId(null),
      },
    );
  }, [selectedPod, configviewUuidsMutation]);

  const handleConfigviewUuid = useCallback((uuid: string) => {
    if (!selectedPod?.has_debug || !uuid) return;

    setActiveCardId('configview-uuid');
    configviewMutation.mutate(
      {
        pod_name: selectedPod.name,
        namespace: selectedPod.namespace,
        uuid,
      },
      {
        onSuccess: (data) => {
          setOutput({ type: 'raw', data });
          setActiveCardId(null);
        },
        onSettled: () => setActiveCardId(null),
      },
    );
  }, [selectedPod, configviewMutation]);

  const handleRawExec = useCallback(() => {
    if (!selectedPod?.has_debug || !rawCommand.trim()) return;

    setActiveCardId('raw');
    setShowConfigviewUuids(false);
    execMutation.mutate(
      {
        pod_name: selectedPod.name,
        namespace: selectedPod.namespace,
        command: rawCommand.trim(),
      },
      {
        onSuccess: (data) => {
          setOutput({ type: 'raw', data });
          setActiveCardId(null);
        },
        onSettled: () => setActiveCardId(null),
      },
    );
  }, [selectedPod, rawCommand, execMutation]);

  const handleNetkvest = useCallback(() => {
    if (!selectedPod?.has_debug || !netkvestSnatPool.trim() || !netkvestDestination.trim()) return;

    setActiveCardId('netkvest');
    setShowConfigviewUuids(false);
    execMutation.mutate(
      {
        pod_name: selectedPod.name,
        namespace: selectedPod.namespace,
        command: `netkvest -s ${netkvestSnatPool.trim()} -d ${netkvestDestination.trim()} -u ${netkvestUtility}`,
        timeout: 60, // netkvest can take longer due to ping/traceroute
      },
      {
        onSuccess: (data) => {
          setOutput({ type: 'raw', data });
          setActiveCardId(null);
        },
        onSettled: () => setActiveCardId(null),
      },
    );
  }, [selectedPod, netkvestSnatPool, netkvestDestination, netkvestUtility, execMutation]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleRawExec();
    }
  }, [handleRawExec]);

  // Reset the command picker when switching categories
  const handleCategoryChange = useCallback((value: CommandCategory) => {
    setCategory(value);
    if (value === 'tmctl') setSelectedCommandId(TMCTL_COMMANDS[0].id);
    else if (value === 'bdt_cli') setSelectedCommandId(BDT_COMMANDS[0].id);
  }, []);

  // Single Run entry point — dispatches based on the selected category
  const handleRun = useCallback(() => {
    if (!selectedPod?.has_debug) return;
    switch (category) {
      case 'tmctl': handleTmctlCard(selectedCommandId); break;
      case 'bdt_cli': handleBdtCard(selectedCommandId); break;
      case 'configview': handleConfigviewUuids(); break;
      case 'netkvest': handleNetkvest(); break;
      case 'raw': handleRawExec(); break;
    }
  }, [category, selectedCommandId, selectedPod, handleTmctlCard, handleBdtCard, handleConfigviewUuids, handleNetkvest, handleRawExec]);

  // Bring fresh output into view so it's never lost below the fold
  useEffect(() => {
    if (output && outputRef.current) {
      outputRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, [output]);

  // Description shown under the command dropdown
  const selectedCommand: CommandCard | undefined =
    category === 'tmctl'
      ? TMCTL_COMMANDS.find((c) => c.id === selectedCommandId)
      : category === 'bdt_cli'
        ? BDT_COMMANDS.find((c) => c.id === selectedCommandId)
        : undefined;


  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  // No TMM pods found
  if (podsQuery.data && podsQuery.data.pods.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-card p-8 text-center">
        <Server className="w-12 h-12 mx-auto mb-4 text-muted-foreground" />
        <h3 className="text-lg font-medium mb-2">No TMM Pods Found</h3>
        <p className="text-sm text-muted-foreground">
          No f5-tmm pods were found in this cluster. TMM debug commands require an active BNK deployment.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Pod Selector */}
      <Card className="p-4 bg-card border-border">
        <PodSelector
          pods={podsQuery.data?.pods ?? []}
          selectedPod={selectedPod}
          onSelect={setSelectedPod}
          isLoading={podsQuery.isLoading}
          onRefresh={() => podsQuery.refetch()}
        />
      </Card>

      {/* No debug sidecar warning */}
      {selectedPod && !selectedPod.has_debug && (
        <div className="flex items-center gap-3 rounded-lg border border-warning/30 bg-warning/10 px-4 py-3">
          <AlertTriangle className="w-5 h-5 text-warning flex-shrink-0" />
          <div>
            <p className="text-sm font-medium text-warning">Debug Sidecar Not Available</p>
            <p className="text-xs mt-0.5 text-muted-foreground">
              Pod &quot;{selectedPod.name}&quot; does not have a debug container.
              This may be an older BNK version. Debug commands cannot be executed.
            </p>
          </div>
        </div>
      )}

      {/* Compact action bar — pick a category + command, run, output appears right below */}
      {selectedPod?.has_debug && (
        <Card className="p-4 bg-card border-border space-y-3">
          <div className="flex flex-wrap items-end gap-3">
            {/* Category selector */}
            <div className="flex flex-col gap-1">
              <label className="text-xs font-medium text-muted-foreground">Category</label>
              <Select value={category} onValueChange={(v) => handleCategoryChange(v as CommandCategory)}>
                <SelectTrigger className="w-[230px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(Object.keys(CATEGORY_LABELS) as CommandCategory[]).map((cat) => (
                    <SelectItem key={cat} value={cat}>
                      {CATEGORY_LABELS[cat]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {/* Command selector — tmctl / bdt_cli */}
            {(category === 'tmctl' || category === 'bdt_cli') && (
              <div className="flex flex-col gap-1 min-w-[240px]">
                <label className="text-xs font-medium text-muted-foreground">Command</label>
                <Select value={selectedCommandId} onValueChange={setSelectedCommandId}>
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {(category === 'tmctl' ? TMCTL_COMMANDS : BDT_COMMANDS).map((card) => (
                      <SelectItem key={card.id} value={card.id}>
                        <span className="flex items-center gap-2">
                          {getCardIcon(card)}
                          {card.name}
                        </span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}

            {/* Raw command input */}
            {category === 'raw' && (
              <div className="flex flex-col gap-1 flex-1 min-w-[260px]">
                <label className="text-xs font-medium text-muted-foreground">Command</label>
                <div className="relative">
                  <Terminal className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <Input
                    value={rawCommand}
                    onChange={(e) => setRawCommand(e.target.value)}
                    onKeyDown={handleKeyDown}
                    placeholder="Enter any debug sidecar command... (e.g. tmctl -d blade tmm_stat)"
                    className="pl-10 font-mono text-sm"
                  />
                </div>
              </div>
            )}

            {/* Run button — netkvest has its own button inside its form */}
            {category !== 'netkvest' && (
              <Button
                onClick={handleRun}
                disabled={isAnyLoading || (category === 'raw' && !rawCommand.trim())}
              >
                {isAnyLoading ? (
                  <Loader2 className="w-4 h-4 animate-spin mr-2" />
                ) : (
                  <Play className="w-4 h-4 mr-2" />
                )}
                {category === 'configview' ? 'List UUIDs' : 'Run'}
              </Button>
            )}
          </div>

          {/* Selected command description */}
          {selectedCommand && (
            <p className="text-xs text-muted-foreground">{selectedCommand.description}</p>
          )}

          {/* Netkvest form */}
          {category === 'netkvest' && (
            <div className="space-y-3 pt-1">
              <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                <div>
                  <label className="text-xs font-medium mb-1 block text-muted-foreground">
                    Source SNAT Pool
                  </label>
                  <Input
                    value={netkvestSnatPool}
                    onChange={(e) => setNetkvestSnatPool(e.target.value)}
                    placeholder="e.g. egress-snatpool"
                    className="text-sm"
                  />
                </div>
                <div>
                  <label className="text-xs font-medium mb-1 block text-muted-foreground">
                    Destination IP
                  </label>
                  <Input
                    value={netkvestDestination}
                    onChange={(e) => setNetkvestDestination(e.target.value)}
                    placeholder="e.g. 22.22.22.100"
                    className="text-sm font-mono"
                  />
                </div>
                <div>
                  <label className="text-xs font-medium mb-1 block text-muted-foreground">
                    Utility
                  </label>
                  <div className="flex gap-2">
                    <button
                      onClick={() => setNetkvestUtility('ping')}
                      className={cn(
                        'flex-1 px-3 py-1.5 rounded-md border text-sm transition-colors',
                        netkvestUtility === 'ping'
                          ? 'bg-primary/10 border-primary text-primary'
                          : 'bg-card border-border text-foreground/80 hover:border-border/70',
                      )}
                    >
                      Ping
                    </button>
                    <button
                      onClick={() => setNetkvestUtility('traceroute')}
                      className={cn(
                        'flex-1 px-3 py-1.5 rounded-md border text-sm transition-colors',
                        netkvestUtility === 'traceroute'
                          ? 'bg-primary/10 border-primary text-primary'
                          : 'bg-card border-border text-foreground/80 hover:border-border/70',
                      )}
                    >
                      Traceroute
                    </button>
                  </div>
                </div>
              </div>
              <Button
                onClick={handleNetkvest}
                disabled={!netkvestSnatPool.trim() || !netkvestDestination.trim() || isAnyLoading}
                size="sm"
              >
                {activeCardId === 'netkvest' ? (
                  <Loader2 className="w-4 h-4 animate-spin mr-2" />
                ) : (
                  <Radar className="w-4 h-4 mr-2" />
                )}
                Run Connectivity Check
              </Button>
            </div>
          )}

          {/* ConfigView UUID picker (shown after listing UUIDs) */}
          {category === 'configview' && showConfigviewUuids && configviewUuids.length > 0 && (
            <div className="pt-1">
              <h4 className="text-xs font-semibold uppercase tracking-wider mb-2 text-muted-foreground">
                Select Configuration UUID
              </h4>
              <div className="flex gap-2 mb-3">
                <Input
                  value={configviewUuid}
                  onChange={(e) => setConfigviewUuid(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') handleConfigviewUuid(configviewUuid);
                  }}
                  placeholder="Enter or select a UUID..."
                  className="font-mono text-sm"
                />
                <Button
                  onClick={() => handleConfigviewUuid(configviewUuid)}
                  disabled={!configviewUuid.trim() || isAnyLoading}
                  size="sm"
                >
                  {activeCardId === 'configview-uuid' ? <Loader2 className="w-4 h-4 animate-spin" /> : 'View'}
                </Button>
              </div>
              <div className="flex flex-wrap gap-1.5 max-h-[200px] overflow-y-auto">
                {configviewUuids.map((uuid) => (
                  <button
                    key={uuid}
                    onClick={() => {
                      setConfigviewUuid(uuid);
                      handleConfigviewUuid(uuid);
                    }}
                    className="px-2 py-1 rounded text-xs font-mono border border-border bg-muted/50 text-foreground/80 hover:bg-muted transition-colors"
                  >
                    {uuid}
                  </button>
                ))}
              </div>
            </div>
          )}
        </Card>
      )}

      {/* Output Area */}
      {output && (
        <Card ref={outputRef} className="overflow-hidden bg-card border-border">
          <div className="flex items-center justify-between px-4 py-2 border-b border-border bg-muted/50">
            <span className="text-sm font-medium text-foreground/80">
              Output
              {output.type === 'table' && output.data.rows.length > 0 && (
                <span className="ml-2 text-xs font-normal text-muted-foreground">
                  {output.data.rows.length} row{output.data.rows.length !== 1 ? 's' : ''}
                </span>
              )}
            </span>
            <div className="flex items-center gap-2">
              {output.type === 'table' && output.data.duration_ms > 0 && (
                <span className="text-xs flex items-center gap-1 text-muted-foreground">
                  <Clock className="w-3 h-3" />
                  {output.data.duration_ms}ms
                </span>
              )}
            </div>
          </div>

          {output.type === 'table' ? (
            output.data.exit_code === 0 ? (
              <TmctlTable data={output.data} />
            ) : (
              <RawOutput
                output={output.data.raw}
                stderr={output.data.stderr}
                exitCode={output.data.exit_code}
                durationMs={output.data.duration_ms}
                command={output.data.command}
              />
            )
          ) : output.type === 'raw' ? (
            <RawOutput
              output={output.data.stdout}
              stderr={output.data.stderr}
              exitCode={output.data.exit_code}
              durationMs={output.data.duration_ms}
              command={output.data.command}
            />
          ) : output.type === 'uuids' ? (
            <RawOutput
              output={output.data.raw}
              stderr={output.data.stderr}
              exitCode={output.data.exit_code}
              durationMs={output.data.duration_ms}
              command={output.data.command}
            />
          ) : null}
        </Card>
      )}
    </div>
  );
}
