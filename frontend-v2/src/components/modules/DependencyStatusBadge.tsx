/* eslint-disable react-refresh/only-export-components */
/**
 * Dependency Status Badge Component
 *
 * Shows the status of a module dependency with visual indicators:
 * - Ready: Module has been successfully applied and outputs are available
 * - Pending: Module exists but hasn't been applied yet
 * - Failed: Module apply failed or module doesn't exist
 */

import { Badge } from '@/components/ui/badge';
import { CheckCircle2, Clock, XCircle, AlertCircle } from 'lucide-react';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import type { ProjectModule } from '@/types';

interface DependencyStatusBadgeProps {
  dependencyModule: ProjectModule | null;
}

type StatusVariant = 'success' | 'warning' | 'destructive' | 'muted';

export function DependencyStatusBadge({ dependencyModule }: DependencyStatusBadgeProps) {
  if (!dependencyModule) {
    return (
      <Popover>
        <PopoverTrigger asChild>
          <Badge variant="destructive" className="text-[10px] cursor-help">
            <XCircle className="h-3 w-3 mr-1" />
            Unknown
          </Badge>
        </PopoverTrigger>
        <PopoverContent className="w-64">
          <div className="space-y-1">
            <p className="font-semibold text-sm text-destructive">Dependency Not Found</p>
            <p className="text-xs text-muted-foreground">
              This dependency module no longer exists in the project.
            </p>
          </div>
        </PopoverContent>
      </Popover>
    );
  }

  const status = dependencyModule.status;

  // Determine dependency status
  let icon: React.ReactNode;
  let variant: StatusVariant;
  let statusText: string;
  let statusDescription: string;

  if (status === 'applied' || status === 'deployed') {
    icon = <CheckCircle2 className="h-3 w-3 mr-1" />;
    variant = 'success';
    statusText = 'Ready';
    statusDescription = 'Module has been applied successfully and outputs are available.';
  } else if (status === 'not_initialized' || status === 'planned') {
    icon = <Clock className="h-3 w-3 mr-1" />;
    variant = 'warning';
    statusText = 'Pending';
    statusDescription = 'Module must be applied before it can be used as a dependency.';
  } else if (status === 'apply_failed' || status === 'plan_failed' || status === 'init_failed') {
    icon = <XCircle className="h-3 w-3 mr-1" />;
    variant = 'destructive';
    statusText = 'Failed';
    statusDescription = 'Module apply failed. Fix the issue before using as a dependency.';
  } else {
    icon = <AlertCircle className="h-3 w-3 mr-1" />;
    variant = 'muted';
    statusText = 'Unknown';
    statusDescription = `Module status: ${status}`;
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Badge variant={variant} className="text-[10px] cursor-help">
          {icon}
          {dependencyModule.module_name}
        </Badge>
      </PopoverTrigger>
      <PopoverContent className="w-64">
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            {icon}
            <p className="font-semibold text-sm">{dependencyModule.module_name}</p>
          </div>
          <div className="space-y-1">
            <p className="text-xs font-medium">
              Status: {statusText}
            </p>
            <p className="text-xs text-muted-foreground">
              {statusDescription}
            </p>
          </div>
          {dependencyModule.library_module?.category && (
            <p className="text-xs text-muted-foreground">
              Category: {dependencyModule.library_module.category}
            </p>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}

/**
 * Check if all dependencies for a module are ready
 */
export function checkDependenciesReady(
  module: ProjectModule,
  allModules: ProjectModule[]
): { ready: boolean; pendingDeps: string[]; failedDeps: string[] } {
  if (!module.dependencies || module.dependencies.length === 0) {
    return { ready: true, pendingDeps: [], failedDeps: [] };
  }

  const pendingDeps: string[] = [];
  const failedDeps: string[] = [];

  module.dependencies.forEach((depId: number) => {
    const dep = allModules.find((m) => m.id === depId);

    if (!dep) {
      failedDeps.push(`Module ID ${depId}`);
      return;
    }

    const status = dep.status;

    if (status === 'applied' || status === 'deployed') {
      // Ready - do nothing
    } else if (status === 'apply_failed' || status === 'plan_failed' || status === 'init_failed') {
      failedDeps.push(dep.module_name);
    } else {
      pendingDeps.push(dep.module_name);
    }
  });

  return {
    ready: pendingDeps.length === 0 && failedDeps.length === 0,
    pendingDeps,
    failedDeps,
  };
}

/**
 * Get a human-readable error message for dependency issues
 */
export function getDependencyErrorMessage(module: ProjectModule, allModules: ProjectModule[]): string | null {
  const { ready, pendingDeps, failedDeps } = checkDependenciesReady(module, allModules);

  if (ready) {
    return null;
  }

  if (failedDeps.length > 0) {
    return `Cannot deploy ${module.module_name}: Dependencies failed - ${failedDeps.join(', ')}`;
  }

  if (pendingDeps.length > 0) {
    return `Cannot deploy ${module.module_name}: Dependencies not applied - ${pendingDeps.join(', ')}`;
  }

  return null;
}
