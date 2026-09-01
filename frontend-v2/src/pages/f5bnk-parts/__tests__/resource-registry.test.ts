import { describe, it, expect } from 'vitest';
import { getRegistryEntry, getDetailComponent, getContextActions, getResourceIcon } from '../resource-registry';
import { ServiceDetail } from '@/components/k8s/f5bnk-details';

describe('resource registry', () => {
  it('returns Service detail component and context actions', () => {
    const entry = getRegistryEntry('Service');
    expect(entry.detailComponent).toBe(ServiceDetail);
    expect(entry.contextActions).toHaveLength(1);
    expect(entry.contextActions[0].label).toBe('View Service Details');
  });

  it('getDetailComponent returns ServiceDetail for Service kind', () => {
    expect(getDetailComponent('Service')).toBe(ServiceDetail);
  });

  it('getContextActions returns actions for Service kind', () => {
    const actions = getContextActions('Service');
    expect(actions.map((a) => a.label)).toContain('View Service Details');
  });

  it('getResourceIcon returns an icon for Service kind', () => {
    expect(getResourceIcon('Service')).toBeTruthy();
  });

  it('returns default entry for unknown kinds', () => {
    const entry = getRegistryEntry('UnknownKind');
    expect(entry.detailComponent).toBeNull();
    expect(entry.contextActions).toEqual([]);
  });
});
