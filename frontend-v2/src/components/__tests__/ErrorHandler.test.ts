/**
 * Tests for error-handler.ts
 *
 * Covers: maps known error patterns to correct suggestions and actions,
 * handles unknown errors gracefully.
 */
import { describe, it, expect } from 'vitest';
import { AxiosError } from 'axios';
import { parseApiError, formatErrorForToast, getErrorIcon } from '@/lib/error-handler';

// Helper to create mock AxiosError
function createAxiosError(
  status: number,
  data?: Record<string, unknown>,
  message?: string
): AxiosError {
  const error = new AxiosError(
    message || `Request failed with status code ${status}`,
    status === 0 ? 'ERR_NETWORK' : undefined,
    undefined,
    undefined,
    {
      status,
      data: data || {},
      statusText: '',
      headers: {},
      config: {} as never,
    } as never
  );
  return error;
}

describe('parseApiError', () => {
  // =========================================================================
  // HTTP Status Code Handling
  // =========================================================================

  it('handles 401 Unauthorized', () => {
    const error = createAxiosError(401);
    const parsed = parseApiError(error);

    expect(parsed.title).toBe('Authentication Required');
    expect(parsed.severity).toBe('warning');
    expect(parsed.action?.label).toBe('Log In');
    expect(parsed.action?.route).toBe('/login');
  });

  it('maps downstream K8S_API_ERROR unauthorized to actionable EKS guidance', () => {
    const error = createAxiosError(401, {
      error: {
        code: 'K8S_API_ERROR',
        detail: 'Kubernetes API Unauthorized: the server has asked for the client to provide credentials',
      },
    });
    const parsed = parseApiError(error, {
      detectedPlatformProfile: 'eks',
    });

    expect(parsed.title).toBe('Cluster Authentication Expired');
    expect(parsed.message).toContain('AWS/EKS cluster access credentials');
    expect(parsed.suggestion).toContain('Refresh kubeconfig');
    expect(parsed.action?.route).toBe('/kubernetes');
    expect(parsed.severity).toBe('warning');
  });

  it('maps downstream K8S_API_ERROR unauthorized to platform-neutral guidance outside EKS context', () => {
    const error = createAxiosError(401, {
      error: {
        code: 'K8S_API_ERROR',
        detail: 'Kubernetes API Unauthorized: the server has asked for the client to provide credentials',
      },
    });
    const parsed = parseApiError(error, {
      detectedPlatformProfile: 'generic_onprem',
    });

    expect(parsed.title).toBe('Cluster Authentication Expired');
    expect(parsed.message).toBe('Cluster access credentials appear to be expired or invalid.');
    expect(parsed.suggestion).toBe('Refresh kubeconfig for this cluster, then retry the operation.');
    expect(parsed.action?.route).toBe('/kubernetes');
    expect(parsed.severity).toBe('warning');
  });

  it('does not remap generic 401s without K8S_API_ERROR code', () => {
    const error = createAxiosError(401, {
      error: {
        code: 'UNAUTHORIZED',
        message: 'Unauthorized',
      },
    });
    const parsed = parseApiError(error);

    expect(parsed.title).toBe('Authentication Required');
    expect(parsed.action?.route).toBe('/login');
  });

  it('maps OCP SCC-related failures to platform-aware remediation guidance', () => {
    const error = createAxiosError(403, {
      error: {
        code: 'K8S_API_ERROR',
        detail: 'forbidden: unable to validate against any security context constraint',
      },
    });

    const parsed = parseApiError(error, {
      detectedPlatformProfile: 'ocp',
      platformCapabilities: { scc: true },
    });

    expect(parsed.title).toBe('OpenShift Security Binding Required');
    expect(parsed.message).toContain('rejected by OpenShift security constraints');
    expect(parsed.suggestion).toContain('security binding');
    expect(parsed.action?.route).toBe('/kubernetes');
    expect(parsed.severity).toBe('warning');
  });

  it('maps OCP ingress/operator-managed networking failures to platform-aware guidance', () => {
    const error = createAxiosError(403, {
      error: {
        code: 'K8S_API_ERROR',
        detail: 'admission webhook denied ingress: operator-managed route required',
      },
    });

    const parsed = parseApiError(error, {
      detectedPlatformProfile: 'ocp',
      platformCapabilities: { openshift_routes: true },
    });

    expect(parsed.title).toBe('OpenShift Networking Path Required');
    expect(parsed.message).toContain('rejected for the detected OpenShift context');
    expect(parsed.suggestion).toContain('Route/operator networking path');
    expect(parsed.action?.route).toBe('/kubernetes');
    expect(parsed.severity).toBe('warning');
  });

  it('does not remap generic OCP ingress RBAC failures to operator-managed networking guidance', () => {
    const error = createAxiosError(403, {
      error: {
        code: 'K8S_API_ERROR',
        detail: 'forbidden: User "dev" cannot create resource "ingresses" in API group "networking.k8s.io" in the namespace "default"',
      },
    });

    const parsed = parseApiError(error, {
      detectedPlatformProfile: 'ocp',
      platformConstraints: { operator_required_for_networking: true },
    });

    expect(parsed.title).toBe('Permission Denied');
    expect(parsed.action?.route).toBe('/system');
  });

  it('handles 403 Forbidden', () => {
    const error = createAxiosError(403);
    const parsed = parseApiError(error);

    expect(parsed.title).toBe('Permission Denied');
    expect(parsed.severity).toBe('error');
    expect(parsed.action?.route).toBe('/system');
  });

  it('handles 404 Not Found', () => {
    const error = createAxiosError(404);
    const parsed = parseApiError(error);

    expect(parsed.title).toBe('Not Found');
    expect(parsed.severity).toBe('warning');
  });

  it('handles 409 Conflict', () => {
    const error = createAxiosError(409, {
      error: { message: 'Project name already taken' },
    });
    const parsed = parseApiError(error);

    expect(parsed.title).toBe('Conflict');
    expect(parsed.severity).toBe('warning');
  });

  it('handles 422 Validation Error', () => {
    const error = createAxiosError(422, {
      error: { message: 'Invalid field values' },
    });
    const parsed = parseApiError(error);

    expect(parsed.title).toBe('Validation Error');
    expect(parsed.severity).toBe('error');
  });

  it('handles 500 Server Error', () => {
    const error = createAxiosError(500);
    const parsed = parseApiError(error);

    expect(parsed.title).toBe('Server Error');
    expect(parsed.severity).toBe('error');
    expect(parsed.action?.route).toBe('/tasks');
  });

  it('handles 503 Service Unavailable', () => {
    const error = createAxiosError(503);
    const parsed = parseApiError(error);

    expect(parsed.title).toBe('Service Unavailable');
    expect(parsed.severity).toBe('warning');
    expect(parsed.action?.route).toBe('/system');
  });

  // =========================================================================
  // Network & Timeout Errors
  // =========================================================================

  it('handles network error', () => {
    const error = new AxiosError('Network Error', 'ERR_NETWORK');
    const parsed = parseApiError(error);

    expect(parsed.title).toBe('Network Error');
    expect(parsed.severity).toBe('error');
    expect(parsed.action?.route).toBe('/system');
  });

  it('handles timeout error', () => {
    const error = new AxiosError('timeout of 30000ms exceeded', 'ECONNABORTED');
    const parsed = parseApiError(error);

    expect(parsed.title).toBe('Request Timeout');
    expect(parsed.severity).toBe('warning');
    expect(parsed.action?.route).toBe('/tasks');
  });

  // =========================================================================
  // Domain-Specific Error Patterns
  // =========================================================================

  it('maps dependency errors correctly', () => {
    const error = createAxiosError(400, {
      error: { message: 'Module depends on vpc which has not been applied' },
    });
    const parsed = parseApiError(error);

    expect(parsed.title).toBe('Dependency Error');
    expect(parsed.suggestion).toContain('dependencies');
  });

  it('maps "already exists" errors', () => {
    // Use status 400 (not 409) since 409 is caught by the status-specific handler first
    const error = createAxiosError(400, {
      error: { message: 'Resource already exists' },
    });
    const parsed = parseApiError(error);

    expect(parsed.title).toBe('Resource Exists');
    expect(parsed.severity).toBe('warning');
  });

  it('maps credentials/AccessDenied errors', () => {
    const error = createAxiosError(400, {
      error: { message: 'credentials verification failed: AccessDenied' },
    });
    const parsed = parseApiError(error);

    expect(parsed.title).toBe('Credentials Error');
    expect(parsed.action?.route).toBe('/auth-templates');
  });

  it('maps cluster offline errors', () => {
    const error = createAxiosError(502, {
      error: { message: 'cluster is offline or unreachable' },
    });
    const parsed = parseApiError(error);

    expect(parsed.title).toBe('Cluster Offline');
    expect(parsed.action?.route).toBe('/operators');
  });

  it('maps operator disconnected errors', () => {
    const error = createAxiosError(502, {
      error: { message: 'operator not connected' },
    });
    const parsed = parseApiError(error);

    expect(parsed.title).toBe('Operator Disconnected');
    expect(parsed.action?.route).toBe('/operators');
  });

  it('maps metrics-server errors', () => {
    const error = createAxiosError(400, {
      error: { message: 'metrics-server not installed' },
    });
    const parsed = parseApiError(error);

    expect(parsed.title).toBe('Metrics Unavailable');
    expect(parsed.severity).toBe('info');
    expect(parsed.action?.route).toBe('/helm');
  });

  it('maps state locked errors', () => {
    const error = createAxiosError(423, {
      error: { message: 'State is locked by another process' },
    });
    const parsed = parseApiError(error);

    expect(parsed.title).toBe('State Locked');
    expect(parsed.action?.route).toBe('/tasks');
  });

  it('maps BLUEPRINT_MODULES_MISSING by code to blueprint module guidance', () => {
    const error = createAxiosError(400, {
      error: {
        code: 'BLUEPRINT_MODULES_MISSING',
        message: 'Required modules missing from catalog',
      },
    });
    const parsed = parseApiError(error);

    expect(parsed.title).toBe('Blueprint Modules Missing');
    expect(parsed.suggestion).toBe('Sync the catalog source that contains the missing modules, then retry.');
    expect(parsed.action?.route).toBe('/catalog');
    expect(parsed.severity).toBe('warning');
  });

  it('does not classify generic blocked messages as State Locked', () => {
    const error = createAxiosError(400, {
      error: {
        message: 'Deployment blocked because prerequisites are missing',
      },
    });
    const parsed = parseApiError(error);

    expect(parsed.title).toBe('Operation Failed');
  });

  // =========================================================================
  // Edge Cases
  // =========================================================================

  it('handles null/undefined error', () => {
    const parsed = parseApiError(null);
    expect(parsed.title).toBe('Operation Failed');
    expect(parsed.severity).toBe('error');
  });

  it('handles string error', () => {
    const parsed = parseApiError('Something went wrong');
    expect(parsed.message).toBe('Something went wrong');
  });

  it('handles plain Error instance', () => {
    const parsed = parseApiError(new Error('Generic error'));
    expect(parsed.message).toBe('Generic error');
  });

  it('handles unknown error shape', () => {
    const parsed = parseApiError({ weird: 'object' });
    expect(parsed.title).toBe('Operation Failed');
  });
});

describe('formatErrorForToast', () => {
  it('formats error for toast display', () => {
    const error = createAxiosError(500);
    const formatted = formatErrorForToast(error);

    expect(formatted.title).toBeDefined();
    expect(typeof formatted.title).toBe('string');
  });

  it('includes action from parsed error', () => {
    const error = createAxiosError(401);
    const formatted = formatErrorForToast(error);

    expect(formatted.action?.label).toBe('Log In');
    expect(formatted.action?.route).toBe('/login');
  });
});

describe('getErrorIcon', () => {
  it('returns correct icons for each severity', () => {
    expect(getErrorIcon('error')).toContain('❌');
    expect(getErrorIcon('warning')).toContain('⚠️');
    expect(getErrorIcon('info')).toContain('ℹ️');
  });
});
