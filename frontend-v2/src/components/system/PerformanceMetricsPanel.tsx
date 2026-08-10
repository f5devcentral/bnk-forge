import { SectionCard } from '@/components/ui/section-card';
import { Badge } from '@/components/ui/badge';
import { usePerformanceMetrics } from '@/hooks/useSystem';

export function PerformanceMetricsPanel() {
  const { data, isLoading, error } = usePerformanceMetrics();

  if (isLoading) {
    return (
      <SectionCard title="Performance Metrics">
        <p className="text-sm text-muted-foreground">Loading performance data...</p>
      </SectionCard>
    );
  }

  if (error) {
    return (
      <SectionCard title="Performance Metrics">
        <p className="text-sm text-destructive">Failed to load performance metrics</p>
      </SectionCard>
    );
  }

  if (!data) return null;

  const hasFailedRequests = data.api.failed_requests_last_hour > 0;
  const hasLongRunningTask = data.tasks.longest_running?.duration != null;
  const avgResponseTime = data.api.avg_response_time_ms;
  const databaseSize = data.database.size_mb;
  const avgTaskDuration = data.tasks.avg_duration_seconds;

  return (
    <SectionCard title="Performance Metrics">
      <p className="text-sm text-muted-foreground mb-6">
        System performance indicators (auto-refreshes every 30 seconds)
      </p>
      <div className="space-y-6">
        {/* API Performance */}
        <div className="space-y-3">
          <h3 className="font-semibold">API Performance</h3>
          <div className="grid grid-cols-3 gap-4">
            <div>
              <p className="text-2xl font-bold">
                {avgResponseTime != null ? `${avgResponseTime.toFixed(0)}ms` : '—'}
              </p>
              <p className="text-sm text-muted-foreground">Avg Response Time</p>
            </div>
            <div>
              <p className="text-2xl font-bold">{data.api.requests_last_hour}</p>
              <p className="text-sm text-muted-foreground">Requests/Hour</p>
            </div>
            <div>
              <div className="flex items-center gap-2">
                <p className="text-2xl font-bold">{data.api.failed_requests_last_hour}</p>
                {hasFailedRequests && (
                  <Badge variant="destructive" className="text-xs">
                    Errors
                  </Badge>
                )}
              </div>
              <p className="text-sm text-muted-foreground">Failed Requests</p>
            </div>
          </div>
        </div>

        {/* Database Performance */}
        <div className="space-y-3 pt-4 border-t border-border">
          <h3 className="font-semibold">Database</h3>
          <div className="grid grid-cols-3 gap-4">
            <div>
              <p className="text-2xl font-bold">{databaseSize != null ? `${databaseSize.toFixed(1)} MB` : '—'}</p>
              <p className="text-sm text-muted-foreground">Database Size</p>
            </div>
            <div>
              <p className="text-2xl font-bold">{data.database.connections}</p>
              <p className="text-sm text-muted-foreground">Connections</p>
            </div>
            <div>
              <p className="text-2xl font-bold">{data.database.slow_queries_last_hour}</p>
              <p className="text-sm text-muted-foreground">Slow Queries</p>
            </div>
          </div>
        </div>

        {/* Task Performance */}
        <div className="space-y-3 pt-4 border-t border-border">
          <h3 className="font-semibold">Task Execution</h3>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <p className="text-2xl font-bold">
                {avgTaskDuration != null ? `${avgTaskDuration.toFixed(1)}s` : '—'}
              </p>
              <p className="text-sm text-muted-foreground">Avg Duration</p>
            </div>
            <div>
              {hasLongRunningTask ? (
                <>
                  <div className="flex items-center gap-2">
                      <p className="text-2xl font-bold">
                      {data.tasks.longest_running!.duration!.toFixed(0)}s
                    </p>
                    <Badge variant="warning" className="text-xs">
                      {data.tasks.longest_running!.type}
                    </Badge>
                  </div>
                  <p className="text-sm text-muted-foreground">Longest Running</p>
                </>
              ) : (
                <>
                  <p className="text-2xl font-bold text-muted-foreground">-</p>
                  <p className="text-sm text-muted-foreground">No Active Tasks</p>
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </SectionCard>
  );
}
