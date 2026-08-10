import { SectionCard } from '@/components/ui/section-card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { useRecentErrors } from '@/hooks/useSystem';
import { AlertCircle, ExternalLink } from 'lucide-react';
import { Link } from 'react-router-dom';
import { formatTimeAgo } from '@/lib/time-utils';

export function RecentErrorsPanel() {
  const { data, isLoading, error } = useRecentErrors(10);

  if (isLoading) {
    return (
      <SectionCard title="Recent Errors">
        <p className="text-sm text-muted-foreground">Loading error history...</p>
      </SectionCard>
    );
  }

  if (error) {
    return (
      <SectionCard title="Recent Errors">
        <p className="text-sm text-destructive">Failed to load recent errors</p>
      </SectionCard>
    );
  }

  if (!data) return null;

  const hasErrors = data.errors.length > 0;

  return (
    <SectionCard title="Recent Errors">
      <div className="flex items-center justify-between mb-4">
        <p className="text-sm text-muted-foreground">
          Last 10 failed tasks (auto-refreshes every 15 seconds)
        </p>
        <Badge variant={hasErrors ? 'destructive' : 'secondary'}>
          {data.total} Total
        </Badge>
      </div>
      {!hasErrors ? (
        <div className="text-center py-8 text-muted-foreground">
          <p>No recent errors</p>
        </div>
      ) : (
        <div className="space-y-3">
          {data.errors.map((err) => (
            <div
              key={err.task_id}
              className="flex items-start justify-between gap-4 p-3 border border-border rounded-lg hover:bg-muted/50 transition-colors"
            >
              <div className="flex items-start gap-3 flex-1 min-w-0">
                <AlertCircle className="h-4 w-4 text-destructive mt-0.5 flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <Badge variant="outline" className="text-xs">
                      {err.type}
                    </Badge>
                    <span className="text-sm font-medium">{err.project}</span>
                    {err.module && (
                      <span className="text-sm text-muted-foreground">
                        / {err.module}
                      </span>
                    )}
                  </div>
                  <p className="text-sm text-muted-foreground mt-1 line-clamp-2">
                    {err.error}
                  </p>
                  <p className="text-xs text-muted-foreground mt-1">
                    {formatTimeAgo(err.timestamp)}
                    {err.exit_code !== null && ` • Exit code: ${err.exit_code}`}
                  </p>
                </div>
              </div>
              <Link to={`/tasks/${err.task_id}`}>
                <Button variant="outline" size="sm" className="flex-shrink-0">
                  <ExternalLink className="h-4 w-4" />
                </Button>
              </Link>
            </div>
          ))}
        </div>
      )}
    </SectionCard>
  );
}
