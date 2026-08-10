/**
 * BenchmarkConfigsTab — D-020: config CRUD, JSON editor, field reference guide.
 * Configs table + field reference are rendered in SectionCards. Form-submit
 * buttons stay primary (solid). Action icons are ghost variant.
 */
import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Skeleton } from '@/components/ui/skeleton';
import { TimeAgo } from '@/components/ui/TimeAgo';
import { SectionCard } from '@/components/ui/section-card';
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Plus,
  Pencil,
  Download,
  Trash2,
  FileJson,
  BookOpen,
  AlertCircle,
  Loader2,
} from 'lucide-react';
import {
  useBenchmarkConfigs,
  useDeleteBenchmarkConfig,
  useCreateBenchmarkConfig,
  useUpdateBenchmarkConfig,
} from '@/hooks/useBenchmarks';
import { ProxyBadge, downloadJson } from './benchmark-utils';

// ============================================================================
// Config Templates
// ============================================================================

/** Default RunConfig template for new configs — matches ai-perf RunConfig schema */
const DEFAULT_RUN_CONFIG = {
  url: 'http://your-llm-endpoint:8000',
  model: 'your-model-name',
  endpoint_type: 'chat',
  endpoint: '/v1/chat/completions',
  streaming: true,
  request_count: 100,
  concurrency: 10,
  warmup_request_count: 5,
  request_timeout_seconds: 600,
  isl: 550,
  osl: 150,
  _proxy_type: 'nodeport',
  _target_name: '',
};

/** burst_driver.py config template — the standalone llm-bench tool */
const BURST_DRIVER_CONFIG = {
  phases: [
    { name: 'warm-up', num_requests: 200, concurrency: 10000, request_rate: 5.0, prompt_pool: 'short' },
    { name: 'burst-heavy', num_requests: 4500, concurrency: 10000, request_rate: 0, prompt_pool: 'xl' },
    { name: 'cool-down', num_requests: 100, concurrency: 10000, request_rate: 0.5, prompt_pool: 'tiny' },
    { name: 'burst-mixed', num_requests: 5000, concurrency: 10000, request_rate: 0, prompt_pool: 'all', category_quotas: { xl: 50, large: 100 } },
    { name: 'valley', num_requests: 150, concurrency: 10000, request_rate: 1.0, prompt_pool: 'short' },
    { name: 'burst-large', num_requests: 4000, concurrency: 10000, request_rate: 0, prompt_pool: 'large' },
    { name: 'quiet', num_requests: 80, concurrency: 10000, request_rate: 0.5, prompt_pool: 'tiny' },
    { name: 'burst-max', num_requests: 6000, concurrency: 10000, request_rate: 0, prompt_pool: 'all' },
    { name: 'recovery', num_requests: 200, concurrency: 10000, request_rate: 2.0, prompt_pool: 'medium' },
    { name: 'spike', num_requests: 4500, concurrency: 10000, request_rate: 0, prompt_pool: 'large' },
    { name: 'final-cooldown', num_requests: 100, concurrency: 10000, request_rate: 0.5, prompt_pool: 'tiny' },
  ],
};

// ============================================================================
// ConfigDialog
// ============================================================================

function ConfigDialog({
  mode,
  config,
  open,
  onOpenChange,
}: {
  mode: 'create' | 'edit';
  config?: { id: number; name: string; description: string | null; tool: string; config_json: Record<string, unknown> };
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const createConfig = useCreateBenchmarkConfig();
  const updateConfig = useUpdateBenchmarkConfig();

  const [name, setName] = useState(config?.name || '');
  const [description, setDescription] = useState(config?.description || '');
  const [tool, setTool] = useState(config?.tool || 'aiperf');
  const [jsonText, setJsonText] = useState(
    config?.config_json
      ? JSON.stringify(config.config_json, null, 2)
      : JSON.stringify(DEFAULT_RUN_CONFIG, null, 2)
  );
  const [jsonError, setJsonError] = useState<string | null>(null);

  // Reset form when dialog opens with new data
  const resetKey = config?.id ?? 'new';
  const [lastResetKey, setLastResetKey] = useState(resetKey);
  if (resetKey !== lastResetKey) {
    setLastResetKey(resetKey);
    setName(config?.name || '');
    setDescription(config?.description || '');
    setTool(config?.tool || 'aiperf');
    setJsonText(
      config?.config_json
        ? JSON.stringify(config.config_json, null, 2)
        : JSON.stringify(DEFAULT_RUN_CONFIG, null, 2)
    );
    setJsonError(null);
  }

  const handleToolChange = (newTool: string) => {
    setTool(newTool);
    try {
      const current = JSON.parse(jsonText);
      const isDefaultAiperf = JSON.stringify(current) === JSON.stringify(DEFAULT_RUN_CONFIG);
      const isDefaultBurst = JSON.stringify(current) === JSON.stringify(BURST_DRIVER_CONFIG);
      if (isDefaultAiperf || isDefaultBurst || (mode === 'create' && !config)) {
        setJsonText(JSON.stringify(
          newTool === 'llm-bench' ? BURST_DRIVER_CONFIG : DEFAULT_RUN_CONFIG,
          null, 2
        ));
      }
    } catch {
      // Keep current JSON if it can't be parsed
    }
  };

  const handleSave = () => {
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(jsonText);
      setJsonError(null);
    } catch (e) {
      setJsonError(String(e));
      return;
    }

    if (!name.trim()) return;

    if (mode === 'create') {
      createConfig.mutate(
        { name: name.trim(), description: description.trim() || undefined, tool, config_json: parsed },
        {
          onSuccess: () => {
            onOpenChange(false);
            setName('');
            setDescription('');
            setTool('aiperf');
            setJsonText(JSON.stringify(DEFAULT_RUN_CONFIG, null, 2));
          },
        }
      );
    } else if (config) {
      updateConfig.mutate(
        { configId: config.id, data: { name: name.trim(), description: description.trim() || undefined, tool, config_json: parsed } },
        { onSuccess: () => onOpenChange(false) }
      );
    }
  };

  const isPending = createConfig.isPending || updateConfig.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{mode === 'create' ? 'Create Config' : 'Edit Config'}</DialogTitle>
          <DialogDescription>
            {mode === 'create'
              ? 'Save a RunConfig preset that can be pulled by agents or used to trigger runs.'
              : 'Update the saved RunConfig preset.'}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="config-name">Name</Label>
              <Input id="config-name" value={name} onChange={e => setName(e.target.value)} placeholder="e.g. envoy-stress-test" />
            </div>
            <div className="space-y-2">
              <Label htmlFor="config-tool">Tool</Label>
              <Select value={tool} onValueChange={handleToolChange}>
                <SelectTrigger id="config-tool"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="aiperf">aiperf</SelectItem>
                  <SelectItem value="llm-bench">llm-bench (burst_driver)</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="config-desc">Description</Label>
            <Input id="config-desc" value={description} onChange={e => setDescription(e.target.value)} placeholder="Optional description" />
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label htmlFor="config-json">Config JSON</Label>
              <span className="text-xs text-muted-foreground">
                {tool === 'aiperf' ? 'RunConfig schema (base_url, model, proxy, phases, ...)' : 'burst_config.json (phases array)'}
              </span>
            </div>
            <Textarea
              id="config-json"
              value={jsonText}
              onChange={e => { setJsonText(e.target.value); setJsonError(null); }}
              className="font-mono text-xs min-h-[300px] leading-relaxed"
              placeholder="Paste RunConfig JSON here..."
            />
            {jsonError && (
              <p className="text-xs text-destructive flex items-center gap-1">
                <AlertCircle className="h-3 w-3" /> Invalid JSON: {jsonError}
              </p>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={handleSave} disabled={!name.trim() || isPending}>
            {isPending ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : null}
            {mode === 'create' ? 'Create' : 'Save'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ============================================================================
// Main Component
// ============================================================================

export function BenchmarkConfigsTab() {
  const { data: configs, isLoading } = useBenchmarkConfigs();
  const deleteConfig = useDeleteBenchmarkConfig();
  const [createOpen, setCreateOpen] = useState(false);
  const [editConfig, setEditConfig] = useState<{ id: number; name: string; description: string | null; tool: string; config_json: Record<string, unknown> } | null>(null);

  if (isLoading) return <Skeleton className="h-48 w-full" />;

  return (
    <div className="space-y-6">
      {/* Header with create button */}
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          Saved RunConfig presets. Use the JSON to build aiperf CLI commands.
        </p>
        <Button size="sm" onClick={() => setCreateOpen(true)} className="gap-1.5">
          <Plus className="h-3.5 w-3.5" />
          New Config
        </Button>
      </div>

      {!configs || configs.length === 0 ? (
        <SectionCard>
          <div className="text-center py-8">
            <FileJson className="h-12 w-12 mx-auto text-muted-foreground mb-3" />
            <p className="text-foreground">No saved configurations</p>
            <p className="text-xs text-muted-foreground mt-1">Create a config preset, then use the saved JSON to build your aiperf profile command.</p>
          </div>
        </SectionCard>
      ) : (
        <SectionCard
          title={`${configs.length} ${configs.length === 1 ? 'config' : 'configs'}`}
          compact
        >
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Tool</TableHead>
                  <TableHead>Proxy</TableHead>
                  <TableHead>Model</TableHead>
                  <TableHead>Base URL</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {configs.map(config => {
                  const cj = config.config_json as Record<string, unknown>;
                  return (
                    <TableRow key={config.id}>
                      <TableCell>
                        <div>
                          <span className="font-medium text-foreground">{config.name}</span>
                          {config.description && <p className="text-xs text-muted-foreground truncate max-w-[200px]">{config.description}</p>}
                        </div>
                      </TableCell>
                      <TableCell><Badge variant="outline" className="text-xs">{config.tool}</Badge></TableCell>
                      <TableCell>{cj?.proxy ? <ProxyBadge proxy={String(cj.proxy)} /> : '—'}</TableCell>
                      <TableCell className="text-sm max-w-[200px] truncate">{String(cj?.model || '—')}</TableCell>
                      <TableCell className="text-xs font-mono max-w-[200px] truncate">{String(cj?.base_url || '—')}</TableCell>
                      <TableCell className="text-sm text-muted-foreground"><TimeAgo dateStr={config.created_at} /></TableCell>
                      <TableCell className="text-right">
                        <div className="flex items-center justify-end gap-1">
                          <Button size="icon" variant="ghost" className="h-7 w-7" onClick={() => setEditConfig(config)} title="Edit">
                            <Pencil className="h-3.5 w-3.5" />
                          </Button>
                          <Button size="icon" variant="ghost" className="h-7 w-7" onClick={() => downloadJson(config.config_json as object, `${config.name}.json`)} title="Download JSON">
                            <Download className="h-3.5 w-3.5" />
                          </Button>
                          <Button size="icon" variant="ghost" className="h-7 w-7 text-destructive" onClick={() => deleteConfig.mutate(config.id)} title="Delete">
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        </SectionCard>
      )}

      {/* Field Reference Guide */}
      <SectionCard>
        <h4 className="text-sm font-semibold mb-3 flex items-center gap-2 text-foreground">
          <BookOpen className="h-4 w-4 text-info" />
          Config Field Reference — aiperf CLI Mapping
        </h4>
        <p className="text-xs text-muted-foreground mb-3">
          Config keys map directly to <code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">aiperf profile</code> CLI flags.
          See <a href="https://github.com/ai-dynamo/aiperf" target="_blank" rel="noopener noreferrer" className="text-primary hover:underline">ai-dynamo/aiperf</a> docs for full reference.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-2 text-xs">
          <div>
            <p className="text-muted-foreground"><code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">url</code> <span className="text-muted-foreground/70">(--url)</span> — LLM or proxy endpoint URL</p>
          </div>
          <div>
            <p className="text-muted-foreground"><code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">model</code> <span className="text-muted-foreground/70">(--model)</span> — Model name (must match server)</p>
          </div>
          <div>
            <p className="text-muted-foreground"><code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">endpoint_type</code> <span className="text-muted-foreground/70">(--endpoint-type)</span> — API type: <code className="px-1 bg-muted/60 border border-border rounded text-success">chat</code> · <code className="px-1 bg-muted/60 border border-border rounded text-success">completions</code> · <code className="px-1 bg-muted/60 border border-border rounded text-success">embeddings</code></p>
          </div>
          <div>
            <p className="text-muted-foreground"><code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">endpoint</code> <span className="text-muted-foreground/70">(--endpoint)</span> — Custom API path (e.g. <code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">/v1/chat/completions</code>)</p>
          </div>
          <div>
            <p className="text-muted-foreground"><code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">streaming</code> <span className="text-muted-foreground/70">(--streaming)</span> — Enable streaming (required for TTFT/ITL metrics)</p>
          </div>
          <div>
            <p className="text-muted-foreground"><code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">request_count</code> <span className="text-muted-foreground/70">(--request-count)</span> — Total number of requests to send</p>
          </div>
          <div>
            <p className="text-muted-foreground"><code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">concurrency</code> <span className="text-muted-foreground/70">(--concurrency)</span> — Concurrent in-flight requests</p>
          </div>
          <div>
            <p className="text-muted-foreground"><code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">request_timeout_seconds</code> <span className="text-muted-foreground/70">(--request-timeout-seconds)</span> — HTTP timeout per request</p>
          </div>
          <div>
            <p className="text-muted-foreground"><code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">isl</code> <span className="text-muted-foreground/70">(--isl)</span> — Input sequence length mean (tokens, default: 550)</p>
          </div>
          <div>
            <p className="text-muted-foreground"><code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">osl</code> <span className="text-muted-foreground/70">(--osl)</span> — Output sequence length mean (tokens, default: 150)</p>
          </div>
          <div>
            <p className="text-muted-foreground"><code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">warmup_request_count</code> <span className="text-muted-foreground/70">(--warmup-request-count)</span> — Warmup requests before benchmark</p>
          </div>
          <div>
            <p className="text-muted-foreground"><code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">request_rate</code> <span className="text-muted-foreground/70">(--request-rate)</span> — Target requests/sec (optional, alternative to concurrency)</p>
          </div>
          <div>
            <p className="text-muted-foreground"><code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">tokenizer</code> <span className="text-muted-foreground/70">(--tokenizer)</span> — HuggingFace id or <code className="px-1 bg-muted/60 border border-border rounded text-info">builtin</code> (default, tiktoken, no download). Set to a real HF id for accurate token metrics.</p>
          </div>
        </div>
        <div className="mt-3 pt-3 border-t border-border">
          <p className="text-xs text-muted-foreground">
            <span className="font-medium text-foreground/80">Keys prefixed with <code className="px-1 bg-muted/60 border border-border rounded text-warning">_</code></span> (e.g. <code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">_proxy_type</code>, <code className="px-1 bg-muted/60 border border-border rounded text-foreground/80">_target_name</code>) are Forge metadata — not passed to the aiperf CLI.
          </p>
        </div>
      </SectionCard>

      {/* Create Dialog */}
      <ConfigDialog mode="create" open={createOpen} onOpenChange={setCreateOpen} />

      {/* Edit Dialog */}
      {editConfig && (
        <ConfigDialog
          mode="edit"
          config={editConfig}
          open={!!editConfig}
          onOpenChange={(open) => { if (!open) setEditConfig(null); }}
        />
      )}
    </div>
  );
}
