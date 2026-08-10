/**
 * F5 BNK Resource Registry
 *
 * Maps resource.kind → detail component, context menu actions, and icon.
 * Replaces the 24-branch if/else chain and 17 conditional dropdown items
 * in the original F5BNK.tsx with a single registry lookup.
 */

import type { ComponentType } from 'react';
import type { LucideIcon } from 'lucide-react';
import type { K8sResource } from '@/types/kubernetes';
import {
  Shield, Globe, Route, Network, Server, Activity, Settings,
  Code, ShieldAlert, FileText, Map, Eye,
} from 'lucide-react';

// Detail panel components
import { GatewayDetail } from '@/components/k8s/GatewayDetail';
import { HTTPRouteDetail } from '@/components/k8s/HTTPRouteDetail';
import {
  VlanDetail,
  FirewallPolicyDetail,
  SecurityPolicyDetail,
  NetworkPolicyDetail,
  iRuleDetail as IRuleDetailPanel,
  SnatPoolDetail,
  AddressListDetail,
  PortListDetail,
  CNEInstanceDetail,
  StaticRouteDetail,
  EgressDetail,
  HslPublisherDetail,
  LogProfileDetail,
  GlobalOptionsDetail,
  DdosGlobalDetail,
  GatewayClassDetail,
  L4RouteDetail,
  IpamRangeDetail,
  BnkGatewayDetail,
  FirewallRuleListDetail,
} from '@/components/k8s/f5bnk-details';

import { VIEW_POLICY_MAP, VIEW_AI_ANALYZERS } from './bnk-constants';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** A detail panel component accepts a single K8s resource */
export interface DetailPanelProps {
  resource: K8sResource;
}

/**
 * A context action for a resource kind.
 * - `navigate` means "switch the sidebar to this view"
 * - `select` means "select this resource in the detail panel"
 * - `dialog` means "open a specific dialog"
 */
export interface ResourceContextAction {
  label: string;
  icon: LucideIcon;
  type: 'navigate' | 'select' | 'dialog';
  /** For 'navigate': the view key to switch to */
  targetView?: string;
  /** For 'dialog': which dialog to open */
  dialogKey?: string;
}

export interface ResourceRegistryEntry {
  /** Detail panel component (or null for fallback) */
  detailComponent: ComponentType<DetailPanelProps> | null;
  /** Context-specific dropdown actions */
  contextActions: ResourceContextAction[];
  /** Icon for this resource kind */
  icon: LucideIcon;
}

// ---------------------------------------------------------------------------
// Registry Data
// ---------------------------------------------------------------------------

const registry: Record<string, ResourceRegistryEntry> = {
  Gateway: {
    detailComponent: GatewayDetail,
    contextActions: [
      { label: 'View Policies', icon: Shield, type: 'navigate', targetView: VIEW_POLICY_MAP },
    ],
    icon: Globe,
  },
  HTTPRoute: {
    detailComponent: HTTPRouteDetail,
    contextActions: [
      { label: 'View Route Details', icon: Activity, type: 'select' },
    ],
    icon: Route,
  },
  GatewayClass: {
    detailComponent: GatewayClassDetail,
    contextActions: [],
    icon: Shield,
  },
  F5SPKVlan: {
    detailComponent: VlanDetail,
    contextActions: [
      { label: 'View Configuration', icon: Server, type: 'select' },
    ],
    icon: Network,
  },
  F5BigFwPolicy: {
    detailComponent: FirewallPolicyDetail,
    contextActions: [
      { label: 'View Gateway Associations', icon: Globe, type: 'navigate', targetView: VIEW_POLICY_MAP },
    ],
    icon: Shield,
  },
  F5BigFwRulelist: {
    detailComponent: FirewallRuleListDetail,
    contextActions: [
      { label: 'View Rules', icon: Shield, type: 'select' },
    ],
    icon: Shield,
  },
  BNKSecPolicy: {
    detailComponent: SecurityPolicyDetail,
    contextActions: [
      { label: 'View Gateway Associations', icon: Globe, type: 'navigate', targetView: VIEW_POLICY_MAP },
    ],
    icon: Shield,
  },
  BNKNetPolicy: {
    detailComponent: NetworkPolicyDetail,
    contextActions: [
      { label: 'View Extensions', icon: Settings, type: 'select' },
    ],
    icon: Network,
  },
  F5BigCneIrule: {
    detailComponent: IRuleDetailPanel,
    contextActions: [
      { label: 'View iRule Code', icon: Code, type: 'dialog', dialogKey: 'irule-viewer' },
    ],
    icon: Code,
  },
  F5SPKSnatpool: {
    detailComponent: SnatPoolDetail,
    contextActions: [],
    icon: Server,
  },
  F5BigCneAddresslist: {
    detailComponent: AddressListDetail,
    contextActions: [],
    icon: Network,
  },
  F5BigCnePortlist: {
    detailComponent: PortListDetail,
    contextActions: [],
    icon: Network,
  },
  CNEInstance: {
    detailComponent: CNEInstanceDetail,
    contextActions: [
      { label: 'View Features & Config', icon: Server, type: 'select' },
    ],
    icon: Server,
  },
  F5SPKStaticRoute: {
    detailComponent: StaticRouteDetail,
    contextActions: [
      { label: 'View Configuration', icon: Server, type: 'select' },
    ],
    icon: Route,
  },
  F5SPKEgress: {
    detailComponent: EgressDetail,
    contextActions: [
      { label: 'View Egress Config', icon: Network, type: 'select' },
    ],
    icon: Network,
  },
  F5BigLogHslpub: {
    detailComponent: HslPublisherDetail,
    contextActions: [
      { label: 'View Publisher Config', icon: Activity, type: 'select' },
    ],
    icon: Activity,
  },
  F5BigLogProfile: {
    detailComponent: LogProfileDetail,
    contextActions: [
      { label: 'View Log Config', icon: FileText, type: 'select' },
    ],
    icon: FileText,
  },
  F5BigGlobalOptions: {
    detailComponent: GlobalOptionsDetail,
    contextActions: [],
    icon: Settings,
  },
  F5SPKGlobalOptions: {
    detailComponent: GlobalOptionsDetail,
    contextActions: [],
    icon: Settings,
  },
  F5BigDdosGlobal: {
    detailComponent: DdosGlobalDetail,
    contextActions: [
      { label: 'View DDoS Config', icon: ShieldAlert, type: 'select' },
    ],
    icon: ShieldAlert,
  },
  L4Route: {
    detailComponent: L4RouteDetail,
    contextActions: [
      { label: 'View Backends', icon: Route, type: 'select' },
    ],
    icon: Route,
  },
  IPAMRange: {
    detailComponent: IpamRangeDetail,
    contextActions: [
      { label: 'View Allocations', icon: Network, type: 'select' },
    ],
    icon: Network,
  },
  F5BnkGateway: {
    detailComponent: BnkGatewayDetail,
    contextActions: [
      { label: 'View IPAM Config', icon: Network, type: 'select' },
    ],
    icon: Globe,
  },
  F5BigAnalyzer: {
    detailComponent: null,
    contextActions: [
      { label: 'View AI Dashboard', icon: Activity, type: 'navigate', targetView: VIEW_AI_ANALYZERS },
    ],
    icon: Activity,
  },
};

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

const DEFAULT_ENTRY: ResourceRegistryEntry = {
  detailComponent: null,
  contextActions: [],
  icon: Eye,
};

/** Look up the full registry entry for a resource kind */
export function getRegistryEntry(kind: string): ResourceRegistryEntry {
  return registry[kind] ?? DEFAULT_ENTRY;
}

/** Get the detail component for a resource kind (null = use fallback) */
export function getDetailComponent(kind: string): ComponentType<DetailPanelProps> | null {
  return (registry[kind]?.detailComponent) ?? null;
}

/** Get the context-specific actions for a resource kind */
export function getContextActions(kind: string): ResourceContextAction[] {
  return registry[kind]?.contextActions ?? [];
}

/** Get the icon for a resource kind */
export function getResourceIcon(kind: string): LucideIcon {
  return registry[kind]?.icon ?? Eye;
}

/** Quick actions that appear in the detail panel (beyond Describe/Edit/Delete) */
export function getDetailQuickActions(kind: string): ResourceContextAction[] {
  const actions: ResourceContextAction[] = [];

  // iRule code viewer
  if (kind === 'F5BigCneIrule') {
    actions.push({ label: 'View Code', icon: Code, type: 'dialog', dialogKey: 'irule-viewer' });
  }

  // Policy map navigation for gateways and policies
  if (kind === 'Gateway' || kind === 'F5BigFwPolicy' || kind === 'BNKSecPolicy') {
    actions.push({ label: 'Policy Map', icon: Map, type: 'navigate', targetView: VIEW_POLICY_MAP });
  }

  // AI dashboard navigation
  if (kind === 'F5BigAnalyzer') {
    actions.push({ label: 'AI Dashboard', icon: Activity, type: 'navigate', targetView: VIEW_AI_ANALYZERS });
  }

  return actions;
}
