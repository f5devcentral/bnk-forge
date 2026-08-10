/**
 * A2AProtocolReference — Quick reference for the A2A protocol.
 *
 * Static reference panel showing JSON-RPC methods, task lifecycle,
 * agent card schema, and how BNK handles A2A traffic at each layer.
 * No backend needed.
 */

import { useState } from 'react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import {
  BookOpen, Send, Search, XCircle, List, Bell,
  Settings, ExternalLink, ArrowRight,
} from 'lucide-react';

// ---------------------------------------------------------------------------
// Data
// ---------------------------------------------------------------------------

const JSON_RPC_METHODS = [
  { method: 'message/send', description: 'Send a message to an agent and receive a task result', icon: Send, streaming: true },
  { method: 'tasks/get', description: 'Get the current state of a task by ID', icon: Search, streaming: false },
  { method: 'tasks/cancel', description: 'Request cancellation of a running task', icon: XCircle, streaming: false },
  { method: 'tasks/list', description: 'List tasks, optionally filtered by state', icon: List, streaming: false },
  { method: 'tasks/pushNotification/set', description: 'Configure push notification webhook for task updates', icon: Bell, streaming: false },
  { method: 'tasks/pushNotification/get', description: 'Get current push notification config for a task', icon: Settings, streaming: false },
];

type StatusVariant = 'info' | 'warning' | 'success' | 'destructive' | 'muted';

const TASK_STATES: { state: string; variant: StatusVariant; dot: string; description: string }[] = [
  { state: 'submitted', variant: 'info', dot: 'bg-info', description: 'Task received, queued for processing' },
  { state: 'working', variant: 'warning', dot: 'bg-warning', description: 'Agent is actively processing the task' },
  { state: 'input-required', variant: 'warning', dot: 'bg-warning', description: 'Agent needs more information from client' },
  { state: 'completed', variant: 'success', dot: 'bg-success', description: 'Task finished successfully with artifacts' },
  { state: 'failed', variant: 'destructive', dot: 'bg-destructive', description: 'Task failed with an error' },
  { state: 'canceled', variant: 'muted', dot: 'bg-muted-foreground', description: 'Task was canceled by the client' },
];

const AGENT_CARD_FIELDS = [
  { field: 'name', type: 'string', required: true, description: 'Human-readable agent name' },
  { field: 'description', type: 'string', required: false, description: 'What the agent does' },
  { field: 'url', type: 'string', required: true, description: 'Agent JSON-RPC endpoint URL' },
  { field: 'version', type: 'string', required: false, description: 'Agent version' },
  { field: 'capabilities', type: 'object', required: false, description: 'streaming, pushNotifications' },
  { field: 'skills', type: 'Skill[]', required: false, description: 'List of skills the agent provides' },
  { field: 'defaultInputModes', type: 'string[]', required: false, description: 'Accepted input MIME types' },
  { field: 'defaultOutputModes', type: 'string[]', required: false, description: 'Produced output MIME types' },
  { field: 'provider', type: 'object', required: false, description: 'organization, url' },
  { field: 'securitySchemes', type: 'object', required: false, description: 'Auth schemes (Bearer, API key, etc.)' },
];

const BNK_LAYERS = [
  { layer: 'L4', what: 'TCP connection management', bnk: 'VIP routing, SNAT, connection pooling' },
  { layer: 'L7 HTTP', what: 'HTTP request/response', bnk: 'Path-based routing, SSE support, load balancing' },
  { layer: 'L7 JSON', what: 'JSON-RPC inspection', bnk: 'iRule JSON_REQUEST/JSON_RESPONSE events, structured logging' },
  { layer: 'Session', what: 'Task persistence', bnk: 'Task ID → server pinning via iRule session tables' },
  { layer: 'Security', what: 'Authentication', bnk: 'JWT validation, JWK key management, TLS termination' },
  { layer: 'Rewriting', what: 'URL translation', bnk: 'Agent-card URL + push callback URL rewriting to Gateway VIP' },
];

// ---------------------------------------------------------------------------
// Section Components
// ---------------------------------------------------------------------------

function SectionHeader({ title }: { title: string }) {
  return (
    <h3 className="text-sm font-semibold uppercase tracking-wider mb-3 text-muted-foreground">
      {title}
    </h3>
  );
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

interface A2AProtocolReferenceProps {
  clusterId: number;
  namespace: string | undefined;
}

export function A2AProtocolReference({ clusterId: _clusterId, namespace: _namespace }: A2AProtocolReferenceProps) {
  const [activeTab, setActiveTab] = useState<'methods' | 'lifecycle' | 'agentcard' | 'bnk'>('methods');

  const cardClass = 'rounded-lg border border-border bg-card p-4';

  return (
    <div className="space-y-4">
      {/* Tab Bar */}
      <div className="flex gap-1 p-1 rounded-lg border border-border bg-muted/50">
        {[
          { key: 'methods' as const, label: 'JSON-RPC Methods' },
          { key: 'lifecycle' as const, label: 'Task Lifecycle' },
          { key: 'agentcard' as const, label: 'Agent Card Schema' },
          { key: 'bnk' as const, label: 'BNK Integration' },
        ].map(tab => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={cn(
              'flex-1 px-3 py-1.5 rounded-md text-xs font-medium transition-colors',
              activeTab === tab.key
                ? 'bg-card text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground/80',
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* JSON-RPC Methods */}
      {activeTab === 'methods' && (
        <div className="space-y-3">
          <SectionHeader title="A2A JSON-RPC Methods" />
          <div className="space-y-2">
            {JSON_RPC_METHODS.map(m => (
              <div key={m.method} className={cardClass}>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <m.icon className="h-4 w-4 flex-shrink-0 text-muted-foreground" />
                    <code className="text-sm font-mono font-medium text-primary">
                      {m.method}
                    </code>
                  </div>
                  <div className="flex gap-1.5">
                    {m.streaming && (
                      <Badge variant="outline" className="text-xs">SSE</Badge>
                    )}
                  </div>
                </div>
                <p className="text-xs mt-1.5 ml-7 text-muted-foreground">
                  {m.description}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Task Lifecycle */}
      {activeTab === 'lifecycle' && (
        <div className="space-y-3">
          <SectionHeader title="Task State Lifecycle" />

          {/* State flow diagram */}
          <div className={cardClass}>
            <div className="flex flex-wrap items-center gap-2 justify-center py-2">
              {['submitted', 'working'].map((state) => {
                const ts = TASK_STATES.find(s => s.state === state);
                return (
                  <div key={state} className="flex items-center gap-2">
                    <Badge variant={ts?.variant ?? 'muted'} className="text-xs">
                      {state}
                    </Badge>
                    <ArrowRight className="h-3 w-3 text-muted-foreground" />
                  </div>
                );
              })}
              <div className="flex flex-col gap-1">
                {['completed', 'failed', 'canceled', 'input-required'].map(state => {
                  const ts = TASK_STATES.find(s => s.state === state);
                  return (
                    <Badge key={state} variant={ts?.variant ?? 'muted'} className="text-xs">
                      {state}
                    </Badge>
                  );
                })}
              </div>
            </div>
          </div>

          {/* State descriptions */}
          <div className="space-y-2">
            {TASK_STATES.map(s => (
              <div key={s.state} className={cardClass}>
                <div className="flex items-center gap-3">
                  <div className={cn('w-2 h-2 rounded-full flex-shrink-0', s.dot)} />
                  <code className="text-sm font-mono font-medium text-foreground/80">
                    {s.state}
                  </code>
                </div>
                <p className="text-xs mt-1 ml-5 text-muted-foreground">
                  {s.description}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Agent Card Schema */}
      {activeTab === 'agentcard' && (
        <div className="space-y-3">
          <SectionHeader title="Agent Card Schema" />
          <p className="text-xs mb-3 text-muted-foreground">
            Served at <code className="px-1 py-0.5 rounded text-xs bg-muted">GET /.well-known/agent-card.json</code> — the standard A2A discovery endpoint.
          </p>
          <div className="rounded-lg border border-border overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-muted/50">
                  <th className="text-left px-4 py-2 text-xs font-medium">Field</th>
                  <th className="text-left px-4 py-2 text-xs font-medium">Type</th>
                  <th className="text-left px-4 py-2 text-xs font-medium w-8">Req</th>
                  <th className="text-left px-4 py-2 text-xs font-medium">Description</th>
                </tr>
              </thead>
              <tbody>
                {AGENT_CARD_FIELDS.map((f, i) => (
                  <tr key={f.field} className={i % 2 === 0 ? 'bg-card' : 'bg-muted/30'}>
                    <td className="px-4 py-2">
                      <code className="text-xs font-mono text-primary">
                        {f.field}
                      </code>
                    </td>
                    <td className="px-4 py-2 text-xs text-muted-foreground">
                      {f.type}
                    </td>
                    <td className="px-4 py-2">
                      {f.required && <Badge variant="warning" className="text-xs">req</Badge>}
                    </td>
                    <td className="px-4 py-2 text-xs text-muted-foreground">
                      {f.description}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* BNK Integration */}
      {activeTab === 'bnk' && (
        <div className="space-y-3">
          <SectionHeader title="How BNK Handles A2A Traffic" />
          <p className="text-xs mb-3 text-muted-foreground">
            F5 BNK acts as an intelligent Layer 7 gateway for A2A protocol traffic, providing inspection, load balancing, session persistence, authentication, and URL translation.
          </p>
          <div className="space-y-2">
            {BNK_LAYERS.map(l => (
              <div key={l.layer} className={cardClass}>
                <div className="flex items-start gap-3">
                  <Badge variant="outline" className="text-xs font-mono flex-shrink-0 mt-0.5">
                    {l.layer}
                  </Badge>
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-foreground/80">
                      {l.what}
                    </p>
                    <p className="text-xs mt-0.5 text-muted-foreground">
                      {l.bnk}
                    </p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Footer link */}
      <div className="flex items-center justify-center pt-2 text-muted-foreground">
        <a
          href="https://google.github.io/A2A/specification/"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 text-xs hover:text-primary transition-colors"
        >
          <BookOpen className="h-3 w-3" />
          Full A2A Protocol Specification
          <ExternalLink className="h-3 w-3" />
        </a>
      </div>
    </div>
  );
}
