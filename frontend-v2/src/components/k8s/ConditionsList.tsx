import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { getSeverityConfig } from '@/lib/health-severity';
import type { K8sCondition } from '@/types/kubernetes';

interface ConditionsListProps {
  conditions: K8sCondition[];
  emptyText?: string;
}

// Condition types where status=True is the bad state (Gateway API listener/route conditions).
const NEGATIVE_POLARITY_TYPES = new Set(['Conflicted', 'Degraded', 'PartiallyInvalid']);

function conditionSeverity(condition: K8sCondition): 'healthy' | 'unhealthy' | 'degraded' {
  const lower = condition.status?.toLowerCase();
  if (lower !== 'true' && lower !== 'false') return 'degraded';
  const good = NEGATIVE_POLARITY_TYPES.has(condition.type) ? lower === 'false' : lower === 'true';
  return good ? 'healthy' : 'unhealthy';
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
        const severity = conditionSeverity(condition);
        const config = getSeverityConfig(severity);
        const Icon = config.icon;

        return (
          <div key={idx} className="rounded-md border bg-muted/50 p-2.5">
            <div className="flex items-center gap-2">
              <Icon className={cn('h-4 w-4', config.color)} />
              <span className="text-sm font-medium">{condition.type}</span>
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
