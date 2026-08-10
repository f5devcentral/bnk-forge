/**
 * FU-004: lib/time-utils — all time formatting functions
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { formatAge, formatTimeAgo, formatDate, formatDateTime, formatAgeFromSeconds, formatDuration } from '../time-utils';

describe('formatAge()', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-02-25T12:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns "Unknown" for null/undefined', () => {
    expect(formatAge(null)).toBe('Unknown');
    expect(formatAge(undefined)).toBe('Unknown');
  });

  it('returns "Unknown" for invalid timestamp', () => {
    expect(formatAge('not-a-date')).toBe('Unknown');
  });

  it('returns "just now" for recent timestamps', () => {
    expect(formatAge('2026-02-25T11:59:50Z')).toBe('just now');
  });

  it('returns "just now" for future timestamps', () => {
    expect(formatAge('2026-02-25T13:00:00Z')).toBe('just now');
  });

  it('returns minutes', () => {
    expect(formatAge('2026-02-25T11:50:00Z')).toBe('10m');
  });

  it('returns hours', () => {
    expect(formatAge('2026-02-25T09:00:00Z')).toBe('3h');
  });

  it('returns days', () => {
    expect(formatAge('2026-02-23T12:00:00Z')).toBe('2d');
  });
});

describe('formatTimeAgo()', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-02-25T12:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('returns empty string for null/undefined', () => {
    expect(formatTimeAgo(null)).toBe('');
    expect(formatTimeAgo(undefined)).toBe('');
  });

  it('returns empty string for invalid timestamp', () => {
    expect(formatTimeAgo('not-a-date')).toBe('');
  });

  it('returns "just now" for recent', () => {
    expect(formatTimeAgo('2026-02-25T11:59:50Z')).toBe('just now');
  });

  it('returns "Xm ago" for minutes', () => {
    expect(formatTimeAgo('2026-02-25T11:50:00Z')).toBe('10m ago');
  });

  it('returns "Xh ago" for hours', () => {
    expect(formatTimeAgo('2026-02-25T09:00:00Z')).toBe('3h ago');
  });

  it('returns "Xd ago" for days', () => {
    expect(formatTimeAgo('2026-02-23T12:00:00Z')).toBe('2d ago');
  });
});

describe('formatDate()', () => {
  it('returns empty string for null/undefined', () => {
    expect(formatDate(null)).toBe('');
    expect(formatDate(undefined)).toBe('');
  });

  it('returns empty string for invalid timestamp', () => {
    expect(formatDate('not-a-date')).toBe('');
  });

  it('formats valid timestamp as localized date', () => {
    const result = formatDate('2026-02-25T12:00:00Z');
    // Locale-dependent, just check it returns a non-empty string
    expect(result).toBeTruthy();
    expect(result.length).toBeGreaterThan(0);
  });
});

describe('formatDateTime()', () => {
  it('returns empty string for null/undefined', () => {
    expect(formatDateTime(null)).toBe('');
    expect(formatDateTime(undefined)).toBe('');
  });

  it('returns empty string for invalid timestamp', () => {
    expect(formatDateTime('not-a-date')).toBe('');
  });

  it('formats valid timestamp as localized datetime', () => {
    const result = formatDateTime('2026-02-25T12:00:00Z');
    expect(result).toBeTruthy();
    expect(result.length).toBeGreaterThan(0);
  });
});

describe('formatAgeFromSeconds()', () => {
  it('returns "just now" for < 60s', () => {
    expect(formatAgeFromSeconds(0)).toBe('just now');
    expect(formatAgeFromSeconds(59)).toBe('just now');
  });

  it('returns minutes (singular)', () => {
    expect(formatAgeFromSeconds(60)).toBe('1 minute ago');
  });

  it('returns minutes (plural)', () => {
    expect(formatAgeFromSeconds(300)).toBe('5 minutes ago');
  });

  it('returns hours (singular)', () => {
    expect(formatAgeFromSeconds(3600)).toBe('1 hour ago');
  });

  it('returns hours (plural)', () => {
    expect(formatAgeFromSeconds(7200)).toBe('2 hours ago');
  });

  it('returns days (singular)', () => {
    expect(formatAgeFromSeconds(86400)).toBe('1 day ago');
  });

  it('returns days (plural)', () => {
    expect(formatAgeFromSeconds(172800)).toBe('2 days ago');
  });
});

describe('formatDuration()', () => {
  it('formats seconds only', () => {
    expect(formatDuration(30)).toBe('30s');
    expect(formatDuration(0)).toBe('0s');
  });

  it('formats minutes only (exact)', () => {
    expect(formatDuration(60)).toBe('1m');
    expect(formatDuration(120)).toBe('2m');
  });

  it('formats minutes + seconds', () => {
    expect(formatDuration(90)).toBe('1m 30s');
    expect(formatDuration(150)).toBe('2m 30s');
  });

  it('formats hours only (exact)', () => {
    expect(formatDuration(3600)).toBe('1h');
  });

  it('formats hours + minutes', () => {
    expect(formatDuration(3660)).toBe('1h 1m');
    expect(formatDuration(5400)).toBe('1h 30m');
  });
});
