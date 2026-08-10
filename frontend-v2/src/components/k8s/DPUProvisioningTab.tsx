/**
 * DPU Provisioning Tab
 *
 * Combined read-only view of DPF provisioning resources:
 *   - DPUSets — provisioning groups (BFB, flavor, strategy, replica counts)
 *   - DPU lifecycle objects — individual DPU phases managed by DPUSets
 *   - BFB images — BlueField Boot images with download status
 *   - DPU Flavors — system-level config templates
 *
 * Data comes from the shared DPF unified data cache.
 */

import { useState, useMemo } from 'react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import {
  CheckCircle2,
  XCircle,
  HelpCircle,
  ChevronDown,
  ChevronRight,
  Layers,
  HardDrive,
  Box,
  Cpu,
  Settings,
  Download,
} from 'lucide-react';
import type { DpfDpuSet, DpfDpu, DpfBfb, DpfDpuFlavor, DpfK8sCondition, DpfNodeEffect } from '@/types';

// ── Props ─────────────────────────────────────────────────────────────────

interface DPUProvisioningTabProps {
  dpuSets: DpfDpuSet[];
  dpus: DpfDpu[];
  bfbs: DpfBfb[];
  flavors: DpfDpuFlavor[];
  isLoading?: boolean;
}

// ── Shared Helpers ────────────────────────────────────────────────────────

type ConditionStatus = 'True' | 'False' | 'Unknown' | undefined;

function conditionIcon(status: ConditionStatus) {
  switch (status) {
    case 'True':  return CheckCircle2;
    case 'False': return XCircle;
    default:      return HelpCircle;
  }
}

function conditionColor(status: ConditionStatus) {
  switch (status) {
    case 'True':  return 'text-success';
    case 'False': return 'text-destructive';
    default:      return 'text-muted-foreground';
  }
}

function nodeEffectLabel(effect: DpfNodeEffect | undefined): string | undefined {
  if (!effect) return undefined;
  if (effect.drain) return 'Drain';
  if (effect.taint) return `Taint (${effect.taint.key ?? 'custom'})`;
  if (effect.noEffect) return 'No Effect';
  if (effect.hold) return 'Hold';
  if (effect.customAction) return `Custom: ${effect.customAction}`;
  if (effect.customLabel) return 'Custom Label';
  return undefined;
}

type PhaseBadgeVariant = 'success' | 'info' | 'warning' | 'destructive' | 'muted';

function phaseBadgeVariant(phase: string | undefined): PhaseBadgeVariant {
  switch (phase) {
    case 'Ready':             return 'success';
    case 'DPU-provisioning':  return 'info';
    case 'Node-effect':       return 'info';
    case 'Initializing':      return 'warning';
    case 'Deleting':          return 'warning';
    case 'Error':             return 'destructive';
    case 'Downloading':       return 'info';
    default:                  return 'muted';
  }
}

// ── Section Header ────────────────────────────────────────────────────────

function SectionHeader({
  icon: Icon,
  title,
  count,
}: {
  icon: typeof Layers;
  title: string;
  count: number;
}) {
  return (
    <div className="flex items-center gap-2 mb-3">
      <Icon className="h-4 w-4 text-muted-foreground" />
      <h3 className="text-sm font-semibold text-foreground">
        {title}
      </h3>
      <Badge variant="outline" className="text-[10px] px-1.5 py-0">
        {count}
      </Badge>
    </div>
  );
}

// ── DPUSet Card ───────────────────────────────────────────────────────────

function DPUSetCard({
  dpuSet,
  managedDpus,
}: {
  dpuSet: DpfDpuSet;
  managedDpus: DpfDpu[];
}) {
  const [expanded, setExpanded] = useState(false);
  const spec = dpuSet.spec;
  const status = dpuSet.status;
  const bfbName = spec?.dpuTemplate?.spec?.bfb?.name;
  const flavorName = spec?.dpuTemplate?.spec?.dpuFlavor;
  const strategy = spec?.strategy?.type ?? 'OnDelete';
  // DPUSet status uses dpuStatistics map (phase → count), not replicas
  const stats = status?.dpuStatistics ?? {};
  const total = Object.values(stats).reduce((sum, n) => sum + n, 0);
  const ready = stats['Ready'] ?? 0;

  return (
    <div className="rounded-lg border border-border bg-card">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center w-full gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/50"
      >
        {expanded
          ? <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
          : <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
        }
        <Layers className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="font-medium text-sm text-foreground">
          {dpuSet.metadata.name}
        </span>

        {bfbName && (
          <Badge variant="outline" className="text-[10px] px-1.5 py-0">
            BFB: {bfbName}
          </Badge>
        )}
        {flavorName && (
          <Badge variant="outline" className="text-[10px] px-1.5 py-0">
            Flavor: {flavorName}
          </Badge>
        )}

        <div className="flex-1" />

        {/* DPU statistics */}
        <div className="flex items-center gap-2 text-xs">
          <span className="font-medium text-foreground/80">
            {ready}/{total}
          </span>
          <span className="text-muted-foreground">ready</span>
          {(stats['Error'] ?? 0) > 0 && (
            <Badge variant="destructive" className="text-[10px] px-1.5 py-0">
              {stats['Error']} error
            </Badge>
          )}
        </div>
      </button>

      {expanded && (
        <div className="px-4 pb-4 pl-12 space-y-4 border-t border-border bg-muted/50">
          {/* Spec details */}
          <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-2 pt-3 text-xs">
            <DetailField label="BFB Image" value={bfbName} />
            <DetailField label="DPU Flavor" value={flavorName} />
            <DetailField label="Strategy" value={strategy} />
            <DetailField
              label="Max Unavailable"
              value={spec?.strategy?.rollingUpdate?.maxUnavailable != null ? String(spec.strategy.rollingUpdate.maxUnavailable) : undefined}
            />
            <DetailField label="Node Effect" value={nodeEffectLabel(spec?.dpuTemplate?.spec?.nodeEffect)} />
            <DetailField label="Secure Boot" value={spec?.dpuTemplate?.spec?.secureBoot ? 'Enabled' : undefined} />
          </div>

          {/* Managed DPUs */}
          {managedDpus.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold mb-2 text-foreground/80">
                Managed DPUs ({managedDpus.length})
              </h4>
              <div className="space-y-1">
                {managedDpus.map((dpu) => {
                  const phase = dpu.status?.phase ?? 'Unknown';
                  const variant = phaseBadgeVariant(phase);
                  return (
                    <div
                      key={dpu.metadata.uid ?? dpu.metadata.name}
                      className="flex items-center gap-3 rounded px-3 py-1.5 text-xs bg-card"
                    >
                      <Cpu className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                      <span className="font-medium min-w-[120px] text-foreground/80">
                        {dpu.metadata.name}
                      </span>
                      {dpu.spec?.dpuDevice && (
                        <span className="text-muted-foreground">
                          → {dpu.spec.dpuDevice}
                        </span>
                      )}
                      <div className="flex-1" />
                      <Badge variant={variant} className="text-[10px] px-2 py-0">
                        {phase}
                      </Badge>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Conditions */}
          {(status?.conditions ?? []).length > 0 && (
            <ConditionList conditions={status!.conditions!} />
          )}
        </div>
      )}
    </div>
  );
}

// ── BFB Card ──────────────────────────────────────────────────────────────

function BFBCard({ bfb }: { bfb: DpfBfb }) {
  const phase = bfb.status?.phase ?? 'Unknown';
  const variant = phaseBadgeVariant(phase);
  const conditions = bfb.status?.conditions ?? [];

  return (
    <div className="rounded-lg border p-4 border-border bg-card">
      <div className="flex items-center gap-3">
        <HardDrive className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="font-medium text-sm text-foreground">
          {bfb.metadata.name}
        </span>
        <div className="flex-1" />
        <Badge variant={variant} className="text-[10px] px-2 py-0">
          {phase === 'Downloading' && <Download className="h-3 w-3 mr-1 animate-pulse" />}
          {phase}
        </Badge>
      </div>
      {bfb.spec?.url && (
        <p className="mt-2 text-xs truncate text-muted-foreground">
          {bfb.spec.url}
        </p>
      )}
      {bfb.spec?.fileName && (
        <p className="mt-1 text-xs font-mono text-muted-foreground">
          {bfb.spec.fileName}
        </p>
      )}
      {conditions.length > 0 && (
        <div className="mt-3">
          <ConditionList conditions={conditions} compact />
        </div>
      )}
    </div>
  );
}

// ── DPU Flavor Card ───────────────────────────────────────────────────────

function FlavorCard({ flavor }: { flavor: DpfDpuFlavor }) {
  const [expanded, setExpanded] = useState(false);
  const spec = flavor.spec;
  const hasGrub = (spec?.grub?.kernelParameters ?? []).length > 0;
  const hasSysctl = (spec?.sysctl?.parameters ?? []).length > 0;
  const hasNvConfig = (spec?.nvconfig ?? []).length > 0;
  const hasOvs = !!spec?.ovs?.rawConfigScript;
  const hasBfCfg = (spec?.bfcfgParameters ?? []).length > 0;
  const hasConfigFiles = (spec?.configFiles ?? []).length > 0;
  const hasContainerd = !!spec?.containerdConfig?.registryEndpoint;
  const hasDpuResources = spec?.dpuResources && Object.keys(spec.dpuResources).length > 0;

  const detailCount = [hasGrub, hasSysctl, hasNvConfig, hasOvs, hasBfCfg, hasConfigFiles, hasContainerd, hasDpuResources].filter(Boolean).length;

  return (
    <div className="rounded-lg border border-border bg-card">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center w-full gap-3 px-4 py-3 text-left transition-colors hover:bg-muted/50"
      >
        {expanded
          ? <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
          : <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" />
        }
        <Settings className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="font-medium text-sm text-foreground">
          {flavor.metadata.name}
        </span>
        <div className="flex-1" />
        {spec?.dpuMode && (
          <Badge variant="outline" className="text-[10px] px-1.5 py-0">
            {spec.dpuMode}
          </Badge>
        )}
        {detailCount > 0 && (
          <span className="text-xs text-muted-foreground">
            {detailCount} config section{detailCount !== 1 ? 's' : ''}
          </span>
        )}
      </button>

      {expanded && (
        <div className="px-4 pb-4 pl-12 space-y-3 border-t border-border bg-muted/50">
          {spec?.dpuMode && (
            <ConfigSection title="DPU Mode">
              <code className="text-[11px] font-mono text-muted-foreground">
                {spec.dpuMode}
              </code>
            </ConfigSection>
          )}
          {hasGrub && (
            <ConfigSection title="Kernel Parameters">
              {spec!.grub!.kernelParameters!.map((p, i) => (
                <code key={i} className="block text-[11px] font-mono text-muted-foreground">
                  {p}
                </code>
              ))}
            </ConfigSection>
          )}
          {hasSysctl && (
            <ConfigSection title="Sysctl Parameters">
              {spec!.sysctl!.parameters!.map((p, i) => (
                <code key={i} className="block text-[11px] font-mono text-muted-foreground">
                  {p}
                </code>
              ))}
            </ConfigSection>
          )}
          {hasNvConfig && (
            <ConfigSection title="NVConfig (Firmware)">
              {spec!.nvconfig!.map((nv, i) => (
                <div key={i} className="mb-1">
                  <span className="text-[10px] font-medium text-muted-foreground">
                    device: {nv.device ?? '*'}
                  </span>
                  {(nv.parameters ?? []).map((p, j) => (
                    <code key={j} className="block text-[11px] font-mono pl-2 text-muted-foreground">
                      {p}
                    </code>
                  ))}
                </div>
              ))}
            </ConfigSection>
          )}
          {hasOvs && (
            <ConfigSection title="OVS Config">
              <pre className="text-[11px] font-mono whitespace-pre-wrap text-muted-foreground">
                {spec!.ovs!.rawConfigScript}
              </pre>
            </ConfigSection>
          )}
          {hasBfCfg && (
            <ConfigSection title="BF Config Parameters">
              {spec!.bfcfgParameters!.map((p, i) => (
                <code key={i} className="block text-[11px] font-mono text-muted-foreground">
                  {p}
                </code>
              ))}
            </ConfigSection>
          )}
          {hasConfigFiles && (
            <ConfigSection title="Config Files">
              {spec!.configFiles!.map((f, i) => (
                <div key={i} className="mb-1">
                  <code className="text-[11px] font-mono text-foreground/80">
                    {f.path} {f.operation && `(${f.operation})`} {f.permissions && `[${f.permissions}]`}
                  </code>
                </div>
              ))}
            </ConfigSection>
          )}
          {hasContainerd && (
            <ConfigSection title="Container Registry">
              <code className="text-[11px] font-mono text-muted-foreground">
                {spec!.containerdConfig!.registryEndpoint}
              </code>
            </ConfigSection>
          )}
          {hasDpuResources && (
            <ConfigSection title="DPU Resources">
              {Object.entries(spec!.dpuResources!).map(([k, v]) => (
                <code key={k} className="block text-[11px] font-mono text-muted-foreground">
                  {k}: {v}
                </code>
              ))}
            </ConfigSection>
          )}
          {detailCount === 0 && (
            <p className="text-xs pt-2 text-muted-foreground">
              No configuration sections defined in this flavor.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

// ── Config Section Helper ─────────────────────────────────────────────────

function ConfigSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="pt-2">
      <h5 className="text-[10px] uppercase tracking-wider mb-1 text-muted-foreground">
        {title}
      </h5>
      <div className="rounded px-3 py-2 bg-card">
        {children}
      </div>
    </div>
  );
}

// ── Condition List Helper ─────────────────────────────────────────────────

function ConditionList({
  conditions,
  compact,
}: {
  conditions: DpfK8sCondition[];
  compact?: boolean;
}) {
  return (
    <div className="space-y-1">
      {!compact && (
        <h4 className="text-xs font-semibold mb-1.5 text-foreground/80">
          Conditions
        </h4>
      )}
      {conditions.map((cond) => {
        const Icon = conditionIcon(cond.status as ConditionStatus);
        return (
          <div
            key={cond.type}
            className="flex items-center gap-2 rounded px-3 py-1.5 text-xs bg-card"
          >
            <Icon className={cn('h-3.5 w-3.5 shrink-0', conditionColor(cond.status as ConditionStatus))} />
            <span className="font-medium text-foreground/80">
              {cond.type}
            </span>
            {cond.reason && (
              <span className="text-muted-foreground">({cond.reason})</span>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Detail Field Helper ───────────────────────────────────────────────────

function DetailField({
  label,
  value,
}: {
  label: string;
  value: string | undefined | null;
}) {
  if (!value) return null;
  return (
    <div>
      <span className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <p className="mt-0.5 text-foreground/80">
        {value}
      </p>
    </div>
  );
}

// ── Empty State ───────────────────────────────────────────────────────────

function EmptyProvisioning() {
  return (
    <div className="flex flex-col items-center justify-center rounded-lg border border-border bg-muted/50 p-10 text-center">
      <Layers className="h-10 w-10 mb-3 text-muted-foreground" />
      <h3 className="text-base font-semibold mb-1 text-foreground/80">
        No Provisioning Resources
      </h3>
      <p className="text-sm max-w-md text-muted-foreground">
        No DPUSets, BFB images, or DPU Flavors found. Create a DPUSet to begin provisioning DPUs.
      </p>
    </div>
  );
}

// ── Main Component ────────────────────────────────────────────────────────

export function DPUProvisioningTab({ dpuSets, dpus, bfbs, flavors, isLoading }: DPUProvisioningTabProps) {
  // Map DPUs to their owning DPUSet
  const dpusBySet = useMemo(() => {
    const map = new Map<string, DpfDpu[]>();
    for (const dpu of dpus) {
      const setName = dpu.spec?.dpuSet ?? '__unmanaged__';
      const existing = map.get(setName) ?? [];
      existing.push(dpu);
      map.set(setName, existing);
    }
    return map;
  }, [dpus]);

  if (isLoading) {
    return (
      <div className="space-y-4">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="h-20 rounded-lg animate-pulse bg-muted/50" />
        ))}
      </div>
    );
  }

  const totalResources = dpuSets.length + bfbs.length + flavors.length;
  if (totalResources === 0) {
    return <EmptyProvisioning />;
  }

  return (
    <div className="space-y-6">
      {/* DPU Sets */}
      {dpuSets.length > 0 && (
        <div>
          <SectionHeader icon={Layers} title="DPU Sets" count={dpuSets.length} />
          <div className="space-y-3">
            {dpuSets.map((dpuSet) => (
              <DPUSetCard
                key={dpuSet.metadata.uid ?? dpuSet.metadata.name}
                dpuSet={dpuSet}
                managedDpus={dpusBySet.get(dpuSet.metadata.name) ?? []}
              />
            ))}
          </div>
        </div>
      )}

      {/* Unmanaged DPUs (not owned by any DPUSet) */}
      {(dpusBySet.get('__unmanaged__') ?? []).length > 0 && (
        <div>
          <SectionHeader icon={Cpu} title="Unmanaged DPUs" count={dpusBySet.get('__unmanaged__')!.length} />
          <div className="rounded-lg border p-4 space-y-1 border-border bg-card">
            {dpusBySet.get('__unmanaged__')!.map((dpu) => {
              const phase = dpu.status?.phase ?? 'Unknown';
              const variant = phaseBadgeVariant(phase);
              return (
                <div
                  key={dpu.metadata.uid ?? dpu.metadata.name}
                  className="flex items-center gap-3 rounded px-3 py-1.5 text-xs bg-muted/50"
                >
                  <Cpu className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="font-medium text-foreground/80">
                    {dpu.metadata.name}
                  </span>
                  {dpu.spec?.dpuDevice && (
                    <span className="text-muted-foreground">→ {dpu.spec.dpuDevice}</span>
                  )}
                  <div className="flex-1" />
                  <Badge variant={variant} className="text-[10px] px-2 py-0">
                    {phase}
                  </Badge>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* BFB Images */}
      {bfbs.length > 0 && (
        <div>
          <SectionHeader icon={HardDrive} title="BFB Images" count={bfbs.length} />
          <div className="space-y-3">
            {bfbs.map((bfb) => (
              <BFBCard key={bfb.metadata.uid ?? bfb.metadata.name} bfb={bfb} />
            ))}
          </div>
        </div>
      )}

      {/* DPU Flavors */}
      {flavors.length > 0 && (
        <div>
          <SectionHeader icon={Box} title="DPU Flavors" count={flavors.length} />
          <div className="space-y-3">
            {flavors.map((flavor) => (
              <FlavorCard key={flavor.metadata.uid ?? flavor.metadata.name} flavor={flavor} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
