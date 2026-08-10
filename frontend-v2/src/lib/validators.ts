/**
 * Common validation functions for form inputs
 */

export interface ValidationResult {
  isValid: boolean;
  error?: string;
}

/**
 * Validate CIDR block format (e.g., "10.0.0.0/16")
 */
export function validateCIDR(value: string): ValidationResult {
  if (!value) {
    return { isValid: false, error: 'CIDR block is required' };
  }

  const cidrRegex = /^(\d{1,3}\.){3}\d{1,3}\/\d{1,2}$/;
  if (!cidrRegex.test(value)) {
    return { isValid: false, error: 'Invalid CIDR format. Example: 10.0.0.0/16' };
  }

  // Validate IP octets (0-255)
  const [ip, prefix] = value.split('/');
  const octets = ip.split('.').map(Number);

  if (octets.some(octet => octet < 0 || octet > 255)) {
    return { isValid: false, error: 'IP octets must be between 0 and 255' };
  }

  // Validate prefix length
  const prefixNum = parseInt(prefix);
  if (prefixNum < 0 || prefixNum > 32) {
    return { isValid: false, error: 'Prefix must be between 0 and 32' };
  }

  return { isValid: true };
}

/**
 * Validate port number (1-65535)
 */
export function validatePort(value: string | number): ValidationResult {
  const port = typeof value === 'string' ? parseInt(value) : value;

  if (isNaN(port)) {
    return { isValid: false, error: 'Port must be a number' };
  }

  if (port < 1 || port > 65535) {
    return { isValid: false, error: 'Port must be between 1 and 65535' };
  }

  return { isValid: true };
}

/**
 * Validate required field
 */
export function validateRequired(value: string, fieldName: string = 'This field'): ValidationResult {
  if (!value || value.trim() === '') {
    return { isValid: false, error: `${fieldName} is required` };
  }
  return { isValid: true };
}

/**
 * Validate email format
 */
export function validateEmail(value: string): ValidationResult {
  if (!value) {
    return { isValid: false, error: 'Email is required' };
  }

  const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
  if (!emailRegex.test(value)) {
    return { isValid: false, error: 'Invalid email format' };
  }

  return { isValid: true };
}

/**
 * Validate URL format
 */
export function validateURL(value: string): ValidationResult {
  if (!value) {
    return { isValid: false, error: 'URL is required' };
  }

  try {
    new URL(value);
    return { isValid: true };
  } catch {
    return { isValid: false, error: 'Invalid URL format. Example: https://example.com' };
  }
}

/**
 * Validate string length
 */
export function validateLength(
  value: string,
  min?: number,
  max?: number,
  fieldName: string = 'This field'
): ValidationResult {
  const length = value.length;

  if (min !== undefined && length < min) {
    return { isValid: false, error: `${fieldName} must be at least ${min} characters` };
  }

  if (max !== undefined && length > max) {
    return { isValid: false, error: `${fieldName} must be at most ${max} characters` };
  }

  return { isValid: true };
}

/**
 * Validate number range
 */
export function validateRange(
  value: number,
  min?: number,
  max?: number,
  fieldName: string = 'Value'
): ValidationResult {
  if (min !== undefined && value < min) {
    return { isValid: false, error: `${fieldName} must be at least ${min}` };
  }

  if (max !== undefined && value > max) {
    return { isValid: false, error: `${fieldName} must be at most ${max}` };
  }

  return { isValid: true };
}

/**
 * Validate AWS region format
 */
export function validateAWSRegion(value: string): ValidationResult {
  if (!value) {
    return { isValid: false, error: 'Region is required' };
  }

  const regionRegex = /^[a-z]{2}-[a-z]+-\d{1}$/;
  if (!regionRegex.test(value)) {
    return { isValid: false, error: 'Invalid region format. Example: us-east-1' };
  }

  return { isValid: true };
}

/**
 * Validate Kubernetes resource name (RFC 1123)
 */
export function validateK8sResourceName(value: string): ValidationResult {
  if (!value) {
    return { isValid: false, error: 'Resource name is required' };
  }

  // RFC 1123: lowercase alphanumeric, hyphens, max 253 chars
  const k8sNameRegex = /^[a-z0-9]([-a-z0-9]*[a-z0-9])?$/;

  if (value.length > 253) {
    return { isValid: false, error: 'Resource name must be at most 253 characters' };
  }

  if (!k8sNameRegex.test(value)) {
    return {
      isValid: false,
      error: 'Invalid format. Use lowercase letters, numbers, and hyphens only',
    };
  }

  return { isValid: true };
}

/**
 * Validate JSON format
 */
export function validateJSON(value: string): ValidationResult {
  if (!value || value.trim() === '') {
    return { isValid: true }; // Empty is valid (optional field)
  }

  try {
    JSON.parse(value);
    return { isValid: true };
  } catch (_error) {
    return { isValid: false, error: 'Invalid JSON format' };
  }
}

/**
 * Validate YAML format
 */
export function validateYAML(value: string): ValidationResult {
  if (!value || value.trim() === '') {
    return { isValid: true }; // Empty is valid (optional field)
  }

  // Basic YAML validation (more thorough would require a YAML parser)
  // Check for common YAML syntax errors
  const lines = value.split('\n');

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Check for tabs (YAML doesn't allow tabs for indentation)
    if (line.includes('\t')) {
      return {
        isValid: false,
        error: `Line ${i + 1}: YAML does not allow tabs. Use spaces for indentation.`,
      };
    }
  }

  return { isValid: true };
}

/**
 * Validate multiple fields at once
 */
export function validateFields(
  validations: Array<() => ValidationResult>
): { isValid: boolean; errors: string[] } {
  const errors: string[] = [];

  for (const validate of validations) {
    const result = validate();
    if (!result.isValid && result.error) {
      errors.push(result.error);
    }
  }

  return {
    isValid: errors.length === 0,
    errors,
  };
}

/**
 * Debounce validation for real-time input
 */
export function debounceValidation<T extends (...args: unknown[]) => unknown>(
  fn: T,
  delay: number = 300
): (...args: Parameters<T>) => void {
  let timeoutId: ReturnType<typeof setTimeout>;

  return (...args: Parameters<T>) => {
    clearTimeout(timeoutId);
    timeoutId = setTimeout(() => fn(...args), delay);
  };
}
