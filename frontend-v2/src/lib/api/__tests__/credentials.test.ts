/**
 * FU-034: lib/api/credentials — credential template API calls
 */
import { describe, it, expect } from 'vitest';
import { credentialsApi } from '../credentials';

describe('credentialsApi', () => {
  it('listCredentialTemplates returns array of templates', async () => {
    // API does .then(res => res.data) — handler returns plain array
    const result = await credentialsApi.listCredentialTemplates();
    expect(Array.isArray(result)).toBe(true);
    expect(result.length).toBeGreaterThan(0);
  });

  it('createCredentialTemplate returns created template', async () => {
    const result = await credentialsApi.createCredentialTemplate({
      name: 'Test Template',
      provider: 'aws',
      credentials: { access_key_id: 'xxx', secret_access_key: 'yyy' },
    } as Parameters<typeof credentialsApi.createCredentialTemplate>[0]);
    expect(result).toHaveProperty('id');
    expect(result).toHaveProperty('name', 'Test Template');
  });

  it('testCredentialTemplate returns result', async () => {
    const result = await credentialsApi.testCredentialTemplate(1);
    expect(result).toBeDefined();
  });
});
