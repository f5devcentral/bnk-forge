import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { resolveCredStatus } from '../resolveCredStatus';
import type { CloudCredentialTemplate } from '@/types';

const NOW = new Date('2026-06-05T12:00:00Z').getTime();

// Minimal valid AWS template — caller fills in overrides per test
const base: CloudCredentialTemplate = {
  id: 1,
  name: 'test',
  provider: 'aws',
  has_aws_secret_access_key: true,
  has_aws_session_token: false,
  aws_sso_enabled: false,
  has_gcp_credentials: false,
  has_azure_credentials: false,
  has_ibmcloud_api_key: false,
  has_ssh_password: false,
  has_ssh_key: false,
  is_default: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  projects_count: 0,
  last_successful_call_at: null,
  last_error_at: null,
  last_error_code: null,
  last_error_message: null,
};

function make(overrides: Partial<CloudCredentialTemplate>): CloudCredentialTemplate {
  return { ...base, ...overrides };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
});

describe('resolveCredStatus — failed', () => {
  it('returns failed when error is newer than success', () => {
    const t = make({
      last_error_at: '2026-06-05T11:30:00Z',   // 30m ago
      last_successful_call_at: '2026-06-05T10:00:00Z', // 2h ago
      last_error_code: 'ExpiredTokenException',
    });
    const result = resolveCredStatus(t);
    expect(result.level).toBe('failed');
    expect(result.headline).toBe('Failed: ExpiredTokenException');
  });

  it('failed beats a future lease (Card 1 scenario)', () => {
    const t = make({
      aws_credentials_expiry: '2026-06-05T15:00:00Z', // 3h in the future
      last_error_at: '2026-06-05T11:45:00Z',
      last_successful_call_at: '2026-06-05T09:00:00Z',
      last_error_code: 'ExpiredTokenException',
    });
    const result = resolveCredStatus(t);
    expect(result.level).toBe('failed');
    // Detail should mention the lease is still valid
    expect(result.detail).toContain('lease clock still reads valid');
  });

  it('returns failed when there is an error and no success at all', () => {
    const t = make({
      last_error_at: '2026-06-05T11:00:00Z',
      last_successful_call_at: null,
      last_error_code: 'NoCredentialProviders',
      last_error_message: 'Unable to locate credentials',
    });
    const result = resolveCredStatus(t);
    expect(result.level).toBe('failed');
    expect(result.detail).toContain('NoCredentialProviders: Unable to locate credentials');
  });
});

describe('resolveCredStatus — expired', () => {
  it('returns expired when lease is in the past', () => {
    const t = make({
      aws_credentials_expiry: '2026-06-05T10:00:00Z', // 2h ago
    });
    const result = resolveCredStatus(t);
    expect(result.level).toBe('expired');
    expect(result.headline).toBe('Expired — re-auth');
  });

  it('expired beats a stale successful observation (Card 2 scenario)', () => {
    const t = make({
      aws_credentials_expiry: '2026-06-05T05:00:00Z', // 7h ago
      last_successful_call_at: '2026-06-05T04:30:00Z', // 7.5h ago
    });
    const result = resolveCredStatus(t);
    expect(result.level).toBe('expired');
    expect(result.detail).toContain('Last verified');
  });
});

describe('resolveCredStatus — warning', () => {
  it('returns warning when expiry is within 1 hour', () => {
    const t = make({
      aws_credentials_expiry: '2026-06-05T12:30:00Z', // 30m from now
    });
    const result = resolveCredStatus(t);
    expect(result.level).toBe('warning');
    expect(result.headline).toBe('Expiring soon');
    expect(result.detail).toContain('Expires in 30m');
  });

  it('is NOT warning when expiry is exactly 1 hour from now (boundary)', () => {
    const t = make({
      aws_credentials_expiry: '2026-06-05T13:00:00Z', // exactly 1h
    });
    const result = resolveCredStatus(t);
    expect(result.level).toBe('ok');
  });
});

describe('resolveCredStatus — ok', () => {
  it('returns ok with remaining time when lease is valid and > 1h', () => {
    const t = make({
      aws_credentials_expiry: '2026-06-05T15:00:00Z', // 3h from now
      last_successful_call_at: '2026-06-05T11:50:00Z',
    });
    const result = resolveCredStatus(t);
    expect(result.level).toBe('ok');
    expect(result.headline).toBe('OK · 3h left');
    expect(result.detail).toContain('Last verified');
  });

  it('returns ok with days remaining for long-lived lease', () => {
    const t = make({
      aws_credentials_expiry: '2026-06-07T12:00:00Z', // exactly 2d from now
    });
    const result = resolveCredStatus(t);
    expect(result.level).toBe('ok');
    expect(result.headline).toBe('OK · 2d left');
  });

  it('returns ok with no remaining suffix when no lease but success exists', () => {
    const t = make({
      last_successful_call_at: '2026-06-05T11:55:00Z',
    });
    const result = resolveCredStatus(t);
    expect(result.level).toBe('ok');
    expect(result.headline).toBe('OK');
  });
});

describe('resolveCredStatus — unknown', () => {
  it('returns unknown when no expiry and no observation', () => {
    const result = resolveCredStatus(base);
    expect(result.level).toBe('unknown');
    expect(result.headline).toBe('');
  });
});

describe('resolveCredStatus — timestamp handling', () => {
  it('handles timestamps WITHOUT a Z suffix (backend omits it)', () => {
    // Expired timestamp without Z — must still be parsed as UTC
    const t = make({
      aws_credentials_expiry: '2026-06-05T10:00:00', // no Z, 2h ago UTC
    });
    const result = resolveCredStatus(t);
    expect(result.level).toBe('expired');
  });

  it('handles invalid/garbage timestamps gracefully (no NaN)', () => {
    const t = make({
      aws_credentials_expiry: 'not-a-date',
      last_error_at: 'also-not-a-date',
    });
    const result = resolveCredStatus(t);
    // Should not crash, and not produce NaN in headline/detail
    expect(result.headline).not.toContain('NaN');
    expect(result.detail).not.toContain('NaN');
  });
});
