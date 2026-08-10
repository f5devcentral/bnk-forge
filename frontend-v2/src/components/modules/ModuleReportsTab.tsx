import { useState } from 'react';
import { AlertCircle, FileText, FileJson, ScrollText, File as FileIcon } from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { useModuleReports, useModuleReportContent } from '@/hooks/useModuleReports';
import type { ModuleReportFile } from '@/lib/api/projects';

interface Props {
  moduleId: number;
}

function kindIcon(kind: string) {
  if (kind === 'md') return FileText;
  if (kind === 'json') return FileJson;
  if (kind === 'log') return ScrollText;
  return FileIcon;
}

function fileLabel(path: string): string {
  const parts = path.split('/');
  return parts[parts.length - 1] || path;
}

/**
 * Minimal, dependency-free markdown renderer. No markdown library is bundled
 * (verified against package.json), and #458 explicitly forbids adding one — so
 * we style headings and preserve everything else as preformatted text. Content
 * is plain text from the workspace; we never dangerouslySetInnerHTML.
 */
function RenderedMarkdown({ content }: { content: string }) {
  const lines = content.split('\n');
  const blocks: React.ReactNode[] = [];
  let buffer: string[] = [];
  let inFence = false;

  const flush = (key: string) => {
    if (buffer.length === 0) return;
    blocks.push(
      <pre
        key={key}
        className="whitespace-pre-wrap break-words font-mono text-xs text-foreground/80"
      >
        {buffer.join('\n')}
      </pre>
    );
    buffer = [];
  };

  lines.forEach((line, i) => {
    if (line.trim().startsWith('```')) {
      inFence = !inFence;
      buffer.push(line);
      return;
    }
    if (!inFence) {
      const h = /^(#{1,3})\s+(.*)$/.exec(line);
      if (h) {
        flush(`pre-${i}`);
        const level = h[1].length;
        const text = h[2];
        blocks.push(
          <div
            key={`h-${i}`}
            className={cn(
              'font-semibold text-foreground',
              level === 1 && 'text-base mt-4 mb-1',
              level === 2 && 'text-sm mt-3 mb-1',
              level === 3 && 'text-sm mt-2 mb-0.5 text-foreground/90'
            )}
          >
            {text}
          </div>
        );
        return;
      }
    }
    buffer.push(line);
  });
  flush('pre-final');

  return <div className="space-y-1">{blocks}</div>;
}

function ReportContent({ moduleId, path }: { moduleId: number; path: string }) {
  const { data, isLoading, isError, error } = useModuleReportContent(moduleId, path);

  if (isLoading) {
    return <div className="h-24 rounded animate-pulse bg-muted" />;
  }
  if (isError) {
    return (
      <div className="p-3 rounded-lg flex items-start gap-2 bg-destructive/10 text-destructive text-sm">
        <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
        {(error as Error)?.message ?? 'Failed to load report file'}
      </div>
    );
  }
  if (!data) return null;

  if (data.kind === 'md') {
    return (
      <div className="p-3 rounded-lg bg-card border border-border max-h-[55vh] overflow-y-auto">
        <RenderedMarkdown content={data.content} />
      </div>
    );
  }

  let text = data.content;
  if (data.kind === 'json') {
    try {
      text = JSON.stringify(JSON.parse(data.content), null, 2);
    } catch {
      text = data.content; // not valid JSON — show raw
    }
  }

  return (
    <pre className="p-3 rounded-lg bg-muted text-xs font-mono whitespace-pre-wrap break-words max-h-[55vh] overflow-y-auto text-foreground/80">
      {text}
    </pre>
  );
}

/**
 * Reports tab (D-034 PR-2.5, #458): surfaces the run/scenario/bench reports the
 * module's ctl tool wrote into its persistent workspace. Lists report runs
 * newest-first; selecting a file renders it (.md as basic markdown, .json
 * pretty-printed, .log/other as preformatted text).
 */
export function ModuleReportsTab({ moduleId }: Props) {
  const { data, isLoading, isError, error } = useModuleReports(moduleId);
  const [selected, setSelected] = useState<string | null>(null);

  if (isLoading) {
    return (
      <div className="p-4 rounded-lg bg-muted/40 space-y-3">
        {[0, 1, 2].map((i) => (
          <div key={i} className="h-10 rounded animate-pulse bg-muted" />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <div className="p-4 rounded-lg flex items-start gap-2 bg-destructive/10 text-destructive">
        <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
        <div className="text-sm">
          Failed to load reports: {(error as Error)?.message ?? 'unknown error'}
        </div>
      </div>
    );
  }

  const runs = data?.runs ?? [];

  if (runs.length === 0) {
    return (
      <div className="p-6 rounded-lg text-center text-sm bg-muted/40 text-muted-foreground">
        <FileText className="h-6 w-6 mx-auto mb-2 opacity-40" />
        No reports yet. When this module&apos;s tool runs an e2e / scenario / bench
        test, its report will appear here.
      </div>
    );
  }

  return (
    <div className="p-4 rounded-lg bg-muted/40 space-y-4">
      <h4 className="text-sm font-semibold">Reports</h4>

      <div className="space-y-3">
        {runs.map((run) => {
          const files = run.files ?? [];
          // Render the selected file's content inline, directly beneath the run
          // that owns it — otherwise a single panel at the end of a long run
          // list appears off-screen and the click reads as a no-op.
          const selectedInRun = files.find((f: ModuleReportFile) => f.path === selected);
          return (
            <div key={run.stamp || '(root)'} className="rounded-lg border border-border bg-card p-3">
              <div className="text-xs font-mono text-muted-foreground mb-2">
                {run.stamp || 'top-level'}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {files.map((file: ModuleReportFile) => {
                  const Icon = kindIcon(file.kind);
                  const isActive = selected === file.path;
                  return (
                    <button
                      key={file.path}
                      type="button"
                      onClick={() => setSelected(isActive ? null : file.path)}
                      className={cn(
                        'inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-mono transition-colors',
                        isActive
                          ? 'border-primary bg-primary/10 text-foreground'
                          : 'border-border bg-muted text-muted-foreground hover:bg-muted/70'
                      )}
                      title={`${file.path} (${file.size} bytes)`}
                    >
                      <Icon className="h-3 w-3 shrink-0" />
                      {fileLabel(file.path)}
                    </button>
                  );
                })}
                {files.length === 0 && (
                  <Badge variant="outline" className="text-[10px]">
                    no files
                  </Badge>
                )}
              </div>
              {selectedInRun && (
                <div className="mt-3">
                  <ReportContent moduleId={moduleId} path={selectedInRun.path} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
