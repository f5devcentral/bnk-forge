/**
 * WafWizardFrame — shared tab-based wizard shell used by all 4 WAF CRD create/edit forms.
 *
 * Design:
 *  - Free-navigation tabs (click any tab at any time)
 *  - A dot indicator on tab labels that have unfilled required fields
 *  - A single "Create" / "Save" button (disabled until all required fields are filled)
 *  - No "Next" button — users navigate tabs freely
 */

import { cn } from '@/lib/utils';
import { useTheme } from '@/context/ThemeContext';
import { AlertTriangle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { RefreshCw } from 'lucide-react';

export interface WafWizardTab {
  key: string;
  label: string;
  /** Returns a list of validation error strings for this tab. Empty = valid. */
  validate: () => string[];
  content: React.ReactNode;
}

interface WafWizardFrameProps {
  tabs: WafWizardTab[];
  activeTab: string;
  onTabChange: (key: string) => void;
  allErrors: string[];
  isPending: boolean;
  submitLabel?: string;
  onSubmit: () => void;
  onCancel: () => void;
  submitError?: string | null;
  statusNote?: React.ReactNode;
  /** Optional toolbar slot (Clone / Drafts / Import) — shown above tab bar */
  toolbar?: React.ReactNode;
}

export function WafWizardFrame({
  tabs, activeTab, onTabChange, allErrors, isPending, submitLabel = 'Create',
  onSubmit, onCancel, submitError, statusNote, toolbar,
}: WafWizardFrameProps) {
  const { isDark } = useTheme();
  const active = tabs.find(t => t.key === activeTab) ?? tabs[0];

  return (
    <div className="flex flex-col gap-0">
      {toolbar && <div className="px-4 pt-3 pb-0">{toolbar}</div>}
      {/* Tab bar */}
      <div className={cn(
        'flex gap-0.5 border-b flex-wrap',
        isDark ? 'border-border bg-card/30' : 'border-border bg-muted/50'
      )}>
        {tabs.map(tab => {
          const errors = tab.validate();
          const hasErrors = errors.length > 0;
          const isActive = tab.key === activeTab;
          return (
            <button
              key={tab.key}
              onClick={() => onTabChange(tab.key)}
              className={cn(
                'relative flex items-center gap-1.5 px-4 py-2.5 text-xs font-medium border-b-2 transition-colors whitespace-nowrap',
                isActive
                  ? isDark
                    ? 'border-primary/50 text-white bg-card'
                    : 'border-primary text-foreground bg-white'
                  : isDark
                    ? 'border-transparent text-muted-foreground hover:text-foreground/90 hover:bg-card/50'
                    : 'border-transparent text-muted-foreground hover:text-foreground/80 hover:bg-white'
              )}
            >
              {tab.label}
              {/* Red dot = tab has unfilled required fields */}
              {hasErrors && (
                <span className="h-1.5 w-1.5 rounded-full bg-destructive shrink-0" title={errors.join('; ')} />
              )}
            </button>
          );
        })}
      </div>

      {/* Active tab content */}
      <div className="p-4 min-h-[320px] overflow-y-auto max-h-[60vh]">
        {active?.content}
      </div>

      {/* Footer */}
      <div className={cn(
        'border-t px-4 py-3 flex items-center justify-between gap-3',
        isDark ? 'border-border bg-card/30' : 'border-border bg-muted/50'
      )}>
        <div className="flex-1">
          {submitError && (
            <div className="flex items-start gap-1.5 text-xs text-destructive dark:text-destructive/80">
              <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
              <span>{submitError}</span>
            </div>
          )}
          {statusNote}
          {allErrors.length > 0 && !submitError && (
            <p className="text-xs text-muted-foreground">
              {allErrors.length} required field{allErrors.length > 1 ? 's' : ''} still needed (tabs with <span className="inline-block h-1.5 w-1.5 rounded-full bg-destructive mx-0.5 align-middle" /> dots).
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="ghost" onClick={onCancel} disabled={isPending}>Cancel</Button>
          <Button
            size="sm"
            className="bg-primary hover:bg-primary/90 min-w-24"
            onClick={onSubmit}
            disabled={allErrors.length > 0 || isPending}
          >
            {isPending ? (
              <><RefreshCw className="h-3.5 w-3.5 mr-1.5 animate-spin" />Working…</>
            ) : submitLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
