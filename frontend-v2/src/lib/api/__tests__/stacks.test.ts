/**
 * FU-023: lib/api/stacks — stack API calls
 */
import { describe, it, expect } from 'vitest';
import { stacksApi } from '../stacks';

describe('stacksApi', () => {
  it('fetchStackTemplates returns array of templates', async () => {
    // API does .then(res => res.data) — handler returns plain array
    const templates = await stacksApi.fetchStackTemplates();
    expect(Array.isArray(templates)).toBe(true);
    expect(templates.length).toBeGreaterThan(0);
  });

  it('fetchStackInstances returns array of instances', async () => {
    // API does .then(res => res.data) — handler returns plain array
    const instances = await stacksApi.fetchStackInstances(1);
    expect(Array.isArray(instances)).toBe(true);
    expect(instances.length).toBeGreaterThan(0);
  });

  it('previewStackTemplate returns preview data', async () => {
    const preview = await stacksApi.previewStackTemplate('aws-k8s-foundation');
    expect(preview).toBeDefined();
  });

  it('checkStackPrerequisites returns check result', async () => {
    const result = await stacksApi.checkStackPrerequisites('aws-k8s-foundation', 1);
    expect(result).toBeDefined();
  });
});
