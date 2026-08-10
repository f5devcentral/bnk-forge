/**
 * API Module Index
 * 
 * This file re-exports all domain-specific API modules and provides
 * a unified `api` object for backward compatibility.
 * 
 * NEW CODE: Import domain-specific modules directly:
 *   import { projectsApi } from '@/lib/api/projects';
 *   import { kubernetesApi } from '@/lib/api/kubernetes';
 * 
 * EXISTING CODE: Continue using the unified `api` object:
 *   import { api } from '@/lib/api';
 */

// Re-export the client for direct use
export { apiClient } from './client';

// Re-export domain-specific APIs
export { benchmarksApi } from './benchmarks';
export { authApi } from './auth';
export { projectsApi } from './projects';
export { kubernetesApi } from './kubernetes';
export { helmApi } from './helm';
export { tasksApi } from './tasks';
export { stacksApi } from './stacks';
export { systemApi } from './system';
export { credentialsApi } from './credentials';
export { driftApi } from './drift';
export { operatorsApi } from './operators';
export { alertsApi } from './alerts';
export { modulesApi } from './modules';
export { blueprintCatalogApi } from './blueprint-catalog';
export { fleetApi } from './fleet';
export { runbooksApi } from './runbooks';
export { snapshotsApi } from './snapshots';
export { configPromotionApi } from './config-promotion';
export { sshCredentialsApi } from './ssh-credentials';
export { containerRegistriesApi } from './container-registries';
export { licensingApi } from './licensing';
export { recoveryApi } from './recovery';
export { qkviewApi } from './qkview';
export { tmmDebugApi } from './tmm-debug';
export { notificationsApi } from './notifications';
export { settingsApi } from './settings';
export { auditApi } from './audit';
export { backupApi } from './backup';
export { llmObservabilityApi } from './llmObservability';

// Import all for the unified api object
import { benchmarksApi } from './benchmarks';
import { authApi } from './auth';
import { projectsApi } from './projects';
import { kubernetesApi } from './kubernetes';
import { helmApi } from './helm';
import { tasksApi } from './tasks';
import { stacksApi } from './stacks';
import { systemApi } from './system';
import { credentialsApi } from './credentials';
import { sshCredentialsApi } from './ssh-credentials';
import { containerRegistriesApi } from './container-registries';
import { driftApi } from './drift';
import { operatorsApi } from './operators';
import { alertsApi } from './alerts';
import { modulesApi } from './modules';
import { blueprintCatalogApi } from './blueprint-catalog';
import { fleetApi } from './fleet';
import { runbooksApi } from './runbooks';
import { snapshotsApi } from './snapshots';
import { configPromotionApi } from './config-promotion';
import { licensingApi } from './licensing';
import { recoveryApi } from './recovery';
import { qkviewApi } from './qkview';
import { tmmDebugApi } from './tmm-debug';

/**
 * Unified API object for backward compatibility
 * 
 * All methods from all domain-specific APIs are available here.
 * This allows existing code to continue working without changes.
 */
export const api = {
  // Benchmarks
  ...benchmarksApi,
  
  // Auth
  ...authApi,
  
  // Projects & Modules
  ...projectsApi,
  
  // Kubernetes & F5 BNK
  ...kubernetesApi,
  
  // Helm
  ...helmApi,
  
  // Tasks
  ...tasksApi,
  
  // Stacks
  ...stacksApi,
  
  // System
  ...systemApi,
  
  // Credentials
  ...credentialsApi,
  
  // Drift
  ...driftApi,
  
  // Operators
  ...operatorsApi,
  
  // Alerts
  ...alertsApi,
  
  // Module Sources & Registry
  ...modulesApi,

  // Blueprint Catalog
  ...blueprintCatalogApi,

  // Fleet Dashboard
  ...fleetApi,
  
  // Runbook Automation
  ...runbooksApi,

  // Config Snapshots
  ...snapshotsApi,

  // Config Promotion
  ...configPromotionApi,

  // SSH Credentials (first-class on-prem/bastion access)
  ...sshCredentialsApi,

  // Container Registries (first-class OCI registry access)
  ...containerRegistriesApi,

  // BNK Licensing (CWC license management via operator)
  ...licensingApi,

  // Recovery (K8s cert rotation & cluster recovery)
  ...recoveryApi,

  // QKView (BNK diagnostics)
  ...qkviewApi,

  // TMM Debug (traffic management debugging)
  ...tmmDebugApi,
};
