/**
 * F5 BNK sub-module barrel export
 */
export {
  VIEW_HEALTH, VIEW_POLICY_MAP, VIEW_AI_ANALYZERS, VIEW_TOPOLOGY, VIEW_TRAFFIC_FLOW, VIEW_UPGRADE, VIEW_DIAGNOSTICS, VIEW_BACKENDS, VIEW_POLICY_BUILDER, VIEW_CONFIG_BUILDER, VIEW_DPF_INFRA,
  VIEW_A2A_DISCOVERY, VIEW_A2A_TEMPLATES, VIEW_A2A_IRULE_LIBRARY, VIEW_A2A_REFERENCE,
  SPECIAL_VIEWS, isSpecialView,
  bnkResourceCategories,
} from './bnk-constants';

export { F5BNKSidebar } from './F5BNKSidebar';
export { buildBnkCategories } from './bnk-categories';
export type { BnkCategory, BnkCategoryItem } from './bnk-categories';
export { F5BNKResourceTable } from './F5BNKResourceTable';
export { F5BNKDetailPanel } from './F5BNKDetailPanel';
export {
  getRegistryEntry, getDetailComponent, getContextActions,
  getResourceIcon, getDetailQuickActions,
} from './resource-registry';
export type { ResourceContextAction, DetailPanelProps, ResourceRegistryEntry } from './resource-registry';
