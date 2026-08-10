/**
 * Kubernetes Resource Table — Table rendering with status logic & action menus
 * Extracted from KubernetesV2.tsx (FE-002)
 *
 * Renders the resource list table with dynamic columns based on resource type,
 * status badges, and per-row context menus.
 */

import { memo } from 'react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  MoreVertical,
  Eye,
  FileText,
  Trash2,
  RotateCw,
  Activity,
  Edit,
  Maximize,
  Terminal,
  Server,
} from 'lucide-react';
import { getStatusColor, getRestartCount, calculateAge } from './k8s-constants';
import { getResourceStatus } from './k8s-status';
import type { K8sResource } from '@/types';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface ResourceAction {
  type: 'rollout' | 'nodeOps' | 'describe' | 'edit' | 'terminal' | 'logs' | 'restart' | 'scale' | 'delete';
  resource: K8sResource;
}

interface K8sResourceTableProps {
  resources: K8sResource[];
  selectedResourceType: string;
  selectedResource: K8sResource | null;
  detailedView: boolean;
  nodeOperationsDisabledReason?: string | null;
  networkMutationBoundaryNotice?: string | null;
  onSelectResource: (resource: K8sResource) => void;
  onAction: (action: ResourceAction) => void;
  borderDefault: string;
}

// ---------------------------------------------------------------------------
// FE-006: Memoized Row Component — prevents re-renders when sibling rows change
// ---------------------------------------------------------------------------

interface K8sResourceRowProps {
  resource: K8sResource;
  selectedResourceType: string;
  isSelected: boolean;
  detailedView: boolean;
  nodeOperationsDisabledReason?: string | null;
  networkMutationBoundaryNotice?: string | null;
  onSelectResource: (resource: K8sResource) => void;
  onAction: (action: ResourceAction) => void;
  borderDefault: string;
}

const K8sResourceRow = memo(function K8sResourceRow({
  resource,
  selectedResourceType,
  isSelected,
  detailedView,
  nodeOperationsDisabledReason,
  networkMutationBoundaryNotice,
  onSelectResource,
  onAction,
  borderDefault,
}: K8sResourceRowProps) {
  const status = getResourceStatus(resource, selectedResourceType);

  // Handle both camelCase and snake_case from API
  const creationTime =
    resource.metadata?.creationTimestamp ||
    resource.metadata?.creation_timestamp;
  const age = creationTime ? calculateAge(creationTime) : 'Unknown';

  return (
    <tr
      className={cn(
        'cursor-pointer transition-colors border-b',
        borderDefault,
        isSelected ? 'bg-primary/10' : 'hover:bg-muted/50'
      )}
      onClick={() => onSelectResource(resource)}
    >
      <td className="py-2.5 px-4">
        <code className="text-xs font-mono">
          {resource.metadata?.name}
        </code>
      </td>
      <td className="py-2.5 px-4">
        <Badge className={cn('text-[10px]', getStatusColor(status))}>
          {status}
        </Badge>
      </td>
      {detailedView && (
        <>
          <td className="py-2.5 px-4">
            <code className="text-xs font-mono text-muted-foreground">
              {selectedResourceType === 'node'
                ? resource.metadata?.labels?.['kubernetes.io/hostname'] ||
                  resource.metadata?.name ||
                  '-'
                : resource.spec?.nodeName ||
                  resource.spec?.node_name ||
                  '-'}
            </code>
          </td>
          <td className="py-2.5 px-4">
            <code className="text-xs font-mono text-muted-foreground">
              {selectedResourceType === 'node'
                ? resource.status?.addresses?.find(
                    (a: { type: string; address: string }) =>
                      a.type === 'InternalIP'
                  )?.address || '-'
                : resource.status?.podIP ||
                  resource.status?.pod_ip ||
                  resource.spec?.clusterIP ||
                  resource.spec?.cluster_ip ||
                  '-'}
            </code>
          </td>
          <td className="py-2.5 px-4">
            {selectedResourceType === 'node' ? (
              <code className="text-xs font-mono text-muted-foreground">
                {resource.metadata?.labels?.['node.kubernetes.io/instance-type'] ||
                  resource.metadata?.labels?.['beta.kubernetes.io/instance-type'] ||
                  '-'}
              </code>
            ) : (
              <span
                className={cn(
                  'text-sm',
                  getRestartCount(resource) > 0
                    ? 'text-warning'
                    : 'text-muted-foreground'
                )}
              >
                {getRestartCount(resource)}
              </span>
            )}
          </td>
        </>
      )}
      <td className="py-2.5 px-4">
        <span className="text-sm text-muted-foreground">
          {age}
        </span>
      </td>
      <td className="py-2.5 px-4 text-right">
        <DropdownMenu>
          <DropdownMenuTrigger asChild onClick={(e) => e.stopPropagation()}>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-7 p-0"
              aria-label="Resource actions"
            >
              <MoreVertical className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {selectedResourceType === 'deployment' && (
              <>
                <DropdownMenuItem
                  onClick={(e) => { e.stopPropagation(); onAction({ type: 'rollout', resource }); }}
                >
                  <Activity className="h-4 w-4 mr-2" />
                  Rollout Management
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={(e) => {
                    e.stopPropagation();
                    onAction({ type: 'scale', resource });
                  }}
                >
                  <Maximize className="h-4 w-4 mr-2" />
                  Scale
                </DropdownMenuItem>
              </>
            )}
            {selectedResourceType === 'node' && (
              <DropdownMenuItem
                disabled={!!nodeOperationsDisabledReason}
                onClick={(e) => {
                  e.stopPropagation();
                  if (!nodeOperationsDisabledReason) {
                    onAction({ type: 'nodeOps', resource });
                  }
                }}
              >
                <Server className="h-4 w-4 mr-2" />
                {nodeOperationsDisabledReason ? 'Node Operations (OpenShift-managed)' : 'Node Operations'}
              </DropdownMenuItem>
            )}
            {selectedResourceType === 'node' && nodeOperationsDisabledReason && (
              <DropdownMenuItem disabled className="text-xs whitespace-normal leading-snug">
                {nodeOperationsDisabledReason}
              </DropdownMenuItem>
            )}
            <DropdownMenuItem
              onClick={(e) => {
                e.stopPropagation();
                onAction({ type: 'describe', resource });
              }}
            >
              <Eye className="h-4 w-4 mr-2" />
              Describe
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={(e) => {
                e.stopPropagation();
                onAction({ type: 'edit', resource });
              }}
            >
              <Edit className="h-4 w-4 mr-2" />
              Edit YAML
            </DropdownMenuItem>
            {networkMutationBoundaryNotice && (
              <DropdownMenuItem disabled className="text-xs whitespace-normal leading-snug">
                {networkMutationBoundaryNotice}
              </DropdownMenuItem>
            )}
            {selectedResourceType === 'pod' && (
              <>
                <DropdownMenuItem
                  onClick={(e) => {
                    e.stopPropagation();
                    onAction({ type: 'terminal', resource });
                  }}
                >
                  <Terminal className="h-4 w-4 mr-2" />
                  Terminal
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={(e) => {
                    e.stopPropagation();
                    onAction({ type: 'logs', resource });
                  }}
                >
                  <FileText className="h-4 w-4 mr-2" />
                  Logs
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={(e) => {
                    e.stopPropagation();
                    onAction({ type: 'restart', resource });
                  }}
                >
                  <RotateCw className="h-4 w-4 mr-2" />
                  Restart
                </DropdownMenuItem>
              </>
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem
              className="text-destructive"
              onClick={(e) => {
                e.stopPropagation();
                onAction({ type: 'delete', resource });
              }}
            >
              <Trash2 className="h-4 w-4 mr-2" />
              Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </td>
    </tr>
  );
});

// ---------------------------------------------------------------------------
// Table Component
// ---------------------------------------------------------------------------

export function K8sResourceTable({
  resources,
  selectedResourceType,
  selectedResource,
  detailedView,
  nodeOperationsDisabledReason,
  networkMutationBoundaryNotice,
  onSelectResource,
  onAction,
  borderDefault,
}: K8sResourceTableProps) {
  return (
    <div className="flex-1 overflow-auto">
      <table className="w-full">
        <thead className="sticky top-0 bg-card">
          <tr className="text-xs text-muted-foreground">
            <th scope="col" className="text-left py-2 px-4 font-medium">
              Name
            </th>
            <th scope="col" className="text-left py-2 px-4 font-medium">
              Status
            </th>
            {detailedView && (
              <>
                <th scope="col" className="text-left py-2 px-4 font-medium">
                  {selectedResourceType === 'node' ? 'Hostname' : 'Node'}
                </th>
                <th scope="col" className="text-left py-2 px-4 font-medium">
                  IP
                </th>
                <th scope="col" className="text-left py-2 px-4 font-medium">
                  {selectedResourceType === 'node' ? 'Type' : 'Restarts'}
                </th>
              </>
            )}
            <th scope="col" className="text-left py-2 px-4 font-medium">
              Age
            </th>
            <th scope="col" className="text-right py-2 px-4 font-medium">
              Actions
            </th>
          </tr>
        </thead>
        <tbody>
          {resources.map((resource: K8sResource) => (
            <K8sResourceRow
              key={resource.metadata?.uid}
              resource={resource}
              selectedResourceType={selectedResourceType}
              isSelected={selectedResource?.metadata?.uid === resource.metadata?.uid}
              detailedView={detailedView}
              nodeOperationsDisabledReason={nodeOperationsDisabledReason}
              networkMutationBoundaryNotice={networkMutationBoundaryNotice}
              onSelectResource={onSelectResource}
              onAction={onAction}
              borderDefault={borderDefault}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}
