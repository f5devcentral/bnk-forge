/**
 * ValueJourneyBanner — GAP-004 guided value journey progress tracker
 *
 * Shows an inline banner on the Dashboard when a value journey is active.
 * Guides users through: Deploy → Validate → Benchmark → Diagnose → Export
 * D-020: token-pure, no raw palette colors, no isDark ternaries, no gradients.
 */

import { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  loadValueJourney,
  restartJourney,
  completeJourneyStep,
  type JourneyStepId,
  type ValueJourneyState,
} from '@/lib/value-journey';
import {
  generateEvidenceReport,
  downloadEvidenceReport,
} from '@/lib/evidence-export';
import {
  Rocket,
  CheckCircle2,
  ArrowRight,
  X,
  FolderGit2,
  BarChart3,
  Stethoscope,
  FileDown,
  ShieldCheck,
} from 'lucide-react';

interface JourneyStepDef {
  id: JourneyStepId;
  label: string;
  icon: React.ElementType;
  href: (state: ValueJourneyState) => string;
}

const STEPS: JourneyStepDef[] = [
  { id: 'deploy', label: 'Deploy', icon: Rocket, href: (s) => `/projects/${s.projectId}` },
  { id: 'validate', label: 'Validate', icon: ShieldCheck, href: (s) => `/projects/${s.projectId}` },
  { id: 'benchmark', label: 'Benchmark', icon: BarChart3, href: () => '/benchmarks' },
  { id: 'diagnose', label: 'Diagnose', icon: Stethoscope, href: () => '/f5bnk' },
  { id: 'export', label: 'Export', icon: FileDown, href: (s) => `/projects/${s.projectId}` },
];

export function ValueJourneyBanner() {
  const [journey, setJourney] = useState<ValueJourneyState | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    setJourney(loadValueJourney());
  }, []);

  const handleDismiss = useCallback(() => {
    restartJourney();
    setDismissed(true);
  }, []);

  const handleExport = useCallback(() => {
    if (!journey) return;
    const report = generateEvidenceReport({
      id: journey.projectId,
      name: journey.blueprintName,
    });
    downloadEvidenceReport(report);
    // Mark export step as done
    const updated = completeJourneyStep('export');
    if (updated) setJourney(updated);
  }, [journey]);

  if (!journey || dismissed) return null;

  const completedCount = Object.values(journey.steps).filter(Boolean).length;
  const totalSteps = STEPS.length;
  const allDone = completedCount === totalSteps;

  // Find the next incomplete step
  const nextStep = STEPS.find((s) => !journey.steps[s.id]);

  return (
    <div className="rounded-lg border border-primary/20 bg-primary/5 p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <FolderGit2 className="h-4 w-4 text-primary" />
          <span className="text-sm font-semibold text-foreground">
            Value Journey: {journey.blueprintName}
          </span>
          <Badge
            variant="outline"
            className={cn(
              'text-[10px]',
              allDone
                ? 'bg-success/10 text-success border-success/20'
                : 'bg-primary/10 text-primary border-primary/20'
            )}
          >
            {completedCount}/{totalSteps}
          </Badge>
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="h-6 w-6 p-0"
          onClick={handleDismiss}
          aria-label="Dismiss journey"
        >
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>

      {/* Step progress */}
      <div className="flex items-center gap-1">
        {STEPS.map((step, idx) => {
          const done = journey.steps[step.id];
          const isNext = nextStep?.id === step.id;
          const StepIcon = step.icon;

          return (
            <div key={step.id} className="flex items-center">
              <Link
                to={step.href(journey)}
                className={cn(
                  'flex items-center gap-1.5 px-2.5 py-1.5 rounded-md text-xs font-medium transition-all',
                  done
                    ? 'bg-success/10 text-success'
                    : isNext
                      ? 'bg-primary/10 text-primary ring-1 ring-primary/30'
                      : 'text-muted-foreground'
                )}
              >
                {done ? (
                  <CheckCircle2 className="h-3.5 w-3.5" />
                ) : (
                  <StepIcon className="h-3.5 w-3.5" />
                )}
                <span className="hidden sm:inline">{step.label}</span>
              </Link>
              {idx < STEPS.length - 1 && (
                <ArrowRight className="h-3 w-3 mx-0.5 flex-shrink-0 text-border" />
              )}
            </div>
          );
        })}
      </div>

      {/* Next action CTA */}
      {nextStep && !allDone && (
        <div className="mt-3 flex items-center gap-2">
          <Link to={nextStep.href(journey)}>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs gap-1.5"
            >
              Next: {nextStep.label}
              <ArrowRight className="h-3 w-3" />
            </Button>
          </Link>
          {completedCount >= 2 && (
            <Button
              size="sm"
              variant="ghost"
              onClick={handleExport}
              className="h-7 text-xs gap-1.5 text-muted-foreground hover:text-foreground"
            >
              <FileDown className="h-3 w-3" />
              Export Report
            </Button>
          )}
        </div>
      )}

      {allDone && (
        <div className="mt-3 flex items-center gap-2">
          <p className="text-xs text-success">
            Journey complete! All validation steps finished.
          </p>
          <Button
            size="sm"
            variant="outline"
            onClick={handleExport}
            className="h-7 text-xs gap-1.5"
          >
            <FileDown className="h-3 w-3" />
            Export Report
          </Button>
        </div>
      )}
    </div>
  );
}
