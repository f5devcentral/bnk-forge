/**
 * AttentionCard — Shows a failure or drift item needing user action.
 * Extracted from Dashboard.tsx.
 * D-020: token-pure, no raw palette colors, no isDark ternaries.
 */

import { cn } from '@/lib/utils';
import { ChevronRight, GitCompare, XCircle } from 'lucide-react';
import { Link } from 'react-router-dom';

export interface AttentionItem {
  id: number;
  type: 'failure' | 'drift';
  project: string;
  module: string;
  message: string;
  projectId: number;
}

export function AttentionCard({ item }: { item: AttentionItem }) {
  const isFailure = item.type === 'failure';

  return (
    <Link to={`/projects/${item.projectId}`}>
      <div className={cn(
        'p-4 rounded-xl border transition-all hover:shadow-sm cursor-pointer group',
        isFailure
          ? 'bg-destructive/5 border-destructive/20 hover:border-destructive/30'
          : 'bg-warning/5 border-warning/20 hover:border-warning/30'
      )}>
        <div className="flex items-start gap-3">
          <div className={cn(
            'p-2 rounded-lg',
            isFailure ? 'bg-destructive/10' : 'bg-warning/10'
          )}>
            {isFailure ? (
              <XCircle className="h-5 w-5 text-destructive" />
            ) : (
              <GitCompare className="h-5 w-5 text-warning" />
            )}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <span className="font-semibold text-foreground">{item.module}</span>
              <span className="text-border">·</span>
              <span className="text-sm text-muted-foreground">{item.project}</span>
            </div>
            <p className="text-sm line-clamp-1 text-muted-foreground">{item.message}</p>
          </div>
          <span className={cn(
            'flex-shrink-0 flex items-center text-sm',
            isFailure ? 'text-destructive' : 'text-warning'
          )}>
            {isFailure ? 'View Error' : 'Review Drift'}
            <ChevronRight className="h-3.5 w-3.5 ml-1" />
          </span>
        </div>
      </div>
    </Link>
  );
}
