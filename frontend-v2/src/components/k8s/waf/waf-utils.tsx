import { AlertTriangle } from 'lucide-react';

const RFC1123_RE = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/;

export function validateK8sName(name: string): string | null {
  if (!name) return 'Name is required.';
  if (name.length > 63) return 'Must be ≤ 63 characters.';
  if (!RFC1123_RE.test(name)) return 'Lowercase alphanumeric with hyphens; cannot start or end with a hyphen.';
  return null;
}

export function extractApiError(e: unknown): string {
  const resp = (e as { response?: { data?: { detail?: string; message?: string; error?: string | { message?: string } } } })?.response?.data;
  if (resp?.detail) return String(resp.detail);
  if (resp?.message) return String(resp.message);
  if (typeof resp?.error === 'string') return resp.error;
  if (typeof resp?.error === 'object' && resp.error?.message) return resp.error.message;
  return e instanceof Error ? e.message : String(e);
}

export function InlineError({ message }: { message: string }) {
  return (
    <div className="flex items-start gap-1.5 rounded-md border border-red-300 bg-red-50 dark:border-red-900 dark:bg-red-900/20 px-3 py-2 text-xs text-red-600 dark:text-red-400">
      <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
      <span>{message}</span>
    </div>
  );
}
