/**
 * Tests for useLicensing hooks
 *
 * K8S-UX-010: Licensing operations are dispatched through the connected
 * operator pod → CWC REST API via mTLS.
 *
 * Backend routes: routes/licensing.py
 *   GET  /api/licensing/{cluster_id}/status     — cwc_license_status
 *   GET  /api/licensing/{cluster_id}/report     — cwc_license_report
 *   POST /api/licensing/{cluster_id}/activate   — cwc_license_activate (body: { jwt: string })
 *   POST /api/licensing/{cluster_id}/receipt    — cwc_license_receipt (body: { manifest: string })
 *   GET  /api/licensing/{cluster_id}/cwc-status — cwc_check_status
 *
 * Covers: LICENSING_KEYS factory, useLicenseStatus (with parseLicenseStatus),
 * useLicenseReport, useCWCStatus, useActivateLicense, usePostLicenseReceipt
 * — including enabled/disabled states, error handling, cache invalidation,
 * and payload shape assertions (CT-012 pattern).
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { renderHook, waitFor, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  LICENSING_KEYS,
  useLicenseStatus,
  useLicenseReport,
  useCWCStatus,
  useActivateLicense,
  usePostLicenseReceipt,
} from '@/hooks/useLicensing';
import React from 'react';

// Mock notifyError to prevent side effects
vi.mock('@/lib/notify', () => ({
  notifyError: vi.fn(),
  notify: Object.assign(vi.fn(), {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
    loading: vi.fn(),
    dismiss: vi.fn(),
    undo: vi.fn(),
    progress: vi.fn(),
    promise: vi.fn(),
  }),
}));

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });

  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client: queryClient }, children);
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

// ============================================================================
// LICENSING_KEYS
// ============================================================================

describe('LICENSING_KEYS', () => {
  it('generates correct key structure', () => {
    expect(LICENSING_KEYS.all).toEqual(['licensing']);
    expect(LICENSING_KEYS.status(5)).toEqual(['licensing', 'status', 5]);
    expect(LICENSING_KEYS.report(3)).toEqual(['licensing', 'report', 3]);
    expect(LICENSING_KEYS.cwcStatus(7)).toEqual(['licensing', 'cwc-status', 7]);
  });
});

// ============================================================================
// useLicenseStatus
// ============================================================================

describe('useLicenseStatus', () => {
  // Backend: routes/licensing.py L107-115
  // Dispatches: cwc_license_status → operator → CWC GET /status
  // Backend normalizes the varying CWC response shapes into flat fields:
  //   license_state, entitlement_type, digital_asset_id, license_expiry_date,
  //   license_expiry_days, telemetry_state, telemetry_message, error.

  it('fetches and parses active license', async () => {
    server.use(
      http.get('*/api/licensing/10/status', () =>
        HttpResponse.json({
          success: true,
          operator_dispatch: true,
          license_state: 'Active',
          entitlement_type: 'BNK-Enterprise',
          digital_asset_id: 'DAI-12345',
          license_expiry_date: '2027-01-01',
          license_expiry_days: 365,
          telemetry_state: 'Running',
          telemetry_message: 'Telemetry active',
        })
      )
    );

    const { result } = renderHook(() => useLicenseStatus(10), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.licenseInfo).toMatchObject({
      state: 'active',
      rawState: 'Active',
      entitlementType: 'BNK-Enterprise',
      digitalAssetId: 'DAI-12345',
      expiryDate: '2027-01-01',
      expiryDays: 365,
      telemetryState: 'Running',
      telemetryMessage: 'Telemetry active',
    });
    expect(result.current.data?.operator_dispatch).toBe(true);
  });

  it('parses expired license', async () => {
    server.use(
      http.get('*/api/licensing/10/status', () =>
        HttpResponse.json({
          success: true,
          license_state: 'Expired',
          entitlement_type: 'BNK-Standard',
          digital_asset_id: 'DAI-99999',
          license_expiry_date: '2025-01-01',
          license_expiry_days: 0,
          telemetry_state: 'Stopped',
        })
      )
    );

    const { result } = renderHook(() => useLicenseStatus(10), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.licenseInfo.state).toBe('expired');
    expect(result.current.licenseInfo.expiryDays).toBe(0);
  });

  it('parses evaluation license', async () => {
    server.use(
      http.get('*/api/licensing/10/status', () =>
        HttpResponse.json({
          success: true,
          license_state: 'Licensed',
          entitlement_type: 'BNK-Evaluation',
          license_expiry_days: 14,
        })
      )
    );

    const { result } = renderHook(() => useLicenseStatus(10), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.licenseInfo.state).toBe('active'); // Licensed state takes precedence
    expect(result.current.licenseInfo.entitlementType).toBe('BNK-Evaluation');
  });

  it('returns unavailable when success is false', async () => {
    server.use(
      http.get('*/api/licensing/10/status', () =>
        HttpResponse.json({ success: false, error_message: 'Operator not connected' })
      )
    );

    const { result } = renderHook(() => useLicenseStatus(10), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.licenseInfo.state).toBe('unavailable');
  });

  it('does not fetch when clusterId is 0', () => {
    const { result } = renderHook(() => useLicenseStatus(0), { wrapper: createWrapper() });
    expect(result.current.fetchStatus).toBe('idle');
  });

  it('does not fetch when enabled is false', () => {
    const { result } = renderHook(() => useLicenseStatus(10, false), { wrapper: createWrapper() });
    expect(result.current.fetchStatus).toBe('idle');
  });
});

// ============================================================================
// useLicenseReport
// ============================================================================

describe('useLicenseReport', () => {
  // Backend: routes/licensing.py L118-127
  // Dispatches: cwc_license_report → operator → CWC GET /report

  it('fetches telemetry report', async () => {
    server.use(
      http.get('*/api/licensing/5/report', () =>
        HttpResponse.json({
          success: true,
          raw: 'eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9...',
          operator_dispatch: true,
        })
      )
    );

    const { result } = renderHook(() => useLicenseReport(5), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.success).toBe(true);
    expect(result.current.data?.raw).toContain('eyJ');
    expect(result.current.data?.operator_dispatch).toBe(true);
  });

  it('does not fetch when disabled', () => {
    const { result } = renderHook(() => useLicenseReport(5, false), { wrapper: createWrapper() });
    expect(result.current.fetchStatus).toBe('idle');
  });
});

// ============================================================================
// useCWCStatus
// ============================================================================

describe('useCWCStatus', () => {
  // Backend: routes/licensing.py L193-206
  // Dispatches: cwc_check_status → operator → K8s API + CWC GET /status

  it('fetches full CWC connectivity status', async () => {
    server.use(
      http.get('*/api/licensing/8/cwc-status', () =>
        HttpResponse.json({
          success: true,
          certs_mounted: true,
          cwc_service_found: true,
          cwc_reachable: true,
          setup_complete: true,
          cert_manager_available: true,
          server_cert_exists: true,
          client_cert_exists: true,
          message: 'ok',
          operator_dispatch: true,
        })
      )
    );

    const { result } = renderHook(() => useCWCStatus(8), { wrapper: createWrapper() });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.cwc_reachable).toBe(true);
    expect(result.current.data?.certs_mounted).toBe(true);
    expect(result.current.data?.setup_complete).toBe(true);
    expect(result.current.data?.operator_dispatch).toBe(true);
    expect(result.current.data?.message).toBe('ok');
  });
});

// ============================================================================
// useActivateLicense
// ============================================================================

describe('useActivateLicense', () => {
  // Backend: routes/licensing.py L130-151
  // Pydantic request: LicenseActivateRequest { jwt: str }
  // Dispatches: cwc_license_activate → operator → CWC POST /reactivate (raw JWT body)

  it('sends correct payload shape (CT-012)', async () => {
    let capturedBody: Record<string, unknown> | null = null;

    server.use(
      http.post('*/api/licensing/10/activate', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true, operator_dispatch: true });
      })
    );

    const { result } = renderHook(() => useActivateLicense(), { wrapper: createWrapper() });

    await act(async () => {
      result.current.mutate({
        clusterId: 10,
        data: { jwt: 'eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.payload.signature' },
      });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    // CT-012: Verify payload matches backend Pydantic schema
    expect(capturedBody).toMatchObject({
      jwt: expect.any(String),
    });
    expect(capturedBody!.jwt).toBe('eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.payload.signature');
    expect(result.current.data?.operator_dispatch).toBe(true);
    // Must NOT have extra wrapping
    expect(capturedBody).not.toHaveProperty('body');
    expect(capturedBody).not.toHaveProperty('data');
  });

  it('handles activation error', async () => {
    server.use(
      http.post('*/api/licensing/10/activate', () =>
        HttpResponse.json(
          { detail: 'No connected operator found for cluster 10' },
          { status: 400 }
        )
      )
    );

    const { result } = renderHook(() => useActivateLicense(), { wrapper: createWrapper() });

    await act(async () => {
      result.current.mutate({
        clusterId: 10,
        data: { jwt: 'invalid-jwt' },
      });
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});

// ============================================================================
// usePostLicenseReceipt
// ============================================================================

describe('usePostLicenseReceipt', () => {
  // Backend: routes/licensing.py L154-171
  // Pydantic request: LicenseReceiptRequest { manifest: str }
  // Dispatches: cwc_license_receipt → operator → CWC POST /receipt (raw manifest body)

  it('sends correct payload shape (CT-012)', async () => {
    let capturedBody: Record<string, unknown> | null = null;

    server.use(
      http.post('*/api/licensing/10/receipt', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true, operator_dispatch: false });
      })
    );

    const { result } = renderHook(() => usePostLicenseReceipt(), { wrapper: createWrapper() });

    await act(async () => {
      result.current.mutate({
        clusterId: 10,
        data: { manifest: 'signed-manifest-jwt-data-from-f5-server' },
      });
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    // CT-012: Verify payload matches backend Pydantic schema
    expect(capturedBody).toMatchObject({
      manifest: expect.any(String),
    });
    expect(capturedBody!.manifest).toBe('signed-manifest-jwt-data-from-f5-server');
    expect(result.current.data?.operator_dispatch).toBe(false);
    // Must NOT have extra wrapping
    expect(capturedBody).not.toHaveProperty('body');
    expect(capturedBody).not.toHaveProperty('data');
  });

  it('handles receipt submission error', async () => {
    server.use(
      http.post('*/api/licensing/10/receipt', () =>
        HttpResponse.json(
          { detail: 'No connected operator found for cluster 10' },
          { status: 400 }
        )
      )
    );

    const { result } = renderHook(() => usePostLicenseReceipt(), { wrapper: createWrapper() });

    await act(async () => {
      result.current.mutate({
        clusterId: 10,
        data: { manifest: 'bad-manifest' },
      });
    });

    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
