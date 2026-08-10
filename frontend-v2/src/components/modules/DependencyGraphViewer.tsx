import { useState, useEffect, useCallback } from 'react';
import { SectionCard } from '@/components/ui/section-card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import ReactFlow, {
  Node,
  Edge,
  Controls,
  Background,
  useNodesState,
  useEdgesState,
  BackgroundVariant,
  MarkerType,
  Position,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { RefreshCw, GitBranch, AlertTriangle } from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '@/lib/api';
import type { ProjectModule } from '@/types';
import { notify } from '@/lib/notify';

interface DependencyGraphViewerProps {
  module: ProjectModule;
}

interface GraphNode {
  id: string;
  type: string;
  name: string;
  address: string;
  dependencies?: string[];
}

interface GraphData {
  nodes: GraphNode[];
  edges: Array<{ from: string; to: string }>;
}

export function DependencyGraphViewer({ module }: DependencyGraphViewerProps) {
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stats, setStats] = useState({ totalNodes: 0, totalEdges: 0, maxDepth: 0 });

  const getNodeColor = (type: string) => {
    const colorMap: Record<string, string> = {
      resource: '#10b981', // green
      data: '#3b82f6', // blue
      module: '#8b5cf6', // purple
      output: '#f59e0b', // amber
      variable: '#ec4899', // pink
      provider: '#6366f1', // indigo
      default: '#64748b', // slate
    };
    return colorMap[type] || colorMap.default;
  };

  const getNodeIcon = (type: string) => {
    const iconMap: Record<string, string> = {
      resource: '🔧',
      data: '📊',
      module: '📦',
      output: '📤',
      variable: '⚙️',
      provider: '☁️',
      default: '●',
    };
    return iconMap[type] || iconMap.default;
  };

  const loadGraph = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.getDependencyGraph(module.id);
      // API returns {exists: true, nodes: [], edges: []} - graph data is at root level
      const graphData: GraphData = {
        nodes: response.nodes || [],
        edges: response.edges || []
      };

      // Calculate layout using a simple hierarchical approach
      const nodeMap = new Map<string, GraphNode>();
      graphData.nodes.forEach((node) => nodeMap.set(node.address, node));

      // Calculate depth for each node
      const depths = new Map<string, number>();
      const visited = new Set<string>();

      const calculateDepth = (nodeAddress: string, depth = 0): number => {
        if (visited.has(nodeAddress)) return depths.get(nodeAddress) || 0;
        visited.add(nodeAddress);

        const node = nodeMap.get(nodeAddress);
        if (!node || !node.dependencies || node.dependencies.length === 0) {
          depths.set(nodeAddress, depth);
          return depth;
        }

        const maxChildDepth = Math.max(
          ...node.dependencies.map((dep) => calculateDepth(dep, depth + 1))
        );
        depths.set(nodeAddress, maxChildDepth);
        return maxChildDepth;
      };

      // Calculate depths for all nodes
      graphData.nodes.forEach((node) => {
        if (!visited.has(node.address)) {
          calculateDepth(node.address);
        }
      });

      const maxDepth = Math.max(...Array.from(depths.values()), 0);

      // Group nodes by depth
      const nodesByDepth = new Map<number, GraphNode[]>();
      graphData.nodes.forEach((node) => {
        const depth = depths.get(node.address) || 0;
        if (!nodesByDepth.has(depth)) {
          nodesByDepth.set(depth, []);
        }
        nodesByDepth.get(depth)!.push(node);
      });

      // Position nodes
      const flowNodes: Node[] = [];
      const horizontalSpacing = 300;
      const verticalSpacing = 150;

      nodesByDepth.forEach((nodesAtDepth, depth) => {
        const yOffset = depth * verticalSpacing;
        const totalWidth = (nodesAtDepth.length - 1) * horizontalSpacing;
        const startX = -totalWidth / 2;

        nodesAtDepth.forEach((node, index) => {
          const xOffset = startX + index * horizontalSpacing;
          const color = getNodeColor(node.type);
          const icon = getNodeIcon(node.type);

          flowNodes.push({
            id: node.address,
            type: 'default',
            position: { x: xOffset, y: yOffset },
            data: {
              label: (
                <div className="flex flex-col items-center gap-1 p-2">
                  <div className="text-2xl">{icon}</div>
                  <div className="font-semibold text-xs text-center">{node.name}</div>
                  <Badge variant="outline" className="text-xs">
                    {node.type}
                  </Badge>
                </div>
              ),
            },
            sourcePosition: Position.Bottom,
            targetPosition: Position.Top,
            style: {
              background: color,
              color: 'white',
              border: '2px solid rgba(255, 255, 255, 0.3)',
              borderRadius: '8px',
              minWidth: '180px',
              fontSize: '11px',
            },
          });
        });
      });

      // Create edges
      const flowEdges: Edge[] = graphData.edges.map((edge, index) => ({
        id: `edge-${index}`,
        source: edge.from,
        target: edge.to,
        type: 'smoothstep',
        animated: true,
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: '#64748b',
        },
        style: {
          stroke: '#64748b',
          strokeWidth: 2,
        },
      }));

      setNodes(flowNodes);
      setEdges(flowEdges);
      setStats({
        totalNodes: flowNodes.length,
        totalEdges: flowEdges.length,
        maxDepth: maxDepth + 1,
      });
    } catch (err: unknown) {
      const message = isAxiosError(err)
        ? err.response?.data?.error?.message || 'Failed to load dependency graph'
        : 'Failed to load dependency graph';
      setError(message);
      notify.error('Failed to load dependency graph');
    } finally {
      setIsLoading(false);
    }
  }, [module.id, setNodes, setEdges]);

  useEffect(() => {
    loadGraph();
  }, [loadGraph]);

  return (
    <SectionCard title="Dependency graph" className="h-full" compact>
      <div className="flex items-center justify-between mb-3">
        <p className="text-sm text-muted-foreground">
          Visual representation of resource dependencies within this module.
        </p>
        <div className="flex items-center gap-2">
          {stats.totalNodes > 0 && (
            <div className="flex items-center gap-3 text-sm text-muted-foreground">
              <span>
                <strong>{stats.totalNodes}</strong> nodes
              </span>
              <span>
                <strong>{stats.totalEdges}</strong> edges
              </span>
              <span>
                <strong>{stats.maxDepth}</strong> depth
              </span>
            </div>
          )}
          <Button size="sm" variant="outline" onClick={loadGraph} disabled={isLoading}>
            <RefreshCw className={`h-4 w-4 mr-2 ${isLoading ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
        </div>
      </div>
      <div className="-mx-4 -mb-4">
        {isLoading && nodes.length === 0 ? (
          <div className="flex items-center justify-center h-[600px] bg-muted/20">
            <div className="text-center">
              <RefreshCw className="h-12 w-12 animate-spin mx-auto mb-4 text-muted-foreground" />
              <p className="text-sm text-muted-foreground">Loading dependency graph...</p>
            </div>
          </div>
        ) : error ? (
          <div className="p-6">
            <Alert variant="destructive">
              <AlertTriangle className="h-4 w-4" />
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          </div>
        ) : nodes.length === 0 ? (
          <div className="flex items-center justify-center h-[600px] bg-muted/20">
            <Alert className="max-w-md">
              <GitBranch className="h-4 w-4" />
              <AlertDescription>
                No dependency graph available. This module may not have been initialized or applied yet.
              </AlertDescription>
            </Alert>
          </div>
        ) : (
          <div className="h-[600px] border-t">
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              fitView
              attributionPosition="bottom-left"
              minZoom={0.1}
              maxZoom={2}
              defaultViewport={{ x: 0, y: 0, zoom: 0.8 }}
            >
              <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
              <Controls
                showZoom
                showFitView
                showInteractive
                position="bottom-right"
              />
            </ReactFlow>
          </div>
        )}
      </div>
    </SectionCard>
  );
}
