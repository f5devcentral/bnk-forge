/**
 * Evidence Export — GAP-005
 *
 * Assembles a stakeholder-ready validation report from project data.
 * Generates a downloadable JSON file with environment fingerprint,
 * deployment status, and validation summary.
 */

import { loadValueJourney } from './value-journey';

export interface EvidenceReport {
  report_type: 'bnk-forge-validation-report';
  version: '1.0.0';
  generated_at: string;
  environment: {
    forge_url: string;
    user_agent: string;
  };
  journey: {
    blueprint_name: string;
    blueprint_slug: string;
    project_id: number;
    started_at: string;
    steps_completed: string[];
    steps_remaining: string[];
  } | null;
  project: {
    id: number;
    name: string;
    project_type?: string;
    region?: string;
    module_count?: number;
    deployed_count?: number;
  } | null;
}

export function generateEvidenceReport(projectData?: {
  id: number;
  name: string;
  project_type?: string;
  region?: string;
  module_count?: number;
  deployed_count?: number;
}): EvidenceReport {
  const journey = loadValueJourney();

  const journeySection = journey
    ? {
        blueprint_name: journey.blueprintName,
        blueprint_slug: journey.blueprintSlug,
        project_id: journey.projectId,
        started_at: journey.startedAt,
        steps_completed: Object.entries(journey.steps)
          .filter(([, done]) => done)
          .map(([step]) => step),
        steps_remaining: Object.entries(journey.steps)
          .filter(([, done]) => !done)
          .map(([step]) => step),
      }
    : null;

  return {
    report_type: 'bnk-forge-validation-report',
    version: '1.0.0',
    generated_at: new Date().toISOString(),
    environment: {
      forge_url: window.location.origin,
      user_agent: navigator.userAgent,
    },
    journey: journeySection,
    project: projectData ?? null,
  };
}

export function downloadEvidenceReport(report: EvidenceReport): void {
  const content = JSON.stringify(report, null, 2);
  const blob = new Blob([content], { type: 'application/json' });
  const url = URL.createObjectURL(blob);

  const timestamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
  const projectSlug = report.project?.name?.toLowerCase().replace(/\s+/g, '-') ?? 'report';
  const filename = `bnk-forge-${projectSlug}-${timestamp}.json`;

  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
