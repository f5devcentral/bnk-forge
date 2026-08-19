/**
 * F5 BNK Detail Panel — Registry-driven detail component lookup
 *
 * Extracted from F5BNK.tsx. Replaces the 24-branch if/else chain
 * with a single `getDetailComponent(kind)` call.
 */

import { Button } from '@/components/ui/button';
import { formatAge } from '@/lib/time-utils';
import { Eye, Edit, Trash2, X } from 'lucide-react';
import { getDetailComponent, getDetailQuickActions } from './resource-registry';
import type { K8sResource } from '@/types/kubernetes';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface F5BNKDetailPanelProps {
  resource: K8sResource;
  onClose: () => void;
  onDescribe: (resource: K8sResource) => void;
  onEdit: (resource: K8sResource) => void;
  onDelete: (resource: K8sResource) => void;
  /** Navigate to a special view (e.g. policy map, AI dashboard) */
  onNavigateView: (viewKey: string) => void;
  /** Open a specific dialog (e.g. irule code viewer) */
  onOpenDialog: (dialogKey: string, resource: K8sResource) => void;
  borderDefault: string;
  clusterId?: number;
}

// ---------------------------------------------------------------------------
// Fallback detail panel for unknown resource kinds
// ---------------------------------------------------------------------------

function FallbackDetail({ resource }: { resource: K8sResource }) {
  return (
    <div className="p-3 rounded-lg bg-muted/50">
      <h4 className="text-xs font-semibold mb-2">Metadata</h4>
      <div className="space-y-1.5 text-xs">
        <div className="flex justify-between">
          <span className="text-muted-foreground">Kind:</span>
          <code className="font-mono text-foreground">
            {resource.kind}
          </code>
        </div>
        <div className="flex justify-between">
          <span className="text-muted-foreground">Namespace:</span>
          <code className="font-mono text-foreground">
            {resource.metadata?.namespace || 'cluster-wide'}
          </code>
        </div>
        <div className="flex justify-between">
          <span className="text-muted-foreground">Age:</span>
          <span className="text-foreground">
            {formatAge(resource.metadata?.creationTimestamp || '')}
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted-foreground">API Version:</span>
          <code className="font-mono text-foreground">
            {resource.apiVersion}
          </code>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function F5BNKDetailPanel({
  resource,
  onClose,
  onDescribe,
  onEdit,
  onDelete,
  onNavigateView,
  onOpenDialog,
  borderDefault: _borderDefault,
  clusterId,
}: F5BNKDetailPanelProps) {
  const DetailComponent = getDetailComponent(resource.kind);
  const quickActions = getDetailQuickActions(resource.kind);

  return (
    <div className="h-full">
      <div className="p-4">
        {/* Header */}
        <div className="flex items-start justify-between mb-4">
          <div>
            <h3 className="font-semibold text-lg mb-1">{resource.metadata?.name}</h3>
            <p className="text-xs text-muted-foreground">
              {resource.kind} • {resource.metadata?.namespace}
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            className="h-8 w-8 p-0"
            onClick={onClose}
          >
            <X className="h-4 w-4" />
          </Button>
        </div>

        {/* Quick Actions */}
        <div className="grid grid-cols-2 gap-2 mb-4">
          <Button
            variant="outline"
            size="sm"
            className="h-9 text-xs"
            onClick={() => onDescribe(resource)}
          >
            <Eye className="h-3.5 w-3.5 mr-1.5" />
            Describe
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-9 text-xs"
            onClick={() => onEdit(resource)}
          >
            <Edit className="h-3.5 w-3.5 mr-1.5" />
            Edit YAML
          </Button>

          {/* Registry-driven quick actions */}
          {quickActions.map((action) => {
            const ActionIcon = action.icon;
            return (
              <Button
                key={action.label}
                variant="outline"
                size="sm"
                className="h-9 text-xs"
                onClick={() => {
                  if (action.type === 'navigate' && action.targetView) {
                    onNavigateView(action.targetView);
                  } else if (action.type === 'dialog' && action.dialogKey) {
                    onOpenDialog(action.dialogKey, resource);
                  }
                }}
              >
                <ActionIcon className="h-3.5 w-3.5 mr-1.5" />
                {action.label}
              </Button>
            );
          })}

          <Button
            variant="outline"
            size="sm"
            className="h-9 text-xs text-destructive"
            onClick={() => onDelete(resource)}
          >
            <Trash2 className="h-3.5 w-3.5 mr-1.5" />
            Delete
          </Button>
        </div>

        {/* Resource-specific detail panel */}
        {DetailComponent ? (
          <DetailComponent resource={resource} clusterId={clusterId} />
        ) : (
          <FallbackDetail resource={resource} />
        )}
      </div>
    </div>
  );
}
