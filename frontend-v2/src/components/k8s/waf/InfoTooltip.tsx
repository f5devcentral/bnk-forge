/**
 * InfoTooltip — small "i" icon that shows help text on hover, matching NIM's
 * per-panel help affordance: a bold panel name, a description, then the
 * Start/End of the queried window and any active Filters. Self-contained
 * (wraps its own TooltipProvider).
 */
import { createContext, useContext } from 'react';
import { Info } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipTrigger, TooltipProvider } from '@/components/ui/tooltip';

interface DashboardQueryWindow {
  start: Date;
  end: Date;
  filters: string;
}

const QueryWindowContext = createContext<DashboardQueryWindow | null>(null);

/** Provides the current dashboard time range / filters to all InfoTooltips beneath it. */
export function InfoTooltipQueryWindowProvider({ value, children }: { value: DashboardQueryWindow; children: React.ReactNode }) {
  return <QueryWindowContext.Provider value={value}>{children}</QueryWindowContext.Provider>;
}

function fmtTs(d: Date): string {
  return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function InfoTooltip({ title, text }: { title: string; text: string }) {
  const window = useContext(QueryWindowContext);

  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button type="button" className="text-muted-foreground/50 hover:text-muted-foreground transition-colors" aria-label="More info">
            <Info className="h-3.5 w-3.5" />
          </button>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-xs p-3 space-y-2">
          <p className="text-sm font-semibold text-foreground leading-snug">{title}</p>
          <p className="text-xs text-muted-foreground leading-relaxed">{text}</p>
          {window && (
            <div className="pt-2 border-t border-border space-y-0.5">
              <p className="text-[11px] text-muted-foreground/80"><span className="font-medium text-muted-foreground">Start:</span> {fmtTs(window.start)}</p>
              <p className="text-[11px] text-muted-foreground/80"><span className="font-medium text-muted-foreground">End:</span> {fmtTs(window.end)}</p>
              <p className="text-[11px] text-muted-foreground/80"><span className="font-medium text-muted-foreground">Filters:</span> {window.filters || 'None'}</p>
            </div>
          )}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
