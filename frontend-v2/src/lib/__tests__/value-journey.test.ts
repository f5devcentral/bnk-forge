/**
 * Tests for value-journey.ts — GAP-004
 */
import { describe, it, expect, beforeEach } from 'vitest';
import {
  loadValueJourney,
  startValueJourney,
  completeJourneyStep,
  restartJourney,
  VALUE_JOURNEY_STORAGE_KEY,
  type ValueJourneyState,
} from '../value-journey';

describe('value-journey', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  describe('loadValueJourney', () => {
    it('returns null when nothing is stored', () => {
      expect(loadValueJourney()).toBeNull();
    });

    it('returns null for invalid JSON', () => {
      localStorage.setItem(VALUE_JOURNEY_STORAGE_KEY, 'not-json');
      expect(loadValueJourney()).toBeNull();
    });

    it('returns null for malformed state (missing required fields)', () => {
      localStorage.setItem(VALUE_JOURNEY_STORAGE_KEY, JSON.stringify({ foo: 'bar' }));
      expect(loadValueJourney()).toBeNull();
    });

    it('loads valid state and fills missing step keys', () => {
      const state: ValueJourneyState = {
        projectId: 1,
        blueprintSlug: 'test-slug',
        blueprintName: 'Test Blueprint',
        startedAt: '2026-01-01T00:00:00Z',
        updatedAt: '2026-01-01T00:00:00Z',
        steps: { deploy: true, validate: false, benchmark: false, diagnose: false, export: false },
      };
      localStorage.setItem(VALUE_JOURNEY_STORAGE_KEY, JSON.stringify(state));

      const loaded = loadValueJourney();
      expect(loaded).not.toBeNull();
      expect(loaded!.projectId).toBe(1);
      expect(loaded!.blueprintSlug).toBe('test-slug');
      expect(loaded!.steps.deploy).toBe(true);
      expect(loaded!.steps.validate).toBe(false);
    });
  });

  describe('startValueJourney', () => {
    it('creates a new journey with deploy step completed', () => {
      const state = startValueJourney({
        projectId: 42,
        blueprintSlug: 'aws-k8s-foundation',
        blueprintName: 'AWS EKS Foundation',
      });

      expect(state.projectId).toBe(42);
      expect(state.blueprintSlug).toBe('aws-k8s-foundation');
      expect(state.blueprintName).toBe('AWS EKS Foundation');
      expect(state.steps.deploy).toBe(true);
      expect(state.steps.validate).toBe(false);
      expect(state.steps.benchmark).toBe(false);
      expect(state.steps.diagnose).toBe(false);
      expect(state.steps.export).toBe(false);
    });

    it('persists to localStorage', () => {
      startValueJourney({
        projectId: 1,
        blueprintSlug: 'test',
        blueprintName: 'Test',
      });

      const raw = localStorage.getItem(VALUE_JOURNEY_STORAGE_KEY);
      expect(raw).not.toBeNull();
      const parsed = JSON.parse(raw!);
      expect(parsed.projectId).toBe(1);
    });
  });

  describe('completeJourneyStep', () => {
    it('returns null when no journey exists', () => {
      expect(completeJourneyStep('validate')).toBeNull();
    });

    it('marks the specified step as complete', () => {
      startValueJourney({
        projectId: 1,
        blueprintSlug: 'test',
        blueprintName: 'Test',
      });

      const updated = completeJourneyStep('validate');
      expect(updated).not.toBeNull();
      expect(updated!.steps.deploy).toBe(true);
      expect(updated!.steps.validate).toBe(true);
      expect(updated!.steps.benchmark).toBe(false);
    });

    it('persists updated state', () => {
      startValueJourney({
        projectId: 1,
        blueprintSlug: 'test',
        blueprintName: 'Test',
      });

      completeJourneyStep('benchmark');

      const loaded = loadValueJourney();
      expect(loaded!.steps.benchmark).toBe(true);
    });
  });

  describe('restartJourney', () => {
    it('clears localStorage', () => {
      startValueJourney({
        projectId: 1,
        blueprintSlug: 'test',
        blueprintName: 'Test',
      });

      expect(loadValueJourney()).not.toBeNull();

      restartJourney();

      expect(loadValueJourney()).toBeNull();
    });
  });
});
