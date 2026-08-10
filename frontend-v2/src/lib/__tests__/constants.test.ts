/**
 * FU-010: lib/constants — all application constants
 */
import { describe, it, expect } from 'vitest';
import {
  QUERY_STALE_TIME,
  DEBOUNCE_MS,
  DISPLAY_LIMITS,
  TEXT_LIMITS,
  POLL_INTERVALS,
  DRIFT_SCHEDULE_INTERVALS,
  DRIFT_SCHEDULE_LABELS,
} from '../constants';

describe('QUERY_STALE_TIME', () => {
  it('has expected keys', () => {
    expect(QUERY_STALE_TIME.NEVER).toBe(0);
    expect(QUERY_STALE_TIME.SHORT).toBe(5000);
    expect(QUERY_STALE_TIME.DEFAULT).toBe(30000);
    expect(QUERY_STALE_TIME.LONG).toBe(60000);
  });

  it('values are all numbers > 0 or exactly 0 (NEVER)', () => {
    for (const [key, value] of Object.entries(QUERY_STALE_TIME)) {
      expect(typeof value).toBe('number');
      if (key !== 'NEVER') {
        expect(value).toBeGreaterThan(0);
      }
    }
  });

  it('maintains ordering: shorter < longer', () => {
    expect(QUERY_STALE_TIME.VERY_SHORT).toBeLessThan(QUERY_STALE_TIME.SHORT);
    expect(QUERY_STALE_TIME.SHORT).toBeLessThan(QUERY_STALE_TIME.DEFAULT);
    expect(QUERY_STALE_TIME.DEFAULT).toBeLessThan(QUERY_STALE_TIME.LONG);
    expect(QUERY_STALE_TIME.LONG).toBeLessThan(QUERY_STALE_TIME.VERY_LONG);
  });
});

describe('DEBOUNCE_MS', () => {
  it('has expected keys', () => {
    expect(DEBOUNCE_MS.DEFAULT).toBe(300);
    expect(DEBOUNCE_MS.SEARCH).toBe(300);
    expect(DEBOUNCE_MS.API_SEARCH).toBe(800);
  });

  it('API_SEARCH is longer than DEFAULT', () => {
    expect(DEBOUNCE_MS.API_SEARCH).toBeGreaterThan(DEBOUNCE_MS.DEFAULT);
  });
});

describe('DISPLAY_LIMITS', () => {
  it('has expected keys', () => {
    expect(DISPLAY_LIMITS.DASHBOARD_ATTENTION).toBe(2);
    expect(DISPLAY_LIMITS.RECENT_ACTIVITY).toBe(5);
    expect(DISPLAY_LIMITS.DASHBOARD_CARDS).toBe(6);
  });

  it('values are all positive integers', () => {
    for (const value of Object.values(DISPLAY_LIMITS)) {
      expect(Number.isInteger(value)).toBe(true);
      expect(value).toBeGreaterThan(0);
    }
  });
});

describe('TEXT_LIMITS', () => {
  it('has description short limit', () => {
    expect(TEXT_LIMITS.DESCRIPTION_SHORT).toBe(60);
  });
});

describe('POLL_INTERVALS', () => {
  it('values are all positive numbers', () => {
    for (const value of Object.values(POLL_INTERVALS)) {
      expect(typeof value).toBe('number');
      expect(value).toBeGreaterThan(0);
    }
  });

  it('maintains ordering: faster < slower', () => {
    expect(POLL_INTERVALS.FAST).toBeLessThan(POLL_INTERVALS.STANDARD);
    expect(POLL_INTERVALS.STANDARD).toBeLessThan(POLL_INTERVALS.MEDIUM);
    expect(POLL_INTERVALS.MEDIUM).toBeLessThan(POLL_INTERVALS.SLOW);
    expect(POLL_INTERVALS.SLOW).toBeLessThan(POLL_INTERVALS.VERY_SLOW);
  });
});

describe('DRIFT_SCHEDULE_INTERVALS', () => {
  it('has expected intervals', () => {
    expect(DRIFT_SCHEDULE_INTERVALS.FIVE_MIN).toBe('300');
    expect(DRIFT_SCHEDULE_INTERVALS.TWENTYFOUR_HOURS).toBe('86400');
  });

  it('each interval has a corresponding label', () => {
    for (const value of Object.values(DRIFT_SCHEDULE_INTERVALS)) {
      expect(DRIFT_SCHEDULE_LABELS[value]).toBeDefined();
    }
  });
});
