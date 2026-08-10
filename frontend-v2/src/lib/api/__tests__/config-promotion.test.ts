/**
 * FU-033: lib/api/config-promotion — config promotion API calls
 */
import { describe, it, expect } from 'vitest';
import { configPromotionApi } from '../config-promotion';

describe('configPromotionApi', () => {
  it('promoteConfig returns result', async () => {
    const result = await configPromotionApi.promoteConfig(1, 2, false);
    expect(result).toBeDefined();
  });

  it('listPromotions returns array', async () => {
    const result = await configPromotionApi.listPromotions();
    expect(result).toHaveProperty('promotions');
    expect(Array.isArray(result.promotions)).toBe(true);
  });
});
