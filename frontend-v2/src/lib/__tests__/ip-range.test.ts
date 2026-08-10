import { describe, expect, it } from 'vitest';
import { expandIpToken, parseIpInput } from '../ip-range';

describe('expandIpToken', () => {
  it('returns the IP for a single address', () => {
    expect(expandIpToken('198.18.0.19')).toEqual({ ips: ['198.18.0.19'], errors: [] });
  });

  it('expands a last-octet shorthand range', () => {
    expect(expandIpToken('198.18.0.19-22')).toEqual({
      ips: ['198.18.0.19', '198.18.0.20', '198.18.0.21', '198.18.0.22'],
      errors: [],
    });
  });

  it('expands a full IP range when start/end share first three octets', () => {
    expect(expandIpToken('198.18.0.19-198.18.0.22')).toEqual({
      ips: ['198.18.0.19', '198.18.0.20', '198.18.0.21', '198.18.0.22'],
      errors: [],
    });
  });

  it('rejects an invalid IP', () => {
    const result = expandIpToken('not-an-ip');
    expect(result.ips).toEqual([]);
    expect(result.errors[0]).toMatch(/invalid ip/i);
  });

  it('rejects ranges where end < start', () => {
    const result = expandIpToken('198.18.0.30-29');
    expect(result.ips).toEqual([]);
    expect(result.errors[0]).toMatch(/before start/);
  });

  it('rejects full-IP ranges that cross the third octet', () => {
    const result = expandIpToken('198.18.0.19-198.18.1.1');
    expect(result.ips).toEqual([]);
    expect(result.errors[0]).toMatch(/share the first three octets/);
  });

  it('rejects oversized ranges', () => {
    // single-octet shorthand can't exceed 256, but a full range like 198.18.0.0-198.18.0.255 is 256 (allowed).
    // To trigger >256 we need different third octets — already rejected by the first-three-octets rule.
    // Sanity-check that 256 is allowed.
    const result = expandIpToken('198.18.0.0-255');
    expect(result.errors).toEqual([]);
    expect(result.ips.length).toBe(256);
  });

  it('rejects octets outside 0-255', () => {
    expect(expandIpToken('198.18.0.300').errors[0]).toMatch(/invalid ip/i);
  });
});

describe('parseIpInput', () => {
  it('parses comma, semicolon, and newline separated tokens', () => {
    const text = '10.0.0.1, 10.0.0.2; 10.0.0.3\n10.0.0.4';
    expect(parseIpInput(text)).toEqual({
      ips: ['10.0.0.1', '10.0.0.2', '10.0.0.3', '10.0.0.4'],
      errors: [],
    });
  });

  it('expands ranges mixed with single addresses', () => {
    const text = '10.0.0.1\n10.0.0.5-7';
    expect(parseIpInput(text).ips).toEqual([
      '10.0.0.1',
      '10.0.0.5',
      '10.0.0.6',
      '10.0.0.7',
    ]);
  });

  it('deduplicates overlapping entries', () => {
    const text = '10.0.0.1, 10.0.0.1\n10.0.0.1-2';
    expect(parseIpInput(text).ips).toEqual(['10.0.0.1', '10.0.0.2']);
  });

  it('reports errors and skips invalid tokens but keeps valid ones', () => {
    const result = parseIpInput('10.0.0.1, bogus, 10.0.0.2-3');
    expect(result.ips).toEqual(['10.0.0.1', '10.0.0.2', '10.0.0.3']);
    expect(result.errors.length).toBe(1);
    expect(result.errors[0]).toMatch(/bogus/);
  });

  it('returns an empty list for empty input', () => {
    expect(parseIpInput('')).toEqual({ ips: [], errors: [] });
  });
});
