/**
 * State Info Popover Component
 * Shows state information when clicking a module status badge.
 * CP-011: Only fetch state when popover is opened (not on mount)
 */

import { useState } from 'react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import {
  Database, HardDrive, Clock, Hash, Layers,
  AlertTriangle,
} from 'lucide-react';
import { getModuleStatusColor } from '@/lib/status-colors';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { formatTimeAgo } from '@/lib/time-utils';
import { statusConfig, defaultStatus } from './StatusConfig';
import type { ProjectModule } from '@/types';

export function StateInfoPopover({ module }: { module: ProjectModule }) {
  const [isOpen, setIsOpen] = useState(false);
  const { data: stateInfo } = useQuery({
    queryKey: ['module-state', module.id],
    queryFn: () => api.getModuleState(module.id),
    enabled: isOpen && !!module.id,
    staleTime: 5 * 60 * 1000,
  });

  const status = statusConfig[module.status] || defaultStatus;
  const hasState = stateInfo?.metadata?.serial > 0;
  const isDeploying =
    module.status === 'initializing' ||
    module.status === 'planning' ||
    module.status === 'applying' ||
    module.status === 'destroying';

  const formatDate = (dateStr: string | null | undefined) => formatTimeAgo(dateStr) || '—';

  return (
    <Popover onOpenChange={setIsOpen}>
      <PopoverTrigger asChild>
        <button className="focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2 rounded">
          <div className="flex flex-col items-start gap-0.5">
            <Badge
              variant="secondary"
              data-testid="module-status"
              className={cn(
                'text-xs gap-1 cursor-pointer hover:opacity-80 transition-opacity',
                getModuleStatusColor(module.status)
              )}
            >
              {status.icon}
              {status.label}
            </Badge>
            {isDeploying && module.stage_detail && (
              <span className="text-xs text-muted-foreground ml-2 animate-pulse">
                {module.stage_detail}
              </span>
            )}
          </div>
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-72 p-0" align="start">
        {/* Header */}
        <div className="px-4 py-3 border-b border-border">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Database className={cn('h-4 w-4', hasState ? 'text-success' : 'text-muted-foreground')} />
              <span className="font-semibold text-sm">State Information</span>
            </div>
            {hasState ? (
              <Badge variant="success" className="text-xs">
                State #{module.state_serial}
              </Badge>
            ) : (
              <Badge variant="muted" className="text-xs">
                No State
              </Badge>
            )}
          </div>
        </div>

        {/* Content */}
        <div className="p-4 space-y-3">
          {hasState ? (
            <>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Layers className="h-3.5 w-3.5 text-muted-foreground" />
                  <span className="text-sm text-muted-foreground">Resources</span>
                </div>
                <span className="text-sm font-medium text-foreground">
                  {module.resource_count || 0} managed
                </span>
              </div>
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Hash className="h-3.5 w-3.5 text-muted-foreground" />
                  <span className="text-sm text-muted-foreground">Serial</span>
                </div>
                <span className="text-sm font-mono text-foreground">
                  {module.state_serial}
                </span>
              </div>
              {module.state_size && (
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <HardDrive className="h-3.5 w-3.5 text-muted-foreground" />
                    <span className="text-sm text-muted-foreground">Size</span>
                  </div>
                  <span className="text-sm font-mono text-foreground">
                    {(module.state_size / 1024).toFixed(1)} KB
                  </span>
                </div>
              )}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Clock className="h-3.5 w-3.5 text-muted-foreground" />
                  <span className="text-sm text-muted-foreground">Modified</span>
                </div>
                <span className="text-sm text-foreground">
                  {formatDate(module.updated_at)}
                </span>
              </div>
              <div className="mt-3 p-2 rounded text-xs font-mono break-all bg-muted/50 text-muted-foreground">
                /app/state/{module.project_id}/{module.id}/terraform.tfstate
              </div>
            </>
          ) : (
            <div className="text-center py-4">
              <AlertTriangle className="h-8 w-8 mx-auto mb-2 text-muted-foreground" />
              <p className="text-sm text-muted-foreground">
                {module.status === 'not_initialized'
                  ? 'Initialize this module to create state'
                  : 'No state file exists yet'}
              </p>
            </div>
          )}
        </div>

        {/* Actions section removed — View State and Recover are not yet wired up */}
      </PopoverContent>
    </Popover>
  );
}
