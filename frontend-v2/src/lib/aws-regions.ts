/**
 * Complete list of AWS regions with metadata
 * Updated: 2026-01-27
 */

export interface AWSRegionInfo {
  value: string;
  label: string;
  flag: string;
  continent: string;
}

export const AWS_REGIONS: readonly AWSRegionInfo[] = [
  // US Regions
  { value: 'us-east-1', label: 'US East (N. Virginia)', flag: '🇺🇸', continent: 'North America' },
  { value: 'us-east-2', label: 'US East (Ohio)', flag: '🇺🇸', continent: 'North America' },
  { value: 'us-west-1', label: 'US West (N. California)', flag: '🇺🇸', continent: 'North America' },
  { value: 'us-west-2', label: 'US West (Oregon)', flag: '🇺🇸', continent: 'North America' },

  // Canada
  { value: 'ca-central-1', label: 'Canada (Central)', flag: '🇨🇦', continent: 'North America' },
  { value: 'ca-west-1', label: 'Canada (Calgary)', flag: '🇨🇦', continent: 'North America' },

  // Europe
  { value: 'eu-central-1', label: 'EU (Frankfurt)', flag: '🇩🇪', continent: 'Europe' },
  { value: 'eu-central-2', label: 'EU (Zurich)', flag: '🇨🇭', continent: 'Europe' },
  { value: 'eu-west-1', label: 'EU (Ireland)', flag: '🇮🇪', continent: 'Europe' },
  { value: 'eu-west-2', label: 'EU (London)', flag: '🇬🇧', continent: 'Europe' },
  { value: 'eu-west-3', label: 'EU (Paris)', flag: '🇫🇷', continent: 'Europe' },
  { value: 'eu-north-1', label: 'EU (Stockholm)', flag: '🇸🇪', continent: 'Europe' },
  { value: 'eu-south-1', label: 'EU (Milan)', flag: '🇮🇹', continent: 'Europe' },
  { value: 'eu-south-2', label: 'EU (Spain)', flag: '🇪🇸', continent: 'Europe' },

  // Asia Pacific
  { value: 'ap-northeast-1', label: 'Asia Pacific (Tokyo)', flag: '🇯🇵', continent: 'Asia Pacific' },
  { value: 'ap-northeast-2', label: 'Asia Pacific (Seoul)', flag: '🇰🇷', continent: 'Asia Pacific' },
  { value: 'ap-northeast-3', label: 'Asia Pacific (Osaka)', flag: '🇯🇵', continent: 'Asia Pacific' },
  { value: 'ap-southeast-1', label: 'Asia Pacific (Singapore)', flag: '🇸🇬', continent: 'Asia Pacific' },
  { value: 'ap-southeast-2', label: 'Asia Pacific (Sydney)', flag: '🇦🇺', continent: 'Asia Pacific' },
  { value: 'ap-southeast-3', label: 'Asia Pacific (Jakarta)', flag: '🇮🇩', continent: 'Asia Pacific' },
  { value: 'ap-southeast-4', label: 'Asia Pacific (Melbourne)', flag: '🇦🇺', continent: 'Asia Pacific' },
  { value: 'ap-southeast-5', label: 'Asia Pacific (Malaysia)', flag: '🇲🇾', continent: 'Asia Pacific' },
  { value: 'ap-south-1', label: 'Asia Pacific (Mumbai)', flag: '🇮🇳', continent: 'Asia Pacific' },
  { value: 'ap-south-2', label: 'Asia Pacific (Hyderabad)', flag: '🇮🇳', continent: 'Asia Pacific' },
  { value: 'ap-east-1', label: 'Asia Pacific (Hong Kong)', flag: '🇭🇰', continent: 'Asia Pacific' },

  // Middle East
  { value: 'me-south-1', label: 'Middle East (Bahrain)', flag: '🇧🇭', continent: 'Middle East' },
  { value: 'me-central-1', label: 'Middle East (UAE)', flag: '🇦🇪', continent: 'Middle East' },
  { value: 'il-central-1', label: 'Israel (Tel Aviv)', flag: '🇮🇱', continent: 'Middle East' },

  // Africa
  { value: 'af-south-1', label: 'Africa (Cape Town)', flag: '🇿🇦', continent: 'Africa' },

  // South America
  { value: 'sa-east-1', label: 'South America (São Paulo)', flag: '🇧🇷', continent: 'South America' },

  // GovCloud
  { value: 'us-gov-west-1', label: 'AWS GovCloud (US-West)', flag: '🏛️', continent: 'GovCloud' },
  { value: 'us-gov-east-1', label: 'AWS GovCloud (US-East)', flag: '🏛️', continent: 'GovCloud' },
] as const;

export type AWSRegion = typeof AWS_REGIONS[number]['value'];

/**
 * Get region info by value
 */
export function getRegionInfo(regionValue: string): AWSRegionInfo | undefined {
  return AWS_REGIONS.find(r => r.value === regionValue);
}

/**
 * Get display info for a project's location — provider-aware.
 * For AWS projects: returns the full AWS region info (flag + label).
 * For non-AWS / on-prem projects: returns a generic location display.
 * Returns null if no location info is available.
 *
 * @param cloudProvider - The project's cloud_provider field
 * @param region - The project's region field (also used as location label for on-prem)
 * @param credentialProvider - The credential template's provider (ssh, aws, etc.)
 * @param projectType - The project's project_type field (cloud-aws, cloud-azure, cloud-gcp, cloud-ibm, kubernetes)
 */
export function getProjectLocationInfo(
  cloudProvider?: string | null,
  region?: string | null,
  credentialProvider?: string | null,
  projectType?: string | null,
): { flag: string; label: string; display: string } | null {
  const p = (cloudProvider || '').toLowerCase().trim();
  const pt = (projectType || '').toLowerCase().trim();
  const reg = (region || '').trim();

  // Kubernetes / on-prem project type or SSH credential — always show on-prem style
  if (pt === 'kubernetes' || credentialProvider === 'ssh' || p === 'on-prem' || p === 'bare-metal' || p === 'metal') {
    if (reg) {
      // User set a custom location label (e.g., "singapore", "lab-rack-3")
      return { flag: '🖥️', label: reg, display: reg };
    }
    return { flag: '🖥️', label: 'On-Premises', display: 'On-Prem' };
  }

  const effectiveProvider = p || (pt.startsWith('cloud-') ? pt.replace('cloud-', '') : pt);

  // If a region is specified, resolve via getClusterLocationInfo
  if (reg) {
    const loc = getClusterLocationInfo(effectiveProvider, reg);
    if (loc) {
      return loc;
    }
    return { flag: '🌐', label: reg, display: reg };
  }

  // On-prem or kubernetes projects without region
  if (p === 'on-prem' || p === 'bare-metal' || p === 'metal' || p === 'kubernetes' || p === 'none') {
    return { flag: '🖥️', label: 'On-Premises', display: 'On-Prem' };
  }

  // No provider and no region
  return null;
}

/**
 * Group regions by continent for organized display
 */
export const AWS_REGIONS_GROUPED = AWS_REGIONS.reduce((acc, region) => {
  if (!acc[region.continent]) {
    acc[region.continent] = [];
  }
  acc[region.continent].push(region);
  return acc;
}, {} as Record<string, AWSRegionInfo[]>);

/**
 * Continent display order for UX
 */
export const CONTINENT_ORDER = [
  'North America',
  'Europe',
  'Asia Pacific',
  'Middle East',
  'Africa',
  'South America',
  'GovCloud',
] as const;

export interface CloudProviderBadgeInfo {
  provider: string;
  label: string;
  shortLabel: string;
  badgeVariant: 'default' | 'secondary' | 'outline';
  badgeClass: string;
}

/**
 * Get display badge metadata for a cloud provider.
 */
export function getCloudProviderBadgeInfo(provider?: string | null): CloudProviderBadgeInfo {
  const p = (provider || '').toLowerCase().trim();
  if (p === 'aws' || p === 'eks') {
    return {
      provider: 'aws',
      label: 'Amazon Web Services',
      shortLabel: 'AWS',
      badgeVariant: 'outline',
      badgeClass: 'border-amber-500/40 text-amber-500 bg-amber-500/10 font-semibold text-[10px] px-1.5 py-0.5',
    };
  }
  if (p === 'gcp' || p === 'gke' || p === 'google') {
    return {
      provider: 'gcp',
      label: 'Google Cloud Platform (GKE)',
      shortLabel: 'GKE',
      badgeVariant: 'outline',
      badgeClass: 'border-blue-500/40 text-blue-500 bg-blue-500/10 font-semibold text-[10px] px-1.5 py-0.5',
    };
  }
  if (p === 'azure' || p === 'aks') {
    return {
      provider: 'azure',
      label: 'Microsoft Azure (AKS)',
      shortLabel: 'AZR',
      badgeVariant: 'outline',
      badgeClass: 'border-sky-500/40 text-sky-500 bg-sky-500/10 font-semibold text-[10px] px-1.5 py-0.5',
    };
  }
  if (p === 'ibm' || p === 'roks' || p === 'ibmcloud') {
    return {
      provider: 'ibm',
      label: 'IBM Cloud (ROKS)',
      shortLabel: 'IBM',
      badgeVariant: 'outline',
      badgeClass: 'border-indigo-500/40 text-indigo-500 bg-indigo-500/10 font-semibold text-[10px] px-1.5 py-0.5',
    };
  }
  if (p === 'on-prem' || p === 'bare-metal' || p === 'metal') {
    return {
      provider: 'bare-metal',
      label: 'Bare-Metal / On-Premises',
      shortLabel: 'METAL',
      badgeVariant: 'outline',
      badgeClass: 'border-emerald-500/40 text-emerald-500 bg-emerald-500/10 font-semibold text-[10px] px-1.5 py-0.5',
    };
  }
  return {
    provider: p || 'k8s',
    label: p ? p.toUpperCase() : 'Kubernetes',
    shortLabel: p ? p.toUpperCase().slice(0, 4) : 'K8S',
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
