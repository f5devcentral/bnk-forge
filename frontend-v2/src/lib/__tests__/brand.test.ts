import { describe, it, expect, afterEach } from 'vitest';
import { getBrand } from '../brand';

afterEach(() => {
  delete window.__BRAND__;
});

describe('getBrand()', () => {
  it("returns 'f5' when window.__BRAND__ is 'f5'", () => {
    window.__BRAND__ = 'f5';
    expect(getBrand()).toBe('f5');
  });

  it("returns 'forge' when window.__BRAND__ is unset", () => {
    expect(getBrand()).toBe('forge');
  });

  it("returns 'forge' for unrecognised values", () => {
    window.__BRAND__ = 'F5';
    expect(getBrand()).toBe('forge');

    window.__BRAND__ = 'acme';
    expect(getBrand()).toBe('forge');
  });
});
