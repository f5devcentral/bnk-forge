/**
 * WafFormToolbar — top toolbar for WAF create forms.
 * Shown only in create mode (not edit). Provides:
 *   • Clone from existing CR (cross-namespace picker)
 *   • Save for Later / Drafts (localStorage)
 *   • Import JSON/YAML (paste or file upload)
 */

import { useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Copy, BookmarkPlus, BookOpen, Upload, X, Trash2, Clock } from 'lucide-react';
import { listDrafts, saveDraft, deleteDraft, relativeTime, type CrdKind, type WafDraft } from '@/lib/waf-drafts';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';

// ── Types ──────────────────────────────────────────────────────────────────

export interface CloneSource {
  name: string;
  namespace: string;
  spec: Record<string, unknown>;
}

interface WafFormToolbarProps {
  kind: CrdKind;
  /** Current form state to save as draft */
  currentState: Record<string, unknown>;
  /** Display name for drafts (usually the crName being typed) */
  currentLabel: string;
  /** Called when user picks a clone source — form should apply the spec */
  onClone: (source: CloneSource) => void;
  /** Called when user picks a draft to restore */
  onRestoreDraft: (draft: WafDraft) => void;
  /** Called when user imports JSON — form should apply it */
  onImport: (raw: Record<string, unknown>) => void;
  /** Existing CRs to offer as clone sources */
  cloneSources: Array<{ name: string; namespace: string; spec?: Record<string, unknown> }>;
}

// ── DraftsPanel ────────────────────────────────────────────────────────────

function DraftsPanel({ kind, currentState, currentLabel, onRestoreDraft, onClose }: {
  kind: CrdKind; currentState: Record<string, unknown>; currentLabel: string;
  onRestoreDraft: (d: WafDraft) => void; onClose: () => void;
}) {
  const [drafts, setDrafts] = useState(() => listDrafts(kind));
  const [restoredId, setRestoredId] = useState<string | null>(null);

  const handleSave = () => {
    const label = (currentLabel || 'untitled').slice(0, 40);
    saveDraft(kind, { name: label, namespace: (currentState.namespace as string) ?? 'default', data: currentState });
    setDrafts(listDrafts(kind));
  };

  const handleDelete = (id: string) => {
    deleteDraft(kind, id);
    setDrafts(listDrafts(kind));
  };

  return (
    <div className="absolute top-full left-0 mt-1 z-50 rounded-lg border border-slate-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 shadow-lg w-80 p-3 space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold">Saved Drafts</p>
        <button onClick={onClose} className="text-muted-foreground hover:text-foreground"><X className="h-3.5 w-3.5" /></button>
      </div>
      {/* Storage info */}
      <p className="text-[10px] text-muted-foreground leading-tight">
        Stored in browser localStorage — survives server restarts and page refreshes, local to this browser only.
        Restoring removes the draft (the CR name will be taken once created; use Clone to copy a live CR).
      </p>

      <Button size="sm" variant="outline" className="w-full h-7 text-xs gap-1.5" onClick={handleSave}>
        <BookmarkPlus className="h-3.5 w-3.5" />
        Save current form as draft{currentLabel ? ` "${currentLabel.slice(0, 20)}"` : ''}
      </Button>

      {drafts.length === 0 ? (
        <p className="text-xs text-muted-foreground py-2 text-center">No drafts saved yet.</p>
      ) : (
        <div className="space-y-1 max-h-52 overflow-y-auto">
          {drafts.map(draft => (
            <div key={draft.id} className="flex items-center gap-2 rounded-md border border-slate-100 dark:border-zinc-800 px-2 py-1.5 hover:bg-slate-50 dark:hover:bg-zinc-800/50">
              <div className="flex-1 min-w-0">
                <p className="text-xs font-medium truncate">{draft.name || 'untitled'}</p>
                <p className="text-[10px] text-muted-foreground flex items-center gap-1">
                  <Clock className="h-2.5 w-2.5" />{relativeTime(draft.savedAt)} · {draft.namespace}
                </p>
              </div>
              <Button size="sm" variant="ghost"
                className={restoredId === draft.id ? 'h-6 text-xs px-2 shrink-0 text-emerald-600' : 'h-6 text-xs px-2 shrink-0'}
                onClick={() => {
                  onRestoreDraft(draft);
                  setRestoredId(draft.id);
                  // Remove after restore — name is taken once created; clone the live CR for reuse
                  deleteDraft(kind, draft.id);
                  setTimeout(() => { setRestoredId(null); setDrafts(listDrafts(kind)); onClose(); }, 600);
                }}>
                {restoredId === draft.id ? '✓ Restored' : 'Restore'}
              </Button>
              <button onClick={() => handleDelete(draft.id)} className="text-muted-foreground hover:text-red-500 shrink-0">
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── ImportPanel ────────────────────────────────────────────────────────────

function ImportPanel({ onImport, onClose }: { onImport: (r: Record<string, unknown>) => void; onClose: () => void }) {
  const [text, setText] = useState('');
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const handleApply = () => {
    try {
      const parsed = JSON.parse(text) as Record<string, unknown>;
      onImport(parsed);
      onClose();
    } catch (e) { setError(`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`); }
  };

  const handleFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => { setText(String(ev.target?.result ?? '')); setError(null); };
    reader.readAsText(file);
  };

  return (
    <div className="absolute top-full left-0 mt-1 z-50 rounded-lg border border-slate-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 shadow-lg w-80 p-3 space-y-2">
      <div className="flex items-center justify-between">
        <p className="text-xs font-semibold">Import JSON</p>
        <button onClick={onClose} className="text-muted-foreground hover:text-foreground"><X className="h-3.5 w-3.5" /></button>
      </div>
      <p className="text-xs text-muted-foreground">Paste a CR JSON (from Export or kubectl get -o json) to pre-fill the form.</p>
      <textarea
        value={text} onChange={e => { setText(e.target.value); setError(null); }}
        className="w-full h-28 rounded-md border border-input bg-background px-2 py-1.5 text-xs font-mono resize-none focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        placeholder='{"apiVersion":"appprotect.f5.com/v1","kind":"APPolicy",...}'
      />
      {error && <p className="text-xs text-red-500">{error}</p>}
      <div className="flex gap-2">
        <Button size="sm" variant="outline" className="h-7 text-xs gap-1" onClick={() => fileRef.current?.click()}>
          <Upload className="h-3 w-3" /> From file
        </Button>
        <input ref={fileRef} type="file" accept=".json,.yaml,.yml" className="hidden" onChange={handleFile} />
        <Button size="sm" className="h-7 text-xs flex-1 bg-blue-600 hover:bg-blue-700" onClick={handleApply} disabled={!text.trim()}>
          Apply to form
        </Button>
      </div>
    </div>
  );
}

// ── Main toolbar ───────────────────────────────────────────────────────────

export function WafFormToolbar({ kind, currentState, currentLabel, onClone, onRestoreDraft, onImport, cloneSources }: WafFormToolbarProps) {
  const [panel, setPanel] = useState<'drafts' | 'import' | null>(null);
  const drafts = listDrafts(kind);
  const draftCount = drafts.length;

  const toggle = (p: 'drafts' | 'import') => setPanel(prev => prev === p ? null : p);
  // Sort clone sources: current namespace first, then alphabetically by namespace/name
  const sortedSources = [...cloneSources].sort((a, b) => {
    const aKey = `${a.namespace}/${a.name}`;
    const bKey = `${b.namespace}/${b.name}`;
    return aKey.localeCompare(bKey);
  });

  return (
    <div className="relative">
      <div className="flex items-center gap-1.5 rounded-md border border-slate-200 dark:border-zinc-700 bg-slate-50 dark:bg-zinc-900/50 px-2 py-1.5">
        {/* Clone from existing CR — shows namespace/name format */}
        {cloneSources.length > 0 && (
          <div className="flex items-center gap-1 mr-1">
            <Copy className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
            <span className="text-xs text-muted-foreground whitespace-nowrap">Clone from:</span>
            <Select value="" onValueChange={v => {
              const src = cloneSources.find(s => `${s.namespace}/${s.name}` === v);
              if (src?.spec) onClone({ name: src.name, namespace: src.namespace, spec: src.spec });
            }}>
              <SelectTrigger className="h-6 w-48 text-xs font-mono"><SelectValue placeholder="namespace/name…" /></SelectTrigger>
              <SelectContent position="popper" className="min-w-max max-h-56">
                {sortedSources.map(s => (
                  <SelectItem key={`${s.namespace}/${s.name}`} value={`${s.namespace}/${s.name}`} className="text-xs font-mono">
                    <span className="text-muted-foreground">{s.namespace}/</span>{s.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}

        <div className="flex-1" />

        {/* Drafts */}
        <Button size="sm" variant="ghost" className="h-6 text-xs gap-1 px-2" onClick={() => toggle('drafts')}>
          <BookOpen className="h-3.5 w-3.5" />
          Drafts
          {draftCount > 0 && (
            <Badge variant="outline" className="text-[10px] h-4 px-1 ml-0.5 bg-blue-500/10 text-blue-600 border-blue-500/20">{draftCount}</Badge>
          )}
        </Button>

        {/* Import */}
        <Button size="sm" variant="ghost" className="h-6 text-xs gap-1 px-2" onClick={() => toggle('import')}>
          <Upload className="h-3.5 w-3.5" /> Import JSON
        </Button>
      </div>

      {panel === 'drafts' && (
        <DraftsPanel kind={kind} currentState={currentState} currentLabel={currentLabel}
          onRestoreDraft={onRestoreDraft} onClose={() => setPanel(null)} />
      )}
      {panel === 'import' && (
        <ImportPanel onImport={onImport} onClose={() => setPanel(null)} />
      )}
    </div>
  );
}
