import { SectionCard } from '@/components/ui/section-card';
import { Badge } from '@/components/ui/badge';
import { useQueueMetrics } from '@/hooks/useSystem';
import type { QueueInfo } from '@/types/system';

interface QueueItemProps {
  name: string;
  queue: QueueInfo;
}

function QueueItem({ name, queue }: QueueItemProps) {
  const total = queue.pending + queue.active;
  const activePercent = total > 0 ? (queue.active / total) * 100 : 0;

  // Warn if queue depth is high
  const isHighQueue = queue.pending > 10;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="font-medium">{name}</span>
          {isHighQueue && (
            <Badge variant="warning" className="text-xs">
              High
            </Badge>
          )}
        </div>
        <span className="text-sm text-muted-foreground">
          {queue.pending} pending, {queue.active} active
        </span>
      </div>
      <div className="h-2 bg-muted rounded-full overflow-hidden">
        <div
          className="h-full bg-primary transition-all"
          style={{ width: `${activePercent}%` }}
        />
      </div>
    </div>
  );
}

export function QueueMetricsPanel() {
  const { data, isLoading, error } = useQueueMetrics();

  if (isLoading) {
    return (
      <SectionCard title="Task Queue Status">
        <p className="text-sm text-muted-foreground">Loading queue metrics...</p>
      </SectionCard>
    );
  }

  if (error) {
    return (
      <SectionCard title="Task Queue Status">
        <p className="text-sm text-destructive">Failed to load queue metrics</p>
      </SectionCard>
    );
  }

  if (!data) return null;

  const hasOfflineWorkers = data.workers.offline > 0;
  const hasNoWorkers = data.workers.total === 0;

  return (
    <SectionCard title="Task Queue Status">
      <p className="text-sm text-muted-foreground mb-6">
        Queue depths and worker status (auto-refreshes every 5 seconds)
      </p>
      <div className="space-y-6">
        {/* Queue Status */}
        <div className="space-y-4">
          <QueueItem name="Default Queue" queue={data.queues.default} />
          <QueueItem name="OpenTofu Queue" queue={data.queues.opentofu} />
        </div>

        {/* Worker Status */}
        <div className="pt-4 border-t border-border">
          <div className="flex items-center justify-between">
            <span className="font-medium">Workers</span>
            <div className="flex items-center gap-2">
              <span className="text-sm">
                {data.workers.active} active
              </span>
              {hasOfflineWorkers && (
                <Badge variant="destructive">
                  {data.workers.offline} offline
                </Badge>
              )}
              {hasNoWorkers && (
                <Badge variant="destructive">
                  No workers available
                </Badge>
              )}
            </div>
          </div>
        </div>

        {/* Task Summary */}
        <div className="pt-4 border-t border-border grid grid-cols-3 gap-4 text-center">
          <div>
            <p className="text-2xl font-bold">{data.tasks.pending}</p>
            <p className="text-sm text-muted-foreground">Pending</p>
          </div>
          <div>
            <p className="text-2xl font-bold">{data.tasks.active}</p>
            <p className="text-sm text-muted-foreground">Active</p>
          </div>
          <div>
            <p className="text-2xl font-bold">{data.tasks.completed_last_hour}</p>
            <p className="text-sm text-muted-foreground">Last Hour</p>
          </div>
        </div>
      </div>
    </SectionCard>
  );
}
