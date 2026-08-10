/**
 * FU-007: lib/status-colors — status color mapping functions.
 *
 * D-020: assertions retuned from raw-palette names (`emerald`/`red`/`amber`/`slate`)
 * to the semantic tokens the module now emits (`success`/`destructive`/`warning`/`muted`/`info`).
 */
import { describe, it, expect } from 'vitest';
import {
  MODULE_STATUS_COLORS,
  TASK_STATUS_COLORS,
  getResourceStatusColor,
  getModuleStatusColor,
  getTaskStatusColor,
} from '../status-colors';

describe('MODULE_STATUS_COLORS', () => {
  it('has expected status keys', () => {
    const expectedKeys = [
      'applied', 'initialized', 'planned', 'planning', 'applying',
      'destroying', 'not_initialized', 'failed', 'error',
      'init_failed', 'plan_failed', 'apply_failed', 'destroy_failed',
    ];
    for (const key of expectedKeys) {
      expect(MODULE_STATUS_COLORS).toHaveProperty(key);
    }
  });

  it('uses success token for applied', () => {
    expect(MODULE_STATUS_COLORS.applied).toContain('success');
  });

  it('uses destructive token for failed states', () => {
    expect(MODULE_STATUS_COLORS.failed).toContain('destructive');
    expect(MODULE_STATUS_COLORS.error).toContain('destructive');
    expect(MODULE_STATUS_COLORS.init_failed).toContain('destructive');
  });
});

describe('TASK_STATUS_COLORS', () => {
  it('has expected status keys', () => {
    const expectedKeys = ['completed', 'in_progress', 'failed', 'cancelled', 'queued', 'pending'];
    for (const key of expectedKeys) {
      expect(TASK_STATUS_COLORS).toHaveProperty(key);
    }
  });

  it('uses success token for completed', () => {
    expect(TASK_STATUS_COLORS.completed).toContain('success');
  });

  it('uses destructive token for failed', () => {
    expect(TASK_STATUS_COLORS.failed).toContain('destructive');
  });
});

describe('getResourceStatusColor()', () => {
  it('returns success for success states', () => {
    expect(getResourceStatusColor('Running')).toContain('success');
    expect(getResourceStatusColor('Ready')).toContain('success');
    expect(getResourceStatusColor('deployed')).toContain('success');
    expect(getResourceStatusColor('Active')).toContain('success');
    expect(getResourceStatusColor('Bound')).toContain('success');
  });

  it('returns destructive for error states', () => {
    expect(getResourceStatusColor('Failed')).toContain('destructive');
    expect(getResourceStatusColor('Error')).toContain('destructive');
    expect(getResourceStatusColor('CrashLoopBackOff')).toContain('destructive');
    expect(getResourceStatusColor('ImagePullBackOff')).toContain('destructive');
  });

  it('returns destructive for NotReady (before Ready match)', () => {
    expect(getResourceStatusColor('NotReady')).toContain('destructive');
  });

  it('returns warning for pending/warning states', () => {
    expect(getResourceStatusColor('Pending')).toContain('warning');
    expect(getResourceStatusColor('Waiting')).toContain('warning');
    expect(getResourceStatusColor('Unknown')).toContain('warning');
  });

  it('returns muted for terminating states', () => {
    expect(getResourceStatusColor('Terminating')).toContain('muted');
    expect(getResourceStatusColor('Deleting')).toContain('muted');
    expect(getResourceStatusColor('Uninstalling')).toContain('muted');
  });

  it('returns muted for empty/null status', () => {
    expect(getResourceStatusColor('')).toContain('muted');
  });

  it('returns muted for unrecognized status', () => {
    expect(getResourceStatusColor('SomeCustomStatus')).toContain('muted');
  });
});

describe('getModuleStatusColor()', () => {
  it('returns correct color for known statuses', () => {
    expect(getModuleStatusColor('applied')).toContain('success');
    expect(getModuleStatusColor('failed')).toContain('destructive');
    expect(getModuleStatusColor('planning')).toContain('warning');
  });

  it('returns not_initialized color for unknown status', () => {
    expect(getModuleStatusColor('something-unknown')).toBe(MODULE_STATUS_COLORS.not_initialized);
  });
});

describe('getTaskStatusColor()', () => {
  it('returns correct color for known statuses', () => {
    expect(getTaskStatusColor('completed')).toContain('success');
    expect(getTaskStatusColor('failed')).toContain('destructive');
    expect(getTaskStatusColor('in_progress')).toContain('info');
  });

  it('returns pending color for unknown status', () => {
    expect(getTaskStatusColor('something-unknown')).toBe(TASK_STATUS_COLORS.pending);
  });
});
