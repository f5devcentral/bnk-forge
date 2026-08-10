/**
 * Tests for deriveRunsViewState / derivePrimaryTabState — pure URL → tab-model
 * derivation. Guards against `?run=abc` fetching /runs/NaN and `?compare=1,,2`
 * silently coercing the empty segment to run id 0 (Number('') === 0), and
 * against legacy `?tab=targets|agents|configs` deep-links breaking after the
 * run-first Overview/Runs/Setup restructure.
 */
import { describe, it, expect } from 'vitest';
import { deriveRunsViewState, derivePrimaryTabState } from '@/pages/benchmark-runs-view';

function params(query: string): URLSearchParams {
  return new URLSearchParams(query);
}

describe('deriveRunsViewState', () => {
  it('defaults to the list sub-view with no params', () => {
    expect(deriveRunsViewState(params(''))).toEqual({
      subView: 'list',
      selectedRunId: null,
      compareRunIds: [],
      selectedGroupId: null,
    });
  });

  it('derives the detail sub-view for a valid run id', () => {
    expect(deriveRunsViewState(params('run=42'))).toEqual({
      subView: 'detail',
      selectedRunId: 42,
      compareRunIds: [],
      selectedGroupId: null,
    });
  });

  it('derives detail with a null selectedRunId for a non-numeric run id (never fetches NaN)', () => {
    expect(deriveRunsViewState(params('run=abc'))).toEqual({
      subView: 'detail',
      selectedRunId: null,
      compareRunIds: [],
      selectedGroupId: null,
    });
  });

  it('rejects a zero or negative run id', () => {
    expect(deriveRunsViewState(params('run=0')).selectedRunId).toBeNull();
    expect(deriveRunsViewState(params('run=-5')).selectedRunId).toBeNull();
  });

  it('derives the compare sub-view and parses comma-separated ids', () => {
    expect(deriveRunsViewState(params('compare=1,2,3'))).toEqual({
      subView: 'compare',
      selectedRunId: null,
      compareRunIds: [1, 2, 3],
      selectedGroupId: null,
    });
  });

  it('drops empty segments instead of coercing them to 0 (?compare=1,,2)', () => {
    const result = deriveRunsViewState(params('compare=1,,2'));
    expect(result.compareRunIds).toEqual([1, 2]);
  });

  it('drops non-numeric and non-positive segments from compare', () => {
    const result = deriveRunsViewState(params('compare=1,abc,-3,0,4'));
    expect(result.compareRunIds).toEqual([1, 4]);
  });

  it('derives the trends sub-view', () => {
    expect(deriveRunsViewState(params('view=trends'))).toEqual({
      subView: 'trends',
      selectedRunId: null,
      compareRunIds: [],
      selectedGroupId: null,
    });
  });

  it('derives the group sub-view for a valid group id (scenario launches)', () => {
    expect(deriveRunsViewState(params('group=9'))).toEqual({
      subView: 'group',
      selectedRunId: null,
      compareRunIds: [],
      selectedGroupId: 9,
    });
  });

  it('derives group with a null selectedGroupId for a non-numeric group id', () => {
    expect(deriveRunsViewState(params('group=abc')).selectedGroupId).toBeNull();
  });

  it('run takes precedence over group, compare, and view when multiple are present', () => {
    const result = deriveRunsViewState(params('run=7&group=9&compare=1,2&view=trends'));
    expect(result.subView).toBe('detail');
    expect(result.selectedRunId).toBe(7);
  });

  it('group takes precedence over compare and view when both are present', () => {
    const result = deriveRunsViewState(params('group=9&compare=1,2&view=trends'));
    expect(result.subView).toBe('group');
  });

  it('compare takes precedence over view when both are present', () => {
    const result = deriveRunsViewState(params('compare=1,2&view=trends'));
    expect(result.subView).toBe('compare');
  });
});

describe('derivePrimaryTabState', () => {
  it('defaults to overview with no tab param (default landing)', () => {
    expect(derivePrimaryTabState(params(''))).toEqual({ primaryTab: 'overview', setupSection: 'targets' });
  });

  it('derives runs from ?tab=runs, independent of section', () => {
    expect(derivePrimaryTabState(params('tab=runs'))).toEqual({ primaryTab: 'runs', setupSection: 'targets' });
  });

  it('derives setup with an explicit section from the canonical ?tab=setup&section=', () => {
    expect(derivePrimaryTabState(params('tab=setup&section=agents'))).toEqual({
      primaryTab: 'setup',
      setupSection: 'agents',
    });
  });

  it('defaults setup section to targets when ?tab=setup has no/invalid section', () => {
    expect(derivePrimaryTabState(params('tab=setup'))).toEqual({ primaryTab: 'setup', setupSection: 'targets' });
    expect(derivePrimaryTabState(params('tab=setup&section=bogus'))).toEqual({
      primaryTab: 'setup',
      setupSection: 'targets',
    });
  });

  it.each(['targets', 'agents', 'configs'] as const)(
    'maps the legacy ?tab=%s deep-link onto Setup with the matching sub-section',
    (legacyTab) => {
      expect(derivePrimaryTabState(params(`tab=${legacyTab}`))).toEqual({
        primaryTab: 'setup',
        setupSection: legacyTab,
      });
    },
  );

  it('falls back to overview for an unrecognized tab value', () => {
    expect(derivePrimaryTabState(params('tab=bogus'))).toEqual({ primaryTab: 'overview', setupSection: 'targets' });
  });
});
