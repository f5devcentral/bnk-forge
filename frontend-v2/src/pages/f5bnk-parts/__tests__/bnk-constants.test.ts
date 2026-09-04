import { describe, it, expect } from 'vitest';
import { bnkResourceCategories, VIEW_POLICY_BUILDER, VIEW_CONFIG_BUILDER } from '../bnk-constants';

describe('bnkResourceCategories', () => {
  it('has Topology & Insights as the first category', () => {
    expect(bnkResourceCategories[0].category).toBe('Topology & Insights');
  });

  it('has Health & Diagnostics as the second category', () => {
    expect(bnkResourceCategories[1].category).toBe('Health & Diagnostics');
  });

  it('has Gateways & Traffic as the third category', () => {
    expect(bnkResourceCategories[2].category).toBe('Gateways & Traffic');
  });

  it('has Policies & Security as the fourth category', () => {
    expect(bnkResourceCategories[3].category).toBe('Policies & Security');
  });

  it('has System & Configuration as the fifth category', () => {
    expect(bnkResourceCategories[4].category).toBe('System & Configuration');
  });

  it('has AI Gateway & A2A as the sixth category', () => {
    expect(bnkResourceCategories[5].category).toBe('AI Gateway & A2A');
  });

  it('places Policy Builder in Policies & Security and Config Builder in System & Configuration', () => {
    const policiesCategory = bnkResourceCategories.find(c => c.category === 'Policies & Security');
    const policyKeys = policiesCategory!.items.map(i => i.key);
    expect(policyKeys).toContain(VIEW_POLICY_BUILDER);

    const systemCategory = bnkResourceCategories.find(c => c.category === 'System & Configuration');
    const systemKeys = systemCategory!.items.map(i => i.key);
    expect(systemKeys).toContain(VIEW_CONFIG_BUILDER);
  });
});
