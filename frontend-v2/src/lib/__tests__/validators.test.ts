/**
 * FU-002: lib/validators — all validation functions
 */
import { describe, it, expect, vi } from 'vitest';
import {
  validateCIDR,
  validatePort,
  validateRequired,
  validateEmail,
  validateURL,
  validateLength,
  validateRange,
  validateAWSRegion,
  validateK8sResourceName,
  validateJSON,
  validateYAML,
  validateFields,
  debounceValidation,
} from '../validators';

describe('validateCIDR()', () => {
  it('validates correct CIDR', () => {
    expect(validateCIDR('10.0.0.0/16')).toEqual({ isValid: true });
    expect(validateCIDR('192.168.1.0/24')).toEqual({ isValid: true });
    expect(validateCIDR('0.0.0.0/0')).toEqual({ isValid: true });
  });

  it('rejects empty value', () => {
    expect(validateCIDR('')).toEqual({ isValid: false, error: 'CIDR block is required' });
  });

  it('rejects invalid format', () => {
    expect(validateCIDR('10.0.0.0')).toEqual({
      isValid: false,
      error: 'Invalid CIDR format. Example: 10.0.0.0/16',
    });
    expect(validateCIDR('not-a-cidr')).toEqual({
      isValid: false,
      error: 'Invalid CIDR format. Example: 10.0.0.0/16',
    });
  });

  it('rejects octets > 255', () => {
    expect(validateCIDR('256.0.0.0/16')).toEqual({
      isValid: false,
      error: 'IP octets must be between 0 and 255',
    });
  });

  it('rejects prefix > 32', () => {
    expect(validateCIDR('10.0.0.0/33')).toEqual({
      isValid: false,
      error: 'Prefix must be between 0 and 32',
    });
  });
});

describe('validatePort()', () => {
  it('validates correct ports', () => {
    expect(validatePort(80)).toEqual({ isValid: true });
    expect(validatePort('443')).toEqual({ isValid: true });
    expect(validatePort(1)).toEqual({ isValid: true });
    expect(validatePort(65535)).toEqual({ isValid: true });
  });

  it('rejects non-numeric', () => {
    expect(validatePort('abc')).toEqual({ isValid: false, error: 'Port must be a number' });
  });

  it('rejects out-of-range', () => {
    expect(validatePort(0)).toEqual({ isValid: false, error: 'Port must be between 1 and 65535' });
    expect(validatePort(65536)).toEqual({ isValid: false, error: 'Port must be between 1 and 65535' });
  });
});

describe('validateRequired()', () => {
  it('passes non-empty string', () => {
    expect(validateRequired('hello')).toEqual({ isValid: true });
  });

  it('fails empty string', () => {
    expect(validateRequired('')).toEqual({ isValid: false, error: 'This field is required' });
  });

  it('fails whitespace-only string', () => {
    expect(validateRequired('   ')).toEqual({ isValid: false, error: 'This field is required' });
  });

  it('uses custom field name', () => {
    expect(validateRequired('', 'Username')).toEqual({ isValid: false, error: 'Username is required' });
  });
});

describe('validateEmail()', () => {
  it('validates correct email', () => {
    expect(validateEmail('user@example.com')).toEqual({ isValid: true });
    expect(validateEmail('admin+tag@domain.co')).toEqual({ isValid: true });
  });

  it('rejects empty value', () => {
    expect(validateEmail('')).toEqual({ isValid: false, error: 'Email is required' });
  });

  it('rejects invalid format', () => {
    expect(validateEmail('not-an-email')).toEqual({ isValid: false, error: 'Invalid email format' });
    expect(validateEmail('user@')).toEqual({ isValid: false, error: 'Invalid email format' });
    expect(validateEmail('@domain.com')).toEqual({ isValid: false, error: 'Invalid email format' });
  });
});

describe('validateURL()', () => {
  it('validates correct URLs', () => {
    expect(validateURL('https://example.com')).toEqual({ isValid: true });
    expect(validateURL('http://localhost:3000/path')).toEqual({ isValid: true });
  });

  it('rejects empty value', () => {
    expect(validateURL('')).toEqual({ isValid: false, error: 'URL is required' });
  });

  it('rejects invalid format', () => {
    expect(validateURL('not-a-url')).toEqual({
      isValid: false,
      error: 'Invalid URL format. Example: https://example.com',
    });
  });
});

describe('validateLength()', () => {
  it('passes within range', () => {
    expect(validateLength('hello', 1, 10)).toEqual({ isValid: true });
  });

  it('fails below min', () => {
    expect(validateLength('hi', 5)).toEqual({
      isValid: false,
      error: 'This field must be at least 5 characters',
    });
  });

  it('fails above max', () => {
    expect(validateLength('hello world', undefined, 5)).toEqual({
      isValid: false,
      error: 'This field must be at most 5 characters',
    });
  });

  it('uses custom field name', () => {
    expect(validateLength('hi', 5, undefined, 'Name')).toEqual({
      isValid: false,
      error: 'Name must be at least 5 characters',
    });
  });

  it('passes when no min/max specified', () => {
    expect(validateLength('anything')).toEqual({ isValid: true });
  });
});

describe('validateRange()', () => {
  it('passes within range', () => {
    expect(validateRange(5, 1, 10)).toEqual({ isValid: true });
  });

  it('fails below min', () => {
    expect(validateRange(0, 1)).toEqual({ isValid: false, error: 'Value must be at least 1' });
  });

  it('fails above max', () => {
    expect(validateRange(11, undefined, 10)).toEqual({ isValid: false, error: 'Value must be at most 10' });
  });

  it('uses custom field name', () => {
    expect(validateRange(200, undefined, 100, 'Score')).toEqual({
      isValid: false,
      error: 'Score must be at most 100',
    });
  });
});

describe('validateAWSRegion()', () => {
  it('validates correct regions', () => {
    expect(validateAWSRegion('us-east-1')).toEqual({ isValid: true });
    expect(validateAWSRegion('ap-southeast-2')).toEqual({ isValid: true });
    expect(validateAWSRegion('eu-central-1')).toEqual({ isValid: true });
  });

  it('rejects empty value', () => {
    expect(validateAWSRegion('')).toEqual({ isValid: false, error: 'Region is required' });
  });

  it('rejects invalid format', () => {
    expect(validateAWSRegion('US-EAST-1')).toEqual({
      isValid: false,
      error: 'Invalid region format. Example: us-east-1',
    });
    expect(validateAWSRegion('useast1')).toEqual({
      isValid: false,
      error: 'Invalid region format. Example: us-east-1',
    });
  });
});

describe('validateK8sResourceName()', () => {
  it('validates correct names', () => {
    expect(validateK8sResourceName('my-app')).toEqual({ isValid: true });
    expect(validateK8sResourceName('nginx123')).toEqual({ isValid: true });
    expect(validateK8sResourceName('a')).toEqual({ isValid: true });
  });

  it('rejects empty value', () => {
    expect(validateK8sResourceName('')).toEqual({ isValid: false, error: 'Resource name is required' });
  });

  it('rejects uppercase', () => {
    expect(validateK8sResourceName('MyApp')).toEqual({
      isValid: false,
      error: 'Invalid format. Use lowercase letters, numbers, and hyphens only',
    });
  });

  it('rejects leading hyphen', () => {
    expect(validateK8sResourceName('-my-app')).toEqual({
      isValid: false,
      error: 'Invalid format. Use lowercase letters, numbers, and hyphens only',
    });
  });

  it('rejects names over 253 chars', () => {
    const longName = 'a'.repeat(254);
    expect(validateK8sResourceName(longName)).toEqual({
      isValid: false,
      error: 'Resource name must be at most 253 characters',
    });
  });
});

describe('validateJSON()', () => {
  it('validates correct JSON', () => {
    expect(validateJSON('{"key": "value"}')).toEqual({ isValid: true });
    expect(validateJSON('[1, 2, 3]')).toEqual({ isValid: true });
  });

  it('treats empty as valid (optional field)', () => {
    expect(validateJSON('')).toEqual({ isValid: true });
    expect(validateJSON('  ')).toEqual({ isValid: true });
  });

  it('rejects invalid JSON', () => {
    expect(validateJSON('{bad json')).toEqual({ isValid: false, error: 'Invalid JSON format' });
  });
});

describe('validateYAML()', () => {
  it('validates correct YAML', () => {
    expect(validateYAML('key: value')).toEqual({ isValid: true });
    expect(validateYAML('- item1\n- item2')).toEqual({ isValid: true });
  });

  it('treats empty as valid (optional field)', () => {
    expect(validateYAML('')).toEqual({ isValid: true });
    expect(validateYAML('  ')).toEqual({ isValid: true });
  });

  it('rejects YAML with tabs', () => {
    const result = validateYAML('key:\n\tvalue');
    expect(result.isValid).toBe(false);
    expect(result.error).toContain('does not allow tabs');
  });
});

describe('validateFields()', () => {
  it('returns valid when all pass', () => {
    const result = validateFields([
      () => validateRequired('hello'),
      () => validateEmail('user@example.com'),
    ]);
    expect(result).toEqual({ isValid: true, errors: [] });
  });

  it('collects all errors', () => {
    const result = validateFields([
      () => validateRequired(''),
      () => validateEmail('bad'),
    ]);
    expect(result.isValid).toBe(false);
    expect(result.errors).toHaveLength(2);
    expect(result.errors[0]).toContain('required');
    expect(result.errors[1]).toContain('email');
  });
});

describe('debounceValidation()', () => {
  it('debounces calls', async () => {
    vi.useFakeTimers();
    const fn = vi.fn();
    const debounced = debounceValidation(fn, 300);

    debounced('a');
    debounced('ab');
    debounced('abc');

    expect(fn).not.toHaveBeenCalled();

    vi.advanceTimersByTime(300);
    expect(fn).toHaveBeenCalledTimes(1);
    expect(fn).toHaveBeenCalledWith('abc');

    vi.useRealTimers();
  });
});
