import type {
  K8sCluster,
  ManagementBoundary,
  PlatformProfile,
  Project,
} from '@/types';

const PLATFORM_LABELS: Record<PlatformProfile, string> = {
  generic_onprem: 'Generic On-Prem',
  eks: 'Amazon EKS',
  aks: 'Azure AKS',
  gke: 'Google GKE',
  roks: 'IBM ROKS',
  ocp: 'OpenShift / OKD',
  unknown: 'Unknown',
};

const MANAGEMENT_BOUNDARY_LABELS: Record<ManagementBoundary, string> = {
  customer_managed_cluster: 'Customer-managed cluster',
  forge_managed_components_only: 'Forge-managed components only',
  unknown: 'Unknown management boundary',
};

export function getPlatformProfileLabel(profile?: PlatformProfile | null): string {
  if (!profile) return PLATFORM_LABELS.unknown;
  return PLATFORM_LABELS[profile] ?? PLATFORM_LABELS.unknown;
}

export function getManagementBoundaryLabel(boundary?: ManagementBoundary | null): string | null {
  if (!boundary) return null;
  return MANAGEMENT_BOUNDARY_LABELS[boundary] ?? MANAGEMENT_BOUNDARY_LABELS.unknown;
}

export function getProjectTargetPlatform(project?: Project | null): PlatformProfile {
  return project?.target_platform_profile ?? 'unknown';
}

export function getClusterDetectedPlatform(cluster?: K8sCluster | null): PlatformProfile {
  return cluster?.detected_platform_profile ?? 'unknown';
}

// Profiles that are compatible with each other (no mismatch warning).
// OCP runs on-prem, so generic_onprem is compatible until a scan confirms OCP.
const COMPATIBLE_PROFILES: Record<string, string[]> = {
  ocp: ['generic_onprem'],
  generic_onprem: ['ocp'],
};

export function getProjectClusterPlatformMismatch(
  project?: Project | null,
  cluster?: K8sCluster | null,
): boolean {
  const target = getProjectTargetPlatform(project);
  const detected = getClusterDetectedPlatform(cluster);

  if (target === 'unknown' || detected === 'unknown') {
    return false;
  }

  if (target === detected) {
    return false;
  }

  // Check compatibility (e.g. OCP project + generic_onprem cluster before scan)
  return !(COMPATIBLE_PROFILES[target]?.includes(detected) ?? false);
}

export function shouldShowDetectEks(project?: Project | null): boolean {
  const target = getProjectTargetPlatform(project);
  return (
    target === 'eks'
    || target === 'roks'
    || target === 'aks'
    || target === 'gke'
    || project?.cloud_provider === 'aws'
    || project?.cloud_provider === 'eks'
    || project?.cloud_provider === 'ibm'
    || project?.cloud_provider === 'roks'
    || project?.cloud_provider === 'azure'
    || project?.cloud_provider === 'aks'
    || project?.cloud_provider === 'gcp'
    || project?.cloud_provider === 'gke'
  );
}

export function shouldShowDetectManagedClustersFromContext(
  targetPlatformProfile?: PlatformProfile | null,
  cloudProvider?: string | null,
): boolean {
  return (
    targetPlatformProfile === 'eks'
    || targetPlatformProfile === 'roks'
    || targetPlatformProfile === 'aks'
    || targetPlatformProfile === 'gke'
    || cloudProvider === 'aws'
    || cloudProvider === 'eks'
    || cloudProvider === 'ibm'
    || cloudProvider === 'roks'
    || cloudProvider === 'azure'
    || cloudProvider === 'aks'
    || cloudProvider === 'gcp'
    || cloudProvider === 'gke'
  );
}

export const shouldShowDetectEksFromContext = shouldShowDetectManagedClustersFromContext;
