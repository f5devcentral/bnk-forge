/**
 * Unit tests for severityFromConditions — pure function, no React needed.
 *
 * Covers:
 *  - No conditions → 'unknown'
 *  - Ready=True → 'healthy'
 *  - Programmed=True → 'healthy'
 *  - Accepted=True → 'healthy'
 *  - Ready=False → 'unhealthy'
 *  - Ready=Unknown → 'degraded'
 *  - Worst severity wins when multiple conditions conflict
 *  - Non-matching condition types ignored (falls back to 'unknown')
 */

import { describe, it, expect } from 'vitest';
import { severityFromConditions } from '../severity';
import type { K8sCondition } from '@/types/kubernetes';

function cond(type: string, status: string, reason?: string): K8sCondition {
  return { type, status, reason };
}

describe('severityFromConditions', () => {
  it('returns unknown when conditions is null', () => {
    expect(severityFromConditions(null)).toBe('unknown');
  });

  it('returns unknown when conditions is undefined', () => {
    expect(severityFromConditions(undefined)).toBe('unknown');
  });

  it('returns unknown when conditions is an empty array', () => {
    expect(severityFromConditions([])).toBe('unknown');
  });

  it('returns unknown when no Ready/Programmed/Accepted conditions present', () => {
    expect(severityFromConditions([cond('SomeOther', 'True')])).toBe('unknown');
  });

  it('returns healthy for Ready=True', () => {
    expect(severityFromConditions([cond('Ready', 'True')])).toBe('healthy');
  });

  it('returns healthy for Programmed=True', () => {
    expect(severityFromConditions([cond('Programmed', 'True')])).toBe('healthy');
  });

  it('returns healthy for Accepted=True', () => {
    expect(severityFromConditions([cond('Accepted', 'True')])).toBe('healthy');
  });

  it('returns unhealthy for Ready=False', () => {
    expect(severityFromConditions([cond('Ready', 'False')])).toBe('unhealthy');
  });

  it('returns unhealthy for Programmed=False', () => {
    expect(severityFromConditions([cond('Programmed', 'False')])).toBe('unhealthy');
  });

  it('returns unhealthy for Accepted=False', () => {
    expect(severityFromConditions([cond('Accepted', 'False')])).toBe('unhealthy');
  });

  it('returns degraded for Ready=Unknown', () => {
    expect(severityFromConditions([cond('Ready', 'Unknown')])).toBe('degraded');
  });

  it('returns degraded for Programmed=Unknown', () => {
    expect(severityFromConditions([cond('Programmed', 'Unknown')])).toBe('degraded');
  });

  it('unhealthy beats healthy (worst severity wins)', () => {
    const conditions = [cond('Ready', 'True'), cond('Programmed', 'False')];
    expect(severityFromConditions(conditions)).toBe('unhealthy');
  });

  it('unhealthy beats degraded', () => {
    const conditions = [cond('Ready', 'Unknown'), cond('Programmed', 'False')];
    expect(severityFromConditions(conditions)).toBe('unhealthy');
  });

  it('degraded beats healthy', () => {
    const conditions = [cond('Ready', 'True'), cond('Programmed', 'Unknown')];
    expect(severityFromConditions(conditions)).toBe('degraded');
  });

  it('ignores unrecognized condition types when relevant ones are present', () => {
    const conditions = [cond('Synced', 'False'), cond('Ready', 'True')];
    expect(severityFromConditions(conditions)).toBe('healthy');
  });

  it('returns unknown when only unrecognized condition types are present', () => {
    const conditions = [cond('Synced', 'False'), cond('Available', 'True')];
    expect(severityFromConditions(conditions)).toBe('unknown');
  });
});
