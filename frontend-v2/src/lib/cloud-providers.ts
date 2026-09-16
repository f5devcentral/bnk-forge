/**
 * Multi-cloud provider metadata and helper functions for BNK Forge.
 * Centralizes cloud provider badges, display names, and multi-cloud region heuristics.
 */

import { getRegionInfo } from './aws-regions';

export interface CloudProviderBadgeInfo {
  provider: string;
  label: string;
  shortLabel: string;
  badgeVariant: 'default' | 'secondary' | 'outline';
  badgeClass: string;
}

export type NormalizedCloudProvider = 'aws' | 'azure' | 'gke' | 'metal' | 'ibm' | 'other';

/**
 * Normalize a cloud provider or project type string into a canonical provider key.
 */
export function normalizeProvider(provider?: string | null): NormalizedCloudProvider {
  const p = (provider || '').toLowerCase().trim();
  if (p === 'aws' || p === 'eks' || p.includes('cloud-aws')) return 'aws';
  if (p === 'azure' || p === 'aks' || p.includes('cloud-azure')) return 'azure';
  if (p === 'gcp' || p === 'gke' || p === 'google' || p.includes('cloud-gcp')) return 'gke';
  if (p === 'bare-metal' || p === 'on-prem' || p === 'metal' || p === 'kubernetes') return 'metal';
  if (p === 'ibm' || p === 'roks' || p === 'ibmcloud' || p.includes('cloud-ibm')) return 'ibm';
  return 'other';
}

/**
 * Strip common provider prefixes (e.g., 'awsbnkctl-', 'azrbnkctl-', 'aws-') to match correlated resources.
 */
export function cleanNameForMatching(name: string): string {
  return name
    .toLowerCase()
    .replace(/^(aws|azr|gke|ibm|metal|gcp|k8s)bnkctl-/, '')
    .replace(/^(aws|azr|gke|ibm|metal|gcp|k8s)-/, '')
    .trim();
}

/**
 * Get display badge metadata for a cloud provider.
 */
export function getCloudProviderBadgeInfo(provider?: string | null): CloudProviderBadgeInfo {
  const norm = normalizeProvider(provider);
  if (norm === 'aws') {
    return {
      provider: 'aws',
      label: 'Amazon Web Services',
      shortLabel: 'AWS',
      badgeVariant: 'outline',
      badgeClass: 'border-warning/40 text-warning bg-warning/10 font-semibold text-[10px] px-1.5 py-0.5',
    };
  }
  if (norm === 'gke') {
    return {
      provider: 'gcp',
      label: 'Google Cloud Platform (GKE)',
      shortLabel: 'GKE',
      badgeVariant: 'outline',
      badgeClass: 'border-primary/40 text-primary bg-primary/10 font-semibold text-[10px] px-1.5 py-0.5',
    };
  }
  if (norm === 'azure') {
    return {
      provider: 'azure',
      label: 'Microsoft Azure (AKS)',
      shortLabel: 'AZR',
      badgeVariant: 'outline',
      badgeClass: 'border-accent/40 text-accent bg-accent/10 font-semibold text-[10px] px-1.5 py-0.5',
    };
  }
  if (norm === 'ibm') {
    return {
      provider: 'ibm',
      label: 'IBM Cloud (ROKS)',
      shortLabel: 'IBM',
      badgeVariant: 'outline',
      badgeClass: 'border-secondary text-secondary-foreground bg-secondary/30 font-semibold text-[10px] px-1.5 py-0.5',
    };
  }
  if (norm === 'metal') {
    return {
      provider: 'bare-metal',
      label: 'Bare-Metal / On-Premises',
      shortLabel: 'METAL',
      badgeVariant: 'outline',
      badgeClass: 'border-success/40 text-success bg-success/10 font-semibold text-[10px] px-1.5 py-0.5',
    };
  }
  const raw = (provider || '').trim();
  return {
    provider: raw || 'k8s',
    label: raw ? raw.toUpperCase() : 'Kubernetes',
    shortLabel: raw ? raw.toUpperCase().slice(0, 4) : 'K8S',
    badgeVariant: 'secondary',
    badgeClass: 'font-semibold text-[10px] px-1.5 py-0.5',
  };
}

/**
 * Get multi-cloud location display info (flag, label, display) for clusters.
 */
export function getClusterLocationInfo(
  cloudProvider?: string | null,
  region?: string | null,
): { flag: string; label: string; display: string } | null {
  if (!region && !cloudProvider) return null;
  const reg = (region || '').trim();
  const p = (cloudProvider || '').toLowerCase().trim();

  if (!reg) {
    if (p === 'on-prem' || p === 'bare-metal' || p === 'metal' || p === 'kubernetes') {
      return { flag: '🖥️', label: 'On-Premises', display: 'On-Prem' };
    }
    return null;
  }

  // Check AWS region table
  const awsInfo = getRegionInfo(reg);
  if (awsInfo) {
    return { flag: awsInfo.flag, label: awsInfo.label, display: reg };
  }

  const regLower = reg.toLowerCase();
  const isGcp = p === 'gcp' || p === 'gke' || p === 'google';
  const isIbm = p === 'ibm' || p === 'roks' || p === 'ibmcloud';
  const isAzure = p === 'azure' || p === 'aks';
  const providerPrefix = isGcp ? 'GCP ' : isIbm ? 'IBM Cloud ' : isAzure ? 'Azure ' : '';

  // Azure region names (single word)
  const azureFlagMap: Record<string, { flag: string; label: string }> = {
    eastus: { flag: '🇺🇸', label: 'East US' },
    eastus2: { flag: '🇺🇸', label: 'East US 2' },
    westus: { flag: '🇺🇸', label: 'West US' },
    westus2: { flag: '🇺🇸', label: 'West US 2' },
    westus3: { flag: '🇺🇸', label: 'West US 3' },
    centralus: { flag: '🇺🇸', label: 'Central US' },
    northcentralus: { flag: '🇺🇸', label: 'North Central US' },
    southcentralus: { flag: '🇺🇸', label: 'South Central US' },
    canadacentral: { flag: '🇨🇦', label: 'Canada Central' },
    canadaeast: { flag: '🇨🇦', label: 'Canada East' },
    westeurope: { flag: '🇳🇱', label: 'West Europe' },
    northeurope: { flag: '🇮🇪', label: 'North Europe' },
    uksouth: { flag: '🇬🇧', label: 'UK South' },
    ukwest: { flag: '🇬🇧', label: 'UK West' },
    francecentral: { flag: '🇫🇷', label: 'France Central' },
    germanywestcentral: { flag: '🇩🇪', label: 'Germany West Central' },
    switzerlandnorth: { flag: '🇨🇭', label: 'Switzerland North' },
    swedencentral: { flag: '🇸🇪', label: 'Sweden Central' },
    norwayeast: { flag: '🇳🇴', label: 'Norway East' },
    italynorth: { flag: '🇮🇹', label: 'Italy North' },
    polandcentral: { flag: '🇵🇱', label: 'Poland Central' },
    spaincentral: { flag: '🇪🇸', label: 'Spain Central' },
    japaneast: { flag: '🇯🇵', label: 'Japan East' },
    japanwest: { flag: '🇯🇵', label: 'Japan West' },
    koreacentral: { flag: '🇰🇷', label: 'Korea Central' },
    southeastasia: { flag: '🇸🇬', label: 'Southeast Asia' },
    eastasia: { flag: '🇭🇰', label: 'East Asia' },
    centralindia: { flag: '🇮🇳', label: 'Central India' },
    southindia: { flag: '🇮🇳', label: 'South India' },
    australiaeast: { flag: '🇦🇺', label: 'Australia East' },
    australiasoutheast: { flag: '🇦🇺', label: 'Australia Southeast' },
    australiacentral: { flag: '🇦🇺', label: 'Australia Central' },
    brazilsouth: { flag: '🇧🇷', label: 'Brazil South' },
    uaenorth: { flag: '🇦🇪', label: 'UAE North' },
    israelcentral: { flag: '🇮🇱', label: 'Israel Central' },
    southafricanorth: { flag: '🇿🇦', label: 'South Africa North' },
  };

  if (azureFlagMap[regLower]) {
    const az = azureFlagMap[regLower];
    return { flag: az.flag, label: `Azure ${az.label}`, display: reg };
  }

  // Multi-cloud / GCP region heuristics
  if (regLower.startsWith('us-') || regLower.startsWith('northamerica-')) {
    const flag = regLower.startsWith('northamerica-northeast') ? '🇨🇦' : '🇺🇸';
    return { flag, label: `${providerPrefix}${reg}`, display: reg };
  }
  if (regLower.startsWith('europe-') || regLower.startsWith('eu-')) {
    let flag = '🇪🇺';
    if (regLower.includes('west1') && !regLower.includes('west10') && !regLower.includes('west12')) flag = '🇧🇪';
    else if (regLower.includes('west2')) flag = '🇬🇧';
    else if (regLower.includes('west3') || regLower.includes('west10') || regLower === 'eu-de') flag = '🇩🇪';
    else if (regLower.includes('west4')) flag = '🇳🇱';
    else if (regLower.includes('west6')) flag = '🇨🇭';
    else if (regLower.includes('west8') || regLower.includes('west12')) flag = '🇮🇹';
    else if (regLower.includes('west9')) flag = '🇫🇷';
    else if (regLower.includes('north1')) flag = '🇫🇮';
    else if (regLower.includes('southwest1') || regLower === 'eu-es') flag = '🇪🇸';
    else if (regLower.includes('central2')) flag = '🇵🇱';
    else if (regLower === 'eu-gb') flag = '🇬🇧';
    return { flag, label: `${providerPrefix}${reg}`, display: reg };
  }
  if (regLower.startsWith('asia-') || regLower.startsWith('ap-') || regLower.startsWith('jp-')) {
    let flag = '🌏';
    if (regLower.includes('east1')) flag = '🇹🇼';
    else if (regLower.includes('east2')) flag = '🇭🇰';
    else if (regLower.includes('northeast1') || regLower.includes('northeast2') || regLower.startsWith('jp-')) flag = '🇯🇵';
    else if (regLower.includes('northeast3')) flag = '🇰🇷';
    else if (regLower.includes('south1') || regLower.includes('south2')) flag = '🇮🇳';
    else if (regLower.includes('southeast1')) flag = '🇸🇬';
    else if (regLower.includes('southeast2')) flag = '🇮🇩';
    return { flag, label: `${providerPrefix}${reg}`, display: reg };
  }
  if (regLower.startsWith('australia-') || regLower.startsWith('au-')) {
    return { flag: '🇦🇺', label: `${providerPrefix}${reg}`, display: reg };
  }
  if (regLower.startsWith('southamerica-') || regLower.startsWith('sa-') || regLower.startsWith('br-')) {
    const flag = regLower.includes('west1') ? '🇨🇱' : '🇧🇷';
    return { flag, label: `${providerPrefix}${reg}`, display: reg };
  }

  // On-prem / Bare-metal custom region label
  if (p === 'on-prem' || p === 'bare-metal' || p === 'metal' || p === 'kubernetes') {
    return { flag: '🖥️', label: reg, display: reg };
  }

  return { flag: '🌐', label: `${providerPrefix}${reg}`, display: reg };
}

/**
 * Format Kubernetes / cloud provider availability zone for user display.
 * In Azure AKS, nodes in non-zonal or regional scale sets report zone "0" (or "<region>-0").
 * This formats zone "0" nicely as "Regional (Non-Zonal)".
 */
export function formatAvailabilityZone(zone?: string | null): string {
  if (!zone || zone === '--') return '--';
  const trimmed = zone.trim();
  if (trimmed === '0' || /^[a-z0-9]+-0$/i.test(trimmed)) {
    return 'Regional (Non-Zonal)';
  }
  return trimmed;
}

