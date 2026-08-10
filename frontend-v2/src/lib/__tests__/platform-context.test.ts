import { describe, expect, it } from 'vitest';
import {
  getPlatformProfileLabel,
  getProjectClusterPlatformMismatch,
  shouldShowDetectManagedClustersFromContext,
  shouldShowDetectEksFromContext,
} from '@/lib/platform-context';

describe('platform-context helpers', () => {
  it('formats canonical platform labels', () => {
    expect(getPlatformProfileLabel('eks')).toBe('Amazon EKS');
    expect(getPlatformProfileLabel('roks')).toBe('IBM ROKS');
    expect(getPlatformProfileLabel('generic_onprem')).toBe('Generic On-Prem');
    expect(getPlatformProfileLabel('unknown')).toBe('Unknown');
  });

  it('detects mismatch only when both sides are known and different', () => {
    expect(getProjectClusterPlatformMismatch(
      { target_platform_profile: 'eks' } as never,
      { detected_platform_profile: 'ocp' } as never,
    )).toBe(true);

    expect(getProjectClusterPlatformMismatch(
      { target_platform_profile: 'eks' } as never,
      { detected_platform_profile: 'eks' } as never,
    )).toBe(false);

    expect(getProjectClusterPlatformMismatch(
      { target_platform_profile: 'unknown' } as never,
      { detected_platform_profile: 'ocp' } as never,
    )).toBe(false);
  });

  it('gates EKS detection using target-platform metadata first', () => {
    expect(shouldShowDetectEksFromContext('eks', 'on-prem')).toBe(true);
    expect(shouldShowDetectEksFromContext('generic_onprem', 'aws')).toBe(true);
    expect(shouldShowDetectEksFromContext('generic_onprem', 'on-prem')).toBe(false);
  });

  it('gates managed-cluster detection for IBM/ROKS too', () => {
    expect(shouldShowDetectManagedClustersFromContext('roks', 'on-prem')).toBe(true);
    expect(shouldShowDetectManagedClustersFromContext('generic_onprem', 'ibm')).toBe(true);
    expect(shouldShowDetectManagedClustersFromContext('generic_onprem', 'on-prem')).toBe(false);
  });

  it('gates managed-cluster detection for Azure/GCP too (#220)', () => {
    // Target-platform profile drives detection.
    expect(shouldShowDetectManagedClustersFromContext('aks', 'on-prem')).toBe(true);
    expect(shouldShowDetectManagedClustersFromContext('gke', 'on-prem')).toBe(true);
    // cloud_provider drives detection.
    expect(shouldShowDetectManagedClustersFromContext('generic_onprem', 'azure')).toBe(true);
    expect(shouldShowDetectManagedClustersFromContext('generic_onprem', 'gcp')).toBe(true);
    expect(shouldShowDetectManagedClustersFromContext('generic_onprem', 'aks')).toBe(true);
    expect(shouldShowDetectManagedClustersFromContext('generic_onprem', 'gke')).toBe(true);
  });
});
