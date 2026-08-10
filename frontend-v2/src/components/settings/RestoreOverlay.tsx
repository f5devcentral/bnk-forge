import { Loader2 } from 'lucide-react';

interface RestoreOverlayProps {
  active: boolean;
  tableCounts?: Record<string, number>;
  warnings?: string[];
  migrationsApplied?: string[];
}

export function RestoreOverlay({ active, tableCounts, warnings, migrationsApplied }: RestoreOverlayProps) {
  if (!active) return null;

  const nonZeroCounts = tableCounts
    ? Object.entries(tableCounts).filter(([, v]) => v > 0)
    : [];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 backdrop-blur-sm">
      <div className="flex flex-col items-center gap-4 rounded-lg border bg-card p-8 shadow-lg max-w-md">
        <Loader2 className="h-10 w-10 animate-spin text-primary" />
        <h2 className="text-lg font-semibold">Restore in progress</h2>

        {nonZeroCounts.length > 0 && (
          <ul className="text-sm text-muted-foreground space-y-1">
            {nonZeroCounts.map(([table, count]) => (
              <li key={table}>
                {count} {table.replace(/_/g, ' ')}
              </li>
            ))}
          </ul>
        )}

        {migrationsApplied && migrationsApplied.length > 0 && (
          <p className="text-sm text-muted-foreground">
            {migrationsApplied.length} migration{migrationsApplied.length !== 1 ? 's' : ''} applied
          </p>
        )}

        {warnings && warnings.length > 0 && (
          <p className="text-sm text-warning">
            {warnings.join(', ')}
          </p>
        )}

        <p className="text-sm text-muted-foreground">
          The system is restarting. This page will reload automatically...
        </p>
      </div>
    </div>
  );
}
