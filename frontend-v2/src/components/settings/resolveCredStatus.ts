import type { CloudCredentialTemplate } from '@/types';

export type CredStatusLevel = 'failed' | 'expired' | 'warning' | 'ok' | 'unknown';

export interface CredStatus {
  level: CredStatusLevel;
  headline: string;
  detail: string;
}

/**
 * Normalise a timestamp string to UTC. Backend may omit the 'Z' suffix, which
 * would cause JS to interpret the value as local time. Appending 'Z' when
 * absent ensures UTC parsing. Returns null on missing/invalid input.
 */
function toDate(ts: string | null | undefined): Date | null {
  if (!ts) return null;
  const s = ts.endsWith('Z') || ts.includes('+') ? ts : `${ts}Z`;
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d;
}

function relativeAge(d: Date): string {
  const secs = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

function remainingLabel(ms: number): string {
  if (ms <= 0) return '';
  const hours = ms / (1000 * 60 * 60);
  if (hours < 1) return `${Math.floor(ms / (1000 * 60))}m left`;
  if (hours < 24) return `${Math.floor(hours)}h left`;
  return `${Math.floor(hours / 24)}d left`;
}

/**
 * Collapse two divergent AWS credential signals into one authoritative status.
 *
 * Priority (first match wins):
 *  1. failed  — last observation is an error newer than last success
 *  2. expired — aws_credentials_expiry is in the past
 *  3. warning — expiry within 1 hour
 *  4. ok      — creds valid (with remaining time if lease present)
 *  5. unknown — no expiry and no observation
 *
 * Only meaningful for provider === 'aws'; callers should guard before
 * calling this for other providers.
 */
export function resolveCredStatus(template: CloudCredentialTemplate): CredStatus {
  const errorAt = toDate(template.last_error_at);
  const successAt = toDate(template.last_successful_call_at);
  const expiryAt = toDate(template.aws_credentials_expiry);
  const now = Date.now();

  // 1. failed: error observation is present and newer than last success
  if (errorAt && (!successAt || errorAt > successAt)) {
    const code = template.last_error_code || 'error';
    const msg = template.last_error_message;
    const detail = [
      msg ? `${code}: ${msg}` : code,
      expiryAt && expiryAt.getTime() > now
        ? `Note: lease clock still reads valid (${remainingLabel(expiryAt.getTime() - now)})`
        : null,
    ]
      .filter(Boolean)
      .join(' · ');
    return { level: 'failed', headline: `Failed: ${code}`, detail };
  }

  // 2. expired: lease is present and in the past
  if (expiryAt && expiryAt.getTime() <= now) {
    const detail = successAt
      ? `Last verified ${relativeAge(successAt)}`
      : 'Never verified via live call';
    return { level: 'expired', headline: 'Expired — re-auth', detail };
  }

  // 3. warning: expiry within 1 hour
  if (expiryAt) {
    const msLeft = expiryAt.getTime() - now;
    if (msLeft < 60 * 60 * 1000) {
      const mins = Math.floor(msLeft / (1000 * 60));
      const detail = successAt ? `Last verified ${relativeAge(successAt)}` : '';
      return {
        level: 'warning',
        headline: 'Expiring soon',
        detail: [detail, `Expires in ${mins}m`].filter(Boolean).join(' · '),
      };
    }
  }

  // 4. ok: valid lease or recent successful call
  if (expiryAt || successAt) {
    const remaining = expiryAt ? remainingLabel(expiryAt.getTime() - now) : null;
    const headline = remaining ? `OK · ${remaining}` : 'OK';
    const detail = successAt ? `Last verified ${relativeAge(successAt)}` : '';
    return { level: 'ok', headline, detail };
  }

  // 5. unknown: no data at all
  return { level: 'unknown', headline: '', detail: '' };
}
