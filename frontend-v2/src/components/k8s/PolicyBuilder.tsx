/**
 * PolicyBuilder — Visual form-based builder for BNK security and network policies.
 *
 * Three-column layout:
 * - Left: Target selection (gateways + listeners from topology)
 * - Center: Policy editor (name, rules, extension refs)
 * - Right: Palette (templates, existing address/port lists, iRules)
 *
 * Generates valid K8s YAML and applies via the existing generic CRUD API.
 * Part of TOPO-003.
 */

import { useState, useMemo, useCallback } from 'react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Globe, Shield, Network, ChevronDown, ChevronRight, Plus, Trash2,
  ArrowUp, ArrowDown, Eye, Play, Check, Code, Wand2, Copy,
  AlertTriangle,
} from 'lucide-react';
import { useBnkData } from '@/hooks/k8s/useBnk';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { notify } from '@/lib/notify';
import { parseApiError } from '@/lib/error-handler';
import { SkeletonTable } from '@/components/ui/skeleton-table';
import { EmptyState } from '@/components/ui/empty-state';
import {
  generatePolicyYaml,
  splitYamlDocuments,
  extractResourceType,
  RULE_TEMPLATES,
  POLICY_TEMPLATES,
} from '@/lib/policy-yaml';
import type {
  PolicyBuilderState,
  PolicyBuilderRule,
  TopologyGateway,
  PaletteAddressList,
  PalettePortList,
  PaletteIRule,
} from '@/types/f5bnk';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

let _counter = 0;
function uid(): string {
  return `rule-${Date.now()}-${++_counter}`;
}

function createEmptyRule(): PolicyBuilderRule {
  return {
    id: uid(),
    name: '',
    action: 'accept',
    ipProtocol: 'tcp',
    source: { addresses: [], addressLists: [], ports: [], portLists: [] },
    destination: { addresses: [], addressLists: [], ports: [], portLists: [] },
    logging: false,
  };
}

function createInitialState(namespace: string): PolicyBuilderState {
  return {
    mode: 'create',
    policyType: 'security',
    name: '',
    namespace: namespace || 'default',
    targetGateway: '',
    targetListener: '',
    firewallPolicyName: '',
    firewallRules: [],
    extensionRefs: [],
  };
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

/** Left panel — gateway/listener target selection */
function TargetsPanel({
  gateways,
  selectedGateway,
  selectedListener,
  onSelect,
}: {
  gateways: TopologyGateway[];
  selectedGateway: string;
  selectedListener: string;
  onSelect: (gw: string, listener: string) => void;
}) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const toggleGw = (name: string) => {
    setExpanded(prev => ({ ...prev, [name]: !prev[name] }));
  };

  if (gateways.length === 0) {
    return (
      <div className="p-4 text-center">
        <Globe className="h-8 w-8 mx-auto mb-2 text-muted-foreground" />
        <p className="text-sm text-muted-foreground">No gateways found</p>
      </div>
    );
  }

  return (
    <div className="p-2 space-y-1">
      {gateways.map(gw => {
        const isExpanded = expanded[gw.name] !== false; // default open
        return (
          <div key={gw.name}>
            <button
              onClick={() => toggleGw(gw.name)}
              className="flex items-center gap-2 w-full px-2 py-1.5 rounded text-sm text-left hover:bg-muted/50"
            >
              {isExpanded ? <ChevronDown className="h-3.5 w-3.5 flex-shrink-0" /> : <ChevronRight className="h-3.5 w-3.5 flex-shrink-0" />}
              <Globe className="h-3.5 w-3.5 flex-shrink-0 text-primary" />
              <span className="font-medium truncate">{gw.name}</span>
              <Badge variant="outline" className="text-[10px] px-1 py-0 ml-auto">{gw.namespace}</Badge>
            </button>
            {isExpanded && (
              <div className="ml-6 space-y-0.5">
                {/* All Listeners option */}
                <button
                  onClick={() => onSelect(gw.name, '')}
                  className={cn(
                    'flex items-center gap-2 w-full px-2 py-1 rounded text-xs',
                    selectedGateway === gw.name && selectedListener === ''
                      ? 'bg-primary/10 text-primary'
                      : 'hover:bg-muted/50 text-muted-foreground',
                  )}
                >
                  <Shield className="h-3 w-3" />
                  All Listeners
                </button>
                {gw.listeners.map(l => (
                  <button
                    key={l.name}
                    onClick={() => onSelect(gw.name, l.name)}
                    className={cn(
                      'flex items-center gap-2 w-full px-2 py-1 rounded text-xs',
                      selectedGateway === gw.name && selectedListener === l.name
                        ? 'bg-primary/10 text-primary'
                        : 'hover:bg-muted/50 text-muted-foreground',
                    )}
                  >
                    <Network className="h-3 w-3" />
                    <span>{l.name}</span>
                    <span className="ml-auto opacity-60">{l.protocol}/{l.port}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/** Center panel — single firewall rule card */
function RuleCard({
  rule,
  index,
  total,
  onChange,
  onRemove,
  onMoveUp,
  onMoveDown,
  addressLists,
  portLists,
}: {
  rule: PolicyBuilderRule;
  index: number;
  total: number;
  onChange: (updated: PolicyBuilderRule) => void;
  onRemove: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  addressLists: PaletteAddressList[];
  portLists: PalettePortList[];
}) {
  const [expanded, setExpanded] = useState(true);

  const updateField = <K extends keyof PolicyBuilderRule>(key: K, value: PolicyBuilderRule[K]) => {
    onChange({ ...rule, [key]: value });
  };

  const updateSourceDest = (
    which: 'source' | 'destination',
    field: string,
    value: string[],
  ) => {
    onChange({
      ...rule,
      [which]: { ...rule[which], [field]: value },
    });
  };

  // Action semantics: accept → success, drop/reject → destructive
  const actionVariant: 'success' | 'destructive' =
    rule.action === 'accept' ? 'success' : 'destructive';

  return (
    <div className="border border-border rounded-lg bg-card">
      {/* Rule header */}
      <div
        className="flex items-center gap-2 px-3 py-2 cursor-pointer hover:bg-muted/50"
        onClick={() => setExpanded(!expanded)}
      >
        <span className="text-xs font-mono text-muted-foreground">
          #{index + 1}
        </span>
        {expanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        <span className="text-sm font-medium truncate flex-1">{rule.name || '(unnamed)'}</span>
        <Badge variant={actionVariant} className="text-[10px] px-1.5">
          {rule.action}
        </Badge>
        <Badge variant="outline" className="text-[10px] px-1.5">{rule.ipProtocol}</Badge>
        <div className="flex items-center gap-0.5 ml-1">
          <button
            onClick={(e) => { e.stopPropagation(); onMoveUp(); }}
            disabled={index === 0}
            className={cn('p-0.5 rounded', index === 0 ? 'opacity-30' : 'hover:bg-muted')}
          >
            <ArrowUp className="h-3 w-3" />
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onMoveDown(); }}
            disabled={index === total - 1}
            className={cn('p-0.5 rounded', index === total - 1 ? 'opacity-30' : 'hover:bg-muted')}
          >
            <ArrowDown className="h-3 w-3" />
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onRemove(); }}
            className="p-0.5 rounded hover:bg-destructive/10 text-destructive ml-1"
          >
            <Trash2 className="h-3 w-3" />
          </button>
        </div>
      </div>

      {/* Rule body */}
      {expanded && (
        <div className="px-3 pb-3 pt-1 space-y-3 border-t border-border">
          {/* Row 1: Name, Action, Protocol */}
          <div className="grid grid-cols-3 gap-2">
            <div>
              <label className="text-[10px] font-medium opacity-60">Name</label>
              <Input
                value={rule.name}
                onChange={e => updateField('name', e.target.value)}
                placeholder="rule-name"
                className="h-7 text-xs"
              />
            </div>
            <div>
              <label className="text-[10px] font-medium opacity-60">Action</label>
              <Select value={rule.action} onValueChange={v => updateField('action', v as PolicyBuilderRule['action'])}>
                <SelectTrigger className="h-7 text-xs"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="accept">Accept</SelectItem>
                  <SelectItem value="drop">Drop</SelectItem>
                  <SelectItem value="reject">Reject</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <label className="text-[10px] font-medium opacity-60">Protocol</label>
              <Select value={rule.ipProtocol} onValueChange={v => updateField('ipProtocol', v as PolicyBuilderRule['ipProtocol'])}>
                <SelectTrigger className="h-7 text-xs"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="tcp">TCP</SelectItem>
                  <SelectItem value="udp">UDP</SelectItem>
                  <SelectItem value="any">Any</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* Row 2: Source */}
          <SourceDestEditor
            label="Source"
            data={rule.source}
            onChange={(field, val) => updateSourceDest('source', field, val)}
            addressLists={addressLists}
            portLists={portLists}
          />

          {/* Row 3: Destination */}
          <SourceDestEditor
            label="Destination"
            data={rule.destination}
            onChange={(field, val) => updateSourceDest('destination', field, val)}
            addressLists={addressLists}
            portLists={portLists}
          />

          {/* Row 4: Logging */}
          <label className="flex items-center gap-2 text-xs cursor-pointer">
            <input
              type="checkbox"
              checked={rule.logging}
              onChange={e => updateField('logging', e.target.checked)}
              className="rounded"
            />
            Enable logging
          </label>
        </div>
      )}
    </div>
  );
}

/** Source/Destination editor — inline addresses, ports, list references */
function SourceDestEditor({
  label,
  data,
  onChange,
  addressLists,
  portLists,
}: {
  label: string;
  data: PolicyBuilderRule['source'];
  onChange: (field: string, value: string[]) => void;
  addressLists: PaletteAddressList[];
  portLists: PalettePortList[];
}) {
  const [addrInput, setAddrInput] = useState('');
  const [portInput, setPortInput] = useState('');

  const addAddress = () => {
    const trimmed = addrInput.trim();
    if (trimmed && !data.addresses.includes(trimmed)) {
      onChange('addresses', [...data.addresses, trimmed]);
      setAddrInput('');
    }
  };

  const addPort = () => {
    const trimmed = portInput.trim();
    if (trimmed && !data.ports.includes(trimmed)) {
      onChange('ports', [...data.ports, trimmed]);
      setPortInput('');
    }
  };

  return (
    <div className="p-2 rounded border border-border bg-muted/50">
      <span className="text-[10px] font-semibold uppercase tracking-wider opacity-50">{label}</span>
      <div className="grid grid-cols-2 gap-2 mt-1">
        {/* Addresses column */}
        <div className="space-y-1">
          <label className="text-[10px] opacity-60">Addresses (CIDR)</label>
          <div className="flex gap-1">
            <Input
              value={addrInput}
              onChange={e => setAddrInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && (e.preventDefault(), addAddress())}
              placeholder="10.0.0.0/8"
              className="h-6 text-[11px] flex-1"
            />
            <Button size="sm" variant="ghost" className="h-6 w-6 p-0" onClick={addAddress}>
              <Plus className="h-3 w-3" />
            </Button>
          </div>
          {data.addresses.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {data.addresses.map(a => (
                <Badge key={a} variant="secondary" className="text-[10px] pr-1 gap-0.5">
                  {a}
                  <button onClick={() => onChange('addresses', data.addresses.filter(x => x !== a))} className="ml-0.5 hover:text-destructive">×</button>
                </Badge>
              ))}
            </div>
          )}
          {/* Address list references */}
          {addressLists.length > 0 && (
            <div>
              <label className="text-[10px] opacity-60">Address Lists</label>
              <div className="flex flex-wrap gap-1 mt-0.5">
                {addressLists.map(al => {
                  const isSelected = data.addressLists.includes(al.name);
                  return (
                    <button
                      key={al.name}
                      onClick={() => {
                        if (isSelected) {
                          onChange('addressLists', data.addressLists.filter(x => x !== al.name));
                        } else {
                          onChange('addressLists', [...data.addressLists, al.name]);
                        }
                      }}
                      className={cn(
                        'text-[10px] px-1.5 py-0.5 rounded border transition-colors',
                        isSelected
                          ? 'border-primary/40 bg-primary/10 text-primary'
                          : 'border-border hover:border-foreground/20 text-muted-foreground',
                      )}
                    >
                      {al.name} ({al.addresses.length})
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {/* Ports column */}
        <div className="space-y-1">
          <label className="text-[10px] opacity-60">Ports</label>
          <div className="flex gap-1">
            <Input
              value={portInput}
              onChange={e => setPortInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && (e.preventDefault(), addPort())}
              placeholder="80"
              className="h-6 text-[11px] flex-1"
            />
            <Button size="sm" variant="ghost" className="h-6 w-6 p-0" onClick={addPort}>
              <Plus className="h-3 w-3" />
            </Button>
          </div>
          {data.ports.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {data.ports.map(p => (
                <Badge key={p} variant="secondary" className="text-[10px] pr-1 gap-0.5">
                  {p}
                  <button onClick={() => onChange('ports', data.ports.filter(x => x !== p))} className="ml-0.5 hover:text-destructive">×</button>
                </Badge>
              ))}
            </div>
          )}
          {/* Port list references */}
          {portLists.length > 0 && (
            <div>
              <label className="text-[10px] opacity-60">Port Lists</label>
              <div className="flex flex-wrap gap-1 mt-0.5">
                {portLists.map(pl => {
                  const isSelected = data.portLists.includes(pl.name);
                  return (
                    <button
                      key={pl.name}
                      onClick={() => {
                        if (isSelected) {
                          onChange('portLists', data.portLists.filter(x => x !== pl.name));
                        } else {
                          onChange('portLists', [...data.portLists, pl.name]);
                        }
                      }}
                      className={cn(
                        'text-[10px] px-1.5 py-0.5 rounded border transition-colors',
                        isSelected
                          ? 'border-primary/40 bg-primary/10 text-primary'
                          : 'border-border hover:border-foreground/20 text-muted-foreground',
                      )}
                    >
                      {pl.name} ({pl.ports.length})
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

/** Right panel — templates and existing resources */
function PalettePanel({
  onAddRuleTemplate,
  onApplyPolicyTemplate,
  onAddIRule,
  iRules,
  extensionRefs,
  policyType,
}: {
  onAddRuleTemplate: (rule: Omit<PolicyBuilderRule, 'id'>) => void;
  onApplyPolicyTemplate: (rules: Array<Omit<PolicyBuilderRule, 'id'>>) => void;
  onAddIRule: (name: string) => void;
  iRules: PaletteIRule[];
  extensionRefs: PolicyBuilderState['extensionRefs'];
  policyType: 'security' | 'network';
}) {
  const sectionClass = 'text-[10px] font-semibold uppercase tracking-wider mb-1.5 text-muted-foreground';
  const itemClass = 'flex items-center gap-2 w-full px-2 py-1.5 rounded text-xs text-left transition-colors hover:bg-muted/50 text-foreground/80';

  return (
    <div className="p-3 space-y-4">
      {policyType === 'security' && (
        <>
          {/* Policy templates */}
          <div>
            <p className={sectionClass}>Policy Templates</p>
            <div className="space-y-0.5">
              {POLICY_TEMPLATES.map(t => (
                <button key={t.label} className={itemClass} onClick={() => onApplyPolicyTemplate(t.rules)}>
                  <Wand2 className="h-3 w-3 text-primary flex-shrink-0" />
                  <div className="min-w-0">
                    <div className="font-medium">{t.label}</div>
                    <div className="opacity-50 text-[10px]">{t.description}</div>
                  </div>
                </button>
              ))}
            </div>
          </div>

          {/* Rule templates */}
          <div>
            <p className={sectionClass}>Add Rule</p>
            <div className="space-y-0.5">
              {RULE_TEMPLATES.map(t => (
                <button key={t.label} className={itemClass} onClick={() => onAddRuleTemplate(t.rule)}>
                  <Plus className="h-3 w-3 text-success flex-shrink-0" />
                  <div className="min-w-0">
                    <div className="font-medium">{t.label}</div>
                    <div className="opacity-50 text-[10px]">{t.description}</div>
                  </div>
                </button>
              ))}
            </div>
          </div>
        </>
      )}

      {policyType === 'network' && iRules.length > 0 && (
        <div>
          <p className={sectionClass}>iRules</p>
          <div className="space-y-0.5">
            {iRules.map(ir => {
              const isAdded = extensionRefs.some(e => e.name === ir.name && e.kind === 'F5BigCneIrule');
              return (
                <button
                  key={ir.name}
                  className={cn(itemClass, isAdded && 'opacity-50')}
                  onClick={() => !isAdded && onAddIRule(ir.name)}
                  disabled={isAdded}
                >
                  <Code className="h-3 w-3 text-warning flex-shrink-0" />
                  <div className="min-w-0 flex-1">
                    <div className="font-medium">{ir.name}</div>
                    <div className="opacity-50 text-[10px]">
                      {ir.lineCount} lines
                      {ir.eventHandlers.length > 0 && ` · ${ir.eventHandlers.join(', ')}`}
                    </div>
                  </div>
                  {isAdded && <Check className="h-3 w-3 text-success" />}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {policyType === 'network' && iRules.length === 0 && (
        <div className="text-center py-6">
          <Code className="h-6 w-6 mx-auto mb-2 text-muted-foreground" />
          <p className="text-xs text-muted-foreground">No iRules found in cluster</p>
        </div>
      )}
    </div>
  );
}

/** YAML preview panel */
function YamlPreview({
  yaml,
  onClose,
}: {
  yaml: string;
  onClose: () => void;
}) {
  const copyToClipboard = () => {
    navigator.clipboard.writeText(yaml);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      onClick={onClose}
    >
      <div
        className="w-[700px] max-h-[80vh] rounded-lg border border-border bg-card shadow-xl flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <h3 className="text-sm font-semibold">Generated YAML Preview</h3>
          <div className="flex items-center gap-2">
            <Button size="sm" variant="ghost" onClick={copyToClipboard} className="h-7 text-xs gap-1">
              <Copy className="h-3 w-3" /> Copy
            </Button>
            <Button size="sm" variant="ghost" onClick={onClose} className="h-7 text-xs">Close</Button>
          </div>
        </div>
        <pre className="flex-1 overflow-auto p-4 text-xs font-mono whitespace-pre text-foreground/80 bg-muted/50">
          {yaml}
        </pre>
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

interface PolicyBuilderProps {
  clusterId: number;
  namespace?: string;
}

export function PolicyBuilder({ clusterId, namespace }: PolicyBuilderProps) {
  const queryClient = useQueryClient();
  const { data: bnkData, isLoading, error } = useBnkData(clusterId, { namespace });

  const [state, setState] = useState<PolicyBuilderState>(() =>
    createInitialState(namespace || 'default')
  );
  const [showPreview, setShowPreview] = useState(false);
  const [isApplying, setIsApplying] = useState(false);

  // Extract data from unified response
  const gateways = bnkData?.topology ?? [];
  const palette = bnkData?.palette;
  const addressLists = palette?.addressLists ?? [];
  const portLists = palette?.portLists ?? [];
  const iRules = palette?.iRules ?? [];
  const existingPolicies = useMemo(() => bnkData?.policyAssociations ?? [], [bnkData?.policyAssociations]);

  // Check for duplicate policy warning
  const duplicateWarning = useMemo(() => {
    if (!state.targetGateway || !state.name) return null;
    const existing = existingPolicies.find(
      p => p.gateway_name === state.targetGateway &&
           (state.targetListener === '' || p.listener_name === state.targetListener)
    );
    if (existing) {
      return `Gateway "${state.targetGateway}" listener "${state.targetListener || 'all'}" already has policy "${existing.bnk_policy_name}" attached.`;
    }
    return null;
  }, [state.targetGateway, state.targetListener, state.name, existingPolicies]);

  // Handlers
  const updateState = useCallback((partial: Partial<PolicyBuilderState>) => {
    setState(prev => ({ ...prev, ...partial }));
  }, []);

  const handleSelectTarget = useCallback((gw: string, listener: string) => {
    updateState({ targetGateway: gw, targetListener: listener });
  }, [updateState]);

  const handleAddRule = useCallback((template: Omit<PolicyBuilderRule, 'id'>) => {
    setState(prev => ({
      ...prev,
      firewallRules: [...prev.firewallRules, { ...template, id: uid() }],
    }));
  }, []);

  const handleApplyTemplate = useCallback((rules: Array<Omit<PolicyBuilderRule, 'id'>>) => {
    setState(prev => ({
      ...prev,
      firewallRules: rules.map(r => ({ ...r, id: uid() })),
    }));
  }, []);

  const handleUpdateRule = useCallback((index: number, updated: PolicyBuilderRule) => {
    setState(prev => ({
      ...prev,
      firewallRules: prev.firewallRules.map((r, i) => i === index ? updated : r),
    }));
  }, []);

  const handleRemoveRule = useCallback((index: number) => {
    setState(prev => ({
      ...prev,
      firewallRules: prev.firewallRules.filter((_, i) => i !== index),
    }));
  }, []);

  const handleMoveRule = useCallback((index: number, direction: -1 | 1) => {
    setState(prev => {
      const rules = [...prev.firewallRules];
      const target = index + direction;
      if (target < 0 || target >= rules.length) return prev;
      [rules[index], rules[target]] = [rules[target], rules[index]];
      return { ...prev, firewallRules: rules };
    });
  }, []);

  const handleAddIRule = useCallback((name: string) => {
    setState(prev => ({
      ...prev,
      extensionRefs: [
        ...prev.extensionRefs,
        { kind: 'F5BigCneIrule', name, group: 'k8s.f5net.com' },
      ],
    }));
  }, []);

  const handleRemoveExtRef = useCallback((index: number) => {
    setState(prev => ({
      ...prev,
      extensionRefs: prev.extensionRefs.filter((_, i) => i !== index),
    }));
  }, []);

  // Validation
  const validationErrors = useMemo(() => {
    const errors: string[] = [];
    if (!state.name) errors.push('Policy name is required');
    if (!state.targetGateway) errors.push('Select a target gateway');
    if (state.policyType === 'security') {
      if (!state.firewallPolicyName) errors.push('Firewall policy name is required');
      if (state.firewallRules.length === 0) errors.push('Add at least one firewall rule');
      state.firewallRules.forEach((r, i) => {
        if (!r.name) errors.push(`Rule #${i + 1} needs a name`);
      });
    }
    if (state.policyType === 'network') {
      if (state.extensionRefs.length === 0) errors.push('Add at least one extension reference');
    }
    return errors;
  }, [state]);

  const isValid = validationErrors.length === 0;

  // Generate YAML
  const generatedYaml = useMemo(() => {
    if (!isValid) return '';
    return generatePolicyYaml(state);
  }, [state, isValid]);

  // Apply handler
  const handleApply = async (dryRun: boolean) => {
    if (!isValid) return;
    setIsApplying(true);
    try {
      const docs = splitYamlDocuments(generatedYaml);
      for (const doc of docs) {
        const resourceType = extractResourceType(doc);
        if (!resourceType) continue;
        await api.createK8sResource(clusterId, resourceType, {
          resource_yaml: doc,
          namespace: state.namespace,
          dry_run: dryRun,
        });
      }
      if (dryRun) {
        notify.success('Dry run passed — all resources are valid', undefined, { category: 'cluster' });
      } else {
        queryClient.invalidateQueries({ queryKey: ['bnk-resources'] });
        queryClient.invalidateQueries({ queryKey: ['k8s', 'clusters', clusterId] });
        // Reset form
        setState(createInitialState(namespace || 'default'));
      }
    } catch (err: unknown) {
      const parsed = parseApiError(err);
      notify.error(dryRun ? 'Dry run failed' : 'Apply failed', parsed.message, { category: 'cluster' });
    } finally {
      setIsApplying(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  if (isLoading) {
    return <SkeletonTable rows={6} columns={3} />;
  }

  if (error) {
    return <EmptyState icon={AlertTriangle} title="Failed to load BNK data" description={parseApiError(error).message} />;
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Policy type toggle + name fields */}
      <div className="flex items-center gap-3 p-3 rounded-lg border border-border bg-card">
        {/* Policy type */}
        <div className="flex gap-0.5 p-0.5 rounded-md border border-border bg-muted/50">
          <button
            onClick={() => updateState({ policyType: 'security' })}
            className={cn(
              'px-3 py-1 rounded text-xs font-medium transition-colors',
              state.policyType === 'security'
                ? 'bg-card text-foreground shadow-sm'
                : 'text-muted-foreground',
            )}
          >
            <Shield className="h-3 w-3 inline mr-1" />
            Security
          </button>
          <button
            onClick={() => updateState({ policyType: 'network' })}
            className={cn(
              'px-3 py-1 rounded text-xs font-medium transition-colors',
              state.policyType === 'network'
                ? 'bg-card text-foreground shadow-sm'
                : 'text-muted-foreground',
            )}
          >
            <Network className="h-3 w-3 inline mr-1" />
            Network
          </button>
        </div>

        {/* Name fields */}
        <div className="flex-1 grid grid-cols-2 gap-2">
          <div>
            <label className="text-[10px] font-medium opacity-60">
              {state.policyType === 'security' ? 'BNKSecPolicy Name' : 'BNKNetPolicy Name'}
            </label>
            <Input
              value={state.name}
              onChange={e => updateState({ name: e.target.value })}
              placeholder="my-policy"
              className="h-7 text-xs"
            />
          </div>
          {state.policyType === 'security' && (
            <div>
              <label className="text-[10px] font-medium opacity-60">F5BigFwPolicy Name</label>
              <Input
                value={state.firewallPolicyName}
                onChange={e => updateState({ firewallPolicyName: e.target.value })}
                placeholder="my-fw-policy"
                className="h-7 text-xs"
              />
            </div>
          )}
        </div>

        {/* Target badge */}
        {state.targetGateway && (
          <Badge variant="outline" className="text-xs">
            <Globe className="h-3 w-3 mr-1" />
            {state.targetGateway}{state.targetListener ? ` / ${state.targetListener}` : ' / all'}
          </Badge>
        )}
      </div>

      {/* Duplicate warning */}
      {duplicateWarning && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg border border-warning/20 bg-warning/10 text-warning text-xs">
          <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0" />
          {duplicateWarning}
        </div>
      )}

      {/* 3-column layout */}
      <div className="grid grid-cols-[200px_1fr_220px] gap-0 rounded-lg border border-border overflow-hidden">
        {/* Left: Targets */}
        <div className="border-r border-border bg-muted/50 overflow-y-auto max-h-[60vh]">
          <div className="px-3 py-2 border-b border-border text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Target Gateway
          </div>
          <TargetsPanel
            gateways={gateways}
            selectedGateway={state.targetGateway}
            selectedListener={state.targetListener}
            onSelect={handleSelectTarget}
          />
        </div>

        {/* Center: Policy Editor */}
        <div className="overflow-y-auto max-h-[60vh] bg-card">
          <div className="px-3 py-2 border-b border-border text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            {state.policyType === 'security' ? 'Firewall Rules' : 'Extension References'}
            {state.policyType === 'security' && (
              <span className="ml-1 opacity-60">({state.firewallRules.length})</span>
            )}
          </div>

          {state.policyType === 'security' && (
            <div className="p-3 space-y-2">
              {state.firewallRules.length === 0 && (
                <div className="text-center py-8">
                  <Shield className="h-8 w-8 mx-auto mb-2 text-muted-foreground" />
                  <p className="text-xs mb-1 text-muted-foreground">No rules yet</p>
                  <p className="text-[10px] text-muted-foreground">
                    Use a template from the palette or add rules manually
                  </p>
                </div>
              )}
              {state.firewallRules.map((rule, i) => (
                <RuleCard
                  key={rule.id}
                  rule={rule}
                  index={i}
                  total={state.firewallRules.length}
                  onChange={updated => handleUpdateRule(i, updated)}
                  onRemove={() => handleRemoveRule(i)}
                  onMoveUp={() => handleMoveRule(i, -1)}
                  onMoveDown={() => handleMoveRule(i, 1)}
                  addressLists={addressLists}
                  portLists={portLists}
                />
              ))}
              <Button
                variant="outline"
                size="sm"
                className="w-full h-7 text-xs"
                onClick={() => handleAddRule(createEmptyRule())}
              >
                <Plus className="h-3 w-3 mr-1" /> Add Custom Rule
              </Button>
            </div>
          )}

          {state.policyType === 'network' && (
            <div className="p-3 space-y-2">
              {state.extensionRefs.length === 0 && (
                <div className="text-center py-8">
                  <Network className="h-8 w-8 mx-auto mb-2 text-muted-foreground" />
                  <p className="text-xs mb-1 text-muted-foreground">No extensions yet</p>
                  <p className="text-[10px] text-muted-foreground">
                    Add iRules from the palette
                  </p>
                </div>
              )}
              {state.extensionRefs.map((ext, i) => (
                <div
                  key={`${ext.kind}-${ext.name}`}
                  className="flex items-center gap-2 px-3 py-2 rounded border border-border bg-card"
                >
                  <Code className="h-3.5 w-3.5 text-warning flex-shrink-0" />
                  <div className="min-w-0 flex-1">
                    <span className="text-xs font-medium">{ext.name}</span>
                    <span className="text-[10px] ml-2 text-muted-foreground">{ext.kind}</span>
                  </div>
                  <button
                    onClick={() => handleRemoveExtRef(i)}
                    className="p-0.5 rounded hover:bg-destructive/10 text-destructive"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Right: Palette */}
        <div className="border-l border-border bg-muted/50 overflow-y-auto max-h-[60vh]">
          <div className="px-3 py-2 border-b border-border text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            Palette
          </div>
          <PalettePanel
            onAddRuleTemplate={handleAddRule}
            onApplyPolicyTemplate={handleApplyTemplate}
            onAddIRule={handleAddIRule}
            iRules={iRules}
            extensionRefs={state.extensionRefs}
            policyType={state.policyType}
          />
        </div>
      </div>

      {/* Validation errors */}
      {validationErrors.length > 0 && (
        <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
          {validationErrors.map(err => (
            <span key={err} className="flex items-center gap-1">
              <AlertTriangle className="h-3 w-3 text-warning" />{err}
            </span>
          ))}
        </div>
      )}

      {/* Action bar */}
      <div className="flex items-center justify-end gap-2">
        <Button
          variant="outline"
          size="sm"
          className="h-8 text-xs gap-1.5"
          onClick={() => setShowPreview(true)}
          disabled={!isValid}
        >
          <Eye className="h-3.5 w-3.5" />
          Preview YAML
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-8 text-xs gap-1.5"
          onClick={() => handleApply(true)}
          disabled={!isValid || isApplying}
        >
          <Play className="h-3.5 w-3.5" />
          Dry Run
        </Button>
        <Button
          size="sm"
          className="h-8 text-xs gap-1.5"
          onClick={() => handleApply(false)}
          disabled={!isValid || isApplying}
        >
          <Check className="h-3.5 w-3.5" />
          Apply
        </Button>
      </div>

      {/* YAML Preview modal */}
      {showPreview && (
        <YamlPreview
          yaml={generatedYaml}
          onClose={() => setShowPreview(false)}
        />
      )}
    </div>
  );
}
