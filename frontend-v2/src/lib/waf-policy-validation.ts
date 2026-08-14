/**
 * Client-side validation for the WAF Policy wizard.
 *
 * Mirrors the *server-side* CRD OpenAPI constraints from nap-policy-operator
 * (appprotect.f5.com/v1 APPolicy / APLogConf / APSignatures / APUserSig) so
 * invalid input is caught before submit instead of surfacing a raw K8s 422.
 * See docs/WAF_POLICY_MANAGER_DESIGN.md §"Field-level validation".
 *
 * Pattern follows DPFSetupWizard.tsx: a pure function returning
 * Record<Step, string[]> of human-readable error messages per step.
 */

import type { APLogConfFormat, APLogConfRequestType } from '@/types';

export type WafWizardStep = 'basics' | 'policy' | 'logging' | 'review';

export interface WafWizardState {
  name: string;
  namespace: string;
  policyJsonText: string; // raw editor text for spec.policy
  logging: {
    enabled: boolean;
    // 'pick' = reference an existing APLogConf; 'create' = create a new one inline
    mode: 'pick' | 'create';
    existingLogConfName: string; // used when mode='pick'
    format: APLogConfFormat;
    formatString: string; // spec.content.format_string (only for user-defined format)
    requestType: APLogConfRequestType;
    maxMessageSize: string; // e.g. "64k"
    maxRequestSize: string; // e.g. "10k" | "any" | numeric string
  };
}

const K8S_NAME_PATTERN = /^[a-z0-9]([-a-z0-9]*[a-z0-9])?$/;
const MAX_MESSAGE_SIZE_PATTERN = /^([1-9]|[1-5][0-9]|6[0-4])k$/;
const MAX_REQUEST_SIZE_PATTERN = /^([1-9][0-9]{0,3}|10[0-1][0-9]{2}|102[0-3][0-9]?|10240|[1-9]k|10k|any)$/;

/** Validates `spec.policy` as JSON. Deliberately does NOT validate App Protect
 * policy semantics — that's the compiler's job (see design doc §7). */
function validatePolicyJson(text: string): string | null {
  if (!text.trim()) return 'Policy JSON is required';
  try {
    const parsed: unknown = JSON.parse(text);
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      return 'Policy JSON must be an object';
    }
  } catch {
    return 'Policy JSON is not valid JSON';
  }
  return null;
}

export function validateWafWizardStep(state: WafWizardState, step: WafWizardStep): string[] {
  const errors: string[] = [];

  switch (step) {
    case 'basics':
      if (!state.name) errors.push('Policy name is required');
      else if (!K8S_NAME_PATTERN.test(state.name)) {
        errors.push('Name must be lowercase alphanumeric, may contain hyphens (RFC 1123)');
      }
      if (!state.namespace) errors.push('Namespace is required');
      break;

    case 'policy': {
      const jsonError = validatePolicyJson(state.policyJsonText);
      if (jsonError) errors.push(jsonError);
      break;
    }

    case 'logging':
      if (state.logging.enabled) {
        if (state.logging.mode === 'pick' && !state.logging.existingLogConfName) {
          errors.push('Select an existing log profile or switch to "Create new"');
        }
        if (state.logging.mode === 'create') {
          if (state.logging.maxMessageSize && !MAX_MESSAGE_SIZE_PATTERN.test(state.logging.maxMessageSize)) {
            errors.push('Max message size must be between 1k and 64k (e.g. "64k")');
          }
          if (state.logging.maxRequestSize && !MAX_REQUEST_SIZE_PATTERN.test(state.logging.maxRequestSize)) {
            errors.push('Max request size must be a number, "Nk", or "any"');
          }
          if (state.logging.format === 'user-defined' && !state.logging.formatString.trim()) {
            errors.push('Format string is required when format is "user-defined"');
          }
        }
      }
      break;

    case 'review':
      break;
  }

  return errors;
}

export function validateWafWizardAll(state: WafWizardState): Record<WafWizardStep, string[]> {
  const steps: WafWizardStep[] = ['basics', 'policy', 'logging', 'review'];
  return steps.reduce(
    (acc, step) => {
      acc[step] = validateWafWizardStep(state, step);
      return acc;
    },
    {} as Record<WafWizardStep, string[]>
  );
}
