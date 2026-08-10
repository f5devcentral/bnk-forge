/**
 * F5 BNK Detail Panels — barrel index
 *
 * Re-exports every detail component from its individual file so that
 * consumers can import from a single path:
 *
 *   import { VlanDetail, FirewallPolicyDetail } from '@/components/k8s/f5bnk-details';
 */

export { VlanDetail } from './VlanDetail';
export { FirewallPolicyDetail } from './FirewallPolicyDetail';
export { SecurityPolicyDetail } from './SecurityPolicyDetail';
export { NetworkPolicyDetail } from './NetworkPolicyDetail';
export { iRuleDetail } from './iRuleDetail';
export { SnatPoolDetail } from './SnatPoolDetail';
export { AddressListDetail } from './AddressListDetail';
export { PortListDetail } from './PortListDetail';
export { CNEInstanceDetail } from './CNEInstanceDetail';
export { StaticRouteDetail } from './StaticRouteDetail';
export { EgressDetail } from './EgressDetail';
export { HslPublisherDetail } from './HslPublisherDetail';
export { LogProfileDetail } from './LogProfileDetail';
export { GlobalOptionsDetail } from './GlobalOptionsDetail';
export { DdosGlobalDetail } from './DdosGlobalDetail';
export { GatewayClassDetail } from './GatewayClassDetail';
export { L4RouteDetail } from './L4RouteDetail';
export { IpamRangeDetail } from './IpamRangeDetail';
export { BnkGatewayDetail } from './BnkGatewayDetail';
export { FirewallRuleListDetail } from './FirewallRuleListDetail';

// Re-export shared types for consumers that need them
export type { DetailPanelProps } from './shared';
