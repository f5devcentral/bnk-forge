import { useState } from 'react';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { usePodMetrics, useNodeMetrics } from '@/hooks/useK8s';
import { Activity, AlertCircle, TrendingDown, Server, Box, RefreshCw } from 'lucide-react';
import type { K8sPodMetric, K8sNodeMetric } from '@/types';

interface ResourceMetricsViewerProps {
  clusterId: number;
  namespace?: string;
  open: boolean;
  onClose: () => void;
}

export function ResourceMetricsViewer({ clusterId, namespace, open, onClose }: ResourceMetricsViewerProps) {
  const [activeTab, setActiveTab] = useState<'pods' | 'nodes'>('pods');
  const [sortBy, setSortBy] = useState<'cpu' | 'memory' | undefined>(undefined);
  const [pollingEnabled, setPollingEnabled] = useState(true);

  const {
    data: podMetricsData,
    isLoading: podMetricsLoading,
    refetch: refetchPodMetrics,
  } = usePodMetrics(
    clusterId,
    { namespace, sort_by: sortBy },
    { enabled: open && activeTab === 'pods', pollingEnabled }
  );

  const {
    data: nodeMetricsData,
    isLoading: nodeMetricsLoading,
    refetch: refetchNodeMetrics,
  } = useNodeMetrics(
    clusterId,
    { sort_by: sortBy },
    { enabled: open && activeTab === 'nodes', pollingEnabled }
  );

  const handleRefresh = () => {
    if (activeTab === 'pods') {
      refetchPodMetrics();
    } else {
      refetchNodeMetrics();
    }
  };

  const formatCPU = (millicores: number) => {
    if (millicores < 1000) {
      return `${millicores}m`;
    }
    return `${(millicores / 1000).toFixed(2)}`;
  };

  const formatMemory = (bytes: number) => {
    const units = ['B', 'Ki', 'Mi', 'Gi', 'Ti'];
    let value = bytes;
    let unitIndex = 0;

    while (value >= 1024 && unitIndex < units.length - 1) {
      value /= 1024;
      unitIndex++;
    }

    return `${value.toFixed(1)}${units[unitIndex]}`;
  };

  const getUsageColor = (percent: number) => {
    if (percent >= 90) return 'text-destructive';
    if (percent >= 80) return 'text-warning';
    if (percent >= 60) return 'text-warning';
    return 'text-success';
  };

  const getUsageBadgeColor = (percent: number): 'destructive' | 'warning' | 'success' => {
    if (percent >= 90) return 'destructive';
    if (percent >= 80) return 'warning';
    return 'success';
  };

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-4xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Activity className="w-5 h-5" />
              <DialogTitle>Resource Metrics</DialogTitle>
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPollingEnabled(!pollingEnabled)}
              >
                <RefreshCw className={`w-4 h-4 mr-1 ${pollingEnabled ? 'animate-spin' : ''}`} />
                {pollingEnabled ? 'Auto-refresh On' : 'Auto-refresh Off'}
              </Button>
              <Button variant="outline" size="sm" onClick={handleRefresh}>
                <RefreshCw className="w-4 h-4 mr-1" />
                Refresh
              </Button>
            </div>
          </div>
          <DialogDescription>
            View CPU and memory usage for pods and nodes
            {namespace && ` in namespace: ${namespace}`}
          </DialogDescription>
        </DialogHeader>

        <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as 'pods' | 'nodes')}>
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="pods">
              <Box className="w-4 h-4 mr-2" />
              Pods
            </TabsTrigger>
            <TabsTrigger value="nodes">
              <Server className="w-4 h-4 mr-2" />
              Nodes
            </TabsTrigger>
          </TabsList>

          <TabsContent value="pods" className="space-y-4">
            {podMetricsLoading && (
              <div className="text-center py-8 text-muted-foreground">
                Loading pod metrics...
              </div>
            )}

            {!podMetricsLoading && !podMetricsData?.available && (
              <Alert variant="destructive">
                <AlertCircle className="w-4 h-4" />
                <AlertDescription>
                  {podMetricsData?.error || 'Metrics server not available'}
                </AlertDescription>
              </Alert>
            )}

            {!podMetricsLoading && podMetricsData?.available && (
              <>
                <div className="flex gap-2 mb-4">
                  <Button
                    variant={sortBy === 'cpu' ? 'default' : 'outline'}
                    size="sm"
                    onClick={() => setSortBy(sortBy === 'cpu' ? undefined : 'cpu')}
                  >
                    Sort by CPU {sortBy === 'cpu' && <TrendingDown className="w-3 h-3 ml-1" />}
                  </Button>
                  <Button
                    variant={sortBy === 'memory' ? 'default' : 'outline'}
                    size="sm"
                    onClick={() => setSortBy(sortBy === 'memory' ? undefined : 'memory')}
                  >
                    Sort by Memory {sortBy === 'memory' && <TrendingDown className="w-3 h-3 ml-1" />}
                  </Button>
                </div>

                <div className="border rounded-lg overflow-hidden">
                  <table className="w-full">
                    <thead className="bg-muted">
                      <tr className="text-left text-sm">
                        <th className="p-3 font-medium">Pod</th>
                        <th className="p-3 font-medium">Namespace</th>
                        <th className="p-3 font-medium text-right">CPU</th>
                        <th className="p-3 font-medium text-right">Memory</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y">
                      {podMetricsData?.metrics?.length === 0 && (
                        <tr>
                          <td colSpan={4} className="p-8 text-center text-muted-foreground">
                            No pod metrics available
                          </td>
                        </tr>
                      )}
                      {podMetricsData?.metrics?.map((pod: K8sPodMetric, index: number) => (
                        <tr key={index} className="hover:bg-muted/50">
                          <td className="p-3 font-mono text-sm">{pod.name}</td>
                          <td className="p-3 text-sm">
                            <Badge variant="outline">{pod.namespace}</Badge>
                          </td>
                          <td className="p-3 text-right font-mono text-sm">
                            <span className={getUsageColor(80)}>
                              {formatCPU(pod.cpu_millicores ?? 0)}
                            </span>
                          </td>
                          <td className="p-3 text-right font-mono text-sm">
                            <span className={getUsageColor(80)}>
                              {formatMemory(pod.memory_bytes ?? 0)}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <div className="text-sm text-muted-foreground">
                  Showing {podMetricsData?.metrics?.length || 0} pods
                  {pollingEnabled && ' · Auto-refreshing every 10 seconds'}
                </div>
              </>
            )}
          </TabsContent>

          <TabsContent value="nodes" className="space-y-4">
            {nodeMetricsLoading && (
              <div className="text-center py-8 text-muted-foreground">
                Loading node metrics...
              </div>
            )}

            {!nodeMetricsLoading && !nodeMetricsData?.available && (
              <Alert variant="destructive">
                <AlertCircle className="w-4 h-4" />
                <AlertDescription>
                  {nodeMetricsData?.error || 'Metrics server not available'}
                </AlertDescription>
              </Alert>
            )}

            {!nodeMetricsLoading && nodeMetricsData?.available && (
              <>
                <div className="flex gap-2 mb-4">
                  <Button
                    variant={sortBy === 'cpu' ? 'default' : 'outline'}
                    size="sm"
                    onClick={() => setSortBy(sortBy === 'cpu' ? undefined : 'cpu')}
                  >
                    Sort by CPU {sortBy === 'cpu' && <TrendingDown className="w-3 h-3 ml-1" />}
                  </Button>
                  <Button
                    variant={sortBy === 'memory' ? 'default' : 'outline'}
                    size="sm"
                    onClick={() => setSortBy(sortBy === 'memory' ? undefined : 'memory')}
                  >
                    Sort by Memory {sortBy === 'memory' && <TrendingDown className="w-3 h-3 ml-1" />}
                  </Button>
                </div>

                <div className="border rounded-lg overflow-hidden">
                  <table className="w-full">
                    <thead className="bg-muted">
                      <tr className="text-left text-sm">
                        <th className="p-3 font-medium">Node</th>
                        <th className="p-3 font-medium text-right">CPU</th>
                        <th className="p-3 font-medium text-right">CPU %</th>
                        <th className="p-3 font-medium text-right">Memory</th>
                        <th className="p-3 font-medium text-right">Memory %</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y">
                      {nodeMetricsData?.metrics?.length === 0 && (
                        <tr>
                          <td colSpan={5} className="p-8 text-center text-muted-foreground">
                            No node metrics available
                          </td>
                        </tr>
                      )}
                      {nodeMetricsData?.metrics?.map((node: K8sNodeMetric, index: number) => (
                        <tr key={index} className="hover:bg-muted/50">
                          <td className="p-3 font-mono text-sm">{node.name}</td>
                          <td className="p-3 text-right font-mono text-sm">
                            <div>{formatCPU(node.cpu_millicores ?? 0)}</div>
                            <div className="text-xs text-muted-foreground">
                              / {formatCPU(node.allocatable_cpu_millicores ?? 0)}
                            </div>
                          </td>
                          <td className="p-3 text-right">
                            <Badge variant={getUsageBadgeColor(node.cpu_percent ?? 0)}>
                              <span className={getUsageColor(node.cpu_percent ?? 0)}>
                                {node.cpu_percent ?? 0}%
                              </span>
                            </Badge>
                          </td>
                          <td className="p-3 text-right font-mono text-sm">
                            <div>{formatMemory(node.memory_bytes ?? 0)}</div>
                            <div className="text-xs text-muted-foreground">
                              / {formatMemory(node.allocatable_memory_bytes ?? 0)}
                            </div>
                          </td>
                          <td className="p-3 text-right">
                            <Badge variant={getUsageBadgeColor(node.memory_percent ?? 0)}>
                              <span className={getUsageColor(node.memory_percent ?? 0)}>
                                {node.memory_percent ?? 0}%
                              </span>
                            </Badge>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <div className="text-sm text-muted-foreground">
                  Showing {nodeMetricsData?.metrics?.length || 0} nodes
                  {pollingEnabled && ' · Auto-refreshing every 10 seconds'}
                </div>
              </>
            )}
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
