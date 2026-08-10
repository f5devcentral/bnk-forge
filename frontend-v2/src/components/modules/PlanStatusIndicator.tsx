/* eslint-disable react-refresh/only-export-components */
/**
 * Plan Status Indicator Component
 * 
 * Shows the status of saved plans for a module:
 * - "Plan Available" when a valid plan exists
 * - "Plan Outdated" when variables have changed
 * - Nothing when no plan exists
 * 
 * Part of WORK-008: Frontend Plan Status Indicators
 */

import { Badge } from '@/components/ui/badge';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { useQuery } from '@tanstack/react-query';
import { FileCheck, AlertTriangle } from 'lucide-react';
import { apiClient } from '@/lib/api';
import { formatAgeFromSeconds } from '@/lib/time-utils';
import type { PlanStatus } from '@/types';

interface PlanStatusIndicatorProps {
  moduleId: number;
  compact?: boolean; // Compact mode for tight spaces
}

async function fetchPlanStatus(moduleId: number): Promise<PlanStatus> {
  const response = await apiClient.get<PlanStatus>(`/api/project-modules/${moduleId}/plan-status`);
  return response.data;
}

export function PlanStatusIndicator({ moduleId, compact = false }: PlanStatusIndicatorProps) {
  const { data: planStatus, isLoading, error } = useQuery({
    queryKey: ['plan-status', moduleId],
    queryFn: () => fetchPlanStatus(moduleId),
    staleTime: 30000, // Cache for 30s
    refetchOnWindowFocus: false,
  });

  // Don't show anything while loading or on error
  if (isLoading || error || !planStatus) {
    return null;
  }

  // Don't show anything if no plan exists
  if (!planStatus.has_plan) {
    return null;
  }

  // Plan exists - show status
  if (planStatus.plan_valid) {
    // Valid plan available
    const ageText = planStatus.plan_age_seconds
      ? formatAgeFromSeconds(planStatus.plan_age_seconds)
      : 'recently';

    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Badge variant="success" className="flex items-center gap-1">
              <FileCheck className="h-3 w-3" />
              {!compact && 'Plan Available'}
            </Badge>
          </TooltipTrigger>
          <TooltipContent>
            <div className="text-sm">
              <p className="font-medium">Saved plan ready for apply</p>
              <p className="text-muted-foreground">
                Plan #{planStatus.plan_serial} created {ageText}
              </p>
              {planStatus.plan_output_preview && (
                <pre className="mt-2 text-xs bg-muted p-2 rounded max-w-md overflow-hidden">
                  {planStatus.plan_output_preview.slice(0, 200)}...
                </pre>
              )}
            </div>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  } else {
    // Plan exists but is invalid (vars changed)
    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Badge variant="warning" className="flex items-center gap-1">
              <AlertTriangle className="h-3 w-3" />
              {!compact && 'Plan Outdated'}
            </Badge>
          </TooltipTrigger>
          <TooltipContent>
            <div className="text-sm">
              <p className="font-medium">Plan needs to be refreshed</p>
              <p className="text-muted-foreground">
                {planStatus.reason_invalid}
              </p>
              <p className="text-xs mt-1">
                Run Plan again before Apply
              </p>
            </div>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }
}

// Hook to use plan status in other components
export function usePlanStatus(moduleId: number) {
  return useQuery({
    queryKey: ['plan-status', moduleId],
    queryFn: () => fetchPlanStatus(moduleId),
    staleTime: 30000,
    refetchOnWindowFocus: false,
  });
}
