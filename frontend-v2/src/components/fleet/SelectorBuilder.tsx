/**
 * SelectorBuilder — visual fleet selector editor (D-022 P6 follow-up).
 *
 * Replaces the free-text "env=prod region=us-east-1" input with:
 *   - Repeatable key/value rows (AND semantics).
 *   - Key dropdown populated from /api/fleet/label-vocabulary.
 *   - Value dropdown of known values for that key (plus free-type).
 *   - Debounced live match preview (400 ms) via /api/fleet/preview-selector.
 *   - Empty-selector warning when no rows are present.
 *
 * Emits `onChange(labels: string[])` where each element is "key=value".
 * The caller feeds this into the CreateTargetRequest / UpdateTargetRequest
 * selector field unchanged.
 *
 * Design: SectionCard + semantic tokens + pill badges — no isDark/useTheme.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Plus, X, AlertTriangle, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { useLabelVocabulary, usePreviewSelector } from '@/hooks/useFleet';
import type { PreviewSelectorRequest } from '@/types/fleet';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface SelectorRow {
  /** stable react key */
  _id: number;
  key: string;
  value: string;
}

export interface SelectorBuilderProps {
  /** Current label array e.g. ["env=prod", "vendor=aws"]. */
  value: string[];
  /** Called with the updated labels array on every change. */
  onChange: (labels: string[]) => void;
  /** Disable all inputs (e.g. while a mutation is in flight). */
  disabled?: boolean;
  className?: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

let _idCounter = 0;
function nextId() {
  return ++_idCounter;
}

function parseLabels(labels: string[]): SelectorRow[] {
  return labels
    .filter((l) => l.includes('='))
    .map((l) => {
      const [key, ...rest] = l.split('=');
      return { _id: nextId(), key: key.trim(), value: rest.join('=').trim() };
    });
}

function rowsToLabels(rows: SelectorRow[]): string[] {
  return rows
    .filter((r) => r.key.trim() && r.value.trim())
    .map((r) => `${r.key.trim()}=${r.value.trim()}`);
}

// ---------------------------------------------------------------------------
// SelectorBuilder component
// ---------------------------------------------------------------------------

export function SelectorBuilder({ value, onChange, disabled = false, className }: SelectorBuilderProps) {
  const { data: vocabData } = useLabelVocabulary();
  const vocabMap = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const entry of vocabData?.keys ?? []) {
      m.set(entry.key, entry.values);
    }
    return m;
  }, [vocabData]);
  const allKeys = useMemo(() => Array.from(vocabMap.keys()), [vocabMap]);

  // Internal rows state — initialised from value prop.
  const [rows, setRows] = useState<SelectorRow[]>(() => parseLabels(value));

  // Keep rows in sync when value prop changes from outside (e.g. edit pre-populate).
  // Only re-parse when the serialised form changes to avoid infinite loops.
  const serialised = value.join('\n');
  useEffect(() => {
    setRows(parseLabels(value));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serialised]);

  const notifyParent = useCallback(
    (nextRows: SelectorRow[]) => {
      onChange(rowsToLabels(nextRows));
    },
    [onChange]
  );

  const addRow = () => {
    const next = [...rows, { _id: nextId(), key: '', value: '' }];
    setRows(next);
    notifyParent(next);
  };

  const removeRow = (id: number) => {
    const next = rows.filter((r) => r._id !== id);
    setRows(next);
    notifyParent(next);
  };

  const updateKey = (id: number, key: string) => {
    const next = rows.map((r) =>
      r._id === id ? { ...r, key, value: '' } : r
    );
    setRows(next);
    notifyParent(next);
  };

  const updateValue = (id: number, val: string) => {
    const next = rows.map((r) => (r._id === id ? { ...r, value: val } : r));
    setRows(next);
    notifyParent(next);
  };

  // Live preview — only send when there's at least one complete row.
  const previewRequest = useMemo((): PreviewSelectorRequest | null => {
    const labels = rowsToLabels(rows);
    if (labels.length === 0) return null;
    return { selector: { labels } };
  }, [rows]);

  const { data: preview, isLoading: previewLoading } = usePreviewSelector(previewRequest);

  const isEmpty = rows.length === 0 || rowsToLabels(rows).length === 0;

  return (
    <div className={cn('space-y-2', className)}>
      {/* Row list */}
      <div className="space-y-1.5">
        {rows.map((row) => {
          const knownValues = vocabMap.get(row.key) ?? [];
          return (
            <div key={row._id} className="flex items-center gap-1.5">
              {/* Key selector */}
              <div className="relative flex-1">
                <input
                  list={`keys-${row._id}`}
                  placeholder="label key"
                  value={row.key}
                  onChange={(e) => updateKey(row._id, e.target.value)}
                  disabled={disabled}
                  className={cn(
                    'w-full rounded-md border border-border bg-card text-foreground px-2 py-1 text-xs font-mono',
                    'focus:outline-none focus:ring-1 focus:ring-ring',
                    disabled && 'opacity-50 cursor-not-allowed'
                  )}
                />
                <datalist id={`keys-${row._id}`}>
                  {allKeys.map((k) => (
                    <option key={k} value={k} />
                  ))}
                </datalist>
              </div>
              <span className="text-xs text-muted-foreground shrink-0">=</span>
              {/* Value selector */}
              <div className="relative flex-1">
                <input
                  list={`vals-${row._id}`}
                  placeholder="value"
                  value={row.value}
                  onChange={(e) => updateValue(row._id, e.target.value)}
                  disabled={disabled || !row.key}
                  className={cn(
                    'w-full rounded-md border border-border bg-card text-foreground px-2 py-1 text-xs font-mono',
                    'focus:outline-none focus:ring-1 focus:ring-ring',
                    (disabled || !row.key) && 'opacity-50 cursor-not-allowed'
                  )}
                />
                {knownValues.length > 0 && (
                  <datalist id={`vals-${row._id}`}>
                    {knownValues.map((v) => (
                      <option key={v} value={v} />
                    ))}
                  </datalist>
                )}
              </div>
              {/* Remove row */}
              <button
                type="button"
                onClick={() => removeRow(row._id)}
                disabled={disabled}
                className="shrink-0 h-5 w-5 flex items-center justify-center rounded hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
                title="Remove row"
              >
                <X className="h-3 w-3" />
              </button>
            </div>
          );
        })}
      </div>

      {/* Add row button */}
      <Button
        type="button"
        size="sm"
        variant="outline"
        className="h-6 text-xs gap-1"
        onClick={addRow}
        disabled={disabled}
      >
        <Plus className="h-3 w-3" />
        Add label filter
      </Button>

      {/* Live preview strip */}
      {previewRequest !== null && (
        <div
          className={cn(
            'rounded-lg border px-3 py-2 text-xs',
            isEmpty
              ? 'border-warning/30 bg-warning/5 text-warning'
              : 'border-border bg-muted/40 text-muted-foreground'
          )}
        >
          {previewLoading ? (
            <span className="flex items-center gap-1.5">
              <Loader2 className="h-3 w-3 animate-spin" />
              Checking matches…
            </span>
          ) : preview ? (
            preview.matched_count === 0 ? (
              <span className="flex items-center gap-1.5 text-warning">
                <AlertTriangle className="h-3 w-3 shrink-0" />
                Matches 0 members — this fleet will be empty
              </span>
            ) : (
              <span>
                Matches{' '}
                <span className="font-semibold text-foreground">{preview.matched_count}</span>{' '}
                {preview.matched_count === 1 ? 'member' : 'members'}
                {preview.sample.length > 0 && (
                  <>
                    :{' '}
                    {preview.sample.map((m, i) => (
                      <span key={m.id}>
                        {i > 0 && ', '}
                        <Badge
                          variant="secondary"
                          className="text-[10px] font-normal px-1 py-0 h-4"
                        >
                          {m.name}
                        </Badge>
                      </span>
                    ))}
                    {preview.matched_count > preview.sample.length && (
                      <span className="text-muted-foreground">
                        {' '}+{preview.matched_count - preview.sample.length} more
                      </span>
                    )}
                  </>
                )}
              </span>
            )
          ) : null}
        </div>
      )}

      {/* Empty warning when no rows entered */}
      {isEmpty && rows.length === 0 && (
        <div className="flex items-center gap-1.5 rounded-lg border border-warning/30 bg-warning/5 px-3 py-2 text-xs text-warning">
          <AlertTriangle className="h-3 w-3 shrink-0" />
          No label filters — fleet will match zero members (empty selector = nothing, not all).
        </div>
      )}
    </div>
  );
}
