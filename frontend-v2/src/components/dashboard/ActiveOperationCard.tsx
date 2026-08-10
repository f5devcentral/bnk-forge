/**
 * ActiveOperationCard — Shows a currently running deployment operation.
 * Extracted from Dashboard.tsx.
 * D-020: token-pure, no raw palette colors, no isDark ternaries.
 */

import { Clock, ChevronRight, Loader2 } from 'lucide-react';
import { Link } from 'react-router-dom';

interface ActiveOperationCardProps {
  projectName: string;
  moduleName: string;
  taskType: string;
  projectId: number;
  time: string;
}

export function ActiveOperationCard({ projectName, moduleName, taskType, projectId, time }: ActiveOperationCardProps) {
  return (
    <Link to={`/projects/${projectId}`} className="block group">
      <div className="flex items-center gap-4 p-4 rounded-xl border border-primary/20 bg-primary/5 hover:border-primary/40 hover:shadow-sm transition-all">
        <div className="p-2 rounded-full bg-primary/10">
          <Loader2 className="h-5 w-5 animate-spin text-primary" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-semibold text-sm text-foreground">
              {taskType.charAt(0).toUpperCase() + taskType.slice(1)}
            </span>
            <span className="text-sm truncate text-muted-foreground">{moduleName}</span>
          </div>
          <span className="text-xs text-muted-foreground">{projectName}</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Clock className="h-3 w-3" />
            {time}
          </div>
          <ChevronRight className="h-4 w-4 text-primary transition-transform group-hover:translate-x-1" />
        </div>
      </div>
    </Link>
  );
}
