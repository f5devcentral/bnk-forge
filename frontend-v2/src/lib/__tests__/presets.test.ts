/**
 * FU-012: lib/presets — preset configs and resolver functions
 */
import { describe, it, expect } from 'vitest';
import {
  ENVIRONMENT_PRESETS,
  DEPLOYMENT_INTENTS,
  NETWORK_SIZE_PRESETS,
  CLUSTER_SIZE_PRESETS,
  BNK_PROFILE_PRESETS,
  resolveEnvironmentPreset,
  resolvePresetByModule,
} from '../presets';

describe('ENVIRONMENT_PRESETS', () => {
  it('has dev, standard, and production presets', () => {
    const ids = ENVIRONMENT_PRESETS.map((p) => p.id);
    expect(ids).toContain('dev');
    expect(ids).toContain('standard');
    expect(ids).toContain('production');
  });

  it('each preset has required fields', () => {
    for (const preset of ENVIRONMENT_PRESETS) {
      expect(preset.id).toBeTruthy();
      expect(preset.label).toBeTruthy();
      expect(preset.description).toBeTruthy();
      expect(preset.estimatedCost).toBeTruthy();
      expect(preset.estimatedTime).toBeTruthy();
      expect(preset.network).toBeTruthy();
      expect(preset.cluster).toBeTruthy();
      expect(preset.bnk).toBeTruthy();
    }
  });
});

describe('DEPLOYMENT_INTENTS', () => {
  it('has full-stack, gateway-only, and demo-apps', () => {
    const ids = DEPLOYMENT_INTENTS.map((i) => i.id);
    expect(ids).toContain('full-stack');
    expect(ids).toContain('gateway-only');
    expect(ids).toContain('demo-apps');
  });

  it('each intent has stack slugs', () => {
    for (const intent of DEPLOYMENT_INTENTS) {
      expect(intent.stackSlugs.length).toBeGreaterThan(0);
    }
  });

  it('full-stack requires AWS infra', () => {
    const fullStack = DEPLOYMENT_INTENTS.find((i) => i.id === 'full-stack')!;
    expect(fullStack.requiresAWSInfra).toBe(true);
  });

  it('gateway-only does not require AWS infra', () => {
    const gatewayOnly = DEPLOYMENT_INTENTS.find((i) => i.id === 'gateway-only')!;
    expect(gatewayOnly.requiresAWSInfra).toBe(false);
  });
});

describe('NETWORK_SIZE_PRESETS', () => {
  it('has small, medium, large options', () => {
    const ids = NETWORK_SIZE_PRESETS.options.map((o) => o.id);
    expect(ids).toEqual(['small', 'medium', 'large']);
  });

  it('each option has vpc_cidr in flatVariables', () => {
    for (const option of NETWORK_SIZE_PRESETS.options) {
      expect(option.flatVariables.vpc_cidr).toBeTruthy();
    }
  });
});

describe('CLUSTER_SIZE_PRESETS', () => {
  it('has minimum, standard, high_performance options', () => {
    const ids = CLUSTER_SIZE_PRESETS.options.map((o) => o.id);
    expect(ids).toEqual(['minimum', 'standard', 'high_performance']);
  });

  it('each option specifies node_count', () => {
    for (const option of CLUSTER_SIZE_PRESETS.options) {
      expect(option.flatVariables.node_count).toBeDefined();
    }
  });
});

describe('BNK_PROFILE_PRESETS', () => {
  it('has evaluation, standard, high_availability options', () => {
    const ids = BNK_PROFILE_PRESETS.options.map((o) => o.id);
    expect(ids).toEqual(['evaluation', 'standard', 'high_availability']);
  });
});

describe('resolveEnvironmentPreset()', () => {
  it('resolves "dev" preset into flat variables', () => {
    const vars = resolveEnvironmentPreset('dev');
    expect(vars.vpc_cidr).toBe('10.0.0.0/20');
    expect(vars.node_count).toBe(2);
    expect(vars.instance_type).toBe('t3.large');
    expect(vars.deployment_size).toBe('Small');
  });

  it('resolves "standard" preset', () => {
    const vars = resolveEnvironmentPreset('standard');
    expect(vars.vpc_cidr).toBe('10.0.0.0/16');
    expect(vars.node_count).toBe(3);
    expect(vars.deployment_size).toBe('Medium');
  });

  it('resolves "production" preset', () => {
    const vars = resolveEnvironmentPreset('production');
    expect(vars.vpc_cidr).toBe('10.0.0.0/12');
    expect(vars.deployment_size).toBe('Large');
  });

  it('returns empty object for unknown preset', () => {
    expect(resolveEnvironmentPreset('nonexistent')).toEqual({});
  });
});

describe('resolvePresetByModule()', () => {
  it('resolves "dev" preset grouped by module path', () => {
    const vars = resolvePresetByModule('dev');

    // Should have VPC module
    expect(vars['infra/aws/vpc']).toBeDefined();
    expect(vars['infra/aws/vpc'].vpc_cidr).toBe('10.0.0.0/20');

    // Should have EKS module
    expect(vars['infra/aws/eks']).toBeDefined();
    expect(vars['infra/aws/eks'].node_count).toBe(2);

    // Should have BNK module
    expect(vars['bnk/cneinstance']).toBeDefined();
    expect(vars['bnk/cneinstance'].deployment_size).toBe('Small');
  });

  it('returns empty object for unknown preset', () => {
    expect(resolvePresetByModule('nonexistent')).toEqual({});
  });
});
