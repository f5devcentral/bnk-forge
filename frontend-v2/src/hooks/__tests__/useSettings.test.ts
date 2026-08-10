/**
 * Tests for useSettings hooks
 *
 * Covers: fetching settings, updating a single setting, fetching system
 * defaults, updating defaults, fetching defaults status, and error handling.
 */
import { describe, it, expect } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { useSettings, useSystemDefaults, useDefaultsStatus } from '@/hooks/useSettings';
import React from 'react';

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

// ============================================================================
// Additional MSW handlers for settings endpoints not in default handlers
// ============================================================================

const mockSettingsResponse = {
  settings: {
    general: [
      {
        key: 'site_name',
        value: 'BNK Forge',
        value_type: 'string',
        description: 'Site name',
        is_encrypted: false,
      },
    ],
  },
};

const mockDefaultsStatusResponse = {
  all_configured: true,
  missing: [],
  total_required: 5,
  configured_count: 5,
};

const settingsHandlers = [
  http.get('*/api/settings', () => {
    return HttpResponse.json(mockSettingsResponse);
  }),

  http.put('*/api/settings/:key', async ({ params, request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({
      success: true,
      key: params.key,
      value: body.value,
      message: 'Setting updated',
    });
  }),

  http.get('*/api/system/defaults/status', () => {
    return HttpResponse.json(mockDefaultsStatusResponse);
  }),
];

// ============================================================================
// useSettings
// ============================================================================

describe('useSettings', () => {
  beforeEach(() => {
    server.use(...settingsHandlers);
  });

  it('fetches settings and exposes them', async () => {
    const { result } = renderHook(() => useSettings(), {
      wrapper: createWrapper(),
    });

    expect(result.current.isLoading).toBe(true);

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.settings).toBeDefined();
    expect(result.current.settings.general).toHaveLength(1);
    expect(result.current.settings.general[0]).toMatchObject({
      key: 'site_name',
      value: 'BNK Forge',
      value_type: 'string',
    });
    expect(result.current.error).toBeNull();
  });

  it('updates a single setting via updateSetting', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.put('*/api/settings/:key', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true, message: 'Setting updated' });
      })
    );

    const { result } = renderHook(() => useSettings(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    result.current.updateSetting({ key: 'site_name', value: 'New Name' });

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    expect(capturedBody).toMatchObject({
      key: 'site_name',
      value: 'New Name',
    });
  });

  it('batch updates settings with correct payload shape ({ settings })', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.put('*/api/settings', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true, message: 'Settings updated' });
      })
    );

    const { result } = renderHook(() => useSettings(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    result.current.batchUpdateSettings({
      site_name: 'New Name',
      theme: 'dark',
    });

    await waitFor(() => {
      expect(capturedBody).not.toBeNull();
    });

    // The bug was sending { site_name, theme } directly instead of { settings: { site_name, theme } }
    // Pydantic schema expects { settings: Record<string, string> }
    expect(capturedBody).toMatchObject({
      settings: {
        site_name: 'New Name',
        theme: 'dark',
      },
    });
    // Ensure settings are nested, not at top level
    expect(capturedBody).not.toHaveProperty('site_name');
    expect(capturedBody).not.toHaveProperty('theme');
  });
});

// ============================================================================
// useSystemDefaults
// ============================================================================

describe('useSystemDefaults', () => {
  it('fetches system defaults', async () => {
    const { result } = renderHook(() => useSystemDefaults(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.defaults).toBeDefined();
    expect(result.current.defaults).toMatchObject({
      project: { default_type: { value: 'cloud-aws' } },
    });
    expect(result.current.error).toBeNull();
  });

  it('updates system defaults via updateDefaults', async () => {
    let capturedBody: Record<string, unknown> | null = null;
    server.use(
      http.put('*/api/system/defaults', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ success: true, message: 'Defaults updated' });
      })
    );

    const { result } = renderHook(() => useSystemDefaults(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    result.current.updateDefaults({ default_region: 'us-west-2' });

    await waitFor(() => {
      expect(capturedBody).toBeDefined();
    });

    expect(capturedBody).toMatchObject({
      updates: { default_region: 'us-west-2' },
    });
  });
});

// ============================================================================
// useDefaultsStatus
// ============================================================================

describe('useDefaultsStatus', () => {
  beforeEach(() => {
    server.use(...settingsHandlers);
  });

  it('fetches defaults status and returns computed fields', async () => {
    const { result } = renderHook(() => useDefaultsStatus(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.allConfigured).toBe(true);
    expect(result.current.missing).toEqual([]);
    expect(result.current.totalRequired).toBe(5);
    expect(result.current.configuredCount).toBe(5);
    expect(result.current.error).toBeNull();
  });
});

// ============================================================================
// Error handling
// ============================================================================

describe('useSettings error handling', () => {
  it('handles API errors on settings fetch', async () => {
    server.use(
      http.get('*/api/settings', () => {
        return HttpResponse.json(
          { error: { code: 'INTERNAL_ERROR', message: 'Database error' } },
          { status: 500 }
        );
      })
    );

    const { result } = renderHook(() => useSettings(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.error).toBeDefined();
    // settings should fall back to empty object
    expect(result.current.settings).toEqual({});
  });
});
