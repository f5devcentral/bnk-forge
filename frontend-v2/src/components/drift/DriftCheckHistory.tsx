import { useState } from 'react';
import { SectionCard } from '@/components/ui/section-card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  Loader2,
  AlertTriangle,
  CheckCircle2,
  Clock,
  PlayCircle,
  XCircle,
  MoreVertical,
  Eye,
} from 'lucide-react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { useDriftChecks, useTriggerDriftCheck } from '@/hooks/useDrift';
import { formatTimeAgo } from '@/lib/time-utils';
import type { DriftCheck } from '@/types';

interface DriftCheckHistoryProps {
  projectId: number;
  moduleId?: number;
}

export function DriftCheckHistory({ projectId, moduleId }: DriftCheckHistoryProps) {
  const { data, isLoading, error } = useDriftChecks(projectId, {
    moduleId,
    limit: 20,
    pollingEnabled: true,
  });
  const triggerCheck = useTriggerDriftCheck();
  const [selectedCheck, setSelectedCheck] = useState<DriftCheck | null>(null);

  const handleTriggerCheck = () => {
    triggerCheck.mutate({ projectId, data: moduleId ? { module_ids: [moduleId] } : undefined });
  };

  const getStatusBadge = (check: DriftCheck) => {
    switch (check.status) {
      case 'completed':
        if (check.drift_detected) {
          return (
            <Badge variant="destructive" className="gap-1">
              <AlertTriangle className="h-3 w-3" />
              Drift Detected
            </Badge>
          );
        }
        return (
          <Badge variant="success" className="gap-1">
            <CheckCircle2 className="h-3 w-3" />
            No Drift
          </Badge>
        );
      case 'checking':
        return (
          <Badge variant="info" className="gap-1">
            <Loader2 className="h-3 w-3 animate-spin" />
            Checking
          </Badge>
        );
      case 'scheduled':
        return (
          <Badge variant="muted" className="gap-1">
            <Clock className="h-3 w-3" />
            Scheduled
          </Badge>
        );
      case 'failed':
        return (
          <Badge variant="destructive" className="gap-1">
            <XCircle className="h-3 w-3" />
            Failed
          </Badge>
        );
      default:
        return <Badge variant="muted">{check.status}</Badge>;
    }
  };

  if (isLoading) {
    return (
      <SectionCard>
        <div className="flex items-center justify-center py-8">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      </SectionCard>
    );
  }

  if (error) {
    return (
      <Alert variant="destructive">
        <AlertDescription>
          Failed to load drift check history. {error instanceof Error ? error.message : 'Unknown error'}
        </AlertDescription>
      </Alert>
    );
  }

  const checks = data || [];

  return (
    <>
      <SectionCard title="Drift Check History">
        <div className="flex items-start justify-between -mt-3 mb-5">
          <p className="text-sm text-muted-foreground">
            {moduleId ? 'Module drift check history' : 'Project drift check history'}
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={handleTriggerCheck}
            disabled={triggerCheck.isPending}
          >
            {triggerCheck.isPending ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Triggering...
              </>
            ) : (
              <>
                <PlayCircle className="h-4 w-4 mr-2" />
                Check Now
              </>
            )}
          </Button>
        </div>

        {checks.length === 0 ? (
          <div className="text-center py-8 text-muted-foreground">
            <Clock className="h-12 w-12 mx-auto mb-4 opacity-50" />
            <p>No drift checks yet</p>
            <p className="text-sm mt-1">Trigger a manual check or wait for scheduled checks</p>
          </div>
        ) : (
          <div className="border border-border rounded-md overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="text-xs uppercase tracking-wider text-muted-foreground">
                    Module
                  </TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-muted-foreground">
                    Status
                  </TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-muted-foreground">
                    Summary
                  </TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-muted-foreground">
                    Changes
                  </TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-muted-foreground">
                    When
                  </TableHead>
                  <TableHead className="text-xs uppercase tracking-wider text-muted-foreground text-right w-16">
                    {/* actions */}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {checks.map((check) => (
                  <TableRow
                    key={check.id}
                    className="cursor-pointer"
                    onClick={() => setSelectedCheck(check)}
                  >
                    <TableCell>
                      <span className="font-medium text-foreground">
                        {check.module_name || '—'}
                      </span>
                    </TableCell>
                    <TableCell>{getStatusBadge(check)}</TableCell>
                    <TableCell className="text-sm text-muted-foreground max-w-md truncate">
                      {check.drift_summary || '—'}
                    </TableCell>
                    <TableCell>
                      {check.drift_details?.resource_changes ? (
                        <div className="flex gap-1.5">
                          {check.drift_details.resource_changes.add > 0 && (
                            <Badge variant="success" className="text-xs">
                              +{check.drift_details.resource_changes.add}
                            </Badge>
                          )}
                          {check.drift_details.resource_changes.change > 0 && (
                            <Badge variant="warning" className="text-xs">
                              ~{check.drift_details.resource_changes.change}
                            </Badge>
                          )}
                          {check.drift_details.resource_changes.destroy > 0 && (
                            <Badge variant="destructive" className="text-xs">
                              -{check.drift_details.resource_changes.destroy}
                            </Badge>
                          )}
                        </div>
                      ) : (
                        <span className="text-xs text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {formatTimeAgo(check.created_at)}
                    </TableCell>
                    <TableCell
                      className="text-right"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-8 w-8 p-0"
                            aria-label="Drift check actions"
                          >
                            <MoreVertical className="h-4 w-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onClick={() => setSelectedCheck(check)}>
                            <Eye className="h-4 w-4 mr-2" />
                            View details
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </SectionCard>

      {/* Drift Check Details Dialog */}
      <Dialog open={!!selectedCheck} onOpenChange={() => setSelectedCheck(null)}>
        <DialogContent className="max-w-3xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Drift Check Details</DialogTitle>
            <DialogDescription>
              {selectedCheck?.module_name && `Module: ${selectedCheck.module_name} • `}
              {selectedCheck?.created_at && formatTimeAgo(selectedCheck.created_at)}
            </DialogDescription>
          </DialogHeader>
          {selectedCheck && (
            <div className="space-y-4">
              {/* Status */}
              <div>
                <Label className="text-sm font-medium">Status</Label>
                <div className="mt-1">{getStatusBadge(selectedCheck)}</div>
              </div>

              {/* Summary */}
              {selectedCheck.drift_summary && (
                <div>
                  <Label className="text-sm font-medium">Summary</Label>
                  <p className="mt-1 text-sm">{selectedCheck.drift_summary}</p>
                </div>
              )}

              {/* Resource Changes */}
              {selectedCheck.drift_details?.resource_changes && (
                <div>
                  <Label className="text-sm font-medium">Resource Changes</Label>
                  <div className="mt-2 flex gap-4">
                    <div className="flex items-center gap-2">
                      <Badge variant="success">
                        +{selectedCheck.drift_details.resource_changes.add}
                      </Badge>
                      <span className="text-sm text-muted-foreground">to add</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge variant="warning">
                        ~{selectedCheck.drift_details.resource_changes.change}
                      </Badge>
                      <span className="text-sm text-muted-foreground">to change</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <Badge variant="destructive">
                        -{selectedCheck.drift_details.resource_changes.destroy}
                      </Badge>
                      <span className="text-sm text-muted-foreground">to destroy</span>
                    </div>
                  </div>
                </div>
              )}

              {/* Changed Resources */}
              {selectedCheck.drift_details?.changed_resources &&
                selectedCheck.drift_details.changed_resources.length > 0 && (
                  <div>
                    <Label className="text-sm font-medium">Changed Resources</Label>
                    <div className="mt-2 space-y-2">
                      {selectedCheck.drift_details.changed_resources.map((resource, idx) => (
                        <div
                          key={idx}
                          className="flex items-center justify-between p-2 border rounded text-sm"
                        >
                          <code className="text-xs">{resource.address}</code>
                          <Badge variant="muted">{resource.action}</Badge>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

              {/* Error Message */}
              {selectedCheck.error_message && (
                <Alert variant="destructive">
                  <AlertDescription>{selectedCheck.error_message}</AlertDescription>
                </Alert>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}

function Label({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={className}>{children}</div>;
}
