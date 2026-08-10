import { useState } from 'react';
import { isAxiosError } from 'axios';
import { SectionCard } from '@/components/ui/section-card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Loader2, RefreshCw, Container, CheckCircle, XCircle, AlertCircle, ChevronDown, AlertTriangle } from 'lucide-react';
import { useContainerStatus, useRestartContainers } from '@/hooks/useSystem';
import { notify, notifyError } from '@/lib/notify';
import { cn } from '@/lib/utils';

export default function ContainerManagement() {
  const { data: containerData, isLoading, error, refetch } = useContainerStatus();
  const restartMutation = useRestartContainers();
  const [selectedServices, setSelectedServices] = useState<string[]>([]);
  const [isOpen, setIsOpen] = useState(false);

  const handleRestartAll = () => {
    if (confirm('Restart all application containers? This will cause a brief service interruption.')) {
      restartMutation.mutate(undefined, {
        onSuccess: (data) => {
          const failed = data.results.filter((r: { status: string }) => r.status !== 'restarted');
          if (failed.length === 0) {
            notify.success('All containers restarted successfully', undefined, { category: 'system' });
          } else {
            notify.warning(`${data.results.length - failed.length} containers restarted, ${failed.length} failed`, undefined, { category: 'system' });
          }
        },
        onError: (error: unknown) => {
          notifyError(error);
        },
      });
    }
  };

  const handleRestartSelected = () => {
    if (selectedServices.length === 0) {
      notify.error('Please select at least one service to restart');
      return;
    }

    if (confirm(`Restart ${selectedServices.length} selected service(s)?`)) {
      restartMutation.mutate(selectedServices, {
        onSuccess: (data) => {
          const failed = data.results.filter((r: { status: string }) => r.status !== 'restarted');
          if (failed.length === 0) {
            notify.success(`${selectedServices.length} container(s) restarted successfully`, undefined, { category: 'system' });
          } else {
            notify.warning(`${data.results.length - failed.length} containers restarted, ${failed.length} failed`, undefined, { category: 'system' });
          }
          setSelectedServices([]);
        },
        onError: (error: unknown) => {
          notifyError(error);
        },
      });
    }
  };

  const toggleServiceSelection = (service: string) => {
    if (service === 'postgres' || service === 'redis') {
      notify.error('Cannot restart database services for safety');
      return;
    }

    setSelectedServices(prev =>
      prev.includes(service)
        ? prev.filter(s => s !== service)
        : [...prev, service]
    );
  };

  const getStateColor = (state: string) => {
    switch (state.toLowerCase()) {
      case 'running':
        return 'default';
      case 'exited':
      case 'dead':
        return 'destructive';
      case 'restarting':
      case 'paused':
        return 'secondary';
      default:
        return 'outline';
    }
  };

  const getStateIcon = (state: string) => {
    switch (state.toLowerCase()) {
      case 'running':
        return <CheckCircle className="h-4 w-4" />;
      case 'exited':
      case 'dead':
        return <XCircle className="h-4 w-4" />;
      default:
        return <AlertCircle className="h-4 w-4" />;
    }
  };

  // Check if the error is a permission issue
  const isPermissionError = error && (
    (isAxiosError(error) && typeof error.response?.data?.detail === 'string' && error.response.data.detail.includes('permission denied')) ||
    (isAxiosError(error) && typeof error.response?.data?.detail === 'string' && error.response.data.detail.includes('Docker socket')) ||
    (error instanceof Error && error.message.includes('permission denied'))
  );

  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <SectionCard>
        <CollapsibleTrigger asChild>
          <button
            type="button"
            className="flex w-full items-start justify-between text-left -m-2 p-2 rounded-md hover:bg-accent/50 transition-colors"
          >
            <div>
              <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Container management
              </p>
              <p className="text-sm text-muted-foreground mt-1">
                View status and restart Docker containers for BNK Forge services
              </p>
            </div>
            <ChevronDown className={cn(
              'h-5 w-5 transition-transform duration-200 text-muted-foreground shrink-0 mt-1',
              isOpen && 'rotate-180',
            )} />
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="space-y-6 pt-4">
            {isLoading ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : isPermissionError || (error && (!containerData || containerData.containers.length === 0)) ? (
              <div className="rounded-lg border-l-2 border-l-warning border border-border bg-card p-6">
                <div className="flex items-start gap-3">
                  <AlertTriangle className="h-6 w-6 mt-0.5 text-warning" />
                  <div>
                    <h4 className="font-medium mb-2 text-foreground">
                      Docker Socket Not Accessible
                    </h4>
                    <p className="text-sm mb-3 text-foreground/80">
                      Container management requires access to the Docker socket. The backend is running as a non-root
                      user for security, which may not have permission to access the Docker socket on your host.
                    </p>
                    <div className="text-sm space-y-2 text-foreground/80">
                      <p><strong>To fix this, you can:</strong></p>
                      <ol className="list-decimal list-inside space-y-1 ml-2">
                        <li>Add the container user to the host's docker group:
                          <code className="ml-2 px-2 py-0.5 rounded text-xs bg-muted">
                            sudo chmod 666 /var/run/docker.sock
                          </code>
                        </li>
                        <li>Or use container orchestration tools like <code className="font-mono">docker compose ps</code> directly</li>
                      </ol>
                    </div>
                    <p className="text-xs mt-3 text-muted-foreground">
                      Note: This feature is optional. Your application will function normally without container management.
                    </p>
                  </div>
                </div>
              </div>
            ) : containerData && containerData.containers.length > 0 ? (
              <>
                <div className="space-y-3">
                  {containerData.containers.map((container: { service: string; container_name: string; state: string; status: string }) => {
                    const canSelect = container.service !== 'postgres' && container.service !== 'redis';
                    const isSelected = selectedServices.includes(container.service);

                    return (
                      <div
                        key={container.container_name}
                        className={cn(
                          'flex items-center justify-between p-3 border border-border rounded-lg',
                          canSelect ? 'cursor-pointer hover:bg-accent' : 'opacity-50',
                          isSelected && 'bg-accent border-primary',
                        )}
                        onClick={() => canSelect && toggleServiceSelection(container.service)}
                      >
                        <div className="flex items-center gap-3">
                          <input
                            type="checkbox"
                            checked={isSelected}
                            onChange={() => {}}
                            disabled={!canSelect}
                            className="h-4 w-4"
                            onClick={(e) => e.stopPropagation()}
                          />
                          <div>
                            <div className="font-medium">{container.service}</div>
                            <div className="text-xs text-muted-foreground">{container.container_name}</div>
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <Badge variant={getStateColor(container.state)} className="flex items-center gap-1">
                            {getStateIcon(container.state)}
                            {container.state}
                          </Badge>
                        </div>
                      </div>
                    );
                  })}
                </div>

                <div className="flex items-center gap-2 pt-4 border-t border-border">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => refetch()}
                    disabled={isLoading}
                  >
                    <RefreshCw className={`h-4 w-4 mr-2 ${isLoading ? 'animate-spin' : ''}`} />
                    Refresh Status
                  </Button>

                  <Button
                    variant="outline"
                    size="sm"
                    onClick={handleRestartSelected}
                    disabled={restartMutation.isPending || selectedServices.length === 0}
                  >
                    {restartMutation.isPending ? (
                      <><Loader2 className="h-4 w-4 mr-2 animate-spin" /> Restarting...</>
                    ) : (
                      <><RefreshCw className="h-4 w-4 mr-2" /> Restart Selected ({selectedServices.length})</>
                    )}
                  </Button>

                  <Button
                    variant="destructive"
                    size="sm"
                    onClick={handleRestartAll}
                    disabled={restartMutation.isPending}
                  >
                    {restartMutation.isPending ? (
                      <><Loader2 className="h-4 w-4 mr-2 animate-spin" /> Restarting...</>
                    ) : (
                      <><RefreshCw className="h-4 w-4 mr-2" /> Restart All Services</>
                    )}
                  </Button>
                </div>

                <div className="rounded-lg border-l-2 border-l-warning border border-border bg-card p-4">
                  <p className="text-sm text-foreground/80">
                    <strong>Note:</strong> Database services (PostgreSQL, Redis) cannot be restarted from the UI for safety.
                    Restarting the backend will cause a brief API disconnection.
                  </p>
                </div>
              </>
            ) : (
              <div className="text-center py-8 text-muted-foreground">
                <Container className="h-12 w-12 mx-auto mb-4 opacity-50" />
                <p>No containers found</p>
                <p className="text-xs mt-2">Ensure the backend container has access to /var/run/docker.sock</p>
              </div>
            )}
          </div>
        </CollapsibleContent>
      </SectionCard>
    </Collapsible>
  );
}
