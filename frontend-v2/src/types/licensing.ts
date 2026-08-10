// BNK Licensing types — CWC license and telemetry management

/**
 * License status response — normalized by the backend from the raw CWC
 * /status response.  The backend handles all CWC response shape variants
 * (InitialRegistrationStatus, SwitchLicenseStatus, etc.) and returns this
 * stable interface.
 */
export interface LicenseStatusResponse {
  success: boolean;
  operator_dispatch?: boolean;

  /** Normalized license state — e.g. "Active", "Device Registration Failed" */
  license_state?: string;
  /** e.g. "eval", "subscription" */
  entitlement_type?: string | null;
  digital_asset_id?: string | null;
  license_expiry_date?: string | null;
  license_expiry_days?: number | null;
  cluster_name?: string | null;
  /** Error message from CWC if license is in a failed state */
  error?: string | null;
  /** Suggested action from CWC */
  suggested_action?: string | null;
  telemetry_state?: string | null;
  telemetry_message?: string | null;

  /** Raw CWC response for debugging */
  raw_cwc_response?: Record<string, unknown>;

  /** Flat fields for backward compat / extra data */
  [key: string]: unknown;
}

/** Telemetry report response from CWC /report endpoint */
export interface LicenseReportResponse {
  success: boolean;
  operator_dispatch?: boolean;
  /** Raw report data (may be JWT or JSON) */
  raw?: string;
  [key: string]: unknown;
}

/** Mutation response for activate/receipt/renew operations */
export interface LicenseActionResponse {
  success: boolean;
  operator_dispatch?: boolean;
  error_message?: string;
  license_state_pre?: string;
  license_state_post?: string;
  digital_asset_id_post?: string;
  cwc_response?: unknown;
  jwks_validation?: { status: string; error?: string };
  [key: string]: unknown;
}

/** License activation request */
export interface LicenseActivateRequest {
  jwt: string;
}

/** License receipt request */
export interface LicenseReceiptRequest {
  manifest: string;
}

/**
 * License renewal request.
 *
 * Default (force=false): Clean license switch via CWC /reactivate API.
 *   No CWC downtime, telemetry history preserved.
 *
 * Force (force=true): Nuclear renewal — deletes CPCL state secrets,
 *   restarts CWC pod (~2 min downtime), then reactivates.
 */
export interface LicenseRenewRequest {
  jwt: string;
  force?: boolean;
}

/** JWKS validation response from POST /validate-jwks */
export interface JwksValidationResponse {
  success: boolean;
  status?: 'valid' | 'patched' | 'not_found';
  namespace?: string;
  message?: string;
}

/** CWC connectivity status (from cwc_check_status operator command) */
export interface CWCStatusResponse {
  success: boolean;
  certs_mounted: boolean;
  cwc_service_found: boolean;
  cwc_reachable: boolean;
  setup_complete: boolean;
  cert_manager_available: boolean;
  message?: string;
  server_cert_exists?: boolean;
  client_cert_exists?: boolean;
  cwc_status?: LicenseStatusResponse;
  operator_dispatch?: boolean;
}

/** Parsed license info for UI display */
export interface LicenseInfo {
  state: 'active' | 'expired' | 'evaluation' | 'unknown' | 'unavailable';
  /** Raw license state string from CWC (e.g. "Active", "Device Registration Failed") */
  rawState?: string;
  entitlementType?: string;
  digitalAssetId?: string;
  expiryDate?: string;
  expiryDays?: number;
  telemetryState?: string;
  telemetryMessage?: string;
  /** Error message from CWC when license is in a failed state */
  error?: string;
  /** Suggested action from CWC */
  suggestedAction?: string;
}
