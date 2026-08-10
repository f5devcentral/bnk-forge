/**
 * React Query hooks for BNK licensing operations.
 *
 * K8S-UX-010: All licensing operations are dispatched through the connected
 * operator pod → CWC REST API via mTLS.
 */
import { useMemo } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { licensingApi } from '@/lib/api/licensing';

import type {
  LicenseActivateRequest,
  LicenseRenewRequest,
  LicenseReceiptRequest,
  LicenseInfo,
  LicenseStatusResponse,
  JwksValidationResponse,
} from '@/types';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

export const LICENSING_KEYS = {
  all: ['licensing'] as const,
  status: (clusterId: number) => [...LICENSING_KEYS.all, 'status', clusterId] as const,
  report: (clusterId: number) => [...LICENSING_KEYS.all, 'report', clusterId] as const,
  cwcStatus: (clusterId: number) => [...LICENSING_KEYS.all, 'cwc-status', clusterId] as const,
};

/**
 * Parse the normalized license status response into a friendly LicenseInfo
 * object for the UI.
 *
 * The backend normalizes the varying CWC response shapes into flat fields:
 *   license_state, entitlement_type, digital_asset_id, license_expiry_date,
 *   license_expiry_days, telemetry_state, telemetry_message, error, etc.
 */
function parseLicenseStatus(data: LicenseStatusResponse | undefined): LicenseInfo {
  if (!data || !data.success) {
    return { state: 'unavailable' };
  }

  const licenseState = data.license_state;
  const entitlementType = (data.entitlement_type ?? '').toLowerCase();
  const expiryDays = data.license_expiry_days ?? undefined;
  const errorMsg = data.error;

  // If no license state at all, mark as unknown
  if (!licenseState || licenseState === 'Unknown') {
    // If there's an error, it's a failed state, not truly unknown
    if (errorMsg) {
      return {
        state: 'expired', // Show as a problem state
        entitlementType: data.entitlement_type ?? undefined,
        digitalAssetId: data.digital_asset_id ?? undefined,
        expiryDate: data.license_expiry_date ?? undefined,
        expiryDays,
        telemetryState: data.telemetry_state ?? undefined,
        telemetryMessage: errorMsg,
      };
    }
    return { state: 'unknown' };
  }

  let state: LicenseInfo['state'] = 'unknown';
  const lsLower = licenseState.toLowerCase();

  if (
    lsLower === 'active' ||
    lsLower === 'licensed' ||
    lsLower === 'verification complete' ||
    lsLower.includes('active')
  ) {
    state = 'active';
  } else if (
    lsLower === 'expired' ||
    lsLower.includes('expired') ||
    (expiryDays !== undefined && expiryDays <= 0)
  ) {
    state = 'expired';
  } else if (
    lsLower.includes('failed') ||
    lsLower.includes('error') ||
    lsLower.includes('unverifiable')
  ) {
    state = 'expired'; // Treat failed registration as a problem state
  } else if (
    entitlementType.includes('eval') ||
    entitlementType.includes('trial')
  ) {
    state = 'evaluation';
  } else if (
    lsLower.includes('initialization') ||
    lsLower.includes('provisioning') ||
    lsLower.includes('pending')
  ) {
    state = 'unknown'; // Still in progress
  } else if (expiryDays !== undefined && expiryDays > 0) {
    state = 'active';
  }

  return {
    state,
    rawState: licenseState,
    entitlementType: data.entitlement_type ?? undefined,
    digitalAssetId: data.digital_asset_id ?? undefined,
    expiryDate: data.license_expiry_date ?? undefined,
    expiryDays,
    telemetryState: data.telemetry_state ?? undefined,
    telemetryMessage: data.telemetry_message ?? undefined,
    error: data.error ?? undefined,
    suggestedAction: data.suggested_action ?? undefined,
  };
}

/** Get CWC license and telemetry status for a cluster */
export function useLicenseStatus(clusterId: number, enabled = true) {
  const query = useQuery({
    queryKey: LICENSING_KEYS.status(clusterId),
    queryFn: () => licensingApi.getStatus(clusterId),
    enabled: enabled && clusterId > 0,
    staleTime: 60_000, // 1 minute — license status changes infrequently
    retry: 1,
  });

  const licenseInfo = useMemo(() => parseLicenseStatus(query.data), [query.data]);

  return {
    ...query,
    licenseInfo,
  };
}

/** Get CWC telemetry report for a cluster */
export function useLicenseReport(clusterId: number, enabled = true) {
  return useQuery({
    queryKey: LICENSING_KEYS.report(clusterId),
    queryFn: () => licensingApi.getReport(clusterId),
    enabled: enabled && clusterId > 0,
    staleTime: 30_000,
    retry: 1,
  });
}

/** Full CWC connectivity and certificate status check */
export function useCWCStatus(clusterId: number, enabled = true) {
  return useQuery({
    queryKey: LICENSING_KEYS.cwcStatus(clusterId),
    queryFn: () => licensingApi.getCWCStatus(clusterId),
    enabled: enabled && clusterId > 0,
    staleTime: 60_000,
    retry: 1,
  });
}

/** Activate/switch CWC license */
export function useActivateLicense() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ clusterId, data }: { clusterId: number; data: LicenseActivateRequest }) =>
      licensingApi.activate(clusterId, data),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: LICENSING_KEYS.status(variables.clusterId) });
      queryClient.invalidateQueries({ queryKey: LICENSING_KEYS.cwcStatus(variables.clusterId) });
    },
  });
}

/**
 * Renew / switch license.
 *
 * Default (force=false): Clean API-based switch via CWC /reactivate.
 *   No CWC downtime, telemetry history preserved.
 *
 * Force (force=true): Nuclear renewal — deletes CPCL state, restarts
 *   CWC pod (~2 min downtime), then reactivates.
 */
export function useRenewLicense() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ clusterId, data }: { clusterId: number; data: LicenseRenewRequest }) =>
      licensingApi.renew(clusterId, data),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: LICENSING_KEYS.status(variables.clusterId) });
      queryClient.invalidateQueries({ queryKey: LICENSING_KEYS.cwcStatus(variables.clusterId) });
    },
  });
}

/** Validate and fix JWKS ConfigMap (cpcl-key-cm) on a cluster */
export function useValidateJwks() {
  const queryClient = useQueryClient();

  return useAppMutation<JwksValidationResponse, Error, { clusterId: number }>({
    mutationFn: ({ clusterId }) => licensingApi.validateJwks(clusterId),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: LICENSING_KEYS.status(variables.clusterId) });
      queryClient.invalidateQueries({ queryKey: LICENSING_KEYS.cwcStatus(variables.clusterId) });
    },
  });
}

/** Send signed manifest (receipt) to CWC */
export function usePostLicenseReceipt() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: ({ clusterId, data }: { clusterId: number; data: LicenseReceiptRequest }) =>
      licensingApi.postReceipt(clusterId, data),
    onSuccess: (_data, variables) => {
      queryClient.invalidateQueries({ queryKey: LICENSING_KEYS.status(variables.clusterId) });
    },
  });
}
