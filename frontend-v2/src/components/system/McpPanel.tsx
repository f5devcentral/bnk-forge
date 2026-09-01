/**
 * MCP Server panel — tab body for System Administration.
 *
 * KPI strip for status, side-by-side panels (setup guides + tool catalog).
 * Status conveyed via Badge variants only; code blocks use muted surface tokens.
 */

import { useState, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { queryKeys } from '@/lib/queryKeys';
import { systemApi } from '@/lib/api/system';
import type { MCPStatusResponse, MCPToolCategory } from '@/lib/api/system';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { SectionCard } from '@/components/ui/section-card';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import {
  Activity,
  Bot,
  Box,
  Check,
  ChevronDown,
  ChevronRight,
  Clipboard,
  Code2,
  FolderGit2,
  Globe,
  Loader2,
  Package,
  Search,
  Server,
  Settings,
  Shield,
  Terminal,
  Wrench,
  Zap,
} from 'lucide-react';

const ICON_MAP: Record<string, React.ElementType> = {
  Server,
  Box,
  Shield,
  Globe,
  Package,
  Settings,
  FolderGit2,
};

const STALE_TIME = 30_000;

// ──────────────────────────────────────────────────────────────────────────────
// Status pill — token-pure
// ──────────────────────────────────────────────────────────────────────────────

function MCPStatusBadge({ status }: { status: string }) {
  const variants: Record<string, { variant: 'success' | 'warning' | 'muted'; label: string }> = {
    healthy: { variant: 'success', label: 'Healthy' },
    degraded: { variant: 'warning', label: 'Degraded' },
    offline: { variant: 'muted', label: 'Offline' },
  };
  const v = variants[status] ?? variants.offline;
  return <Badge variant={v.variant}>{v.label}</Badge>;
}

// ──────────────────────────────────────────────────────────────────────────────
// Copy button
// ──────────────────────────────────────────────────────────────────────────────

function CopyButton({ text, className }: { text: string; className?: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className={cn('h-7 w-7', className)}
            onClick={handleCopy}
          >
            {copied ? (
              <Check className="h-3.5 w-3.5 text-success" />
            ) : (
              <Clipboard className="h-3.5 w-3.5" />
            )}
          </Button>
        </TooltipTrigger>
        <TooltipContent>{copied ? 'Copied' : 'Copy to clipboard'}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Code block — muted surface, no raw zinc-950
// ──────────────────────────────────────────────────────────────────────────────

function CodeBlock({ code }: { code: string }) {
  return (
    <div className="relative group">
      <pre className="bg-muted/60 text-foreground/90 border border-border rounded-md p-4 pr-12 text-xs overflow-x-auto font-mono leading-relaxed">
        <code>{code}</code>
      </pre>
      <CopyButton
        text={code}
        className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity"
      />
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Status — KPI strip
// ──────────────────────────────────────────────────────────────────────────────

function StatusStrip({
  data,
  isLoading,
}: {
  data: MCPStatusResponse | undefined;
  isLoading: boolean;
}) {
  if (isLoading) {
    return (
      <SectionCard compact>
        <div className="flex items-center justify-center py-8">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      </SectionCard>
    );
  }

  if (!data) return null;

  const tiles = [
    { label: 'Endpoint', value: data.endpoint, mono: true },
    { label: 'Transport', value: data.transport },
    { label: 'Tools available', value: String(data.total_tools) },
    { label: 'Latency', value: data.latency_ms != null ? `${data.latency_ms} ms` : '—' },
  ];

  return (
    <SectionCard compact>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <Bot className="h-5 w-5 text-muted-foreground" />
          <div>
            <div className="text-sm font-semibold text-foreground">MCP Server</div>
            <div className="text-xs text-muted-foreground">
              Model Context Protocol — AI assistant integration
            </div>
          </div>
        </div>
        <MCPStatusBadge status={data.status} />
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {tiles.map((t) => (
          <div key={t.label}>
            <p className="text-xs text-muted-foreground">{t.label}</p>
            <p
              className={cn(
                'text-sm font-medium text-foreground mt-0.5',
                t.mono && 'font-mono',
              )}
            >
              {t.value}
            </p>
          </div>
        ))}
      </div>
      {data.error && (
        <div className="mt-4 rounded-md border border-destructive/20 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {data.error}
        </div>
      )}
    </SectionCard>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Setup guides — collapsible per-IDE config
// ──────────────────────────────────────────────────────────────────────────────

function SetupGuides({ baseUrl }: { baseUrl: string }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const mcpUrl = `${baseUrl}/mcp/`;

  const guides = [
    {
      id: 'claude',
      name: 'Claude Desktop',
      icon: Bot,
      description: 'Add to your Claude Desktop configuration',
      config: JSON.stringify({ mcpServers: { 'bnk-forge': { url: mcpUrl } } }, null, 2),
      instructions:
        'Add to ~/Library/Application Support/Claude/claude_desktop_config.json (macOS) or %APPDATA%\\Claude\\claude_desktop_config.json (Windows)',
    },
    {
      id: 'opencode',
      name: 'OpenCode',
      icon: Terminal,
      description: 'Add to your OpenCode configuration',
      config: JSON.stringify(
        { mcp: { 'bnk-forge': { type: 'remote', url: mcpUrl } } },
        null,
        2,
      ),
      instructions:
        'Add to .opencode/config.json in your project root or ~/.config/opencode/config.json globally',
    },
    {
      id: 'vscode',
      name: 'VS Code Copilot',
      icon: Code2,
      description: 'Add to your VS Code settings',
      config: JSON.stringify(
        { mcp: { servers: { 'bnk-forge': { type: 'http', url: mcpUrl } } } },
        null,
        2,
      ),
      instructions:
        'Add to .vscode/settings.json in your workspace or user settings (Ctrl+Shift+P → Preferences: Open User Settings JSON)',
    },
    {
      id: 'cursor',
      name: 'Cursor',
      icon: Zap,
      description: 'Add to your Cursor MCP configuration',
      config: JSON.stringify({ mcpServers: { 'bnk-forge': { url: mcpUrl } } }, null, 2),
      instructions: "Add to ~/.cursor/mcp.json or your project's .cursor/mcp.json",
    },
  ];

  return (
    <SectionCard title="Quick setup" compact>
      <p className="text-sm text-muted-foreground -mt-1 mb-3">
        Connect your AI assistant to BNK-Forge. Copy the config snippet for your tool.
      </p>
      <div className="space-y-2">
        {guides.map((guide) => {
          const Icon = guide.icon;
          const isOpen = expanded === guide.id;
          return (
            <div key={guide.id} className="border border-border rounded-lg">
              <button
                className="flex items-center gap-3 w-full p-3 text-left hover:bg-muted/40 transition-colors rounded-lg"
                onClick={() => setExpanded(isOpen ? null : guide.id)}
                aria-expanded={isOpen}
              >
                <Icon className="h-4 w-4 text-muted-foreground shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-foreground">{guide.name}</p>
                  <p className="text-xs text-muted-foreground">{guide.description}</p>
                </div>
                {isOpen ? (
                  <ChevronDown className="h-4 w-4 text-muted-foreground shrink-0" />
                ) : (
                  <ChevronRight className="h-4 w-4 text-muted-foreground shrink-0" />
                )}
              </button>
              {isOpen && (
                <div className="px-3 pb-3 space-y-2">
                  <p className="text-xs text-muted-foreground">{guide.instructions}</p>
                  <CodeBlock code={guide.config} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </SectionCard>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Tool catalog — searchable grouped list
// ──────────────────────────────────────────────────────────────────────────────

function ToolCatalog({ catalog }: { catalog: MCPToolCategory[] }) {
  const [search, setSearch] = useState('');
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set());

  const filteredCatalog = useMemo(() => {
    if (!search.trim()) return catalog;
    const q = search.toLowerCase();
    return catalog
      .map((cat) => ({
        ...cat,
        tools: cat.tools.filter(
          (t) =>
            t.name.toLowerCase().includes(q) ||
            t.description.toLowerCase().includes(q),
        ),
      }))
      .filter((cat) => cat.tools.length > 0);
  }, [catalog, search]);

  const totalFiltered = filteredCatalog.reduce((sum, cat) => sum + cat.tools.length, 0);

  const toggleCategory = (category: string) => {
    setExpandedCategories((prev) => {
      const next = new Set(prev);
      if (next.has(category)) next.delete(category);
      else next.add(category);
      return next;
    });
  };

  return (
    <SectionCard title="Tool catalog" compact>
      <p className="text-sm text-muted-foreground -mt-1 mb-3">
        {totalFiltered} tools across {filteredCatalog.length} categories
      </p>
      <div className="relative mb-3">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <Input
          placeholder="Search tools…"
          aria-label="Search tools"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="pl-9 h-9"
        />
      </div>
      <div className="space-y-1">
        {filteredCatalog.map((cat) => {
          const Icon = ICON_MAP[cat.icon] ?? Wrench;
          const isOpen = expandedCategories.has(cat.category);
          return (
            <div key={cat.category}>
              <button
                className="flex items-center gap-2 w-full p-2 text-left hover:bg-muted/40 transition-colors rounded-md"
                onClick={() => toggleCategory(cat.category)}
                aria-expanded={isOpen}
              >
                {isOpen ? (
                  <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
                ) : (
                  <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
                )}
                <Icon className="h-4 w-4 text-muted-foreground" />
                <span className="text-sm font-medium text-foreground flex-1">{cat.category}</span>
                <Badge variant="muted" className="text-xs">{cat.tools.length}</Badge>
              </button>
              {isOpen && (
                <div className="ml-6 mb-2 space-y-1">
                  {cat.tools.map((tool) => (
                    <div key={tool.name} className="flex items-start gap-2 py-1 px-2">
                      <code className="text-xs font-mono text-primary bg-primary/10 px-1.5 py-0.5 rounded shrink-0">
                        {tool.name}
                      </code>
                      <span className="text-xs text-muted-foreground">{tool.description}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
        {filteredCatalog.length === 0 && (
          <div className="text-center py-6 text-muted-foreground text-sm">
            No tools matching “{search}”
          </div>
        )}
      </div>
    </SectionCard>
  );
}

// ──────────────────────────────────────────────────────────────────────────────
// Main panel
// ──────────────────────────────────────────────────────────────────────────────

export function McpPanel() {
  const { data, isLoading } = useQuery<MCPStatusResponse>({
    queryKey: queryKeys.mcp.status(),
    queryFn: systemApi.getMCPStatus,
    staleTime: STALE_TIME,
    refetchInterval: 60_000,
  });

  const baseUrl = `${window.location.protocol}//${window.location.host}`;

  return (
    <div className="space-y-6">
      <StatusStrip data={data} isLoading={isLoading} />

      <div className="grid lg:grid-cols-2 gap-6">
        <SetupGuides baseUrl={baseUrl} />
        {data?.tool_catalog && <ToolCatalog catalog={data.tool_catalog} />}
      </div>

      {/* Activity icon stays referenced through the rest of the page (legend
          for the catalog header — keeps the import alive in case future
          versions re-introduce a section heading icon). */}
      <span className="hidden">
        <Activity />
      </span>
    </div>
  );
}
