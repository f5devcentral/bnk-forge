/**
 * WAF Policy Manager types
 *
 * Types mirror the real, unmodified CRDs from nap-policy-operator
 * (group appprotect.f5.com/v1): APPolicy, APLogConf, APSignatures, APUserSig.
 * See docs/WAF_POLICY_MANAGER_DESIGN.md for the full field map and design.
 */

import type { K8sResource } from './kubernetes';

// ── Shared ─────────────────────────────────────────────────────────────────

export type BundleState = 'pending' | 'processing' | 'ready' | 'invalid';

export interface BundleSignatureRevision {
  attackSignatures?: string;
  botSignatures?: string;
  threatCampaigns?: string;
  userDefinedSignatures?: Array<{ name: string; generation: number }>;
}

export interface BundleStatus {
  state: BundleState;
  location?: string;
  sha256?: string;
  compilerVersion?: string;
  observedGeneration?: number;
  signatures?: BundleSignatureRevision;
}

// ── APPolicy ───────────────────────────────────────────────────────────────

export interface APPolicyModification {
  action: string;
  description?: string;
  entity?: { name: string };
  entityChanges?: { type: string };
}

export interface APPolicySpec {
  policy?: { name?: string } & Record<string, unknown>;
  modifications?: APPolicyModification[];
  modificationsReference?: { link: string };
}

export interface APPolicyStatus {
  bundle?: BundleStatus;
  lastGoodBundle?: BundleStatus;
  currentRevisionInProgress?: string;
  inProgressBundleLocation?: string;
  inProgressGeneration?: number;
  lastAppliedRevision?: string;
}

export interface APPolicyResource extends K8sResource {
  spec?: APPolicySpec;
  status?: APPolicyStatus;
}

// ── APLogConf ──────────────────────────────────────────────────────────────

export type APLogConfFormat = 'splunk' | 'arcsight' | 'default' | 'user-defined' | 'grpc';
export type APLogConfRequestType = 'all' | 'illegal' | 'blocked';

export interface APLogConfSpec {
  content?: {
    format?: APLogConfFormat;
    format_string?: string;
    escaping_characters?: Array<{ from: string; to: string }>;
    list_delimiter?: string;
    list_prefix?: string;
    list_suffix?: string;
    max_message_size?: string; // pattern: ^([1-9]|[1-5][0-9]|6[0-4])k$
    max_request_size?: string; // pattern: 1-10240 or Nk or 'any'
  };
  filter?: { request_type?: APLogConfRequestType };
}

export interface APLogConfResource extends K8sResource {
  spec?: APLogConfSpec;
  status?: { bundle?: BundleStatus };
}

// ── APSignatures (singleton, name must be "apsignatures") ─────────────────

export interface APSignaturesRepositoryAuth {
  type: 'bearer' | 'basic';
  secretName: string;
}

export interface APSignaturesRepositoryTls {
  caBundle?: { secretName: string };
  clientCertificate?: { secretName: string };
  verifyCertificate?: boolean;
}

export interface APSignaturesSpec {
  ['attack-signatures']?: { revision?: string };
  ['bot-signatures']?: { revision?: string };
  ['threat-campaigns']?: { revision?: string };
  repository?: {
    baseUrl?: string;
    authentication?: APSignaturesRepositoryAuth;
    tls?: APSignaturesRepositoryTls;
  };
}

export interface APSignaturesStatus {
  installationState?: 'success' | 'installing' | 'failure';
  ['attack-signatures']?: { installedRevision?: string };
  ['bot-signatures']?: { installedRevision?: string };
  ['threat-campaigns']?: { installedRevision?: string };
}

export interface APSignaturesResource extends K8sResource {
  spec?: APSignaturesSpec;
  status?: APSignaturesStatus;
}

// ── APUserSig ──────────────────────────────────────────────────────────────

export type APUserSigAccuracy = 'high' | 'medium' | 'low';
export type APUserSigRisk = 'high' | 'medium' | 'low';
export type APUserSigSignatureType = 'request' | 'response';
export type APUserSigReferenceType = 'bugtraq' | 'cve' | 'nessus' | 'url';

export interface APUserSigSignature {
  name?: string;
  rule?: string;
  description?: string;
  accuracy?: APUserSigAccuracy;
  risk?: APUserSigRisk;
  signatureType?: APUserSigSignatureType;
  attackType?: { name?: string };
  systems?: Array<{ name?: string }>;
  references?: { type?: APUserSigReferenceType; value?: string };
}

export interface APUserSigSpec {
  tag?: string;
  softwareVersion?: string;
  properties?: string;
  signatures?: APUserSigSignature[];
}

export interface APUserSigStatus {
  installationState?: 'success' | 'installing' | 'failure';
  policyUpdateState?: 'complete' | 'ongoing';
  processing?: { datetime?: string; errors?: string[] };
}

export interface APUserSigResource extends K8sResource {
  spec?: APUserSigSpec;
  status?: APUserSigStatus;
}

// ── Create/update request bodies (sent to bnk-forge backend) ──────────────

export interface WafPolicyCreateRequest {
  name: string;
  namespace: string;
  spec: APPolicySpec;
}

export interface WafPolicyUpdateRequest {
  namespace: string;
  spec: APPolicySpec;
}

export interface WafLogConfCreateRequest {
  name: string;
  namespace: string;
  spec: APLogConfSpec;
}

export interface WafLogConfUpdateRequest {
  namespace: string;
  spec: APLogConfSpec;
}

export interface WafUserSigCreateRequest {
  name: string;
  namespace: string;
  spec: APUserSigSpec;
}

export interface WafUserSigUpdateRequest {
  namespace: string;
  spec: APUserSigSpec;
}

export interface WafSignaturesUpdateRequest {
  namespace: string;
  spec: APSignaturesSpec;
}
