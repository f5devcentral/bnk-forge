/**
 * DeploymentPipeline — Visual ReactFlow pipeline during parallel deployments (UX-004)
 *
 * Shows a visual diagram of modules grouped by dependency layer:
 * - Nodes represent modules with status icons, progress bars, and names
 * - Edges show dependency relationships between layers
 * - Each node is clickable to view per-module log output
 * - Progress bars calculated from time estimates
 *
 * Uses existing WebSocket hooks for real-time status updates.
 */

import { useMemo, useCallback, useState } from 'react';
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
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { useTheme, useThemeClasses } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';
import {
  CheckCircle2,
  Clock,
  Loader2,
  XCircle,
  AlertTriangle,
  SkipForward,
} from 'lucide-react';
import type {
  RunProgress,
  ExecutionPlan,
  ProjectModule,
} from '@/types';
import { LogViewerModal } from '@/components/deployments/LogViewerModal';

// ============================================================================
// Types
// ============================================================================

interface DeploymentPipelineProps {
  /** Execution plan with layer info and time estimates */
  executionPlan: ExecutionPlan | undefined;
  /** Live run progress from the new /orchestration/{run_handle} endpoint */
  executionStatus: RunProgress | undefined;
  /** All project modules (for dependency edges + status) */
  modules: ProjectModule[];
  /** Override container height (px). When set, overrides the smart default. */
  containerHeight?: number;
}

type ModuleNodeStatus = 'pending' | 'running' | 'completed' | 'failed' | 'skipped';

interface ModuleNodeData {
  label: string;
  moduleId: number;
  modulePath: string;
  status: ModuleNodeStatus;
  estimatedTimeMinutes: number;
  layerIndex: number;
  elapsedPercent: number;
  error?: string | null;
  stageDetail?: string | null;
}

// ============================================================================
// Status helpers
// ============================================================================

const statusConfig: Record<ModuleNodeStatus, {
  icon: React.ElementType;
  color: string;
  bgColor: string;
  borderColor: string;
  label: string;
  animate?: boolean;
}> = {
  pending: {
    icon: Clock,
    color: 'text-zinc-400',
    bgColor: 'bg-zinc-500/10',
    borderColor: 'border-zinc-500/30',
    label: 'Waiting',
  },
  running: {
    icon: Loader2,
    color: 'text-blue-400',
    bgColor: 'bg-blue-500/10',
    borderColor: 'border-blue-500/50',
    label: 'Running',
    animate: true,
  },
  completed: {
    icon: CheckCircle2,
    color: 'text-emerald-400',
    bgColor: 'bg-emerald-500/10',
    borderColor: 'border-emerald-500/30',
    label: 'Done',
  },
  failed: {
    icon: XCircle,
    color: 'text-red-400',
    bgColor: 'bg-red-500/10',
    borderColor: 'border-red-500/50',
    label: 'Failed',
  },
  skipped: {
    icon: SkipForward,
    color: 'text-amber-400',
    bgColor: 'bg-amber-500/10',
    borderColor: 'border-amber-500/30',
    label: 'Skipped',
  },
};

function resolveModuleStatus(
  moduleId: number,
  executionStatus: RunProgress | undefined,
  moduleStatus: string
): ModuleNodeStatus {
  // module.status is the live truth — kept fresh via WebSocket task-update
  // invalidation. Prefer it when it has a definitive answer.
  if (moduleStatus === 'deployed' || moduleStatus === 'applied') return 'completed';
  if (moduleStatus === 'failed' || moduleStatus === 'apply_failed' || moduleStatus === 'plan_failed' || moduleStatus === 'destroy_failed' || moduleStatus === 'init_failed') return 'failed';
  if (moduleStatus === 'applying' || moduleStatus === 'planning' || moduleStatus === 'initializing' || moduleStatus === 'destroying') return 'running';

  // Module hasn't started yet — fall back to orchestrator hints if present
  // (skipped tracking, layer pending state).
  if (executionStatus) {
    if (executionStatus.skipped_modules?.includes(moduleId)) return 'skipped';
    for (const layer of executionStatus.layers || []) {
      if (layer.module_ids?.includes(moduleId) && layer.status === 'in_progress') {
        return 'running';
      }
    }
  }

  return 'pending';
}

function calculateElapsedPercent(
  moduleId: number,
  estimatedMinutes: number,
  executionStatus: RunProgress | undefined
): number {
  if (!executionStatus || !executionStatus.started_at) return 0;

  const status = resolveModuleStatus(moduleId, executionStatus, '');
  if (status === 'completed') return 100;
  if (status === 'failed') return 100;
  if (status === 'pending' || status === 'skipped') return 0;

  // Running: estimate from elapsed time
  const startedAt = new Date(executionStatus.started_at).getTime();
  const now = Date.now();
  const elapsedMinutes = (now - startedAt) / 60000;
  if (estimatedMinutes <= 0) return 50; // Default to 50% if no estimate
  return Math.min(95, (elapsedMinutes / estimatedMinutes) * 100);
}

// ============================================================================
// Node rendering
// ============================================================================

function ModuleNodeLabel({ data }: { data: ModuleNodeData }) {
  const cfg = statusConfig[data.status];
  const StatusIcon = cfg.icon;

  return (
    <div className="flex flex-col items-stretch gap-1.5 min-w-[160px] max-w-[200px]">
      <div className="flex items-center gap-2">
        {/* While running with a live phase, the spinner moves down to the phase
            line, so the name row shows just the module name. */}
        {!(data.status === 'running' && data.stageDetail) && (
          <StatusIcon
            className={cn('h-4 w-4 flex-shrink-0', cfg.color, cfg.animate && 'animate-spin')}
          />
        )}
        <span className="text-xs font-semibold truncate flex-1">{data.label}</span>
      </div>

      {/* Live phase (stage_detail) while running, e.g. "[3/6] deploy-prereqs",
          with the spinner on the same line. */}
      {data.status === 'running' && data.stageDetail && (
        <div className="flex items-center gap-1.5">
          <StatusIcon
            className={cn('h-3.5 w-3.5 flex-shrink-0', cfg.color, cfg.animate && 'animate-spin')}
          />
          <span className="text-[10px] font-medium text-info truncate animate-pulse" title={data.stageDetail}>
            {data.stageDetail}
          </span>
        </div>
      )}

      {/* Progress bar */}
      {(data.status === 'running' || data.status === 'completed' || data.status === 'failed') && (
        <div className="w-full">
          <Progress
            value={data.elapsedPercent}
            className="h-1.5"
          />
        </div>
      )}

      <div className="flex items-center justify-between">
        <Badge
          variant="outline"
          className={cn('text-[9px] py-0 px-1.5', cfg.color, cfg.borderColor)}
        >
          {cfg.label}
        </Badge>
        {data.estimatedTimeMinutes > 0 && (
          <span className="text-[9px] text-zinc-500">
            ~{data.estimatedTimeMinutes}m
          </span>
        )}
      </div>

      {data.error && (
        <div className="flex items-start gap-1 mt-0.5">
          <AlertTriangle className="h-3 w-3 text-red-400 flex-shrink-0 mt-0.5" />
          <span className="text-[9px] text-red-400 line-clamp-2">{data.error}</span>
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Main Component
// ============================================================================

export function DeploymentPipeline({
  executionPlan,
  executionStatus,
  modules,
  containerHeight: containerHeightOverride,
}: DeploymentPipelineProps) {
  const { isDark } = useTheme();
  const { borderDefault } = useThemeClasses();
  const [logModuleId, setLogModuleId] = useState<number | null>(null);

  // Build nodes and edges from execution plan
  const { flowNodes, flowEdges } = useMemo(() => {
    if (!executionPlan || !executionPlan.layers || executionPlan.layers.length === 0) {
      return { flowNodes: [], flowEdges: [] };
    }

    const nodes: Node<ModuleNodeData>[] = [];
    const edges: Edge[] = [];

    // Always render top-to-bottom (TB). Nothing scrolls — the container and
    // ReactFlow wrapper are overflow-hidden. Chains longer than ~7 layers
    // render centered and cropped top+bottom instead; pan or use the
    // Controls fit-view button to see the whole graph.
    const tbHSpacing = 240; // horizontal gap between nodes in same layer
    const tbVSpacing = 130; // vertical gap between layers
    const nodeWidth = 200;

    // Build module lookup from project modules
    const moduleMap = new Map<number, ProjectModule>();
    modules.forEach((m) => moduleMap.set(m.id, m));

    executionPlan.layers.forEach((layer) => {
      const layerModules = layer.modules || [];
      const layerIdx = layer.layer_index;

      const modulesInLayer = layerModules.length;
      const totalSpan = (modulesInLayer - 1) * tbHSpacing;
      const startOffset = -totalSpan / 2;

      layerModules.forEach((mod, modIdx) => {
        const projectModule = moduleMap.get(mod.id);
        const moduleStatus = resolveModuleStatus(
          mod.id,
          executionStatus,
          projectModule?.status || mod.status
        );
        const elapsedPercent = calculateElapsedPercent(
          mod.id,
          mod.estimated_time_minutes,
          executionStatus
        );

        // Get error from execution results
        let error: string | null = null;
        if (executionStatus) {
          for (const ld of executionStatus.layers || []) {
            // results is keyed by module_id as string in RunProgress
            const result = ld.results?.[String(mod.id)];
            if (result && !result.success && result.error) {
              error = result.error;
              break;
            }
          }
        }

        const nodeData: ModuleNodeData = {
          label: mod.name,
          moduleId: mod.id,
          modulePath: mod.path,
          status: moduleStatus,
          estimatedTimeMinutes: mod.estimated_time_minutes,
          layerIndex: layerIdx,
          elapsedPercent,
          error,
          stageDetail: projectModule?.stage_detail ?? null,
        };

        nodes.push({
          id: `module-${mod.id}`,
          type: 'default',
          position: { x: startOffset + modIdx * tbHSpacing, y: layerIdx * tbVSpacing },
          data: nodeData,
          sourcePosition: Position.Bottom,
          targetPosition: Position.Top,
          style: {
            background: isDark ? '#18181b' : '#ffffff',
            border: `2px solid ${
              moduleStatus === 'running' ? '#3b82f6' :
              moduleStatus === 'completed' ? '#10b981' :
              moduleStatus === 'failed' ? '#ef4444' :
              moduleStatus === 'skipped' ? '#f59e0b' :
              isDark ? '#3f3f46' : '#e2e8f0'
            }`,
            borderRadius: '12px',
            padding: '12px',
            minWidth: `${nodeWidth}px`,
            cursor: 'pointer',
            boxShadow: moduleStatus === 'running'
              ? '0 0 12px rgba(59, 130, 246, 0.3)'
              : 'none',
          },
        });

        // Create edges from this layer to the next layer
        if (layerIdx < executionPlan.layers.length - 1) {
          const nextLayer = executionPlan.layers[layerIdx + 1];
          const nextModules = nextLayer.modules || [];
          nextModules.forEach((nextMod) => {
            // Check if there's a dependency relationship
            const nextProjectModule = moduleMap.get(nextMod.id);
            if (nextProjectModule?.dependencies?.includes(mod.id)) {
              edges.push({
                id: `edge-${mod.id}-${nextMod.id}`,
                source: `module-${mod.id}`,
                target: `module-${nextMod.id}`,
                type: 'smoothstep',
                animated: moduleStatus === 'running' || moduleStatus === 'completed',
                markerEnd: {
                  type: MarkerType.ArrowClosed,
                  color: isDark ? '#52525b' : '#94a3b8',
                },
                style: {
                  stroke: moduleStatus === 'completed'
                    ? '#10b981'
                    : moduleStatus === 'running'
                      ? '#3b82f6'
                      : isDark ? '#3f3f46' : '#cbd5e1',
                  strokeWidth: 2,
                },
              });
            }
          });
        }
      });
    });

    // If no explicit dependency edges were created, create layer-to-layer edges
    if (edges.length === 0 && executionPlan.layers.length > 1) {
      for (let li = 0; li < executionPlan.layers.length - 1; li++) {
        const currentMods = executionPlan.layers[li].modules || [];
        const nextMods = executionPlan.layers[li + 1].modules || [];

        // Connect last module of current layer to first module of next layer
        if (currentMods.length > 0 && nextMods.length > 0) {
          currentMods.forEach((curMod) => {
            nextMods.forEach((nextMod) => {
              const curStatus = resolveModuleStatus(curMod.id, executionStatus, curMod.status);
              edges.push({
                id: `layer-edge-${curMod.id}-${nextMod.id}`,
                source: `module-${curMod.id}`,
                target: `module-${nextMod.id}`,
                type: 'smoothstep',
                animated: curStatus === 'running' || curStatus === 'completed',
                markerEnd: {
                  type: MarkerType.ArrowClosed,
                  color: isDark ? '#52525b' : '#94a3b8',
                },
                style: {
                  stroke: curStatus === 'completed'
                    ? '#10b981'
                    : curStatus === 'running'
                      ? '#3b82f6'
                      : isDark ? '#3f3f46' : '#cbd5e1',
                  strokeWidth: 1.5,
                  strokeDasharray: '5,5',
                },
              });
            });
          });
        }
      }
    }

    return { flowNodes: nodes, flowEdges: edges };
  }, [executionPlan, executionStatus, modules, isDark]);

  const [nodes, setNodes, onNodesChange] = useNodesState(flowNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(flowEdges);

  // Update nodes when data changes
  useMemo(() => {
    setNodes(flowNodes);
    setEdges(flowEdges);
  }, [flowNodes, flowEdges, setNodes, setEdges]);

  // Custom node rendering via data.label
  const nodesWithLabels = useMemo(() =>
    nodes.map((node) => ({
      ...node,
      data: {
        ...node.data,
        label: <ModuleNodeLabel data={node.data as ModuleNodeData} />,
      },
    })),
    [nodes]
  );

  const handleNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    const data = node.data as ModuleNodeData;
    if (data.moduleId) {
      setLogModuleId(data.moduleId);
    }
  }, []);

  // Container height: clamp between 300px (short chains look fine) and 600px (viewport
  // budget). Chains taller than 600px scroll — nodes are never squashed.
  const defaultHeight = useMemo(() => {
    if (!executionPlan || !executionPlan.layers) return 300;
    const naturalHeight = executionPlan.layers.length * 130 + 120;
    return Math.min(600, Math.max(300, naturalHeight));
  }, [executionPlan]);

  const effectiveHeight = containerHeightOverride || defaultHeight;

  if (!executionPlan || flowNodes.length === 0) {
    return (
      <div className={cn(
        'flex items-center justify-center py-12 text-center',
        isDark ? 'text-zinc-500' : 'text-slate-400'
      )}>
        <div>
          <Clock className="h-10 w-10 mx-auto mb-3 opacity-30" />
          <p className="text-sm font-medium">No execution plan available</p>
          <p className="text-xs mt-1">Run Deploy All to see the visual pipeline</p>
        </div>
      </div>
    );
  }

  return (
    <>
      <div
        className={cn(
          'rounded-xl border overflow-hidden',
          borderDefault,
          isDark ? 'bg-zinc-900/50' : 'bg-white'
        )}
        style={{ height: effectiveHeight, display: 'flex', flexDirection: 'column' }}
      >
        {/* Pipeline header */}
        <div className={cn(
          'px-4 py-2 border-b flex items-center justify-between',
          borderDefault,
          isDark ? 'bg-zinc-800/50' : 'bg-slate-50'
        )}>
          <div className="flex items-center gap-2">
            <span className={cn('text-sm font-semibold', isDark ? 'text-white' : 'text-slate-900')}>
              Deployment Pipeline
            </span>
            <Badge variant="outline" className="text-xs">
              {executionPlan.total_layers} layers / {executionPlan.total_modules} modules
            </Badge>
          </div>
          {executionStatus && (
            <div className="flex items-center gap-3 text-xs">
              <span className={isDark ? 'text-zinc-400' : 'text-slate-500'}>
                {Math.round(executionStatus.progress_percent)}% complete
              </span>
            </div>
          )}
        </div>

        {/* ReactFlow canvas — flex-1 so it fills remaining height after the header.
            fitView is enabled with a minZoom floor of 0.6 so nodes stay readable;
            past ~7 layers the fit hits that floor and gets clamped, so the graph
            is centered and cropped top+bottom on first render. Escape hatches:
            panOnDrag, the Controls fit-view button (viewport minZoom is 0.4, so
            it can zoom out further and show everything), or the drag-resize
            handle in ProjectDetailV2 to grow the container. */}
        <ReactFlow
          nodes={nodesWithLabels}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={handleNodeClick}
          fitView
          fitViewOptions={{ padding: 0.15, minZoom: 0.6 }}
          attributionPosition="bottom-left"
          minZoom={0.4}
          maxZoom={1.5}
          nodesDraggable={false}
          nodesConnectable={false}
          panOnDrag
          proOptions={{ hideAttribution: true }}
          style={{ flex: 1 }}
        >
          <Background
            variant={BackgroundVariant.Dots}
            gap={20}
            size={1}
            color={isDark ? '#27272a' : '#e2e8f0'}
          />
          <Controls
            showZoom
            showFitView
            showInteractive={false}
            position="bottom-right"
            className={isDark ? 'react-flow__controls-dark' : ''}
          />
        </ReactFlow>
      </div>

      {/* Log viewer modal */}
      {logModuleId && (
        <LogViewerModal
          moduleId={logModuleId}
          moduleName={modules.find((m) => m.id === logModuleId)?.name || 'Module'}
          open={!!logModuleId}
          onOpenChange={(open) => {
            if (!open) setLogModuleId(null);
          }}
        />
      )}
    </>
  );
}
