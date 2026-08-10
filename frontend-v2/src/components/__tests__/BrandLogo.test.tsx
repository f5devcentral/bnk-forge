/**
 * Tests for BrandLogo selector component.
 *
 * Stubs window.__BRAND__ to assert the correct SVG logo renders
 * for each brand value.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { render } from '@/test/test-utils';
import { BrandLogo } from '@/components/branding/BrandLogo';

afterEach(() => {
  delete window.__BRAND__;
});

describe('BrandLogo', () => {
  it('renders F5Logo when brand is f5', () => {
    window.__BRAND__ = 'f5';
    const { container } = render(<BrandLogo />);
    // F5Logo has viewBox="0 0 1000 1000"
    const svg = container.querySelector('svg');
    expect(svg).not.toBeNull();
    expect(svg?.getAttribute('viewBox')).toBe('0 0 1000 1000');
  });

  it('renders ForgeLogo by default (no BRAND set)', () => {
    const { container } = render(<BrandLogo />);
    // ForgeLogo has viewBox="0 0 512 512"
    const svg = container.querySelector('svg');
    expect(svg).not.toBeNull();
    expect(svg?.getAttribute('viewBox')).toBe('0 0 512 512');
  });
});
