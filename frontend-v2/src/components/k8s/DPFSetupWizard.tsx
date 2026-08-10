/**
 * DPF Setup Wizard — Greenfield DPU Infrastructure Deployment
 *
 * 6-step wizard that generates and applies DPF K8s resources:
 *   1. Mode — zero-trust vs host-trusted
 *   2. Cluster — DPUCluster type, VIP, version
 *   3. BFB & Provisioning — BFB image, DPUFlavor, DPUSet
 *   4. Services — use-case preset selection (HBN, OVN-K, etc.)
 *   5. Review & Apply — YAML preview, dry-run, apply
 *
 * Follows the ConfigBuilder pattern (TOPO-003b).
 */

import { useState, useMemo, useCallback } from 'react';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Check,
  ArrowLeft,
  ArrowRight,
  Play,
  Code,
  AlertTriangle,
  Shield,
  Monitor,
  Server,
  HardDrive,
  Package,
  Network,
  Cloud,
  Zap,
  Copy,
  Database,
} from 'lucide-react';
import { SectionCard } from '@/components/ui/section-card';
import { useQueryClient } from '@tanstack/react-query';
import { useClusterScanResult } from '@/hooks/k8s/useResources';
import { api } from '@/lib/api';
import { notify } from '@/lib/notify';
import { parseApiError } from '@/lib/error-handler';
import {
  generateAllDpfYaml,
  splitYamlDocuments,
  extractResourceType,
} from '@/lib/dpf-yaml';
import type { DpfSetupState } from '@/lib/dpf-yaml';
import { DPF_USE_CASE_PRESETS, COMMON_BFB_IMAGES } from '@/lib/dpf-presets';
import type { DpfUseCasePreset } from '@/lib/dpf-presets';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type WizardStep = 'mode' | 'cluster' | 'provisioning' | 'services' | 'review';

interface DPFSetupWizardProps {
  clusterId: number;
  onClose?: () => void;
}

// ---------------------------------------------------------------------------
// Steps
// ---------------------------------------------------------------------------

const STEPS: { key: WizardStep; label: string; icon: typeof Shield }[] = [
  { key: 'mode',         label: 'Mode',         icon: Shield },
  { key: 'cluster',      label: 'Cluster',      icon: Server },
  { key: 'provisioning', label: 'Provisioning',  icon: HardDrive },
  { key: 'services',     label: 'Services',     icon: Package },
  { key: 'review',       label: 'Review & Apply', icon: Check },
];

// ---------------------------------------------------------------------------
// Initial state
// ---------------------------------------------------------------------------

function createInitialState(): DpfSetupState {
  return {
    mode: 'host-trusted',
    namespace: 'dpf-operator-system',
    clusterName: 'dpu-cluster-1',
    clusterType: 'kamaji',
    maxNodes: 32,
    vip: '',
    virtualRouterID: 100,
    keepalivedInterface: '',
    bfbName: 'bf3-ubuntu22.04',
    bfbUrl: COMMON_BFB_IMAGES[0]?.url ?? '',
    dpuFlavorName: '',
    dpuFlavorMode: '',
    kernelParameters: [],
    sysctlParameters: [],
    containerdRegistryEndpoint: '',
    nvconfigParameters: [],
    bfcfgParameters: [],
    ovsRawConfigScript: '',
    dpuSetName: 'dpuset-1',
    nodeEffect: 'drain',
    secureBoot: false,
    strategy: 'OnDelete',
    maxUnavailable: '1',
    dpuNodeSelector: {},
    services: [],
    serviceChains: [],
    serviceInterfaces: [],
  };
}

// ---------------------------------------------------------------------------
// Step Indicator (same pattern as ConfigBuilder)
// ---------------------------------------------------------------------------

function StepIndicator({
  steps,
  currentStep,
  onStepClick,
}: {
  steps: typeof STEPS;
  currentStep: WizardStep;
  onStepClick: (step: WizardStep) => void;
}) {
  const currentIdx = steps.findIndex((s) => s.key === currentStep);

  return (
    <div className="flex items-center gap-3 flex-wrap">
      {steps.map((step, i) => {
        const isDone = i < currentIdx;
        const isActive = i === currentIdx;
        return (
          <button
            key={step.key}
            onClick={() => onStepClick(step.key)}
            className={cn(
              'flex items-center gap-2 text-xs transition-colors',
              isActive ? 'text-foreground font-medium' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            <span
              className={cn(
                'flex h-5 w-5 items-center justify-center rounded-full border text-[10px] font-semibold',
                isActive
                  ? 'border-primary bg-primary text-primary-foreground'
                  : isDone
                    ? 'border-primary/40 text-primary'
                    : 'border-border text-muted-foreground',
              )}
            >
              {isDone ? <Check className="h-3 w-3" /> : i + 1}
            </span>
            {step.label}
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 1: Mode Selection
// ---------------------------------------------------------------------------

function ModeStep({
  state,
  onChange,
}: {
  state: DpfSetupState;
  onChange: (partial: Partial<DpfSetupState>) => void;
}) {
  const modes: { key: DpfSetupState['mode']; title: string; desc: string; icon: typeof Monitor }[] = [
    {
      key: 'host-trusted',
      title: 'Host Trusted',
      desc: 'DPU provisioning via rshim from the host OS. Requires host OS access to the DPU. Simpler setup, faster provisioning.',
      icon: Monitor,
    },
    {
      key: 'zero-trust',
      title: 'Zero Trust',
      desc: 'DPU provisioning via Redfish/BMC. No host OS dependency. Requires BMC IP addresses for each DPU. More secure.',
      icon: Shield,
    },
  ];

  return (
    <SectionCard title="Provisioning Mode">
      <p className="text-xs text-muted-foreground mb-4 -mt-3">
        Choose how DPUs will be discovered and provisioned.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {modes.map((m) => {
          const Icon = m.icon;
          const selected = state.mode === m.key;
          return (
            <button
              key={m.key}
              onClick={() => onChange({ mode: m.key })}
              className={cn(
                'flex flex-col items-start p-5 rounded-lg border text-left transition-all',
                selected
                  ? 'border-primary bg-primary/5 ring-1 ring-primary/30'
                  : 'border-border bg-card hover:bg-muted/50',
              )}
            >
              <div className="flex items-center gap-2 mb-2">
                <Icon className={cn('h-5 w-5', selected ? 'text-primary' : 'text-muted-foreground')} />
                <span className="font-semibold text-sm text-foreground/80">
                  {m.title}
                </span>
                {selected && <Check className="h-4 w-4 text-primary ml-auto" />}
              </div>
              <p className="text-xs leading-relaxed text-muted-foreground">
                {m.desc}
              </p>
            </button>
          );
        })}
      </div>

      <div className="grid grid-cols-2 gap-3 mt-5">
        <div>
          <label className="text-xs font-medium opacity-70">Target Namespace</label>
          <Input
            value={state.namespace}
            onChange={(e) => onChange({ namespace: e.target.value })}
            placeholder="dpf-operator-system"
            className="h-8 text-sm"
          />
        </div>
      </div>
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Step 2: Cluster Configuration
// ---------------------------------------------------------------------------

function ClusterStep({
  state,
  onChange,
  kamajiStatus,
}: {
  state: DpfSetupState;
  onChange: (partial: Partial<DpfSetupState>) => void;
  kamajiStatus?: 'detected' | 'missing' | 'partial' | 'unknown';
}) {
  return (
    <SectionCard title="DPU Cluster">
      <p className="text-xs text-muted-foreground mb-4 -mt-3">
        Configure the Kubernetes control plane for DPU nodes.
      </p>

      {/* Kamaji availability check */}
      {state.clusterType === 'kamaji' && kamajiStatus && kamajiStatus !== 'detected' && (
        <div className={cn(
          'flex items-start gap-2 rounded-lg px-4 py-3 text-xs border',
          kamajiStatus === 'missing'
            ? 'border-destructive/20 bg-destructive/10 text-destructive'
            : 'border-warning/20 bg-warning/10 text-warning'
        )}>
          <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
          <div>
            <span className="font-semibold">
              {kamajiStatus === 'missing' ? 'Kamaji Not Detected' : 'Kamaji Partially Installed'}
            </span>
            <p className="mt-0.5 opacity-80">
              {kamajiStatus === 'missing'
                ? 'Kamaji is required for managed DPU cluster control planes. Install Kamaji first, or switch to "Static" cluster type.'
                : 'Kamaji CRDs found but some components may be missing. The DPU cluster may not provision correctly.'}
            </p>
          </div>
        </div>
      )}

      {state.clusterType === 'kamaji' && kamajiStatus === 'detected' && (
        <div className="flex items-center gap-2 rounded-lg px-4 py-2 text-xs bg-success/10 text-success">
          <Check className="h-3.5 w-3.5" />
          Kamaji detected — managed control plane available
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        <div>
          <label className="text-xs font-medium opacity-70">Cluster Name</label>
          <Input
            value={state.clusterName}
            onChange={(e) => onChange({ clusterName: e.target.value })}
            placeholder="dpu-cluster-1"
            className="h-8 text-sm"
          />
          <p className="text-[10px] mt-0.5 text-muted-foreground">
            Unique name for this DPU cluster resource.
          </p>
        </div>
        <div>
          <label className="text-xs font-medium opacity-70">Cluster Type</label>
          <Select value={state.clusterType} onValueChange={(v) => onChange({ clusterType: v as 'kamaji' | 'static' })}>
            <SelectTrigger className="h-8 text-sm"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="kamaji">Kamaji (managed control plane)</SelectItem>
              <SelectItem value="static">Static (external kubeconfig)</SelectItem>
            </SelectContent>
          </Select>
          <p className="text-[10px] mt-0.5 text-muted-foreground">
            {state.clusterType === 'kamaji'
              ? 'Kamaji auto-provisions a K8s control plane for DPU nodes. Requires Kamaji to be installed.'
              : 'Uses an externally provided kubeconfig. You manage the DPU cluster control plane separately.'}
          </p>
        </div>
        <div>
          <label className="text-xs font-medium opacity-70">Max Nodes</label>
          <Input
            type="number"
            value={state.maxNodes}
            onChange={(e) => onChange({ maxNodes: parseInt(e.target.value) || 0 })}
            className="h-8 text-sm"
          />
          <p className="text-[10px] mt-0.5 text-muted-foreground">
            Maximum number of DPU nodes this cluster can manage.
          </p>
        </div>
      </div>

      {state.clusterType === 'kamaji' && (
        <>
          <div>
            <h5 className="text-xs font-semibold mb-1 text-foreground/80">
              Control Plane Endpoint
            </h5>
            <p className="text-[10px] mb-2 text-muted-foreground">
              Kamaji uses Keepalived for HA. The VIP is the stable API server address that DPU nodes connect to.
            </p>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
              <div>
                <label className="text-xs font-medium opacity-70">VIP Address</label>
                <Input
                  value={state.vip}
                  onChange={(e) => onChange({ vip: e.target.value })}
                  placeholder="10.0.0.100"
                  className="h-8 text-sm"
                />
                <p className="text-[10px] mt-0.5 text-muted-foreground">
                  Virtual IP for the DPU cluster API server. Must be reachable from DPU nodes.
                </p>
              </div>
              <div>
                <label className="text-xs font-medium opacity-70">Virtual Router ID</label>
                <Input
                  type="number"
                  value={state.virtualRouterID}
                  onChange={(e) => onChange({ virtualRouterID: Math.max(1, Math.min(255, parseInt(e.target.value) || 1)) })}
                  placeholder="100"
                  className="h-8 text-sm"
                  min={1}
                  max={255}
                />
                <p className="text-[10px] mt-0.5 text-muted-foreground">
                  VRRP ID (1-255). Must be unique on the network segment to avoid VIP conflicts.
                </p>
              </div>
              <div>
                <label className="text-xs font-medium opacity-70">Keepalived Interface</label>
                <Input
                  value={state.keepalivedInterface}
                  onChange={(e) => onChange({ keepalivedInterface: e.target.value })}
                  placeholder="eth0"
                  className="h-8 text-sm"
                />
                <p className="text-[10px] mt-0.5 text-muted-foreground">
                  Network interface for VIP advertisement. Usually the primary interface on host nodes.
                </p>
              </div>
            </div>
          </div>
        </>
      )}
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Step 3: BFB & Provisioning
// ---------------------------------------------------------------------------

function ProvisioningStep({
  state,
  onChange,
}: {
  state: DpfSetupState;
  onChange: (partial: Partial<DpfSetupState>) => void;
}) {
  const [customBfb, setCustomBfb] = useState(false);

  return (
    <SectionCard title="BFB Image & DPU Set">
      <p className="text-xs text-muted-foreground mb-4 -mt-3">
        Select a BlueField Boot image and configure provisioning.
      </p>

      {/* BFB Selection */}
      <div className="space-y-3">
        <h5 className="text-xs font-semibold text-foreground/80">
          BFB Image
        </h5>

        {!customBfb && (
          <div className="space-y-2">
            {COMMON_BFB_IMAGES.map((img) => (
              <button
                key={img.name}
                onClick={() => onChange({ bfbName: img.name, bfbUrl: img.url })}
                className={cn(
                  'flex items-center gap-3 w-full px-4 py-3 rounded-lg border text-left transition-all',
                  state.bfbUrl === img.url
                    ? 'border-primary bg-primary/5 ring-1 ring-primary/30'
                    : 'border-border bg-card hover:bg-muted/50',
                )}
              >
                <HardDrive className={cn('h-4 w-4 shrink-0', state.bfbUrl === img.url ? 'text-primary' : 'text-muted-foreground')} />
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium text-foreground/80">
                    {img.name}
                  </div>
                  <div className="text-[10px] truncate text-muted-foreground">
                    {img.description}
                  </div>
                </div>
                {state.bfbUrl === img.url && <Check className="h-4 w-4 text-primary shrink-0" />}
              </button>
            ))}
            <button
              onClick={() => setCustomBfb(true)}
              className="text-xs underline text-muted-foreground"
            >
              Use custom BFB URL...
            </button>
          </div>
        )}

        {customBfb && (
          <div className="space-y-2">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs font-medium opacity-70">BFB Name</label>
                <Input
                  value={state.bfbName}
                  onChange={(e) => onChange({ bfbName: e.target.value })}
                  placeholder="my-bfb"
                  className="h-8 text-sm"
                />
              </div>
              <div>
                <label className="text-xs font-medium opacity-70">BFB URL</label>
                <Input
                  value={state.bfbUrl}
                  onChange={(e) => onChange({ bfbUrl: e.target.value })}
                  placeholder="https://content.mellanox.com/..."
                  className="h-8 text-sm"
                />
              </div>
            </div>
            <button
              onClick={() => setCustomBfb(false)}
              className="text-xs underline text-muted-foreground"
            >
              Use predefined image...
            </button>
          </div>
        )}
      </div>

      {/* DPU Flavor — collapsed by default, expand for customization */}
      <details className="rounded-lg border border-border">
        <summary className="cursor-pointer px-4 py-3 text-xs font-semibold select-none flex items-center justify-between text-foreground/80 hover:text-foreground">
          <span>DPU Flavor Configuration</span>
          <span className="text-[10px] font-normal text-muted-foreground">
            {state.dpuFlavorMode || state.kernelParameters.length > 0 || state.sysctlParameters.length > 0 || state.containerdRegistryEndpoint
              ? 'Customized'
              : 'Using defaults — click to customize'}
          </span>
        </summary>
        <div className="px-4 pb-4 space-y-3">
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
            <div>
              <label className="text-xs font-medium opacity-70">Flavor Name</label>
              <Input
                value={state.dpuFlavorName}
                onChange={(e) => onChange({ dpuFlavorName: e.target.value })}
                placeholder="Auto-generated if empty"
                className="h-8 text-sm"
              />
            </div>
            <div className="col-span-2">
              <label className="text-xs font-medium opacity-70">DPU Mode</label>
              <Select value={state.dpuFlavorMode || 'auto'} onValueChange={(v) => onChange({ dpuFlavorMode: v === 'auto' ? '' : v as 'dpu' | 'zero-trust' | 'nic' })}>
                <SelectTrigger className="h-8 text-sm"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="auto">Auto (based on install mode)</SelectItem>
                  <SelectItem value="dpu">DPU (standard)</SelectItem>
                  <SelectItem value="zero-trust">Zero-Trust</SelectItem>
                  <SelectItem value="nic">NIC (pass-through)</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-[10px] mt-1 text-muted-foreground">
                {!state.dpuFlavorMode && 'Inherits from provisioning mode — DPU mode for host-trusted, zero-trust mode for zero-trust.'}
                {state.dpuFlavorMode === 'dpu' && 'Host traffic steered through the DPU embedded switch for hardware-accelerated networking.'}
                {state.dpuFlavorMode === 'zero-trust' && 'DPU operates independently of the host OS. Provisioned and managed via BMC/Redfish only.'}
                {state.dpuFlavorMode === 'nic' && 'DPU acts as a standard network card — no SmartNIC processing. For basic connectivity or legacy workloads.'}
              </p>
            </div>
          </div>

          {/* Kernel & Sysctl */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium opacity-70">Kernel Parameters (GRUB)</label>
              <Textarea
                value={state.kernelParameters.join('\n')}
                onChange={(e) => onChange({ kernelParameters: e.target.value.split('\n').map((s) => s.trim()).filter(Boolean) })}
                placeholder={'hugepagesz=2M\nhugepages=1024\nintel_iommu=on'}
                className="text-xs font-mono min-h-[56px] resize-y"
                rows={2}
              />
              <p className="text-[10px] mt-0.5 text-muted-foreground">
                Boot-time kernel parameters. One per line.
              </p>
            </div>
            <div>
              <label className="text-xs font-medium opacity-70">Sysctl Parameters</label>
              <Textarea
                value={state.sysctlParameters.join('\n')}
                onChange={(e) => onChange({ sysctlParameters: e.target.value.split('\n').map((s) => s.trim()).filter(Boolean) })}
                placeholder={'net.core.rmem_max=16777216\nvm.nr_hugepages=1024'}
                className="text-xs font-mono min-h-[56px] resize-y"
                rows={2}
              />
              <p className="text-[10px] mt-0.5 text-muted-foreground">
                Runtime kernel tuning. One per line.
              </p>
            </div>
          </div>

          {/* Container Registry */}
          <div>
            <label className="text-xs font-medium opacity-70">Container Registry (air-gapped)</label>
            <Input
              value={state.containerdRegistryEndpoint}
              onChange={(e) => onChange({ containerdRegistryEndpoint: e.target.value })}
              placeholder="Leave empty for public registries"
              className="h-8 text-sm"
            />
            <p className="text-[10px] mt-0.5 text-muted-foreground">
              Private containerd registry for disconnected environments.
            </p>
          </div>

          {/* Advanced Flavor Settings */}
          <details className="rounded-lg border border-border">
            <summary className="cursor-pointer px-3 py-2 text-[11px] font-medium select-none text-muted-foreground hover:text-foreground/80">
              Advanced: NVConfig, BF Config, OVS Script
            </summary>
            <div className="px-3 pb-3 space-y-3">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div>
                  <label className="text-xs font-medium opacity-70">NVConfig Firmware</label>
                  <Textarea
                    value={state.nvconfigParameters.join('\n')}
                    onChange={(e) => onChange({ nvconfigParameters: e.target.value.split('\n').map((s) => s.trim()).filter(Boolean) })}
                    placeholder={'NUM_OF_VFS=16\nSRIOV_EN=1'}
                    className="text-xs font-mono min-h-[48px] resize-y"
                    rows={2}
                  />
                </div>
                <div>
                  <label className="text-xs font-medium opacity-70">BF Config Parameters</label>
                  <Textarea
                    value={state.bfcfgParameters.join('\n')}
                    onChange={(e) => onChange({ bfcfgParameters: e.target.value.split('\n').map((s) => s.trim()).filter(Boolean) })}
                    placeholder={'--bmc-reboot-timeout=300'}
                    className="text-xs font-mono min-h-[48px] resize-y"
                    rows={2}
                  />
                </div>
              </div>
              <div>
                <label className="text-xs font-medium opacity-70">OVS Configuration Script</label>
                <Textarea
                  value={state.ovsRawConfigScript}
                  onChange={(e) => onChange({ ovsRawConfigScript: e.target.value })}
                  placeholder={'#!/bin/bash\n# Custom OVS setup'}
                  className="text-xs font-mono min-h-[56px] resize-y"
                  rows={2}
                />
              </div>
            </div>
          </details>
        </div>
      </details>

      {/* DPU Set */}
      <div className="space-y-3">
        <h5 className="text-xs font-semibold text-foreground/80">
          DPU Set Configuration
        </h5>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div>
            <label className="text-xs font-medium opacity-70">DPU Set Name</label>
            <Input
              value={state.dpuSetName}
              onChange={(e) => onChange({ dpuSetName: e.target.value })}
              placeholder="dpuset-1"
              className="h-8 text-sm"
            />
          </div>
          <div>
            <label className="text-xs font-medium opacity-70">Update Strategy</label>
            <Select value={state.strategy} onValueChange={(v) => onChange({ strategy: v as 'OnDelete' | 'RollingUpdate' })}>
              <SelectTrigger className="h-8 text-sm"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="OnDelete">OnDelete (manual)</SelectItem>
                <SelectItem value="RollingUpdate">RollingUpdate (gradual)</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <label className="text-xs font-medium opacity-70">Node Effect</label>
            <Select value={state.nodeEffect} onValueChange={(v) => onChange({ nodeEffect: v as 'drain' | 'noEffect' | 'taint' | 'hold' })}>
              <SelectTrigger className="h-8 text-sm"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="drain">Drain (recommended)</SelectItem>
                <SelectItem value="noEffect">No Effect</SelectItem>
                <SelectItem value="taint">Taint (NoSchedule)</SelectItem>
                <SelectItem value="hold">Hold (wait for external)</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-end pb-1">
            <label className="flex items-center gap-2 text-xs font-medium">
              <input
                type="checkbox"
                checked={state.secureBoot}
                onChange={(e) => onChange({ secureBoot: e.target.checked })}
                className="rounded border-border"
              />
              UEFI Secure Boot
            </label>
          </div>
        </div>

        {/* Max Unavailable — shown for RollingUpdate */}
        {state.strategy === 'RollingUpdate' && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <div>
              <label className="text-xs font-medium opacity-70">Max Unavailable</label>
              <Input
                value={state.maxUnavailable}
                onChange={(e) => onChange({ maxUnavailable: e.target.value })}
                placeholder="1"
                className="h-8 text-sm"
              />
              <p className="text-[10px] mt-0.5 text-muted-foreground">
                Max DPUs updated simultaneously. Use a number or percentage (e.g. &quot;25%&quot;).
              </p>
            </div>
          </div>
        )}

        {/* Node Selector */}
        <div>
          <label className="text-xs font-medium opacity-70">Host Node Selector</label>
          <Input
            value={Object.entries(state.dpuNodeSelector).map(([k, v]) => `${k}=${v}`).join(', ')}
            onChange={(e) => {
              const pairs: Record<string, string> = {};
              e.target.value.split(',').forEach((pair) => {
                const [k, v] = pair.split('=').map((s) => s.trim());
                if (k && v) pairs[k] = v;
              });
              onChange({ dpuNodeSelector: pairs });
            }}
            placeholder="Leave empty to target all nodes — e.g. node-role.kubernetes.io/dpu=true, nvidia.com/gpu=present"
            className="h-8 text-sm"
          />
          <p className="text-[10px] mt-0.5 text-muted-foreground">
            Comma-separated label selectors to target specific host nodes. Empty = all DPU-capable nodes.
          </p>
        </div>
      </div>
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Step 4: Services
// ---------------------------------------------------------------------------

function presetIcon(icon: DpfUseCasePreset['icon']) {
  switch (icon) {
    case 'network':  return Network;
    case 'cloud':    return Cloud;
    case 'storage':  return Database;
    case 'shield':   return Shield;
    case 'zap':      return Zap;
  }
}

function ServicesStep({
  state,
  onChange,
}: {
  state: DpfSetupState;
  onChange: (partial: Partial<DpfSetupState>) => void;
}) {
  const [selectedPreset, setSelectedPreset] = useState<string | null>(null);

  const applyPreset = useCallback(
    (preset: DpfUseCasePreset) => {
      setSelectedPreset(preset.id);
      onChange({
        services: preset.services(state.namespace),
        serviceChains: preset.serviceChains(state.namespace),
        serviceInterfaces: preset.serviceInterfaces(state.namespace),
      });
    },
    [onChange, state.namespace]
  );

  return (
    <SectionCard title="DPU Services">
      <p className="text-xs text-muted-foreground mb-4 -mt-3">
        Select a networking use case. Services will be deployed as Helm charts to DPU nodes.
      </p>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {DPF_USE_CASE_PRESETS.map((preset) => {
          const Icon = presetIcon(preset.icon);
          const selected = selectedPreset === preset.id;
          const svcCount = preset.services(state.namespace).length;

          return (
            <button
              key={preset.id}
              onClick={() => applyPreset(preset)}
              className={cn(
                'flex flex-col items-start p-4 rounded-lg border text-left transition-all',
                selected
                  ? 'border-primary bg-primary/5 ring-1 ring-primary/30'
                  : 'border-border bg-card hover:bg-muted/50',
              )}
            >
              <div className="flex items-center gap-2 mb-1.5 w-full">
                <Icon className={cn('h-4 w-4', selected ? 'text-primary' : 'text-muted-foreground')} />
                <span className="font-medium text-sm text-foreground/80">
                  {preset.name}
                </span>
                {selected && <Check className="h-4 w-4 text-primary ml-auto" />}
              </div>
              <p className="text-[11px] leading-relaxed text-muted-foreground">
                {preset.description}
              </p>
              {svcCount > 0 && (
                <Badge variant="outline" className="mt-2 text-[10px] px-1.5 py-0">
                  {svcCount} service{svcCount !== 1 ? 's' : ''}
                </Badge>
              )}
            </button>
          );
        })}
      </div>

      {/* Summary of selected services */}
      {state.services.length > 0 && (
        <div className="rounded-lg border border-border bg-muted/50 p-4">
          <h5 className="text-xs font-semibold mb-2 text-foreground/80">
            Selected Services
          </h5>
          <div className="space-y-1.5">
            {state.services.map((svc) => (
              <div key={svc.name} className="flex items-center gap-2 text-xs">
                <Package className="h-3 w-3 text-muted-foreground" />
                <span className="font-medium text-foreground/80">
                  {svc.name}
                </span>
                <span className="font-mono text-[10px] text-muted-foreground">
                  {svc.repoURL}
                </span>
                {svc.version && (
                  <Badge variant="outline" className="text-[10px] px-1 py-0">
                    v{svc.version}
                  </Badge>
                )}
              </div>
            ))}
          </div>
          {state.serviceChains.length > 0 && (
            <div className="mt-2 pt-2 border-t border-dashed space-y-1">
              {state.serviceChains.map((chain) => (
                <div key={chain.name} className="flex items-center gap-2 text-xs">
                  <Network className="h-3 w-3 text-muted-foreground" />
                  <span className="font-medium text-foreground/80">
                    {chain.name}
                  </span>
                  <span className="text-muted-foreground">
                    {chain.switches.length} switch{chain.switches.length !== 1 ? 'es' : ''}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Step 5: Review & Apply
// ---------------------------------------------------------------------------

function ReviewStep({
  yaml,
  resourceCount,
}: {
  yaml: string;
  resourceCount: number;
}) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(yaml);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <SectionCard title="Review Configuration">
      <div className="flex items-center justify-between mb-4 -mt-3">
        <p className="text-xs text-muted-foreground">
          {resourceCount} resource{resourceCount !== 1 ? 's' : ''} will be created.
        </p>
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-xs gap-1.5"
          onClick={handleCopy}
        >
          {copied ? <Check className="h-3 w-3 text-success" /> : <Copy className="h-3 w-3" />}
          {copied ? 'Copied!' : 'Copy YAML'}
        </Button>
      </div>

      <div className="rounded-lg border border-border overflow-hidden">
        <div className="flex items-center gap-2 px-3 py-1.5 text-xs font-medium border-b border-border bg-muted/50 text-muted-foreground">
          <Code className="h-3 w-3" />
          YAML Preview
        </div>
        <pre className="p-4 text-[11px] font-mono leading-relaxed overflow-auto max-h-[500px] bg-card text-foreground/80">
          {yaml}
        </pre>
      </div>
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Main Wizard Component
// ---------------------------------------------------------------------------

export function DPFSetupWizard({ clusterId, onClose }: DPFSetupWizardProps) {
  const queryClient = useQueryClient();
  const [step, setStep] = useState<WizardStep>('mode');
  const [state, setState] = useState<DpfSetupState>(createInitialState);
  const [isApplying, setIsApplying] = useState(false);

  // Kamaji status from cached scan data (if a scan was previously run)
  const { data: scanResult } = useClusterScanResult(clusterId);
  const kamajiStatus = scanResult?.prerequisites?.kamaji?.status;

  // State updater
  const onChange = useCallback((partial: Partial<DpfSetupState>) => {
    setState((prev) => ({ ...prev, ...partial }));
  }, []);

  // YAML generation
  const generatedYaml = useMemo(() => generateAllDpfYaml(state), [state]);
  const resourceCount = useMemo(
    () => splitYamlDocuments(generatedYaml).length,
    [generatedYaml]
  );

  // Validation
  const stepErrors = useMemo(() => {
    const errs: Record<WizardStep, string[]> = {
      mode: [],
      cluster: [],
      provisioning: [],
      services: [],
      review: [],
    };

    // Mode step
    if (!state.namespace) errs.mode.push('Namespace is required');

    // Cluster step
    if (!state.clusterName) errs.cluster.push('Cluster name is required');

    // Provisioning step
    if (!state.bfbName) errs.provisioning.push('BFB image name is required');
    if (!state.bfbUrl) errs.provisioning.push('BFB image URL is required');
    if (!state.dpuSetName) errs.provisioning.push('DPU Set name is required');

    return errs;
  }, [state]);

  const currentStepValid = stepErrors[step].length === 0;
  const allValid = Object.values(stepErrors).every((e) => e.length === 0);

  // Navigation
  const stepIdx = STEPS.findIndex((s) => s.key === step);
  const canGoBack = stepIdx > 0;
  const canGoForward = stepIdx < STEPS.length - 1;

  const goNext = () => {
    if (canGoForward) setStep(STEPS[stepIdx + 1].key);
  };
  const goBack = () => {
    if (canGoBack) setStep(STEPS[stepIdx - 1].key);
  };

  // Apply
  const handleApply = async (dryRun: boolean) => {
    if (!allValid) return;
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
        notify.success(`Dry run passed — all ${docs.length} resources are valid`, undefined, { category: 'cluster' });
      } else {
        notify.success(`DPF setup complete — ${docs.length} resources created`, undefined, { category: 'cluster' });
        queryClient.invalidateQueries({ queryKey: ['dpf'] });
        queryClient.invalidateQueries({ queryKey: ['k8s', 'clusters', clusterId] });
        onClose?.();
      }
    } catch (err: unknown) {
      const parsed = parseApiError(err);
      notify.error(dryRun ? 'Dry run failed' : 'Apply failed', parsed.message, { category: 'cluster' });
    } finally {
      setIsApplying(false);
    }
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Step indicator */}
      <StepIndicator steps={STEPS} currentStep={step} onStepClick={setStep} />

      {/* Step content */}
      <div className="min-h-[400px]">
        {step === 'mode' && <ModeStep state={state} onChange={onChange} />}
        {step === 'cluster' && <ClusterStep state={state} onChange={onChange} kamajiStatus={kamajiStatus} />}
        {step === 'provisioning' && (
          <ProvisioningStep state={state} onChange={onChange} />
        )}
        {step === 'services' && <ServicesStep state={state} onChange={onChange} />}
        {step === 'review' && (
          <ReviewStep yaml={generatedYaml} resourceCount={resourceCount} />
        )}
      </div>

      {/* Validation errors for current step */}
      {stepErrors[step].length > 0 && (
        <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
          {stepErrors[step].map((err) => (
            <span key={err} className="flex items-center gap-1">
              <AlertTriangle className="h-3 w-3 text-warning" />
              {err}
            </span>
          ))}
        </div>
      )}

      {/* Navigation + actions */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            className="h-8 text-xs gap-1.5"
            onClick={goBack}
            disabled={!canGoBack}
          >
            <ArrowLeft className="h-3.5 w-3.5" /> Back
          </Button>
          {onClose && (
            <Button
              variant="ghost"
              size="sm"
              className="h-8 text-xs"
              onClick={onClose}
            >
              Cancel
            </Button>
          )}
        </div>

        <div className="flex items-center gap-2">
          {step === 'review' ? (
            <>
              <Button
                variant="outline"
                size="sm"
                className="h-8 text-xs gap-1.5"
                onClick={() => handleApply(true)}
                disabled={!allValid || isApplying}
              >
                <Play className="h-3.5 w-3.5" /> Dry Run
              </Button>
              <Button
                size="sm"
                className="h-8 text-xs gap-1.5"
                onClick={() => handleApply(false)}
                disabled={!allValid || isApplying}
              >
                <Check className="h-3.5 w-3.5" /> Apply All ({resourceCount})
              </Button>
            </>
          ) : (
            <Button
              size="sm"
              className="h-8 text-xs gap-1.5"
              onClick={goNext}
              disabled={!canGoForward || !currentStepValid}
            >
              Next <ArrowRight className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
