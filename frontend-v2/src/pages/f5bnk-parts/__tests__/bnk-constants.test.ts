/**
 * Tests for BNK sidebar category constants — TOPO-004-TUNE
 *
 * Verifies:
 * - Category order: Insights first, Build second
 * - Policy Builder is in Build (not Security)
 */
import { describe, it, expect } from 'vitest';
import { bnkResourceCategories, VIEW_POLICY_BUILDER, VIEW_CONFIG_BUILDER } from '../bnk-constants';

describe('bnkResourceCategories', () => {
  it('has Insights as the first category', () => {
    expect(bnkResourceCategories[0].category).toBe('Insights');
  });

  it('has Build as the second category', () => {
    expect(bnkResourceCategories[1].category).toBe('Build');
  });

  it('has Build before Traffic Management and Security', () => {
    const names = bnkResourceCategories.map(c => c.category);
    const buildIdx = names.indexOf('Build');
    const trafficIdx = names.indexOf('Traffic Management');
    const securityIdx = names.indexOf('Security');

    expect(buildIdx).toBeLessThan(trafficIdx);
    expect(buildIdx).toBeLessThan(securityIdx);
  });

  it('places Policy Builder in Build category (not Security)', () => {
    const buildCategory = bnkResourceCategories.find(c => c.category === 'Build');
    const securityCategory = bnkResourceCategories.find(c => c.category === 'Security');

    const buildKeys = buildCategory!.items.map(i => i.key);
    const securityKeys = securityCategory!.items.map(i => i.key);

    expect(buildKeys).toContain(VIEW_POLICY_BUILDER);
    expect(buildKeys).toContain(VIEW_CONFIG_BUILDER);
    expect(securityKeys).not.toContain(VIEW_POLICY_BUILDER);
  });
});
