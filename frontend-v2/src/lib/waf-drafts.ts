/**
 * WAF form draft persistence via localStorage.
 *
 * Drafts are auto-saved on every field change (debounced) and restored
 * when the Create form is opened. Multiple named drafts per CRD type.
 *
 * Storage key: waf-draft:<CrdKind>:<draftId>
 * Draft index:  waf-draft-index:<CrdKind>  →  string[] (list of ids)
 */

export type CrdKind = 'APPolicy' | 'APLogConf' | 'APUserSig';

export interface WafDraft {
  id: string;
  name: string;          // user-visible label, e.g. the CR name being drafted
  savedAt: number;       // Date.now()
  namespace: string;
  data: Record<string, unknown>;  // full form state — opaque per kind
}

const PREFIX = 'waf-draft';
const INDEX_PREFIX = 'waf-draft-index';

function indexKey(kind: CrdKind) { return `${INDEX_PREFIX}:${kind}`; }
function draftKey(kind: CrdKind, id: string) { return `${PREFIX}:${kind}:${id}`; }

export function listDrafts(kind: CrdKind): WafDraft[] {
  try {
    const ids: string[] = JSON.parse(localStorage.getItem(indexKey(kind)) ?? '[]');
    return ids
      .map(id => { try { return JSON.parse(localStorage.getItem(draftKey(kind, id)) ?? '') as WafDraft; } catch { return null; } })
      .filter((d): d is WafDraft => d !== null)
      .sort((a, b) => b.savedAt - a.savedAt);
  } catch { return []; }
}

export function saveDraft(kind: CrdKind, draft: Omit<WafDraft, 'id' | 'savedAt'> & { id?: string }): WafDraft {
  const id = draft.id ?? `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
  const full: WafDraft = { ...draft, id, savedAt: Date.now() };
  try {
    localStorage.setItem(draftKey(kind, id), JSON.stringify(full));
    const ids: string[] = JSON.parse(localStorage.getItem(indexKey(kind)) ?? '[]');
    if (!ids.includes(id)) localStorage.setItem(indexKey(kind), JSON.stringify([...ids, id]));
  } catch { /* localStorage full — silently ignore */ }
  return full;
}

export function deleteDraft(kind: CrdKind, id: string) {
  try {
    localStorage.removeItem(draftKey(kind, id));
    const ids: string[] = JSON.parse(localStorage.getItem(indexKey(kind)) ?? '[]');
    localStorage.setItem(indexKey(kind), JSON.stringify(ids.filter(i => i !== id)));
  } catch { /* ignore */ }
}

export function getDraft(kind: CrdKind, id: string): WafDraft | null {
  try { return JSON.parse(localStorage.getItem(draftKey(kind, id)) ?? '') as WafDraft; } catch { return null; }
}

/** Human-readable "2 minutes ago" / "yesterday" */
export function relativeTime(ts: number): string {
  const secs = Math.floor((Date.now() - ts) / 1000);
  if (secs < 60) return 'just now';
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}
