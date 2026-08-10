import { useState, useEffect, useRef, useCallback } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { Database, Info, Loader2 } from 'lucide-react';
import { api } from '@/lib/api';
import type { ProjectModule } from '@/types';

interface StateStatusIndicatorProps {
  module: ProjectModule;
}

/**
 * PERF-015: Hook to detect if element is visible using IntersectionObserver
 */
function useIsVisible(ref: React.RefObject<HTMLElement | null>): boolean {
  const [isVisible, setIsVisible] = useState(false);

  useEffect(() => {
    if (!ref.current) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setIsVisible(true);
          // Once visible, disconnect - we only need to load once
          observer.disconnect();
        }
      },
      { threshold: 0.1 } // Trigger when 10% visible
    );

    observer.observe(ref.current);
    return () => observer.disconnect();
  }, [ref]);

  return isVisible;
}

export function StateStatusIndicator({ module }: StateStatusIndicatorProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const isVisible = useIsVisible(containerRef);
  const [stateInfo, setStateInfo] = useState<{
    state_exists: boolean;
    serial?: number;
    lineage?: string;
    resources_count?: number;
    state_modified_at?: string;
    status?: string;
  } | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const [hasFetched, setHasFetched] = useState(false);

  const loadStateInfo = useCallback(async () => {
    setIsLoading(true);
    try {
      const info = await api.getModuleStateInfo(module.id);
      setStateInfo(info);
    } catch {
      // Error handled gracefully - component renders empty when stateInfo is null
    } finally {
      setIsLoading(false);
    }
  }, [module.id]);

  // PERF-015: Only fetch when visible AND module is initialized
  useEffect(() => {
    if (isVisible && !hasFetched && module.status && module.status !== 'not_initialized') {
      loadStateInfo();
      setHasFetched(true);
    }
  }, [isVisible, hasFetched, module.id, module.status, loadStateInfo]);

  // Don't show anything if module not initialized yet
  if (!module.status || module.status === 'not_initialized') {
    return <div ref={containerRef} />;
  }

  // PERF-015: Show placeholder while waiting to become visible
  if (!isVisible || isLoading) {
    return (
      <div ref={containerRef} className="flex items-center gap-1 text-xs text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" />
        <span>Checking state...</span>
      </div>
    );
  }

  if (!stateInfo) {
    return <div ref={containerRef} />;
  }

  const { state_exists, serial, lineage, resources_count, state_modified_at, status } = stateInfo;

  // Check if module is deployed but state file is missing (pre-persistence deployments)
  const isDeployedWithoutState = (status === 'applied' || status === 'deployed') && !state_exists;

  return (
    <TooltipProvider>
      <Tooltip open={showDetails} onOpenChange={setShowDetails}>
        <TooltipTrigger asChild>
          <div className="flex items-center gap-1">
            <Badge
              variant={state_exists ? "success" : isDeployedWithoutState ? "destructive" : "secondary"}
              className="text-xs flex items-center gap-1"
            >
              <Database className="h-3 w-3" />
              {state_exists ? (
                <>
                  State #{serial || 0}
                  {resources_count !== undefined && ` (${resources_count} resources)`}
                </>
              ) : isDeployedWithoutState ? (
                'State missing - re-apply'
              ) : (
                'No state file'
              )}
            </Badge>
            {(state_exists || isDeployedWithoutState) && (
              <Button
                variant="ghost"
                size="icon"
                className="h-4 w-4 p-0"
                onClick={() => setShowDetails(!showDetails)}
                aria-label={showDetails ? 'Hide state details' : 'Show state details'}
              >
                <Info className="h-3 w-3" aria-hidden="true" />
              </Button>
            )}
          </div>
        </TooltipTrigger>
        {state_exists ? (
          <TooltipContent className="max-w-sm">
            <div className="space-y-1 text-xs">
              <div><strong>State Serial:</strong> {serial || 'N/A'}</div>
              <div><strong>Resources:</strong> {resources_count || 0}</div>
              {lineage && (
                <div>
                  <strong>Lineage:</strong>
                  <div className="font-mono text-[10px] break-all">{lineage.substring(0, 32)}...</div>
                </div>
              )}
              {state_modified_at && (
                <div>
                  <strong>Last Modified:</strong> {new Date(state_modified_at).toLocaleString()}
                </div>
              )}
              <div className="text-muted-foreground mt-2">
                State file exists and is tracked. Safe to destroy infrastructure.
              </div>
            </div>
          </TooltipContent>
        ) : isDeployedWithoutState ? (
          <TooltipContent className="max-w-sm">
            <div className="space-y-1 text-xs">
              <div className="text-warning font-semibold">State File Missing</div>
              <div className="text-muted-foreground mt-2">
                Infrastructure exists in AWS but state file was lost before Docker volume was added.
                Outputs are preserved in database.
              </div>
              <div className="mt-2">
                <strong>Impact:</strong> Cannot destroy infrastructure without state file.
              </div>
              <div className="mt-2 text-destructive font-semibold">
                DO NOT run Apply — resources already exist in AWS.
              </div>
              <div className="mt-2">
                <strong>Recovery:</strong> Use "Recover State" button in the 3-dot menu to rebuild state from existing infrastructure.
              </div>
            </div>
          </TooltipContent>
        ) : null}
      </Tooltip>
    </TooltipProvider>
  );
}
