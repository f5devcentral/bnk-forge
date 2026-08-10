/**
 * Kubernetes Detail Panel — Right-side resource detail view
 * Extracted from KubernetesV2.tsx (FE-002)
 *
 * Shows metadata, labels, quick actions, and delegates to
 * GatewayDetail / HTTPRouteDetail for Gateway API resources.
 */

import { Button } from '@/components/ui/button';
import {
  X,
  Eye,
  FileText,
  Trash2,
  RotateCw,
  Terminal,
} from 'lucide-react';
import { GatewayDetail } from '@/components/k8s/GatewayDetail';
import { HTTPRouteDetail } from '@/components/k8s/HTTPRouteDetail';
import { calculateAge } from './k8s-constants';
import type { K8sResource } from '@/types';
import type { ResourceAction } from './K8sResourceTable';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface K8sDetailPanelProps {
  resource: K8sResource;
  selectedResourceType: string;
  nodeOperationsDisabledReason?: string | null;
  networkMutationBoundaryNotice?: string | null;
  onClose: () => void;
  onAction: (action: ResourceAction) => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function K8sDetailPanel({
  resource,
  selectedResourceType,
  nodeOperationsDisabledReason,
  networkMutationBoundaryNotice,
  onClose,
  onAction,
}: K8sDetailPanelProps) {
  return (
    <div className="p-4">
      {/* Header */}
      <div className="flex items-start justify-between mb-4">
        <div>
          <h3 className="font-semibold text-lg mb-1">
            {resource.metadata?.name}
          </h3>
          <p className="text-xs text-muted-foreground">
            {resource.kind} • {resource.metadata?.namespace}
          </p>
        </div>
        <Button
          variant="ghost"
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
          onClick={() => onAction({ type: 'describe', resource })}
        >
          <Eye className="h-3.5 w-3.5 mr-1.5" />
          Describe
        </Button>
        {selectedResourceType === 'pod' && (
          <Button
            variant="outline"
            size="sm"
            className="h-9 text-xs"
            onClick={() => onAction({ type: 'logs', resource })}
          >
            <FileText className="h-3.5 w-3.5 mr-1.5" />
            Logs
          </Button>
        )}
        {selectedResourceType === 'pod' && (
          <Button
            variant="outline"
            size="sm"
            className="h-9 text-xs"
            onClick={() => onAction({ type: 'terminal', resource })}
          >
            <Terminal className="h-3.5 w-3.5 mr-1.5" />
            Terminal
          </Button>
        )}
        {selectedResourceType === 'pod' && (
          <Button
            variant="outline"
            size="sm"
            className="h-9 text-xs"
            onClick={() => onAction({ type: 'restart', resource })}
          >
            <RotateCw className="h-3.5 w-3.5 mr-1.5" />
            Restart
          </Button>
        )}
        {selectedResourceType === 'node' && (
          <Button
            variant="outline"
            size="sm"
            className="h-9 text-xs"
            disabled={!!nodeOperationsDisabledReason}
            title={nodeOperationsDisabledReason ?? undefined}
            onClick={() => onAction({ type: 'nodeOps', resource })}
          >
            <Terminal className="h-3.5 w-3.5 mr-1.5" />
            Node Operations
          </Button>
        )}
        <Button
          variant="outline"
          size="sm"
          className="h-9 text-xs text-destructive"
          onClick={() => onAction({ type: 'delete', resource })}
        >
          <Trash2 className="h-3.5 w-3.5 mr-1.5" />
          Delete
        </Button>
      </div>

      {selectedResourceType === 'node' && nodeOperationsDisabledReason && (
        <p className="text-xs mb-4 px-0.5 text-warning">
          {nodeOperationsDisabledReason}
        </p>
      )}

      {networkMutationBoundaryNotice && (
        <p className="text-xs mb-4 px-0.5 text-info">
          {networkMutationBoundaryNotice}
        </p>
      )}

      {/* Gateway-specific detail */}
      {resource.kind === 'Gateway' ? (
        <GatewayDetail resource={resource} />
      ) : resource.kind === 'HTTPRoute' ? (
        <HTTPRouteDetail resource={resource} />
      ) : (
        <>
          {/* Metadata Section */}
          <div className="p-3 rounded-lg mb-4 bg-muted/50">
            <h4 className="text-xs font-semibold mb-2">Metadata</h4>
            <div className="space-y-1.5 text-xs">
              <div className="flex justify-between">
                <span className="text-muted-foreground">
                  Namespace:
                </span>
                <code className="font-mono text-foreground/80">
                  {resource.metadata?.namespace}
                </code>
              </div>
              {resource.spec?.nodeName && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">
                    Node:
                  </span>
                  <code className="font-mono text-foreground/80">
                    {resource.spec.nodeName}
                  </code>
                </div>
              )}
              {(resource.status?.podIP || resource.spec?.clusterIP) && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">
                    IP:
                  </span>
                  <code className="font-mono text-foreground/80">
                    {resource.status?.podIP || resource.spec?.clusterIP}
                  </code>
                </div>
              )}
              <div className="flex justify-between">
                <span className="text-muted-foreground">
                  Age:
                </span>
                <span className="text-foreground/80">
                  {calculateAge(
                    resource.metadata?.creationTimestamp ||
                      resource.metadata?.creation_timestamp ||
                      ''
                  )}
                </span>
              </div>
            </div>
          </div>

          {/* Labels Section */}
          {resource.metadata?.labels &&
            Object.keys(resource.metadata.labels).length > 0 && (
              <div className="p-3 rounded-lg bg-muted/50">
                <h4 className="text-xs font-semibold mb-2">Labels</h4>
                <div className="flex flex-wrap gap-1.5">
                  {Object.entries(resource.metadata.labels).map(
                    ([key, value]) => (
                      <code
                        key={key}
                        className="text-[10px] px-2 py-0.5 rounded font-mono bg-muted text-foreground/80"
                      >
                        {key}={value as string}
                      </code>
                    )
                  )}
                </div>
              </div>
            )}
        </>
      )}
    </div>
  );
}
