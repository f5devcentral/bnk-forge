import { describe, expect, it } from 'vitest';
import {
  getEnginePresentation,
  getLifecycleSummaryBadges,
  isActionSupported,
  resolveLifecycleCapabilities,
  resolveModuleEngineType,
  resolveProjectModuleEngineType,
} from '../module-engine';

describe('module-engine helpers', () => {
  it('resolves explicit and fallback engine types', () => {
    expect(resolveModuleEngineType({ engine_type: 'ansible', module_type: 'manifest' })).toBe('ansible');
    expect(resolveModuleEngineType({ engine_type: undefined, module_type: 'helm_chart' })).toBe('kubernetes');
    expect(resolveModuleEngineType({ engine_type: undefined, module_type: 'terraform' })).toBe('opentofu');
  });

  it('uses explicit lifecycle capabilities when provided', () => {
    const capabilities = resolveLifecycleCapabilities({
      lifecycle_capabilities: {
        supports_init: true,
        supports_plan: true,
        supports_apply: true,
        supports_destroy: false,
        supports_refresh: false,
        supports_drift: false,
      },
    });

    expect(capabilities.supports_destroy).toBe(false);
    expect(isActionSupported({ lifecycle_capabilities: capabilities }, 'destroy')).toBe(false);
    expect(isActionSupported({ lifecycle_capabilities: capabilities }, 'apply')).toBe(true);
  });

  it('falls back to legacy OpenTofu capabilities when metadata is absent', () => {
    const capabilities = resolveLifecycleCapabilities({
      engine_type: undefined,
      lifecycle_capabilities: undefined,
      library_module: undefined,
    });

    expect(capabilities.supports_init).toBe(true);
    expect(capabilities.supports_plan).toBe(true);
    expect(capabilities.supports_apply).toBe(true);
    expect(capabilities.supports_destroy).toBe(true);
  });

  it('keeps top-level project module engine_type authoritative for fallback capabilities', () => {
    const module = {
      engine_type: 'ansible' as const,
      lifecycle_capabilities: undefined,
      library_module: {
        engine_type: undefined,
        module_type: 'terraform' as const,
      },
    };

    expect(resolveProjectModuleEngineType(module)).toBe('ansible');

    const capabilities = resolveLifecycleCapabilities(module);
    expect(capabilities.supports_destroy).toBe(false);
    expect(isActionSupported(module, 'destroy')).toBe(false);
  });

  it('labels container-engine modules as Container (not OpenTofu)', () => {
    expect(getEnginePresentation('container').label).toBe('Container');
    expect(resolveModuleEngineType({ engine_type: 'container', module_type: 'artifact' })).toBe('container');
    const capabilities = resolveLifecycleCapabilities({ engine_type: 'container' } as never);
    expect(capabilities.supports_apply).toBe(true);
    expect(capabilities.supports_destroy).toBe(true);
    expect(capabilities.supports_plan).toBe(false);
  });

  it('builds presentation labels and lifecycle summary badges', () => {
    expect(getEnginePresentation('script', 'script').label).toBe('Governed Script');

    const summary = getLifecycleSummaryBadges({
      supports_init: true,
      supports_plan: false,
      supports_apply: true,
      supports_destroy: false,
      supports_refresh: true,
      supports_drift: false,
    });

    expect(summary).toEqual(['Init', 'Apply', 'Refresh']);
  });
});
