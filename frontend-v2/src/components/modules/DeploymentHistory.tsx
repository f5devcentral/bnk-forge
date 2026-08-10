import { useState, useEffect, useCallback } from 'react';
import { SectionCard } from '@/components/ui/section-card';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Alert, AlertDescription } from '@/components/ui/alert';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  History,
  RefreshCw,
  Filter,
  CheckCircle2,
  XCircle,
  Clock,
  GitBranch,
  PlayCircle,
  Trash2,
  AlertTriangle,
  User,
} from 'lucide-react';
import { api } from '@/lib/api';
import type { ProjectModule } from '@/types';

interface Deployment {
  id: number;
  action: string;
  status: string;
  triggered_by?: string;
  started_at: string;
  completed_at?: string;
  duration_seconds?: number;
  resources_to_add: number;
  resources_to_change: number;
  resources_to_destroy: number;
  exit_code?: number;
  environment?: string;
  git_commit?: string;
  git_branch?: string;
}

interface DeploymentHistoryProps {
  module?: ProjectModule;
  projectId?: number;
}

export function DeploymentHistory({ module, projectId }: DeploymentHistoryProps) {
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionFilter, setActionFilter] = useState<string>('all');
  const [statusFilter, setStatusFilter] = useState<string>('all');

  const loadDeployments = useCallback(async () => {
    setIsLoading(true);
    setError(null);

    try {
      const params = {
        action: actionFilter !== 'all' ? actionFilter : undefined,
        status: statusFilter !== 'all' ? statusFilter : undefined,
        limit: 100,
      };

      let response;
      if (module) {
        response = await api.getModuleDeployments(module.id, params);
      } else if (projectId) {
        response = await api.getProjectDeployments(projectId, params);
      } else {
        throw new Error('Either module or projectId must be provided');
      }

      setDeployments(response.deployments || []);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load deployment history';
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [module, projectId, actionFilter, statusFilter]);

  useEffect(() => {
    loadDeployments();
  }, [loadDeployments]);

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'success':
        return <CheckCircle2 className="h-4 w-4 text-success" />;
      case 'failed':
        return <XCircle className="h-4 w-4 text-destructive" />;
      case 'running':
        return <Clock className="h-4 w-4 text-info animate-spin" />;
      case 'cancelled':
        return <AlertTriangle className="h-4 w-4 text-warning" />;
      default:
        return <Clock className="h-4 w-4 text-muted-foreground" />;
    }
  };

  const getStatusBadge = (status: string) => {
    const variants: Record<string, 'success' | 'destructive' | 'info' | 'outline'> = {
      success: 'success',
      failed: 'destructive',
      running: 'info',
      cancelled: 'outline',
    };
    return variants[status] || 'outline';
  };

  const getActionIcon = (action: string) => {
    switch (action) {
      case 'plan':
        return <GitBranch className="h-4 w-4" />;
      case 'apply':
        return <PlayCircle className="h-4 w-4" />;
      case 'destroy':
        return <Trash2 className="h-4 w-4" />;
      default:
        return <History className="h-4 w-4" />;
    }
  };

  const formatDuration = (seconds?: number) => {
    if (!seconds) return 'N/A';
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m ${Math.round(seconds % 60)}s`;
    return `${Math.floor(seconds / 3600)}h ${Math.round((seconds % 3600) / 60)}m`;
  };

  const formatTimestamp = (timestamp: string) => {
    const date = new Date(timestamp);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 7) return `${diffDays}d ago`;

    return date.toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: date.getFullYear() !== now.getFullYear() ? 'numeric' : undefined,
    });
  };

  return (
    <SectionCard title="Deployment history">
      <p className="text-sm text-muted-foreground -mt-1 mb-4">
        Timeline of all deployment operations.
      </p>
      <div className="space-y-4">
        <div className="flex items-center justify-end">
          <div className="flex gap-2">
            {/* Action Filter */}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button size="sm" variant="outline">
                  <Filter className="h-4 w-4 mr-2" />
                  Action: {actionFilter === 'all' ? 'All' : actionFilter}
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={() => setActionFilter('all')}>
                  All Actions
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setActionFilter('plan')}>
                  Plan
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setActionFilter('apply')}>
                  Apply
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setActionFilter('destroy')}>
                  Destroy
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>

            {/* Status Filter */}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button size="sm" variant="outline">
                  <Filter className="h-4 w-4 mr-2" />
                  Status: {statusFilter === 'all' ? 'All' : statusFilter}
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={() => setStatusFilter('all')}>
                  All Status
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setStatusFilter('success')}>
                  Success
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setStatusFilter('failed')}>
                  Failed
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setStatusFilter('running')}>
                  Running
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => setStatusFilter('cancelled')}>
                  Cancelled
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>

            <Button size="sm" variant="outline" onClick={loadDeployments} disabled={isLoading}>
              <RefreshCw className={`h-4 w-4 mr-2 ${isLoading ? 'animate-spin' : ''}`} />
              Refresh
            </Button>
          </div>
        </div>
        {isLoading && deployments.length === 0 ? (
          <div className="text-center py-12">
            <RefreshCw className="h-12 w-12 animate-spin mx-auto mb-4 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">Loading deployment history...</p>
          </div>
        ) : error ? (
          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : deployments.length === 0 ? (
          <Alert>
            <History className="h-4 w-4" />
            <AlertDescription>
              No deployment history found. Run plan, apply, or destroy to see deployments here.
            </AlertDescription>
          </Alert>
        ) : (
          <ScrollArea className="h-[600px]">
            <div className="relative space-y-4">
              {/* Timeline line */}
              <div className="absolute left-[21px] top-2 bottom-2 w-0.5 bg-border" />

              {deployments.map((deployment, index) => (
                <div key={deployment.id} className="relative pl-12">
                  {/* Timeline dot */}
                  <div className="absolute left-0 top-1.5 flex items-center justify-center w-10 h-10 rounded-full bg-background border-2 border-border">
                    {getStatusIcon(deployment.status)}
                  </div>

                  {/* Deployment card */}
                  <Card className={index === 0 ? 'border-2' : ''}>
                    <CardContent className="pt-4">
                      <div className="flex items-start justify-between mb-3">
                        <div className="flex items-center gap-2">
                          {getActionIcon(deployment.action)}
                          <span className="font-semibold capitalize">{deployment.action}</span>
                          <Badge variant={getStatusBadge(deployment.status)}>
                            {deployment.status}
                          </Badge>
                          {deployment.environment && (
                            <Badge variant="outline" className="text-xs">
                              {deployment.environment}
                            </Badge>
                          )}
                        </div>
                        <span className="text-xs text-muted-foreground">
                          {formatTimestamp(deployment.started_at)}
                        </span>
                      </div>

                      {/* Resource changes */}
                      {(deployment.resources_to_add > 0 ||
                        deployment.resources_to_change > 0 ||
                        deployment.resources_to_destroy > 0) && (
                        <div className="flex gap-4 mb-3 text-sm">
                          {deployment.resources_to_add > 0 && (
                            <div className="flex items-center gap-1 text-success">
                              <span className="font-semibold">+{deployment.resources_to_add}</span>
                              <span className="text-muted-foreground">add</span>
                            </div>
                          )}
                          {deployment.resources_to_change > 0 && (
                            <div className="flex items-center gap-1 text-warning">
                              <span className="font-semibold">~{deployment.resources_to_change}</span>
                              <span className="text-muted-foreground">change</span>
                            </div>
                          )}
                          {deployment.resources_to_destroy > 0 && (
                            <div className="flex items-center gap-1 text-destructive">
                              <span className="font-semibold">-{deployment.resources_to_destroy}</span>
                              <span className="text-muted-foreground">destroy</span>
                            </div>
                          )}
                        </div>
                      )}

                      {/* Metadata */}
                      <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                        {deployment.duration_seconds && (
                          <div className="flex items-center gap-1">
                            <Clock className="h-3 w-3" />
                            {formatDuration(deployment.duration_seconds)}
                          </div>
                        )}
                        {deployment.triggered_by && (
                          <div className="flex items-center gap-1">
                            <User className="h-3 w-3" />
                            {deployment.triggered_by}
                          </div>
                        )}
                        {deployment.git_commit && (
                          <div className="flex items-center gap-1">
                            <GitBranch className="h-3 w-3" />
                            {deployment.git_branch || 'main'}@
                            {deployment.git_commit.substring(0, 7)}
                          </div>
                        )}
                        {deployment.exit_code !== undefined && deployment.exit_code !== 0 && (
                          <div className="flex items-center gap-1 text-destructive">
                            Exit code: {deployment.exit_code}
                          </div>
                        )}
                      </div>
                    </CardContent>
                  </Card>
                </div>
              ))}
            </div>
          </ScrollArea>
        )}
      </div>
    </SectionCard>
  );
}
