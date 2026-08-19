/**
 * Pure URL-derivation for the Benchmarks page's tab model. Extracted from
 * Benchmarks.tsx so the parsing (which must reject NaN / non-positive ids —
 * `?run=abc` or `?compare=1,,2` must never reach the API as /runs/NaN or a
 * silent 0) is directly unit-testable without mounting React.
 */

// ============================================================================
// Runs tab — Detail / Compare / Trends / Group drill-ins
// ============================================================================

export type RunsSubView = 'list' | 'detail' | 'compare' | 'trends' | 'group';

export interface RunsViewState {
  subView: RunsSubView;
  /** Parsed run id for the 'detail' sub-view. Null when `?run=` is present
   * but not a valid positive integer — callers must render a "not found" /
   * invalid-id state instead of querying with an invalid id. */
  selectedRunId: number | null;
  /** Parsed, filtered run ids for the 'compare' sub-view. Empty segments and
   * non-positive-integer entries are dropped rather than coerced to 0/NaN. */
  compareRunIds: number[];
  /** Parsed run-group id for the 'group' sub-view (scenario launches land
   * here). Null when `?group=` is present but not a valid positive integer. */
  selectedGroupId: number | null;
}

function parsePositiveInt(raw: string): number | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const n = Number(trimmed);
  return Number.isInteger(n) && n > 0 ? n : null;
}

export function deriveRunsViewState(searchParams: URLSearchParams): RunsViewState {
  const runIdParam = searchParams.get('run');
  const groupIdParam = searchParams.get('group');
  const compareParam = searchParams.get('compare');
  const viewParam = searchParams.get('view');

  if (runIdParam !== null) {
    return {
      subView: 'detail',
      selectedRunId: parsePositiveInt(runIdParam),
      compareRunIds: [],
      selectedGroupId: null,
    };
  }

  if (groupIdParam !== null) {
    return {
      subView: 'group',
      selectedRunId: null,
      compareRunIds: [],
      selectedGroupId: parsePositiveInt(groupIdParam),
    };
  }

  if (compareParam !== null) {
    const compareRunIds = compareParam
      .split(',')
      .map(parsePositiveInt)
      .filter((n): n is number => n !== null);
    return { subView: 'compare', selectedRunId: null, compareRunIds, selectedGroupId: null };
  }

  if (viewParam === 'trends') {
    return { subView: 'trends', selectedRunId: null, compareRunIds: [], selectedGroupId: null };
  }

  return { subView: 'list', selectedRunId: null, compareRunIds: [], selectedGroupId: null };
}

// ============================================================================
// Primary tab model — Overview / Runs / Setup, incl. legacy ?tab= mapping
//
// Pre-run-first-redesign links used ?tab=targets|agents|configs as PRIMARY
// tabs. Those entities now live as sub-sections under Setup, so legacy links
// are translated rather than broken: ?tab=targets -> {primaryTab: 'setup',
// setupSection: 'targets'}. New canonical links use ?tab=setup&section=...
// ============================================================================

export type PrimaryTab = 'overview' | 'runs' | 'setup';
export type SetupSection = 'targets' | 'agents' | 'configs';

const SETUP_SECTIONS: readonly SetupSection[] = ['targets', 'agents', 'configs'];

function isSetupSection(v: string | null): v is SetupSection {
  return !!v && (SETUP_SECTIONS as readonly string[]).includes(v);
}

export interface PrimaryTabState {
  primaryTab: PrimaryTab;
  setupSection: SetupSection;
}

export function derivePrimaryTabState(searchParams: URLSearchParams): PrimaryTabState {
  const tabParam = searchParams.get('tab');

  if (tabParam === 'runs') {
    return { primaryTab: 'runs', setupSection: 'targets' };
  }

  if (tabParam === 'setup') {
    const section = searchParams.get('section');
    return { primaryTab: 'setup', setupSection: isSetupSection(section) ? section : 'targets' };
  }

  // Legacy deep-link: ?tab=targets|agents|configs -> Setup + matching sub-section.
  if (isSetupSection(tabParam)) {
    return { primaryTab: 'setup', setupSection: tabParam };
  }

  return { primaryTab: 'overview', setupSection: 'targets' };
}
