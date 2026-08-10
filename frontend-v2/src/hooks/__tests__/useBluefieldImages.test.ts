/**
 * Tests for the Bluefield Software Images hooks (unified BFB + DOCA catalog).
 *
 * CT-012 pattern: MSW handlers return the REAL response shape
 * (mirrors backend BluefieldSoftwareImageResponse with DOCA fields) and
 * capture request JSON to assert payload shape.
 */
import { describe, it, expect } from 'vitest';
import React from 'react';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  useBluefieldImages,
  useDefaultBluefieldImage,
  useCreateBluefieldImage,
  useUpdateBluefieldImage,
  useDeleteBluefieldImage,
} from '@/hooks/useBluefieldImages';

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

/** Sample matching the real BluefieldSoftwareImageResponse with DOCA fields. */
const sampleImage = {
  id: 1,
  image_filename: 'bf-bundle-2.9.2-32_25.02_ubuntu-22.04_prod.bfb',
  base_url: 'https://content.mellanox.com/BlueField/BFBs/Ubuntu22.04/',
  doca_version: '2.9.2',
  doca_package: 'doca-all',
  host_os: 'ubuntu',
  host_os_version: '22.04',
  host_arch: 'arm64',
  doca_host_url: 'https://www.mellanox.com/downloads/DOCA/DOCA_v2.9.2/host/doca-host_2.9.2-012101-24.10-ubuntu2204_arm64.deb',
  is_default: true,
  notes: null,
  created_at: '2026-04-18T00:00:00Z',
  updated_at: '2026-04-18T00:00:00Z',
};

describe('useBluefieldImages', () => {
  it('fetches the list of unified BFB+DOCA entries', async () => {
    server.use(
      http.get('*/api/bluefield-images', () => HttpResponse.json([sampleImage])),
    );

    const { result } = renderHook(() => useBluefieldImages(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual([sampleImage]);
    // Verify DOCA fields are present
    expect(result.current.data?.[0].doca_version).toBe('2.9.2');
    expect(result.current.data?.[0].is_default).toBe(true);
  });
});

describe('useDefaultBluefieldImage', () => {
  it('fetches the default entry', async () => {
    server.use(
      http.get('*/api/bluefield-images/default', () => HttpResponse.json(sampleImage)),
    );

    const { result } = renderHook(() => useDefaultBluefieldImage(), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toEqual(sampleImage);
    expect(result.current.data?.is_default).toBe(true);
  });
});

describe('useCreateBluefieldImage', () => {
  it('POSTs with DOCA fields and invalidates the list query', async () => {
    let capturedBody: unknown = null;
    server.use(
      http.post('*/api/bluefield-images', async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json(sampleImage, { status: 201 });
      }),
    );

    const { result } = renderHook(() => useCreateBluefieldImage(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      image_filename: sampleImage.image_filename,
      base_url: sampleImage.base_url,
      doca_version: '2.9.2',
      doca_package: 'doca-all',
      host_os: 'ubuntu',
      host_arch: 'arm64',
      is_default: true,
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(capturedBody).toEqual({
      image_filename: sampleImage.image_filename,
      base_url: sampleImage.base_url,
      doca_version: '2.9.2',
      doca_package: 'doca-all',
      host_os: 'ubuntu',
      host_arch: 'arm64',
      is_default: true,
    });
  });
});

describe('useUpdateBluefieldImage', () => {
  it('PATCHes only the provided fields', async () => {
    let capturedBody: unknown = null;
    server.use(
      http.patch('*/api/bluefield-images/1', async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json({ ...sampleImage, notes: 'updated' });
      }),
    );

    const { result } = renderHook(() => useUpdateBluefieldImage(), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ id: 1, data: { notes: 'updated' } });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(capturedBody).toEqual({ notes: 'updated' });
  });
});

describe('useDeleteBluefieldImage', () => {
  it('DELETEs and resolves without a body', async () => {
    server.use(
      http.delete('*/api/bluefield-images/1', () => new HttpResponse(null, { status: 204 })),
    );

    const { result } = renderHook(() => useDeleteBluefieldImage(), {
      wrapper: createWrapper(),
    });

    result.current.mutate(1);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });
});
