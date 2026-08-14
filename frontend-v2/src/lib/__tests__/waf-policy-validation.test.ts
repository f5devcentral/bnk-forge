/**
 * Tests for WAF policy wizard validation helpers.
 * Mirrors the real appprotect.f5.com/v1 CRD constraints — see
 * docs/WAF_POLICY_MANAGER_DESIGN.md.
 */
import { describe, it, expect } from 'vitest';
import {
  validateWafWizardStep,
  validateWafWizardAll,
  type WafWizardState,
} from '@/lib/waf-policy-validation';

function createState(overrides: Partial<WafWizardState> = {}): WafWizardState {
  return {
    name: 'my-policy',
    namespace: 'default',
    policyJsonText: '{"name": "my-policy"}',
    logging: {
      enabled: false,
      mode: 'pick',
      existingLogConfName: '',
      format: 'default',
      formatString: '',
      requestType: 'illegal',
      maxMessageSize: '10k',
      maxRequestSize: 'any',
    },
    ...overrides,
  };
}

describe('validateWafWizardStep — basics', () => {
  it('passes with a valid name and namespace', () => {
    expect(validateWafWizardStep(createState(), 'basics')).toEqual([]);
  });

  it('requires a name', () => {
    const errors = validateWafWizardStep(createState({ name: '' }), 'basics');
    expect(errors).toContain('Policy name is required');
  });

  it('rejects an invalid RFC 1123 name', () => {
    const errors = validateWafWizardStep(createState({ name: 'My_Policy!' }), 'basics');
    expect(errors.some((e) => e.includes('RFC 1123'))).toBe(true);
  });

  it('requires a namespace', () => {
    const errors = validateWafWizardStep(createState({ namespace: '' }), 'basics');
    expect(errors).toContain('Namespace is required');
  });
});

describe('validateWafWizardStep — policy', () => {
  it('passes with valid JSON', () => {
    expect(validateWafWizardStep(createState(), 'policy')).toEqual([]);
  });

  it('rejects empty text', () => {
    const errors = validateWafWizardStep(createState({ policyJsonText: '' }), 'policy');
    expect(errors).toContain('Policy JSON is required');
  });

  it('rejects malformed JSON', () => {
    const errors = validateWafWizardStep(createState({ policyJsonText: '{not json' }), 'policy');
    expect(errors).toContain('Policy JSON is not valid JSON');
  });

  it('rejects a JSON array (must be an object)', () => {
    const errors = validateWafWizardStep(createState({ policyJsonText: '[1,2,3]' }), 'policy');
    expect(errors).toContain('Policy JSON must be an object');
  });
});

describe('validateWafWizardStep — logging', () => {
  it('skips validation when logging is disabled', () => {
    const state = createState({ logging: { enabled: false, mode: 'pick', existingLogConfName: '', format: 'default', formatString: '', requestType: 'illegal', maxMessageSize: 'bad', maxRequestSize: 'bad' } });
    expect(validateWafWizardStep(state, 'logging')).toEqual([]);
  });

  it('requires existing log conf name when mode=pick', () => {
    const state = createState({ logging: { enabled: true, mode: 'pick', existingLogConfName: '', format: 'default', formatString: '', requestType: 'illegal', maxMessageSize: '10k', maxRequestSize: 'any' } });
    const errors = validateWafWizardStep(state, 'logging');
    expect(errors.some((e) => e.includes('existing log profile') || e.includes('select'))).toBe(true);
  });

  it('passes when pick mode has a log conf selected', () => {
    const state = createState({ logging: { enabled: true, mode: 'pick', existingLogConfName: 'my-log', format: 'default', formatString: '', requestType: 'illegal', maxMessageSize: '10k', maxRequestSize: 'any' } });
    expect(validateWafWizardStep(state, 'logging')).toEqual([]);
  });

  it('rejects an out-of-range max message size in create mode', () => {
    const state = createState({ logging: { enabled: true, mode: 'create', existingLogConfName: '', format: 'default', formatString: '', requestType: 'illegal', maxMessageSize: '65k', maxRequestSize: 'any' } });
    const errors = validateWafWizardStep(state, 'logging');
    expect(errors.some((e) => e.includes('64k'))).toBe(true);
  });

  it('accepts "64k" as the max valid message size', () => {
    const state = createState({ logging: { enabled: true, mode: 'create', existingLogConfName: '', format: 'default', formatString: '', requestType: 'illegal', maxMessageSize: '64k', maxRequestSize: 'any' } });
    expect(validateWafWizardStep(state, 'logging')).toEqual([]);
  });

  it('requires format_string when format=user-defined in create mode', () => {
    const state = createState({ logging: { enabled: true, mode: 'create', existingLogConfName: '', format: 'user-defined', formatString: '', requestType: 'illegal', maxMessageSize: '10k', maxRequestSize: 'any' } });
    const errors = validateWafWizardStep(state, 'logging');
    expect(errors.some((e) => e.includes('Format string'))).toBe(true);
  });
});

describe('validateWafWizardAll', () => {
  it('returns an errors array for every step', () => {
    const result = validateWafWizardAll(createState());
    expect(Object.keys(result).sort()).toEqual(['basics', 'logging', 'policy', 'review'].sort());
    expect(Object.values(result).every((e) => e.length === 0)).toBe(true);
  });
});
