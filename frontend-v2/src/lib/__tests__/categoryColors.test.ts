/**
 * FU-008: lib/categoryColors — category and provider color lookups.
 *
 * D-020: assertions retuned from raw-palette names (`bg-orange-500`/`bg-blue-600`/
 * `bg-gray-500`/`bg-purple-500`/`bg-green-500`) to the semantic tokens the lib now
 * emits (`bg-warning`/`bg-info`/`bg-primary`/`bg-muted-foreground`/`bg-success`).
 */
import { describe, it, expect } from 'vitest';
import { categoryColors, getCategoryColor, getProviderColor } from '../categoryColors';

describe('categoryColors', () => {
  it('has colors for major cloud providers', () => {
    expect(categoryColors.aws).toBeDefined();
    expect(categoryColors.azure).toBeDefined();
    expect(categoryColors.gcp).toBeDefined();
  });

  it('has colors for infrastructure categories', () => {
    expect(categoryColors.network).toBeDefined();
    expect(categoryColors.security).toBeDefined();
    expect(categoryColors.database).toBeDefined();
    expect(categoryColors.storage).toBeDefined();
  });
});

describe('getCategoryColor()', () => {
  it('returns color for known category', () => {
    expect(getCategoryColor('aws')).toBe('bg-warning');
    expect(getCategoryColor('kubernetes')).toBe('bg-primary');
  });

  it('is case-insensitive', () => {
    expect(getCategoryColor('AWS')).toBe('bg-warning');
    expect(getCategoryColor('Azure')).toBe('bg-primary');
  });

  it('returns muted-foreground for undefined/empty', () => {
    expect(getCategoryColor(undefined)).toBe('bg-muted-foreground');
    expect(getCategoryColor('')).toBe('bg-muted-foreground');
  });

  it('returns primary fallback for unknown category', () => {
    expect(getCategoryColor('unknown-category')).toBe('bg-primary');
  });
});

describe('getProviderColor()', () => {
  it('returns color for known provider', () => {
    expect(getProviderColor('aws')).toBe('bg-warning');
    expect(getProviderColor('gcp')).toBe('bg-success');
  });

  it('is case-insensitive', () => {
    expect(getProviderColor('AWS')).toBe('bg-warning');
  });

  it('returns empty string for undefined/empty', () => {
    expect(getProviderColor(undefined)).toBe('');
    expect(getProviderColor('')).toBe('');
  });

  it('returns empty string for unknown provider', () => {
    expect(getProviderColor('unknown')).toBe('');
  });
});
