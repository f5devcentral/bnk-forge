/**
 * Centralized error handling utility for BNK-Forge v2
 *
 * Provides user-friendly error messages with actionable suggestions
 */

import { AxiosError } from 'axios';
import type { ErrorAction } from '@/types/common';
import type { PlatformCapabilities, PlatformConstraints, PlatformProfile } from '@/types/platform';
import { logger } from '@/lib/logger';

interface ErrorResponse {
  message: string;
  detail?: string | Record<string, unknown>;
  error?: string;
  code?: string;
  details?: Record<string, unknown>;
}

export interface ParsedError {
  title: string;
  message: string;
  suggestion?: string;
  /** Clickable fix-it action rendered as a button in the toast */
  action?: ErrorAction;
  severity: 'error' | 'warning' | 'info';
}

export interface ParseApiErrorContext {
  detectedPlatformProfile?: PlatformProfile;
  platformCapabilities?: PlatformCapabilities | null;
  platformConstraints?: PlatformConstraints | null;
}

// Type guard to check if error is an AxiosError
function isAxiosError(error: unknown): error is AxiosError<{ error?: ErrorResponse } & ErrorResponse> {
  return typeof error === 'object' && error !== null && 'isAxiosError' in error;
}

// Type guard for error-like objects
function isErrorLike(error: unknown): error is { message?: string; code?: string; status?: number; data?: unknown; response?: unknown } {
  return typeof error === 'object' && error !== null;
}

/**
 * Parse API error and return user-friendly message
 */
export function parseApiError(error: unknown, context?: ParseApiErrorContext): ParsedError {
  // Default error
  const defaultError: ParsedError = {
    title: 'Operation Failed',
    message: 'An unexpected error occurred. Please try again.',
    severity: 'error',
  };

  // Handle no error
  if (!error) return defaultError;

  // Handle string errors
  if (typeof error === 'string') {
    return { ...defaultError, message: error };
  }

  // Handle Error instances
  if (error instanceof Error && !isAxiosError(error)) {
    return { ...defaultError, message: error.message };
  }

  // Extract error data based on error type
  let errorData: ErrorResponse = { message: '' };
  let statusCode: number | undefined;
  let rawMessage: unknown;

  if (isAxiosError(error)) {
    errorData = error.response?.data?.error || error.response?.data || { message: error.message };
    statusCode = error.response?.status;
    rawMessage = errorData.detail || errorData.message || errorData.error || error.message;
  } else if (isErrorLike(error)) {
    const responseData = (error.response as { data?: ErrorResponse; status?: number } | undefined);
    errorData = responseData?.data || (error.data as ErrorResponse) || { message: '' };
    statusCode = responseData?.status || error.status;
    rawMessage = errorData.detail || errorData.message || errorData.error || error.message;
  }

  // Ensure errorMessage is a string
  const errorMessage = typeof rawMessage === 'string'
    ? rawMessage
    : typeof rawMessage === 'object'
      ? JSON.stringify(rawMessage)
      : String(rawMessage || 'Unknown error');
  const errorMsgLower = errorMessage.toLowerCase();
  const errorCodeValue = errorData.code;
  const domainErrorCode = typeof errorCodeValue === 'string' ? errorCodeValue : undefined;
  const isDetectedOcp = context?.detectedPlatformProfile === 'ocp';
  const isDetectedEks = context?.detectedPlatformProfile === 'eks';
  const isDetectedRoks = context?.detectedPlatformProfile === 'roks';
  const hasSccSecurityBindingSignal =
    !!context?.platformCapabilities?.scc
    || !!context?.platformConstraints?.privileged_workloads_require_platform_security_binding;
  const hasOperatorNetworkingSignal =
    !!context?.platformCapabilities?.openshift_routes
    || !!context?.platformConstraints?.operator_required_for_networking;
  const hasAwsEksHint =
    isDetectedEks
    || errorMsgLower.includes('eks')
    || errorMsgLower.includes('aws');
  const hasIbmRoksHint =
    isDetectedRoks
    || errorMsgLower.includes('roks')
    || errorMsgLower.includes('ibm cloud')
    || errorMsgLower.includes('ibm');
  const isDownstreamK8sUnauthorized =
    statusCode === 401
    && domainErrorCode === 'K8S_API_ERROR'
    && (
      errorMsgLower.includes('unauthorized')
      || errorMsgLower.includes('authentication')
      || errorMsgLower.includes('provide credentials')
      || errorMsgLower.includes('expired token')
    );

  if (isDownstreamK8sUnauthorized) {
    const downstreamUnauthorizedMessage = hasAwsEksHint
      ? 'AWS/EKS cluster access credentials appear to be expired or invalid.'
      : hasIbmRoksHint
        ? 'IBM/ROKS cluster access credentials appear to be expired or invalid.'
      : 'Cluster access credentials appear to be expired or invalid.';
    const downstreamUnauthorizedSuggestion = hasAwsEksHint
      ? 'Refresh kubeconfig for this cluster, or update cloud credentials if they were rotated.'
      : 'Refresh kubeconfig for this cluster, then retry the operation.';

    return {
      title: 'Cluster Authentication Expired',
      message: downstreamUnauthorizedMessage,
      suggestion: downstreamUnauthorizedSuggestion,
      action: { label: 'Manage Clusters', route: '/kubernetes' },
      severity: 'warning',
    };
  }

  const hasSccFailureSignature =
    errorMsgLower.includes('security context constraint')
    || errorMsgLower.includes('securitycontextconstraints')
    || errorMsgLower.includes('unable to validate against any security context constraint')
    || errorMsgLower.includes('forbidden: unable to validate against any security context constraint');

  if (
    isDetectedOcp
    && hasSccSecurityBindingSignal
    && (statusCode === 400 || statusCode === 403 || statusCode === 422)
    && hasSccFailureSignature
  ) {
    return {
      title: 'OpenShift Security Binding Required',
      message: 'This workload change was rejected by OpenShift security constraints for the detected cluster context.',
      suggestion: 'Apply the required platform security binding (SCC-aligned) for this workload, then retry the operation within Forge\'s supported workflow boundary.',
      action: { label: 'Manage Clusters', route: '/kubernetes' },
      severity: 'warning',
    };
  }

  const hasGenericIngressRbacOrValidationFailure =
    (errorMsgLower.includes('ingress') || errorMsgLower.includes('ingresses'))
    && (
      errorMsgLower.includes('cannot create resource "ingresses"')
      || errorMsgLower.includes('cannot update resource "ingresses"')
      || errorMsgLower.includes('rbac')
      || errorMsgLower.includes('validation failed')
      || errorMsgLower.includes('is invalid')
      || errorMsgLower.includes('field is immutable')
    );

  const hasOperatorManagedRouteFailureSignature =
    (errorMsgLower.includes('ingress') || errorMsgLower.includes('route'))
    && (
      errorMsgLower.includes('operator-managed route')
      || errorMsgLower.includes('openshift route')
      || errorMsgLower.includes('use route')
      || errorMsgLower.includes('route-based exposure')
      || errorMsgLower.includes('ingress is not supported on this cluster')
      || (
        errorMsgLower.includes('admission webhook')
        && (
          errorMsgLower.includes('route')
          || errorMsgLower.includes('ingresscontroller')
          || errorMsgLower.includes('openshift')
          || errorMsgLower.includes('operator-managed')
        )
      )
    );

  if (
    isDetectedOcp
    && hasOperatorNetworkingSignal
    && (statusCode === 400 || statusCode === 403 || statusCode === 422)
    && hasOperatorManagedRouteFailureSignature
    && !hasGenericIngressRbacOrValidationFailure
  ) {
    return {
      title: 'OpenShift Networking Path Required',
      message: 'This networking change was rejected for the detected OpenShift context.',
      suggestion: 'Use the platform-managed Route/operator networking path for north-south exposure, then retry within Forge\'s shared Kubernetes workflow.',
      action: { label: 'Manage Clusters', route: '/kubernetes' },
      severity: 'warning',
    };
  }

  // Remote authentication failure (expired/invalid downstream credentials).
  // Must be checked before the generic 401 handler so the structured backend
  // code takes precedence over the session-expiry fallback.
  const isRemoteAuthFailed = domainErrorCode === 'REMOTE_AUTHENTICATION_FAILED';
  if (isRemoteAuthFailed && statusCode === 401) {
    // Pull structured fields forwarded by the backend AuthenticationError.
    // The API response shape is { error: { code, message, details: {...} } } so
    // errorData.details carries the target_type and suggested_action fields.
    const details = errorData.details ?? {};
    const targetType = typeof details.target_type === 'string' ? details.target_type : '';
    const suggestedAction = typeof details.suggested_action === 'string'
      ? details.suggested_action
      : 'Refresh your credentials in Project Settings, then retry.';

    const isAwsTarget = targetType === 'aws' || hasAwsEksHint;

    if (isAwsTarget) {
      return {
        title: 'AWS Credentials Expired',
        message: 'Your AWS credentials are expired or invalid — cluster information is unavailable.',
        suggestion: suggestedAction,
        action: { label: 'Configure Credentials', route: '/auth-templates' },
        severity: 'warning',
      };
    }

    return {
      title: 'Remote Credentials Expired',
      message: 'Authentication to the remote system failed — credentials may be expired or invalid.',
      suggestion: suggestedAction,
      action: { label: 'Configure Credentials', route: '/auth-templates' },
      severity: 'warning',
    };
  }

  // HTTP Status Code specific handling
  if (statusCode === 401) {
    return {
      title: 'Authentication Required',
      message: 'Your session has expired. Please log in again.',
      suggestion: 'Refresh the page to log in',
      action: { label: 'Log In', route: '/login' },
      severity: 'warning',
    };
  }

  if (statusCode === 403) {
    return {
      title: 'Permission Denied',
      message: 'You do not have permission to perform this action.',
      suggestion: 'Contact your administrator for access',
      action: { label: 'View Users', route: '/system' },
      severity: 'error',
    };
  }

  if (statusCode === 404) {
    return {
      title: 'Not Found',
      message: 'The requested resource could not be found.',
      suggestion: 'It may have been deleted. Try refreshing the page.',
      severity: 'warning',
    };
  }

  if (statusCode === 409) {
    return {
      title: 'Conflict',
      message: errorMessage || 'The resource has been modified by another user.',
      suggestion: 'Refresh the page to see the latest changes',
      severity: 'warning',
    };
  }

  if (statusCode === 422) {
    return {
      title: 'Validation Error',
      message: errorMessage || 'The provided data is invalid.',
      suggestion: 'Please check your inputs and try again',
      severity: 'error',
    };
  }

  if (statusCode === 500) {
    return {
      title: 'Server Error',
      message: 'An internal server error occurred.',
      suggestion: 'Please try again later or contact support if the issue persists',
      action: { label: 'View Tasks', route: '/tasks' },
      severity: 'error',
    };
  }

  if (statusCode === 503) {
    return {
      title: 'Service Unavailable',
      message: 'The service is temporarily unavailable.',
      suggestion: 'Please wait a moment and try again',
      action: { label: 'System Health', route: '/system' },
      severity: 'warning',
    };
  }

  // Network errors
  const errorCode = isAxiosError(error) ? error.code : isErrorLike(error) ? error.code : undefined;
  
  if (errorCode === 'ERR_NETWORK' || errorMessage?.includes('Network Error')) {
    return {
      title: 'Network Error',
      message: 'Unable to connect to the server.',
      suggestion: 'Check your internet connection and try again',
      action: { label: 'System Health', route: '/system' },
      severity: 'error',
    };
  }

  // Timeout errors
  if (errorCode === 'ECONNABORTED' || errorMsgLower.includes('timeout')) {
    return {
      title: 'Request Timeout',
      message: 'The operation took too long and was cancelled.',
      suggestion: 'This operation may be running in the background. Check the status in a moment.',
      action: { label: 'View Tasks', route: '/tasks' },
      severity: 'warning',
    };
  }

  if (domainErrorCode === 'BLUEPRINT_MODULES_MISSING') {
    return {
      title: 'Blueprint Modules Missing',
      message: errorMessage,
      suggestion: 'Sync the catalog source that contains the missing modules, then retry.',
      action: { label: 'View Modules', route: '/catalog' },
      severity: 'warning',
    };
  }

  // Module-specific errors
  if (errorMessage?.includes('dependency') || errorMessage?.includes('depends on')) {
    return {
      title: 'Dependency Error',
      message: errorMessage,
      suggestion: 'Ensure all required dependencies are applied first',
      action: { label: 'Deploy Now', route: '/projects' },
      severity: 'error',
    };
  }

  if (errorMessage?.includes('already exists')) {
    return {
      title: 'Resource Exists',
      message: errorMessage,
      suggestion: 'Use a different name or update the existing resource',
      severity: 'warning',
    };
  }

  if (errorMessage?.includes('not found') || errorMessage?.includes('does not exist')) {
    return {
      title: 'Resource Not Found',
      message: errorMessage,
      suggestion: 'The resource may have been deleted. Try refreshing the page.',
      severity: 'warning',
    };
  }

  // Kubernetes-specific errors
  if (errorMessage?.includes('Unauthorized') || errorMessage?.includes('401')) {
    return {
      title: 'Kubernetes Authentication Failed',
      message: 'Unable to authenticate with the cluster.',
      suggestion: 'Refresh your kubeconfig credentials',
      action: { label: 'Manage Clusters', route: '/kubernetes' },
      severity: 'error',
    };
  }

  if (errorMessage?.includes('Forbidden') || errorMessage?.includes('403')) {
    return {
      title: 'Insufficient Permissions',
      message: errorMessage || 'You do not have permission to perform this operation.',
      suggestion: 'Check your RBAC permissions or contact your cluster administrator',
      severity: 'error',
    };
  }

  if (errorMessage?.includes('NotFound') || errorMessage?.includes('404')) {
    return {
      title: 'Resource Not Found',
      message: errorMessage || 'The Kubernetes resource was not found.',
      suggestion: 'The resource may have been deleted. Refresh to see current resources.',
      action: { label: 'View Resources', route: '/kubernetes' },
      severity: 'warning',
    };
  }

  if (errorMessage?.includes('AlreadyExists') || errorMessage?.includes('already exists')) {
    return {
      title: 'Resource Already Exists',
      message: errorMessage,
      suggestion: 'Use a different name or delete the existing resource first',
      severity: 'warning',
    };
  }

  if (errorMessage?.includes('metrics-server')) {
    return {
      title: 'Metrics Unavailable',
      message: 'Metrics server is not installed or not responding.',
      suggestion: 'Install metrics-server to view resource usage',
      action: { label: 'Helm Packages', route: '/helm' },
      severity: 'info',
    };
  }

  // OpenTofu/Terraform errors
  if (errorMessage?.includes('terraform') || errorMessage?.includes('tofu')) {
    return {
      title: 'Infrastructure Error',
      message: errorMessage,
      suggestion: 'Check the logs for detailed error information',
      action: { label: 'View Logs', route: '/tasks' },
      severity: 'error',
    };
  }

  // Cloud credential errors
  if (errorMessage?.includes('credentials') || errorMessage?.includes('AccessDenied')) {
    return {
      title: 'Credentials Error',
      message: 'Unable to authenticate with your cloud provider.',
      suggestion: 'Check your credentials in Project Settings',
      action: { label: 'Configure Credentials', route: '/auth-templates' },
      severity: 'error',
    };
  }

  const lockErrorCodes = new Set(['STATE_LOCKED', 'WORKSPACE_LOCKED', 'MODULE_LOCKED', 'ENTITY_LOCKED', 'LOCK_HELD']);
  const lockPhrasePatterns = [
    /\bstate\s+(is\s+)?locked\b/i,
    /\bworkspace\s+(is\s+)?locked\b/i,
    /\bmodule\s+(is\s+)?locked\b/i,
    /\bentity\s+(is\s+)?locked\b/i,
    /\block\s+held\b/i,
  ];
  const isLockCode = typeof domainErrorCode === 'string' && lockErrorCodes.has(domainErrorCode);
  const hasLockPhrase = lockPhrasePatterns.some((pattern) => pattern.test(errorMessage));

  // State locking errors
  if (statusCode === 423 || isLockCode || hasLockPhrase) {
    return {
      title: 'State Locked',
      message: 'The state is currently locked by another operation.',
      suggestion: 'Wait for the other operation to complete or force unlock if needed',
      action: { label: 'View Tasks', route: '/tasks' },
      severity: 'warning',
    };
  }

  // Drift detection errors
  if (errorMessage?.includes('drift') || errorMessage?.includes('Drift')) {
    return {
      title: 'Drift Detected',
      message: errorMessage,
      suggestion: 'Review the changes and reconcile or accept the current state',
      action: { label: 'Review Changes', route: '/projects' },
      severity: 'warning',
    };
  }

  // Cluster offline / not connected errors
  if (errorMessage?.includes('cluster') && (errorMessage?.includes('offline') || errorMessage?.includes('unreachable') || errorMessage?.includes('not connected'))) {
    return {
      title: 'Cluster Offline',
      message: errorMessage || 'The target cluster is not reachable.',
      suggestion: 'Check your cluster connection or register an operator',
      action: { label: 'Connect Cluster', route: '/operators' },
      severity: 'error',
    };
  }

  // Operator not connected errors
  if (errorMessage?.includes('operator') && (errorMessage?.includes('not connected') || errorMessage?.includes('offline') || errorMessage?.includes('disconnected'))) {
    return {
      title: 'Operator Disconnected',
      message: errorMessage || 'The BNK operator is not connected.',
      suggestion: 'Check operator status or re-register',
      action: { label: 'Manage Operators', route: '/operators' },
      severity: 'error',
    };
  }

  // BNK-specific errors
  // FAR Setup errors
  if (errorMessage?.includes('far') || errorMessage?.includes('F5 App Registry')) {
    if (errorMessage?.includes('auth') || errorMessage?.includes('credential')) {
      return {
        title: 'FAR Authentication Failed',
        message: 'Unable to authenticate with F5 App Registry.',
        suggestion: 'Upload your FAR service account key',
        action: { label: 'Upload Now', route: '/auth-templates' },
        severity: 'error',
      };
    }
    if (errorMessage?.includes('manifest') || errorMessage?.includes('version')) {
      return {
        title: 'Invalid Manifest Version',
        message: 'BNK manifest version format is invalid.',
        suggestion: 'Use format: X.Y.Z-A.B.C-D.E.F (e.g., "2.1.0-3.1736.1-0.1.27")',
        severity: 'error',
      };
    }
  }

  // FLO (F5 Lifecycle Operator) errors
  if (errorMessage?.includes('flo') || errorMessage?.includes('lifecycle operator')) {
    if (errorMessage?.includes('license') || errorMessage?.includes('jwt')) {
      return {
        title: 'FLO License Validation Failed',
        message: 'Unable to validate F5 license.',
        suggestion: 'Check JWT token from MyF5 portal or verify license mode',
        action: { label: 'Upload License', route: '/auth-templates' },
        severity: 'error',
      };
    }
    if (errorMessage?.includes('crd') || errorMessage?.includes('CustomResourceDefinition')) {
      return {
        title: 'FLO CRD Installation Failed',
        message: 'Unable to install Custom Resource Definitions.',
        suggestion: 'Ensure cluster has RBAC permissions and sufficient resources',
        action: { label: 'Check Cluster', route: '/kubernetes' },
        severity: 'error',
      };
    }
  }

  // Gateway errors
  if (errorMessage?.includes('gateway') && (errorMessage?.includes('listener') || errorMessage?.includes('port'))) {
    if (errorMessage?.includes('conflict') || errorMessage?.includes('already in use')) {
      return {
        title: 'Gateway Port Conflict',
        message: 'Listener port is already in use by another Gateway.',
        suggestion: 'Choose a different port or remove the conflicting Gateway',
        action: { label: 'View Gateways', route: '/bnk' },
        severity: 'error',
      };
    }
    if (errorMessage?.includes('tls') || errorMessage?.includes('certificate')) {
      return {
        title: 'TLS Certificate Not Found',
        message: 'Referenced TLS certificate secret does not exist.',
        suggestion: 'Create the TLS certificate secret before deploying Gateway',
        action: { label: 'Manage Secrets', route: '/kubernetes' },
        severity: 'error',
      };
    }
  }

  // HTTPRoute errors
  if (errorMessage?.includes('httproute') || errorMessage?.includes('route')) {
    if (errorMessage?.includes('backend') || errorMessage?.includes('service')) {
      return {
        title: 'Backend Service Not Found',
        message: 'One or more backend services referenced in the route do not exist.',
        suggestion: 'Create the Kubernetes Service before configuring routes',
        action: { label: 'View Services', route: '/kubernetes' },
        severity: 'error',
      };
    }
    if (errorMessage?.includes('hostname') || errorMessage?.includes('dns')) {
      return {
        title: 'Invalid Hostname',
        message: 'Hostname format is invalid.',
        suggestion: 'Use valid DNS format (e.g., "example.com" or "*.example.com")',
        severity: 'error',
      };
    }
  }

  // Return error message with default title
  return {
    title: 'Operation Failed',
    message: errorMessage || 'An unexpected error occurred.',
    suggestion: 'Please try again or check the logs for more details',
    severity: 'error',
  };
}

/**
 * Format error for toast notification
 */
export function formatErrorForToast(error: unknown): {
  title: string;
  description?: string;
  action?: ErrorAction;
} {
  const parsed = parseApiError(error);
  return {
    title: parsed.title,
    description: parsed.suggestion
      ? `${parsed.message}\n${parsed.suggestion}`
      : parsed.message,
    action: parsed.action,
  };
}

/**
 * Get error severity icon
 */
export function getErrorIcon(severity: ParsedError['severity']): string {
  switch (severity) {
    case 'error':
      return '❌';
    case 'warning':
      return '⚠️';
    case 'info':
      return 'ℹ️';
    default:
      return '❌';
  }
}

/**
 * Log error with context for debugging
 */
export function logError(context: string, error: unknown): void {
  const parsed = parseApiError(error);
  logger.error(`[${context}]`, {
    title: parsed.title,
    message: parsed.message,
    suggestion: parsed.suggestion,
    severity: parsed.severity,
    originalError: error,
  });
}
