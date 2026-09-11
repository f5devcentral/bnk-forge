import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { getSeverityConfig } from '@/lib/health-severity';
import type { K8sCondition } from '@/types/kubernetes';

interface ConditionsListProps {
  conditions: K8sCondition[];
  emptyText?: string;
}

function conditionSeverity(status: string): 'healthy' | 'unhealthy' | 'degraded' {
  const lower = status?.toLowerCase();
  if (lower === 'true') return 'healthy';
  if (lower === 'false') return 'unhealthy';
  return 'degraded';
}

export function ConditionsList({
  conditions,
  emptyText = 'No conditions available',
}: ConditionsListProps) {
  if (!conditions || conditions.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">{emptyText}</p>
    );
  }

  return (
    <div className="space-y-2">
      {conditions.map((condition, idx) => {
        const severity = conditionSeverity(condition.status);
        const config = getSeverityConfig(severity);
        const Icon = config.icon;

        return (
          <div
            key={idx}
            className={cn(
              'rounded-md border p-2.5',
              config.border,
              config.bg,
            )}
          >
            <div className="flex items-center gap-2">
              <Icon className={cn('h-4 w-4', config.color)} />
              <span className={cn('text-sm font-medium', config.color)}>
                {condition.type}
              </span>
              <Badge
                variant={severity === 'healthy' ? 'success' : severity === 'unhealthy' ? 'destructive' : 'warning'}
                className="ml-auto text-[10px]"
              >
                {condition.status}
              </Badge>
            </div>
            {condition.reason && (
              <p className="text-xs text-muted-foreground mt-1">
                Reason: {condition.reason}
              </p>
            )}
            {condition.message && (
              <p className="text-xs text-muted-foreground mt-1">
                {condition.message}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}
