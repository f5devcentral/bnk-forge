/**
 * BNK Resource Categories and View Constants
 * Organized by logical workflow for F5 BNK sidebar navigation.
 */

import {
  Shield, Globe, Route, Network, Lock, Server, Activity, Settings,
  LayoutDashboard, Map, Code, ShieldAlert, List, FileText, GitBranch,
  ArrowUpCircle, Stethoscope, Wand2, Hammer, Workflow,
  Bot, Radar, BookOpen,
} from 'lucide-react';

// Special view identifiers (not actual K8s resource types)
export const VIEW_HEALTH = 'view-health';
export const VIEW_POLICY_MAP = 'view-policy-map';
export const VIEW_AI_ANALYZERS = 'view-ai-analyzers';
export const VIEW_TOPOLOGY = 'view-topology';
export const VIEW_UPGRADE = 'view-upgrade';
export const VIEW_DIAGNOSTICS = 'view-diagnostics';
export const VIEW_BACKENDS = 'view-backends';
export const VIEW_POLICY_BUILDER = 'view-policy-builder';
export const VIEW_CONFIG_BUILDER = 'view-config-builder';
export const VIEW_TRAFFIC_FLOW = 'view-traffic-flow';
export const VIEW_DPF_INFRA = 'view-dpf-infra'; // Moved to Fleet page — kept for deep-link backward compat

// A2A Protocol views
export const VIEW_A2A_DISCOVERY = 'view-a2a-discovery';
export const VIEW_A2A_TEMPLATES = 'view-a2a-templates';
export const VIEW_A2A_IRULE_LIBRARY = 'view-a2a-irule-library';
export const VIEW_A2A_REFERENCE = 'view-a2a-reference';

export const SPECIAL_VIEWS = [VIEW_HEALTH, VIEW_POLICY_MAP, VIEW_AI_ANALYZERS, VIEW_TOPOLOGY, VIEW_TRAFFIC_FLOW, VIEW_UPGRADE, VIEW_DIAGNOSTICS, VIEW_BACKENDS, VIEW_POLICY_BUILDER, VIEW_CONFIG_BUILDER, VIEW_DPF_INFRA, VIEW_A2A_DISCOVERY, VIEW_A2A_TEMPLATES, VIEW_A2A_IRULE_LIBRARY, VIEW_A2A_REFERENCE];

export const isSpecialView = (type: string) => SPECIAL_VIEWS.includes(type);

// BNK Resource Categories - Organized by logical workflow
// Order: Insights (see) → Build (create) → Manage (traffic/security/networking)
export const bnkResourceCategories = [
  {
    category: 'Insights',
    icon: LayoutDashboard,
    items: [
      { key: VIEW_TRAFFIC_FLOW, label: 'Traffic Flow', icon: Workflow },
      { key: VIEW_HEALTH, label: 'Health Dashboard', icon: Activity },
      { key: VIEW_TOPOLOGY, label: 'Gateway Topology', icon: GitBranch },
      { key: VIEW_POLICY_MAP, label: 'Policy Gateway Map', icon: Map },
      { key: VIEW_AI_ANALYZERS, label: 'AI Analyzers', icon: Activity },
      { key: VIEW_UPGRADE, label: 'Upgrade', icon: ArrowUpCircle },
      { key: VIEW_DIAGNOSTICS, label: 'Diagnostics', icon: Stethoscope },
    ],
  },
  {
    category: 'Build',
    icon: Hammer,
    items: [
      { key: VIEW_CONFIG_BUILDER, label: 'Configuration Builder', icon: Wand2 },
      { key: VIEW_POLICY_BUILDER, label: 'Policy Builder', icon: Wand2 },
    ],
  },
  {
    category: 'A2A Protocol',
    icon: Bot,
    items: [
      { key: VIEW_A2A_DISCOVERY, label: 'Agent Discovery', icon: Radar },
      { key: VIEW_A2A_TEMPLATES, label: 'A2A Templates', icon: Wand2 },
      { key: VIEW_A2A_IRULE_LIBRARY, label: 'iRule Library', icon: Code },
      { key: VIEW_A2A_REFERENCE, label: 'Protocol Reference', icon: BookOpen },
    ],
  },
  {
    category: 'Traffic Management',
    icon: Globe,
    items: [
      { key: VIEW_BACKENDS, label: 'Backends', icon: Server },
      { key: 'gatewayclass', label: 'Gateway Classes', icon: Shield },
      { key: 'gateway', label: 'Gateways', icon: Globe },
      { key: 'httproute', label: 'HTTP Routes', icon: Route },
      { key: 'grpcroute', label: 'GRPC Routes', icon: Network },
      { key: 'tcproute', label: 'TCP Routes', icon: Network },
      { key: 'udproute', label: 'UDP Routes', icon: Network },
      { key: 'tlsroute', label: 'TLS Routes', icon: Lock },
    ],
  },
  {
    category: 'Security',
    icon: Shield,
    items: [
      { key: 'f5bigfwpolicy', label: 'Firewall Policies', icon: Shield },
      { key: 'f5bigfwrulelist', label: 'Firewall Rule Lists', icon: List },
      { key: 'bnksecpolicy', label: 'Security Policies', icon: Shield },
      { key: 'bnknetpolicy', label: 'Network Policies', icon: Network },
      { key: 'f5bigddosglobal', label: 'DDoS Protection', icon: ShieldAlert },
      { key: 'f5bigcneaddresslist', label: 'Address Lists', icon: Network },
      { key: 'f5bigcneportlist', label: 'Port Lists', icon: Network },
    ],
  },
  {
    category: 'Networking',
    icon: Network,
    items: [
      { key: 'f5spkvlan', label: 'VLANs', icon: Network },
      { key: 'f5spkstaticroute', label: 'Static Routes', icon: Route },
      { key: 'f5spksnatpool', label: 'SNAT Pools', icon: Server },
      { key: 'f5spkegress', label: 'Egress Config', icon: Network },
      { key: 'f5bigcneirule', label: 'iRules', icon: Code },
    ],
  },
  {
    category: 'Logging & Telemetry',
    icon: Activity,
    items: [
      { key: 'f5bigloghslpub', label: 'HSL Publishers', icon: Activity },
      { key: 'f5biglogprofile', label: 'Log Profiles', icon: FileText },
    ],
  },
  {
    category: 'System',
    icon: Settings,
    items: [
      { key: 'cneinstance', label: 'CNE Instances', icon: Server },
      { key: 'f5bnkgateway', label: 'BNK Gateway (IPAM)', icon: Globe },
      { key: 'ipamrange', label: 'IPAM Ranges', icon: Network },
      { key: 'f5spkglobaloptions', label: 'Global Options', icon: Settings },
      { key: 'l4route', label: 'L4 Routes', icon: Route },
      { key: 'referencegrant', label: 'Reference Grants', icon: Shield },
    ],
  },
];
