/**
 * FU-022: lib/api/modules — module source & registry API calls
 */
import { describe, it, expect } from 'vitest';
import { modulesApi } from '../modules';

describe('modulesApi', () => {
  it('getModuleSources returns array of sources', async () => {
    // API does .then(res => res.data) — handler returns plain array
    const sources = await modulesApi.getModuleSources();
    expect(Array.isArray(sources)).toBe(true);
    expect(sources.length).toBeGreaterThan(0);
  });

  it('getVariableMappings returns mapping data', async () => {
    // API does .then(res => res.data) — handler returns plain array
    const mappings = await modulesApi.getVariableMappings(1);
    expect(Array.isArray(mappings)).toBe(true);
  });

  it('searchRegistry returns results', async () => {
    const results = await modulesApi.searchRegistry({ query: 'vpc' });
    expect(results).toBeDefined();
    expect(results).toHaveProperty('modules');
  });

  it('getVariableMappingTemplates returns data', async () => {
    const templates = await modulesApi.getVariableMappingTemplates();
    expect(templates).toBeDefined();
    expect(templates).toHaveProperty('templates');
  });
});
