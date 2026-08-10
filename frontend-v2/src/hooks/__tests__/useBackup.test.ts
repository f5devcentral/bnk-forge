/**
 * Tests for backup/restore hooks
 * CT-012: Response shapes match backend Pydantic schemas
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useBackupStatus, useCreateBackup, useMaintenanceStatus } from '../useBackup';
import type { BackupStatusResponse, MaintenanceStatusResponse } from '@/types/backup';
import {
  mockBackupStatus,
  mockBackupStatusInProgress,
  mockMaintenanceActive,
  mockMaintenanceStatus,
} from '@/test/mocks/handlers';
import { server } from '@/test/mocks/server';

beforeEach(() => {
  vi.restoreAllMocks();
});

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
// useBackupStatus
// ============================================================================

describe('useBackupStatus', () => {
  it('fetches backup status with correct shape', async () => {
    const { result } = renderHook(() => useBackupStatus(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(mockBackupStatus);
    expect(result.current.data?.in_progress).toBe(false);
    expect(result.current.data?.operation).toBeNull();
  });

  it('handles in-progress status', async () => {
    server.use(
      http.get('*/api/system/backup/status', () => {
        return HttpResponse.json(mockBackupStatusInProgress);
      })
    );

    const { result } = renderHook(() => useBackupStatus(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data?.in_progress).toBe(true);
    expect(result.current.data?.operation).toBe('restore');
    expect(result.current.data?.started_at).toBe('2026-04-13T12:00:00Z');
    expect(result.current.data?.message).toBe('Database restore in progress. Please wait...');
  });

  it('has all required fields from backend schema', async () => {
    const { result } = renderHook(() => useBackupStatus(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const data = result.current.data;
    expect(data).toHaveProperty('in_progress');
    expect(data).toHaveProperty('operation');
    expect(data).toHaveProperty('started_at');
    expect(data).toHaveProperty('message');
  });
});

// ============================================================================
// useMaintenanceStatus
// ============================================================================

describe('useMaintenanceStatus', () => {
  it('fetches maintenance status with correct shape', async () => {
    const { result } = renderHook(() => useMaintenanceStatus(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data).toEqual(mockMaintenanceStatus);
    expect(result.current.data?.maintenance_mode).toBe(false);
  });

  it('handles active maintenance mode', async () => {
    server.use(
      http.get('*/api/system/maintenance', () => {
        return HttpResponse.json(mockMaintenanceActive);
      })
    );

    const { result } = renderHook(() => useMaintenanceStatus(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data?.maintenance_mode).toBe(true);
    expect(result.current.data?.message).toBe('System maintenance in progress');
    expect(result.current.data?.started_at).toBe('2026-04-13T12:00:00Z');
  });

  it('has all required fields from backend schema', async () => {
    const { result } = renderHook(() => useMaintenanceStatus(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const data = result.current.data;
    expect(data).toHaveProperty('maintenance_mode');
    expect(data).toHaveProperty('message');
    expect(data).toHaveProperty('started_at');
  });
});

describe('useCreateBackup', () => {
  it('uses the backend filename from content-disposition', async () => {
    server.use(
      http.post('*/api/system/backup', async ({ request }) => {
        const body = (await request.json()) as { passphrase: string };
        expect(body.passphrase).toBe('strongpassphrase123');

        return new HttpResponse(new Blob(['fake-archive-data']), {
          headers: {
            'Content-Type': 'application/gzip',
            'Content-Disposition': 'attachment; filename="bnkforge-backup-20260414_101010.tar.gz"',
          },
        });
      })
    );

    const createObjectURLSpy = vi.spyOn(window.URL, 'createObjectURL').mockReturnValue('blob:backup');
    const revokeObjectURLSpy = vi.spyOn(window.URL, 'revokeObjectURL').mockImplementation(() => {});
    const appendChildSpy = vi.spyOn(document.body, 'appendChild');
    const removeChildSpy = vi.spyOn(document.body, 'removeChild');
    const clickSpy = vi.fn();
    const originalCreateElement = document.createElement.bind(document);
    const createElementSpy = vi.spyOn(document, 'createElement').mockImplementation((tagName: string) => {
      const element = originalCreateElement(tagName);
      if (tagName === 'a') {
        element.click = clickSpy;
      }

      return element;
    });

    const { result } = renderHook(() => useCreateBackup(), {
      wrapper: createWrapper(),
    });

    result.current.mutate('strongpassphrase123');

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data?.filename).toBe('bnkforge-backup-20260414_101010.tar.gz');
    expect(clickSpy).toHaveBeenCalledTimes(1);
    expect(createObjectURLSpy).toHaveBeenCalledTimes(1);
    expect(revokeObjectURLSpy).toHaveBeenCalledWith('blob:backup');
    expect(appendChildSpy).toHaveBeenCalled();
    expect(removeChildSpy).toHaveBeenCalled();

    createElementSpy.mockRestore();
  });
});
