export type JourneyStepId = 'deploy' | 'validate' | 'benchmark' | 'diagnose' | 'export';

export interface ValueJourneyState {
  projectId: number;
  blueprintSlug: string;
  blueprintName: string;
  startedAt: string;
  updatedAt: string;
  steps: Record<JourneyStepId, boolean>;
}

export const VALUE_JOURNEY_STORAGE_KEY = 'bnk-forge:value-journey';

const EMPTY_STEPS: Record<JourneyStepId, boolean> = {
  deploy: false,
  validate: false,
  benchmark: false,
  diagnose: false,
  export: false,
};

export function loadValueJourney(): ValueJourneyState | null {
  if (typeof window === 'undefined') return null;
  const raw = window.localStorage.getItem(VALUE_JOURNEY_STORAGE_KEY);
  if (!raw) return null;

  try {
    const parsed = JSON.parse(raw) as ValueJourneyState;
    if (!parsed?.projectId || !parsed?.blueprintSlug || !parsed?.steps) {
      return null;
    }
    return {
      ...parsed,
      steps: { ...EMPTY_STEPS, ...parsed.steps },
    };
  } catch {
    return null;
  }
}

export function saveValueJourney(state: ValueJourneyState): void {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(VALUE_JOURNEY_STORAGE_KEY, JSON.stringify(state));
}

export function startValueJourney(input: {
  projectId: number;
  blueprintSlug: string;
  blueprintName: string;
}): ValueJourneyState {
  const now = new Date().toISOString();
  const state: ValueJourneyState = {
    projectId: input.projectId,
    blueprintSlug: input.blueprintSlug,
    blueprintName: input.blueprintName,
    startedAt: now,
    updatedAt: now,
    steps: {
      ...EMPTY_STEPS,
      deploy: true,
    },
  };
  saveValueJourney(state);
  return state;
}

export function completeJourneyStep(step: JourneyStepId): ValueJourneyState | null {
  const state = loadValueJourney();
  if (!state) return null;
  const updated: ValueJourneyState = {
    ...state,
    updatedAt: new Date().toISOString(),
    steps: {
      ...state.steps,
      [step]: true,
    },
  };
  saveValueJourney(updated);
  return updated;
}

export function restartJourney(): void {
  if (typeof window === 'undefined') return;
  window.localStorage.removeItem(VALUE_JOURNEY_STORAGE_KEY);
}
