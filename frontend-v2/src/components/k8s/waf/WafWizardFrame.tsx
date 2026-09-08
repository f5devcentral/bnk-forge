/**
 * WafWizardFrame — split-pane layout with vertical left sidebar navigation.
 * Left: vertical tab list. Right: scrollable content. Bottom: submit footer.
 */
import { cn } from '@/lib/utils';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';

export interface WafWizardTab {
  key: string;
  label: string;
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
  toolbar?: React.ReactNode;
}

export function WafWizardFrame({
  tabs, activeTab, onTabChange, allErrors, isPending, submitLabel = 'Create',
  onSubmit, onCancel, submitError, statusNote, toolbar,
}: WafWizardFrameProps) {
  const active = tabs.find(t => t.key === activeTab) ?? tabs[0];

  return (
    <div className="flex flex-col h-full min-h-0">
      {toolbar && (
        <div className="px-4 pt-3 pb-2 border-b border-border shrink-0 bg-muted/20">
          {toolbar}
        </div>
      )}

      {/* Split pane */}
      <div className="flex flex-1 min-h-0">
        {/* Vertical sidebar */}
        <nav className="w-44 shrink-0 border-r border-border flex flex-col py-2 bg-muted/10">
          {tabs.map(tab => {
            const errors = tab.validate();
            const isActive = tab.key === activeTab;
            return (
              <button
                key={tab.key}
                onClick={() => onTabChange(tab.key)}
                className={cn(
                  'flex items-center justify-between gap-2 px-3 py-2 mx-2 rounded-md text-xs font-medium text-left transition-colors',
                  isActive
                    ? 'bg-primary/10 text-primary'
                    : 'text-muted-foreground hover:text-foreground hover:bg-accent',
                )}
              >
                <span className="truncate">{tab.label}</span>
                {errors.length > 0 && (
                  <span className="h-1.5 w-1.5 rounded-full bg-destructive shrink-0" title={errors.join('; ')} />
                )}
              </button>
            );
          })}
        </nav>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-5 min-h-0">
          {active?.content}
        </div>
      </div>

      {/* Footer */}
      <div className="border-t border-border px-4 py-3 flex items-center justify-between gap-3 shrink-0 bg-muted/10">
        <div className="flex-1 min-w-0">
          {submitError && (
            <div className="flex items-start gap-1.5 text-xs text-destructive">
              <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
              <span className="break-all">{submitError}</span>
            </div>
          )}
          {statusNote}
          {allErrors.length > 0 && !submitError && (
            <p className="text-xs text-muted-foreground">
              {allErrors.length} required field{allErrors.length > 1 ? 's' : ''} still needed
              {' '}(tabs with <span className="inline-block h-1.5 w-1.5 rounded-full bg-destructive mx-0.5 align-middle" /> dots).
            </p>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <Button size="sm" variant="ghost" onClick={onCancel} disabled={isPending}>Cancel</Button>
          <Button
            size="sm"
            className="bg-primary hover:bg-primary/90 min-w-24"
            onClick={onSubmit}
            disabled={allErrors.length > 0 || isPending}
          >
            {isPending ? <><RefreshCw className="h-3.5 w-3.5 mr-1.5 animate-spin" />Working…</> : submitLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
