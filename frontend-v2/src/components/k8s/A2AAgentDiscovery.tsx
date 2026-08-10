/**
 * A2AAgentDiscovery — Discovers A2A agents running behind BNK Gateways.
 *
 * Scans HTTPRoutes for backend services and optionally probes them for
 * /.well-known/agent-card.json. Displays discovered agents in a card grid
 * with service info, route refs, gateway mapping, and agent card data.
 *
 * Uses the same data pipeline as BackendsCollection — reuses shared
 * topology analysis via the /f5bnk/a2a/agents endpoint.
 */

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Radar, Bot, Globe, Route, Shield, Zap, Bell,
  RefreshCw, ChevronDown, ChevronRight, ExternalLink,
} from 'lucide-react';
import { api } from '@/lib/api';
import { queryKeys } from '@/lib/queryKeys';
import { SkeletonTable } from '@/components/ui/skeleton-table';
import { EmptyState } from '@/components/ui/empty-state';
import type { A2AAgentCandidate } from '@/types/f5bnk';

// ---------------------------------------------------------------------------
// Agent Card Component
// ---------------------------------------------------------------------------

function AgentCard({ agent }: { agent: A2AAgentCandidate }) {
  const [expanded, setExpanded] = useState(false);
  const card = agent.agentCard;
  const isConfirmed = agent.probeStatus === 'success' && card;

  const surfaceClass = isConfirmed
    ? 'border-success/30 bg-success/5'
    : 'border-border bg-muted/50';

  const iconWrapClass = isConfirmed
    ? 'bg-success/10'
    : 'bg-muted';

  return (
    <div className={cn('rounded-lg border p-4 transition-colors', surfaceClass)}>
      {/* Header */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <div className={cn('flex-shrink-0 p-2 rounded-lg', iconWrapClass)}>
            <Bot className={cn('h-5 w-5', isConfirmed ? 'text-success' : 'text-muted-foreground')} />
          </div>
          <div className="min-w-0">
            <h3 className="font-medium truncate">
              {card?.name || agent.name}
            </h3>
            <p className="text-xs truncate text-muted-foreground">
              {agent.namespace}/{agent.name}
              {agent.ports.length > 0 && `:${agent.ports[0].port}`}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <ProbeStatusBadge status={agent.probeStatus} />
        </div>
      </div>

      {/* Description */}
      {card?.description && (
        <p className="text-sm mt-3 line-clamp-2 text-muted-foreground">
          {card.description}
        </p>
      )}

      {/* Capabilities row */}
      {card && (
        <div className="flex flex-wrap gap-1.5 mt-3">
          {card.capabilities?.streaming && (
            <Badge variant="outline" className="text-xs gap-1">
              <Zap className="h-3 w-3" /> Streaming
            </Badge>
          )}
          {card.capabilities?.pushNotifications && (
            <Badge variant="outline" className="text-xs gap-1">
              <Bell className="h-3 w-3" /> Push
            </Badge>
          )}
          {card.version && (
            <Badge variant="outline" className="text-xs">
              v{card.version}
            </Badge>
          )}
          {Object.keys(card.securitySchemes || {}).length > 0 && (
            <Badge variant="outline" className="text-xs gap-1">
              <Shield className="h-3 w-3" /> Auth
            </Badge>
          )}
        </div>
      )}

      {/* Skills */}
      {card?.skills && card.skills.length > 0 && (
        <div className="mt-3">
          <p className="text-xs font-medium mb-1 text-muted-foreground">
            Skills ({card.skills.length})
          </p>
          <div className="flex flex-wrap gap-1">
            {card.skills.slice(0, 5).map((skill, i) => (
              <Badge key={i} variant="secondary" className="text-xs">
                {skill.name}
              </Badge>
            ))}
            {card.skills.length > 5 && (
              <Badge variant="secondary" className="text-xs">
                +{card.skills.length - 5} more
              </Badge>
            )}
          </div>
        </div>
      )}

      {/* Gateway + Route refs (expandable) */}
      <div className="mt-3 pt-3 border-t border-dashed border-border">
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-1 text-xs font-medium w-full text-left text-muted-foreground hover:text-foreground/80"
        >
          {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
          <Globe className="h-3 w-3" />
          {agent.gateways.join(', ')}
          <span className="text-muted-foreground ml-1">
            ({agent.routeRefs.length} route{agent.routeRefs.length !== 1 ? 's' : ''})
          </span>
        </button>
        {expanded && (
          <div className="mt-2 space-y-1 ml-4">
            {agent.routeRefs.map((ref, i) => (
              <div key={i} className="flex items-center gap-2 text-xs text-muted-foreground">
                <Route className="h-3 w-3" />
                <span>{ref.namespace}/{ref.name}</span>
                <Badge variant="outline" className="text-xs py-0">{ref.kind}</Badge>
                {ref.port && <span className="text-muted-foreground">:{ref.port}</span>}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* External link */}
      {card?.url && (
        <a
          href={card.url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-xs mt-2 text-primary hover:text-primary/80"
        >
          <ExternalLink className="h-3 w-3" />
          {card.url}
        </a>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Probe Status Badge
// ---------------------------------------------------------------------------

function ProbeStatusBadge({ status }: { status: string }) {
  switch (status) {
    case 'success':
      return <Badge variant="success" className="text-xs">Confirmed</Badge>;
    case 'error':
      return <Badge variant="outline" className="text-xs">No Agent Card</Badge>;
    case 'pending':
      return <Badge variant="outline" className="text-xs text-muted-foreground">Not Probed</Badge>;
    case 'skipped':
      return <Badge variant="warning" className="text-xs">Skipped</Badge>;
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Summary Card
// ---------------------------------------------------------------------------

function SummaryCard({
  label, value, icon: Icon,
}: {
  label: string; value: number; icon: React.ElementType;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <div className="flex items-center gap-2 mb-1">
        <Icon className="h-4 w-4 text-muted-foreground" />
        <span className="text-xs text-muted-foreground">{label}</span>
      </div>
      <p className="text-2xl font-semibold">{value}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

interface A2AAgentDiscoveryProps {
  clusterId: number;
  namespace: string | undefined;
}

export function A2AAgentDiscovery({ clusterId, namespace }: A2AAgentDiscoveryProps) {
  const [probeEnabled, setProbeEnabled] = useState(false);

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: queryKeys.k8s.clusters.a2aAgents(clusterId, { namespace, probe: probeEnabled }),
    queryFn: () => api.getA2AAgents(clusterId, { namespace, probe: probeEnabled }),
    enabled: !!clusterId,
  });

  const agents = data?.agents ?? [];
  const confirmedCount = agents.filter(a => a.probeStatus === 'success').length;

  if (isLoading) {
    return <SkeletonTable rows={4} columns={3} />;
  }

  if (error) {
    return (
      <EmptyState
        icon={Radar}
        title="Failed to discover agents"
        description={String(error)}
      />
    );
  }

  return (
    <div className="space-y-4">
      {/* Summary */}
      <div className="grid grid-cols-3 gap-3">
        <SummaryCard label="HTTPRoute Backends" value={agents.length} icon={Bot} />
        <SummaryCard label="Confirmed Agents" value={confirmedCount} icon={Radar} />
        <SummaryCard label="Gateways" value={new Set(agents.flatMap(a => a.gateways)).size} icon={Globe} />
      </div>

      {/* Actions */}
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {agents.length === 0
            ? 'No services found behind HTTPRoutes.'
            : `${agents.length} service${agents.length !== 1 ? 's' : ''} behind HTTPRoutes — `
          }
          {agents.length > 0 && !probeEnabled && (
            <span>click <strong>Probe Agents</strong> to check for agent cards.</span>
          )}
          {agents.length > 0 && probeEnabled && (
            <span>{confirmedCount} confirmed A2A agent{confirmedCount !== 1 ? 's' : ''}.</span>
          )}
        </p>
        <div className="flex items-center gap-2">
          {!probeEnabled && agents.length > 0 && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => setProbeEnabled(true)}
              className="gap-1.5"
            >
              <Radar className="h-4 w-4" />
              Probe Agents
            </Button>
          )}
          <Button
            size="sm"
            variant="ghost"
            onClick={() => refetch()}
            disabled={isFetching}
            className="gap-1.5"
          >
            <RefreshCw className={cn('h-4 w-4', isFetching && 'animate-spin')} />
            Refresh
          </Button>
        </div>
      </div>

      {/* Agent Cards Grid */}
      {agents.length === 0 ? (
        <EmptyState
          icon={Bot}
          title="No A2A Agent Candidates"
          description="No services are currently mapped as backends of HTTPRoutes. Deploy an A2A agent behind a BNK Gateway to see it here."
        />
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {agents.map((agent) => (
            <AgentCard key={`${agent.namespace}/${agent.name}`} agent={agent} />
          ))}
        </div>
      )}
    </div>
  );
}
