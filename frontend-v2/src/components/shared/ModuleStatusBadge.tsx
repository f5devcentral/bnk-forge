/* eslint-disable react-refresh/only-export-components */
import { Badge } from '@/components/ui/badge';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';

type StatusVariant = 'default' | 'success' | 'secondary' | 'warning' | 'destructive' | 'outline';

/**
 * Get the badge variant for a module status.
 * Centralizes the status → color mapping logic used across the application.
 *
 * @param status - Module status string
 * @returns Badge variant for the status
 */
export function getStatusVariant(status?: string): StatusVariant {
  switch (status) {
    case 'applied':
    case 'deployed':
      return 'success'; // Green for successfully deployed modules
    case 'initialized':
    case 'active':
      return 'secondary'; // Blue/gray for initialized but not applied
    case 'pending':
    case 'initializing':
    case 'planning':
    case 'applying':
    case 'destroying':
      return 'warning'; // Yellow for in-progress states
    case 'failed':
    case 'error':
      return 'destructive'; // Red for errors
    default:
      return 'outline'; // Gray for unknown states
  }
}

/**
 * Get a human-readable label for a module status.
 * Converts technical status codes to user-friendly labels.
 *
 * @param status - Module status string
 * @returns Human-readable status label
 */
export function getStatusLabel(status?: string): string {
  if (!status) {
    return 'Not deployed';
  }

  // Capitalize first letter and handle special cases
  const label = status.charAt(0).toUpperCase() + status.slice(1);

  // Special case mappings
  const specialCases: Record<string, string> = {
    'Initialized': 'Initialized',
    'Applied': 'Deployed',
    'Deployed': 'Deployed',
    'Initializing': 'Initializing...',
    'Planning': 'Planning...',
    'Applying': 'Deploying...',
    'Destroying': 'Destroying...',
  };

  return specialCases[label] || label;
}

/**
 * Get a descriptive tooltip explaining what the status means.
 *
 * @param status - Module status string
 * @returns Tooltip text describing the status
 */
export function getStatusTooltip(status?: string): string {
  switch (status) {
    case 'applied':
    case 'deployed':
      return 'Module has been successfully deployed and is running';
    case 'initialized':
      return 'OpenTofu initialized, ready to plan and deploy';
    case 'active':
      return 'Module is active in the project';
    case 'pending':
      return 'Waiting to be deployed';
    case 'initializing':
      return 'Running: tofu init - Initializing OpenTofu backend and providers';
    case 'planning':
      return 'Running: tofu plan - Calculating infrastructure changes';
    case 'applying':
      return 'Running: tofu apply - Deploying infrastructure changes';
    case 'destroying':
      return 'Running: tofu destroy - Removing infrastructure resources';
    case 'failed':
    case 'error':
      return 'Deployment failed - Check logs for details';
    default:
      return 'Module has not been deployed yet';
  }
}

interface ModuleStatusBadgeProps {
  status?: string;
  className?: string;
  showTooltip?: boolean;
}

/**
 * Shared component for displaying module status badges.
 * Ensures consistent status colors and labels across all components.
 *
 * @param status - Module status string
 * @param className - Optional additional CSS classes
 * @param showTooltip - Whether to show explanatory tooltip on hover (default: true)
 */
export function ModuleStatusBadge({ status, className = '', showTooltip = true }: ModuleStatusBadgeProps) {
  const variant = getStatusVariant(status);
  const label = getStatusLabel(status);
  const tooltip = getStatusTooltip(status);

  if (!showTooltip) {
    return (
      <Badge data-testid="module-status" variant={variant} className={className}>
        {label}
      </Badge>
    );
  }

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge data-testid="module-status" variant={variant} className={className + ' cursor-help'}>
            {label}
          </Badge>
        </TooltipTrigger>
        <TooltipContent>
          <p className="max-w-xs">{tooltip}</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
