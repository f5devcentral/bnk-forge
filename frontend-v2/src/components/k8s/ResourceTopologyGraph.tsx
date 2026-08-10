/**
 * ResourceTopologyGraph — Generic Kubernetes namespace topology (D-018 P4).
 *
 * Renders Services → Pods → owning workloads as a reactflow v11 + dagre
 * directed graph for a selected cluster namespace.
 *
 * - Service → Pod edges ("selects") are animated (ant-trail).
 * - Workload → Pod edges ("owns") are static.
 * - Node color/icon via getSeverityConfig from lib/health-severity.ts.
 * - Node click → calls back onDescribeResource (synthesises minimal K8sResource
 *   shape so CNF's existing ResourceDescribeViewer can fetch and display it).
 * - READ-ONLY — no mutation.
 * - Layout options: Horizontal (LR dagre), Vertical (TB dagre), Radial (concentric rings).
 */

import { useMemo, useCallback, useState, useEffect } from 'react';
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  type Edge,
  MarkerType,
  type Node,
  Panel,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
} from 'reactflow';
import 'reactflow/dist/style.css';
import dagre from '@dagrejs/dagre';
import { AlertTriangle, Globe } from 'lucide-react';
import { EmptyState } from '@/components/ui/empty-state';
import { Button } from '@/components/ui/button';
import { getSeverityConfig } from '@/lib/health-severity';
import type { TopologyEdge, TopologyNode } from '@/hooks/useTopology';
import type { K8sResource } from '@/types/kubernetes';

// ---------------------------------------------------------------------------
// Layout types
// ---------------------------------------------------------------------------

type LayoutMode = 'horizontal' | 'vertical' | 'radial';

// ---------------------------------------------------------------------------
// Layout constants
// ---------------------------------------------------------------------------

const NODE_WIDTH = 180;
const NODE_HEIGHT = 60;

// Radial layout constants
const BASE_RADIUS = 120;
const RING_GAP = 140;
const MIN_RADIUS = 100;
const NODE_GAP = 20;

// Ring order for radial layout: index 0 = innermost ring
const RADIAL_RING_ORDER: string[] = ['Service', 'Pod', 'Deployment', 'StatefulSet', 'DaemonSet', 'ReplicaSet'];

// ---------------------------------------------------------------------------
// Layout helpers
// ---------------------------------------------------------------------------

function applyDagreLayout(
  rfNodes: Node[],
  rfEdges: Edge[],
  rankdir: 'LR' | 'TB',
): { nodes: Node[]; edges: Edge[] } {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir, ranksep: 80, nodesep: 40 });

  rfNodes.forEach((n) => g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT }));
  rfEdges.forEach((e) => g.setEdge(e.source, e.target));
  dagre.layout(g);

  const positioned = rfNodes.map((n) => {
    const pos = g.node(n.id);
    return {
      ...n,
      position: { x: pos.x - NODE_WIDTH / 2, y: pos.y - NODE_HEIGHT / 2 },
    };
  });

  return { nodes: positioned, edges: rfEdges };
}

function applyRadialLayout(
  rfNodes: Node[],
  rfEdges: Edge[],
  topologyNodes: TopologyNode[],
): { nodes: Node[]; edges: Edge[] } {
  // Group node ids by kind
  const byKind = new Map<string, string[]>();
  for (const tn of topologyNodes) {
    const existing = byKind.get(tn.kind) ?? [];
    existing.push(tn.id);
    byKind.set(tn.kind, existing);
  }

  // Build ring order: known kinds first (in RADIAL_RING_ORDER), then unknown kinds sorted
  const knownKinds = RADIAL_RING_ORDER.filter((k) => byKind.has(k));
  const unknownKinds = [...byKind.keys()].filter((k) => !RADIAL_RING_ORDER.includes(k)).sort();
  const ringKinds = [...knownKinds, ...unknownKinds];

  // Assign each kind a radius sized so circumference fits node count
  const radiusByKind = new Map<string, number>();
  let ringIndex = 0;
  for (const kind of ringKinds) {
    const count = byKind.get(kind)?.length ?? 0;
    const circumferenceRadius = (count * (NODE_WIDTH + NODE_GAP)) / (2 * Math.PI);
    const baseRadius = BASE_RADIUS + ringIndex * RING_GAP;
    const radius = Math.max(MIN_RADIUS, circumferenceRadius, baseRadius);
    radiusByKind.set(kind, radius);
    ringIndex++;
  }

  // Build a lookup from node id → kind for positioning
  const kindById = new Map<string, string>();
  for (const tn of topologyNodes) {
    kindById.set(tn.id, tn.kind);
  }

  // Track per-kind placement index
  const placementIndex = new Map<string, number>();
  for (const kind of ringKinds) {
    placementIndex.set(kind, 0);
  }

  const positioned = rfNodes.map((n) => {
    const kind = kindById.get(n.id) ?? 'Unknown';
    const count = byKind.get(kind)?.length ?? 1;
    const radius = radiusByKind.get(kind) ?? BASE_RADIUS;
    const idx = placementIndex.get(kind) ?? 0;
    placementIndex.set(kind, idx + 1);

    // Offset angle so first node in a ring starts at top
    const theta = (idx / count) * 2 * Math.PI - Math.PI / 2;
    const x = radius * Math.cos(theta) - NODE_WIDTH / 2;
    const y = radius * Math.sin(theta) - NODE_HEIGHT / 2;

    return { ...n, position: { x, y } };
  });

  return { nodes: positioned, edges: rfEdges };
}

function getLayoutedElements(
  rfNodes: Node[],
  rfEdges: Edge[],
  layout: LayoutMode,
  topologyNodes: TopologyNode[],
): { nodes: Node[]; edges: Edge[] } {
  if (rfNodes.length === 0) return { nodes: rfNodes, edges: rfEdges };

  if (layout === 'radial') {
    return applyRadialLayout(rfNodes, rfEdges, topologyNodes);
  }

  const rankdir = layout === 'vertical' ? 'TB' : 'LR';
  return applyDagreLayout(rfNodes, rfEdges, rankdir);
}

// ---------------------------------------------------------------------------
// Node rendering
// ---------------------------------------------------------------------------

const KIND_LABEL: Record<string, string> = {
  Deployment: 'Deploy',
  StatefulSet: 'STS',
  DaemonSet: 'DS',
  ReplicaSet: 'RS',
  Service: 'SVC',
  Pod: 'Pod',
};

function buildRfNode(n: TopologyNode): Node {
  const cfg = getSeverityConfig(n.severity);
  const shortKind = KIND_LABEL[n.kind] ?? n.kind;

  return {
    id: n.id,
    type: 'default',
    data: {
      label: (
        <div className="flex flex-col items-center gap-0.5 px-1 w-full overflow-hidden">
          <span
            className={`text-[9px] font-bold uppercase tracking-wider px-1 rounded ${cfg.color} ${cfg.bg}`}
          >
            {shortKind}
          </span>
          <span className="text-xs font-medium truncate w-full text-center leading-tight">
            {n.name}
          </span>
        </div>
      ),
    },
    position: { x: 0, y: 0 },
    style: {
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
      border: `1.5px solid`,
      borderRadius: 8,
      background: 'transparent',
      cursor: 'pointer',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
    },
    className: `${cfg.bg} ${cfg.border}`,
  };
}

function buildRfEdge(e: TopologyEdge): Edge {
  const isSelects = e.kind === 'selects';
  return {
    id: e.id,
    source: e.source,
    target: e.target,
    animated: isSelects,
    markerEnd: { type: MarkerType.ArrowClosed, width: 12, height: 12 },
    style: {
      strokeDasharray: isSelects ? '5 3' : undefined,
      stroke: isSelects ? '#6366f1' : '#94a3b8',
      strokeWidth: isSelects ? 2 : 1.5,
    },
  };
}

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface ResourceTopologyGraphProps {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
  info?: string | null;
  onDescribeResource: (resource: K8sResource) => void;
}

// ---------------------------------------------------------------------------
// Inner component (needs ReactFlow context for useReactFlow)
// ---------------------------------------------------------------------------

interface TopologyInnerProps extends ResourceTopologyGraphProps {
  layout: LayoutMode;
  onLayoutChange: (layout: LayoutMode) => void;
}

function TopologyInner({
  nodes: topologyNodes,
  edges: topologyEdges,
  info,
  onDescribeResource,
  layout,
  onLayoutChange,
}: TopologyInnerProps) {
  const { fitView } = useReactFlow();

  const { nodes: initialNodes, edges: initialEdges } = useMemo(() => {
    if (topologyNodes.length === 0) return { nodes: [], edges: [] };

    const rfNodes = topologyNodes.map(buildRfNode);
    const rfEdges = topologyEdges.map(buildRfEdge);
    return getLayoutedElements(rfNodes, rfEdges, layout, topologyNodes);
  }, [topologyNodes, topologyEdges, layout]);

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  // Push recomputed layout into state whenever layout or data changes
  useEffect(() => {
    setNodes(initialNodes);
    setEdges(initialEdges);
  }, [initialNodes, initialEdges, setNodes, setEdges]);

  // Fit view whenever layout or data changes
  useEffect(() => {
    // Use a short timeout so ReactFlow has time to render the repositioned nodes
    const id = setTimeout(() => {
      void fitView({ padding: 0.2 });
    }, 50);
    return () => clearTimeout(id);
  }, [layout, initialNodes, fitView]);

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      // Parse node id: "{kind}/{namespace}/{name}"
      const parts = node.id.split('/');
      if (parts.length < 3) return;
      const [kind, namespace, ...nameParts] = parts;
      const name = nameParts.join('/');

      // Synthesise minimal K8sResource so ResourceDescribeViewer can fetch it
      const resource: K8sResource = {
        kind,
        metadata: { name, namespace },
      } as unknown as K8sResource;

      onDescribeResource(resource);
    },
    [onDescribeResource],
  );

  const layoutButtons: { label: string; value: LayoutMode }[] = [
    { label: 'Horizontal', value: 'horizontal' },
    { label: 'Vertical', value: 'vertical' },
    { label: 'Radial', value: 'radial' },
  ];

  return (
    <>
      {info && (
        <div className="absolute top-2 left-1/2 -translate-x-1/2 z-10 flex items-center gap-1 rounded-md bg-warning/10 border border-warning/30 px-3 py-1 text-xs text-warning">
          <AlertTriangle className="h-3 w-3" />
          {info}
        </div>
      )}
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={handleNodeClick}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.2}
        maxZoom={2}
        nodesDraggable
        nodesConnectable={false}
        elementsSelectable
      >
        <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
        <Controls showInteractive={false} />
        <Panel position="top-right">
          <div className="flex items-center gap-1 border rounded-md overflow-hidden bg-background shadow-sm">
            {layoutButtons.map((btn) => (
              <Button
                key={btn.value}
                variant={layout === btn.value ? 'default' : 'ghost'}
                size="sm"
                className="h-7 px-2 rounded-none text-xs"
                onClick={() => onLayoutChange(btn.value)}
              >
                {btn.label}
              </Button>
            ))}
          </div>
        </Panel>
      </ReactFlow>
    </>
  );
}

// ---------------------------------------------------------------------------
// Public component
// ---------------------------------------------------------------------------

export function ResourceTopologyGraph({
  nodes: topologyNodes,
  edges: topologyEdges,
  info,
  onDescribeResource,
}: ResourceTopologyGraphProps) {
  const [layout, setLayout] = useState<LayoutMode>('horizontal');

  if (topologyNodes.length === 0) {
    return (
      <div className="flex items-center justify-center h-full">
        <EmptyState
          icon={Globe}
          title="No topology data"
          description="No services or pods were found in this namespace."
          size="sm"
        />
      </div>
    );
  }

  return (
    <div className="relative h-full w-full">
      <ReactFlowProvider>
        <TopologyInner
          nodes={topologyNodes}
          edges={topologyEdges}
          info={info}
          onDescribeResource={onDescribeResource}
          layout={layout}
          onLayoutChange={setLayout}
        />
      </ReactFlowProvider>
    </div>
  );
}
