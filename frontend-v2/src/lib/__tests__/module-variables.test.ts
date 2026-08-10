import { describe, expect, it } from 'vitest';

import { formatModuleVariableValue, getSensitiveModuleVariableNames } from '@/lib/module-variables';

describe('module variable helpers', () => {
  it('returns sensitive variable names from module metadata', () => {
    const names = getSensitiveModuleVariableNames({
      variables: [
        { name: 'ibmcloud_api_key', type: 'string', required: true, sensitive: true },
        { name: 'region', type: 'string', required: true, sensitive: false },
      ],
    });

    expect(names.has('ibmcloud_api_key')).toBe(true);
    expect(names.has('region')).toBe(false);
  });

  it('masks sensitive values and preserves non-sensitive values', () => {
    const sensitiveNames = new Set(['ibmcloud_api_key']);

    expect(formatModuleVariableValue('ibmcloud_api_key', 'secret-value', sensitiveNames)).toBe('••••••••');
    expect(formatModuleVariableValue('region', 'us-south', sensitiveNames)).toBe('us-south');
  });
});
