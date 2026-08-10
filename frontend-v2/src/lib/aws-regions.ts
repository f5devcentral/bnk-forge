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
  // Kubernetes / on-prem project type — always show on-prem style
  if (projectType === 'kubernetes' || credentialProvider === 'ssh') {
    if (region) {
      // User set a custom location label (e.g., "singapore", "lab-rack-3")
      return { flag: '🖥️', label: region, display: region };
    }
    return { flag: '🖥️', label: 'On-Premises', display: 'On-Prem' };
  }

  // Cloud projects with a known AWS region
  if (region && (cloudProvider === 'aws' || cloudProvider === 'eks' || projectType === 'cloud-aws')) {
    const regionInfo = getRegionInfo(region);
    if (regionInfo) {
      return { flag: regionInfo.flag, label: regionInfo.label, display: region };
    }
    // Unknown AWS region string — still show it without flag
    return { flag: '☁️', label: region, display: region };
  }

  // Azure / GCP with a region set
  if (region && (cloudProvider === 'azure' || projectType === 'cloud-azure')) {
    return { flag: '⛅', label: `Azure ${region}`, display: region };
  }
  if (region && (cloudProvider === 'gcp' || projectType === 'cloud-gcp')) {
    return { flag: '🌤️', label: `GCP ${region}`, display: region };
  }
  if (region && (cloudProvider === 'ibm' || projectType === 'cloud-ibm')) {
    return { flag: '🟦', label: `IBM Cloud ${region}`, display: region };
  }

  // On-prem or kubernetes projects — no region, no flag
  if (cloudProvider === 'on-prem' || cloudProvider === 'kubernetes' || cloudProvider === 'none') {
    return { flag: '🖥️', label: 'On-Premises', display: 'On-Prem' };
  }

  // No cloud_provider set and no region — show nothing
  if (!region && !cloudProvider) {
    return null;
  }

  // Fallback: region is set but provider unknown — show generic
  if (region) {
    return { flag: '🌐', label: region, display: region };
  }

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
