/**
 * Tests for evidence-export.ts — GAP-005
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { generateEvidenceReport, downloadEvidenceReport } from '../evidence-export';
import { startValueJourney } from '../value-journey';

describe('evidence-export', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  describe('generateEvidenceReport', () => {
    it('creates a report with correct structure', () => {
      const report = generateEvidenceReport({ id: 1, name: 'Test Project' });

      expect(report.report_type).toBe('bnk-forge-validation-report');
      expect(report.version).toBe('1.0.0');
      expect(report.generated_at).toBeTruthy();
      expect(report.environment.forge_url).toBeTruthy();
      expect(report.project).toEqual({ id: 1, name: 'Test Project' });
    });

    it('includes journey data when active', () => {
      startValueJourney({
        projectId: 42,
        blueprintSlug: 'aws-k8s-foundation',
        blueprintName: 'AWS K8s',
      });

      const report = generateEvidenceReport({ id: 42, name: 'AWS K8s' });

      expect(report.journey).not.toBeNull();
      expect(report.journey!.blueprint_slug).toBe('aws-k8s-foundation');
      expect(report.journey!.project_id).toBe(42);
      expect(report.journey!.steps_completed).toContain('deploy');
      expect(report.journey!.steps_remaining).toContain('validate');
    });

    it('sets journey to null when no journey active', () => {
      const report = generateEvidenceReport({ id: 1, name: 'Test' });
      expect(report.journey).toBeNull();
    });

    it('sets project to null when no project data provided', () => {
      const report = generateEvidenceReport();
      expect(report.project).toBeNull();
    });
  });

  describe('downloadEvidenceReport', () => {
    it('triggers a file download', () => {
      const createObjectURL = vi.fn(() => 'blob:fake-url');
      const revokeObjectURL = vi.fn();
      const click = vi.fn();

      // Mock URL and createElement
      Object.defineProperty(window, 'URL', {
        value: { createObjectURL, revokeObjectURL },
        writable: true,
      });

      const origCreateElement = document.createElement.bind(document);
      vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
        if (tag === 'a') {
          const el = origCreateElement('a');
          el.click = click;
          return el;
        }
        return origCreateElement(tag);
      });

      const report = generateEvidenceReport({ id: 1, name: 'My Project' });
      downloadEvidenceReport(report);

      expect(createObjectURL).toHaveBeenCalledOnce();
      expect(click).toHaveBeenCalledOnce();
      expect(revokeObjectURL).toHaveBeenCalledOnce();
    });
  });
});
