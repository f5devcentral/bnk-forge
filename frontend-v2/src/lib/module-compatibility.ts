import type { ModuleLibrary } from '@/types';

const PROFILE_LABELS: Record<string, string> = {
  generic_onprem: 'Generic On-Prem',
  eks: 'EKS',
  aks: 'AKS',
  gke: 'GKE',
  roks: 'ROKS',
  ocp: 'OpenShift/OKD',
  unknown: 'Unknown',
};

const OCP_CAPABILITY_SUPPORT_HINTS: Record<string, string> = {
  openshift_routes: 'Route/operator-managed networking semantics',
  scc: 'Security Context Constraints (SCC) security-binding semantics',
  machine_config: 'MachineConfig operator-managed node/day-2 semantics',
  operator_framework: 'operator-managed platform workflow semantics',
};

function formatProfile(profile: string): string {
  return PROFILE_LABELS[profile] ?? profile;
}

export function getCompatibilitySummary(module: ModuleLibrary): string | null {
  const compatibility = module.platform_compatibility;
  if (!compatibility) return null;

  const profiles = compatibility.supported_profiles ?? [];
  const unmapped = compatibility.unmapped_supported_platforms ?? [];
  if (profiles.length === 0) {
    if (unmapped.length > 0) {
      return `Declared compatibility: ${unmapped.join(', ')} (unmapped profile label)`;
    }
    return 'Declared compatibility: profile details unavailable';
  }

  if (compatibility.declared_any) {
    return 'Declared compatibility: all current platform profiles';
  }

  const profileSummary = `Declared compatibility: ${profiles.map(formatProfile).join(', ')}`;
  if (unmapped.length > 0) {
    return `${profileSummary} (+ ${unmapped.join(', ')} declared as unmapped profile label)`;
  }

  return profileSummary;
}

export function getCapabilitySummary(module: ModuleLibrary): string | null {
  const capabilities = module.platform_compatibility?.required_capabilities ?? [];
  if (capabilities.length === 0) {
    return null;
  }
  return `Required capabilities: ${capabilities.join(', ')}`;
}

export function getSupportSemanticsSummary(module: ModuleLibrary): string | null {
  const compatibility = module.platform_compatibility;
  if (!compatibility) {
    return null;
  }

  const profiles = compatibility.supported_profiles ?? [];
  const isOcpDeclared = compatibility.declared_any || profiles.includes('ocp');
  if (!isOcpDeclared) {
    return null;
  }

  const ocpHints = (compatibility.required_capabilities ?? [])
    .map((capability) => OCP_CAPABILITY_SUPPORT_HINTS[capability])
    .filter((hint): hint is string => Boolean(hint));

  if (ocpHints.length > 0) {
    return `OpenShift/OKD support semantics: runtime support follows detected cluster context and ${ocpHints.join('; ')}.`;
  }

  return 'OpenShift/OKD support semantics: runtime support follows detected cluster context and platform constraints instead of generic Kubernetes assumptions.';
}
