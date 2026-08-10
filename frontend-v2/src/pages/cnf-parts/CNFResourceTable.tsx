/**
 * CNF Resource Table — read-only instance list for a selected CRD.
 *
 * Status column derives severity from status.conditions[] (Ready / Programmed /
 * Accepted) via severityFromConditions → getSeverityConfig from lib/health-severity.ts.
 * NO mutation actions (create/edit/delete) — CNF P2 is strictly read-only.
 */

import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Eye } from 'lucide-react';
import { getSeverityConfig } from '@/lib/health-severity';
import { formatAge } from '@/lib/time-utils';
import type { K8sResource, K8sCondition } from '@/types/kubernetes';
import { severityFromConditions } from './severity';

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface CNFResourceTableProps {
  resources: K8sResource[];
  selectedResource: K8sResource | null;
  onSelectResource: (resource: K8sResource) => void;
  onDescribe: (resource: K8sResource) => void;
  borderDefault: string;
}

export function CNFResourceTable({
  resources,
  selectedResource,
  onSelectResource,
  onDescribe,
  borderDefault,
}: CNFResourceTableProps) {
  return (
    <table className="w-full">
      <thead className="sticky top-0 z-10 bg-card">
        <tr className={cn('text-xs border-b text-muted-foreground', borderDefault)}>
          <th scope="col" className="text-left py-3 px-4 font-medium">Name</th>
          <th scope="col" className="text-left py-3 px-4 font-medium">Namespace</th>
          <th scope="col" className="text-left py-3 px-4 font-medium">Status</th>
          <th scope="col" className="text-left py-3 px-4 font-medium">Age</th>
          <th scope="col" className="text-right py-3 px-4 font-medium w-24">Actions</th>
        </tr>
      </thead>
      <tbody>
        {resources.map((resource) => {
          const conditions = resource.status?.conditions as K8sCondition[] | undefined;
          const severity = severityFromConditions(conditions);
          const severityConfig = getSeverityConfig(severity);
          const SeverityIcon = severityConfig.icon;

          return (
            <tr
              key={resource.metadata?.uid ?? resource.metadata?.name}
              className={cn(
                'border-b cursor-pointer transition-colors hover:bg-muted/50',
                borderDefault,
                selectedResource?.metadata?.uid === resource.metadata?.uid && 'bg-primary/10'
              )}
              onClick={() => onSelectResource(resource)}
            >
              <td className="py-3 px-4">
                <code className="text-sm font-mono text-foreground">
                  {resource.metadata?.name}
                </code>
              </td>
              <td className="py-3 px-4">
                <Badge variant="outline" className="text-xs">
                  {resource.metadata?.namespace || 'cluster-wide'}
                </Badge>
              </td>
              <td className="py-3 px-4">
                <div className={cn('flex items-center gap-1.5 text-xs font-medium', severityConfig.color)}>
                  <SeverityIcon className="h-3.5 w-3.5" />
                  {severityConfig.label}
                </div>
              </td>
              <td className="py-3 px-4">
                <span className="text-xs text-muted-foreground">
                  {formatAge(resource.metadata?.creationTimestamp)}
                </span>
              </td>
              <td className="py-3 px-4 text-right">
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 px-2 text-xs"
                  aria-label="View details"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDescribe(resource);
                  }}
                >
                  <Eye className="h-3.5 w-3.5" />
                </Button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
