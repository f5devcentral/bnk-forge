/**
 * ActivityItem — Single row in the activity feed.
 * Extracted from Dashboard.tsx.
 * D-020: token-pure, no raw palette colors, no isDark ternaries.
 */

import { cn } from '@/lib/utils';
import { CheckCircle2, Loader2, XCircle } from 'lucide-react';

interface ActivityItemProps {
  action: string;
  module: string;
  project: string;
  time: string;
  status: 'success' | 'progress' | 'failed';
}

export function ActivityItem({ action, module, project, time, status }: ActivityItemProps) {
  const statusConfig = {
    success: { icon: CheckCircle2, color: 'text-success', bg: 'bg-success/10' },
    progress: { icon: Loader2, color: 'text-primary', bg: 'bg-primary/10' },
    failed: { icon: XCircle, color: 'text-destructive', bg: 'bg-destructive/10' },
  };

  const config = statusConfig[status];
  const StatusIcon = config.icon;

  return (
    <div className="flex items-center gap-3 py-2 px-3 rounded-lg hover:bg-muted/50 transition-colors">
      <div className={cn('p-1.5 rounded-full', config.bg)}>
        <StatusIcon className={cn('h-3.5 w-3.5', config.color, status === 'progress' && 'animate-spin')} />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="font-medium text-xs text-foreground">{action}</span>
          <span className="text-xs truncate text-muted-foreground">{module}</span>
        </div>
        <span className="text-[11px] text-muted-foreground">{project}</span>
      </div>
      <span className="text-[11px] flex-shrink-0 text-muted-foreground">{time}</span>
    </div>
  );
}
