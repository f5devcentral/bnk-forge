/**
 * FU-013: lib/aws-regions — region data and lookup functions
 */
import { describe, it, expect } from 'vitest';
import {
  AWS_REGIONS,
  AWS_REGIONS_GROUPED,
  CONTINENT_ORDER,
  getRegionInfo,
  getProjectLocationInfo,
} from '../aws-regions';

describe('AWS_REGIONS', () => {
  it('has all regions with required fields', () => {
    for (const region of AWS_REGIONS) {
      expect(region.value).toBeTruthy();
      expect(region.label).toBeTruthy();
      expect(region.flag).toBeTruthy();
      expect(region.continent).toBeTruthy();
    }
  });

  it('includes us-east-1', () => {
    const usEast1 = AWS_REGIONS.find((r) => r.value === 'us-east-1');
    expect(usEast1).toBeDefined();
    expect(usEast1!.label).toContain('Virginia');
  });

  it('includes ap-southeast-2', () => {
    const apSe2 = AWS_REGIONS.find((r) => r.value === 'ap-southeast-2');
    expect(apSe2).toBeDefined();
    expect(apSe2!.label).toContain('Sydney');
  });

  it('includes GovCloud regions', () => {
    const govRegions = AWS_REGIONS.filter((r) => r.value.startsWith('us-gov-'));
    expect(govRegions.length).toBeGreaterThanOrEqual(2);
  });
});

describe('AWS_REGIONS_GROUPED', () => {
  it('groups by continent', () => {
    expect(AWS_REGIONS_GROUPED['North America']).toBeDefined();
    expect(AWS_REGIONS_GROUPED['Europe']).toBeDefined();
    expect(AWS_REGIONS_GROUPED['Asia Pacific']).toBeDefined();
  });

  it('all regions are assigned to a group', () => {
    const totalGrouped = Object.values(AWS_REGIONS_GROUPED).reduce(
      (sum, group) => sum + group.length,
      0
    );
    expect(totalGrouped).toBe(AWS_REGIONS.length);
  });
});

describe('CONTINENT_ORDER', () => {
  it('starts with North America', () => {
    expect(CONTINENT_ORDER[0]).toBe('North America');
  });

  it('includes all continents from regions', () => {
    const continents = new Set(AWS_REGIONS.map((r) => r.continent));
    for (const continent of continents) {
      expect(CONTINENT_ORDER).toContain(continent);
    }
  });
});

describe('getRegionInfo()', () => {
  it('returns info for known region', () => {
    const info = getRegionInfo('us-east-1');
    expect(info).toBeDefined();
    expect(info!.value).toBe('us-east-1');
    expect(info!.label).toContain('Virginia');
  });

  it('returns undefined for unknown region', () => {
    expect(getRegionInfo('unknown-region')).toBeUndefined();
  });
});

describe('getProjectLocationInfo()', () => {
  it('returns AWS region info for cloud-aws project', () => {
    const result = getProjectLocationInfo('aws', 'us-east-1', undefined, 'cloud-aws');
    expect(result).not.toBeNull();
    expect(result!.label).toContain('Virginia');
    expect(result!.display).toBe('us-east-1');
  });

  it('returns on-prem info for kubernetes project', () => {
    const result = getProjectLocationInfo(undefined, undefined, undefined, 'kubernetes');
    expect(result).not.toBeNull();
    expect(result!.flag).toBe('\uD83D\uDDA5\uFE0F');
    expect(result!.label).toBe('On-Premises');
  });

  it('returns custom location for kubernetes project with region', () => {
    const result = getProjectLocationInfo(undefined, 'singapore', 'ssh', 'kubernetes');
    expect(result).not.toBeNull();
    expect(result!.label).toBe('singapore');
  });

  it('returns Azure info for cloud-azure project', () => {
    const result = getProjectLocationInfo('azure', 'westeurope', undefined, 'cloud-azure');
    expect(result).not.toBeNull();
    expect(result!.label).toContain('Azure');
  });

  it('returns GCP info for cloud-gcp project', () => {
    const result = getProjectLocationInfo('gcp', 'us-central1', undefined, 'cloud-gcp');
    expect(result).not.toBeNull();
    expect(result!.label).toContain('GCP');
  });

  it('returns IBM info for cloud-ibm project', () => {
    const result = getProjectLocationInfo('ibm', 'us-south', undefined, 'cloud-ibm');
    expect(result).not.toBeNull();
    expect(result!.label).toContain('IBM Cloud');
  });

  it('returns on-prem for on-prem cloud provider', () => {
    const result = getProjectLocationInfo('on-prem');
    expect(result).not.toBeNull();
    expect(result!.label).toBe('On-Premises');
  });

  it('returns null when no provider and no region', () => {
    expect(getProjectLocationInfo(undefined, undefined)).toBeNull();
    expect(getProjectLocationInfo(null, null)).toBeNull();
  });

  it('returns generic info for unknown provider with region', () => {
    const result = getProjectLocationInfo(undefined, 'somewhere');
    expect(result).not.toBeNull();
    expect(result!.label).toBe('somewhere');
  });

  it('handles unknown AWS region string gracefully', () => {
    const result = getProjectLocationInfo('aws', 'xx-unknown-99', undefined, 'cloud-aws');
    expect(result).not.toBeNull();
    expect(result!.display).toBe('xx-unknown-99');
  });
});
