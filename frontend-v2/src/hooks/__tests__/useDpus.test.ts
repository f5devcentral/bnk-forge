/**
 * Tests for per-project DPU hooks.
 *
 * CT-012: MSW handlers return the real backend response shape
 * (mirrors backend/schemas/dpu.py) and capture request bodies to assert
 * payload shape.
 */
import { describe, it, expect } from 'vitest';
import React from 'react';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import {
  useCreateDpu,
  useDeleteDpu,
  useDiscoverAllDpus,
  useDpuSettings,
  useDpus,
  useProbeDpuOs,
  useProbeDpuOsAll,
  useUpdateDpu,
  useUpdateDpuSettings,
} from '@/hooks/useDpus';

function createWrapper() {
  const qc = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client: qc }, children);
  };
}

const PROJECT = 7;

const sampleDpu = {
  id: 1,
  project_id: PROJECT,
  name: 'dpu-1',
  bmc_ip: '192.168.68.100',
  bmc_username: 'root',
  has_bmc_password: true,
  serial_number: 'MT2428XZ0N1D',
  oob0_mac: '5c:25:73:e6:38:68',
  oob0_ipv4: 'dhcp',
  external_vlan_id: null,
  external_vlan_ipv4: null,
  external_vlan_ipv6: null,
  internal_vlan_id: null,
  internal_vlan_ipv4: null,
  internal_vlan_ipv6: null,
  storage_vlan_id: null,
  storage_vlan_ipv4: null,
  storage_vlan_ipv6: null,
  last_discovery_at: null,
  last_discovery_status: null,
  last_discovery_error: null,
  dpu_os_ip: null,
  dpu_os_reachable: null,
  created_at: '2026-04-18T00:00:00Z',
  updated_at: '2026-04-18T00:00:00Z',
};

const sampleSettings = {
  id: 1,
  project_id: PROJECT,
  bluefield_image_id: null,
  high_speed_mtu: null,
  high_speed_lag: false,
  dns_servers: ['1.1.1.1', '8.8.8.8'],
  default_oob_username: null,
  default_os_username: null,
  has_default_oob_password: false,
  has_default_os_password: false,
  created_at: '2026-04-18T00:00:00Z',
  updated_at: '2026-04-18T00:00:00Z',
};

describe('useDpus', () => {
  it('fetches the list of DPUs wrapped in { dpus: [...] }', async () => {
    server.use(
      http.get('*/api/projects/7/dpus', () =>
        HttpResponse.json({ dpus: [sampleDpu] }),
      ),
    );

    const { result } = renderHook(() => useDpus(PROJECT), {
      wrapper: createWrapper(),
    });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.dpus).toHaveLength(1);
    expect(result.current.data?.dpus[0].serial_number).toBe('MT2428XZ0N1D');
  });
});

describe('useCreateDpu', () => {
  it('POSTs to /api/projects/:id/dpus and captures the payload', async () => {
    let captured: unknown = null;
    server.use(
      http.post('*/api/projects/7/dpus', async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json(sampleDpu, { status: 201 });
      }),
    );

    const { result } = renderHook(() => useCreateDpu(PROJECT), {
      wrapper: createWrapper(),
    });

    result.current.mutate({
      bmc_ip: '192.168.68.100',
      bmc_username: 'root',
      bmc_password: 'secret',
      oob0_ipv4: 'dhcp',
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(captured).toMatchObject({
      bmc_ip: '192.168.68.100',
      bmc_username: 'root',
      bmc_password: 'secret',
      oob0_ipv4: 'dhcp',
    });
  });
});

describe('useUpdateDpu', () => {
  it('PATCHes only the provided fields', async () => {
    let captured: unknown = null;
    server.use(
      http.patch('*/api/projects/7/dpus/1', async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ ...sampleDpu, name: 'renamed' });
      }),
    );

    const { result } = renderHook(() => useUpdateDpu(PROJECT), {
      wrapper: createWrapper(),
    });

    result.current.mutate({ dpuId: 1, data: { name: 'renamed' } });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(captured).toEqual({ name: 'renamed' });
  });
});

describe('useDeleteDpu', () => {
  it('DELETEs and resolves', async () => {
    server.use(
      http.delete('*/api/projects/7/dpus/1', () => new HttpResponse(null, { status: 204 })),
    );
    const { result } = renderHook(() => useDeleteDpu(PROJECT), {
      wrapper: createWrapper(),
    });
    result.current.mutate(1);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
  });
});

describe('bulk + DPU-OS hooks', () => {
  it('useDiscoverAllDpus POSTs to /dpus/discover-all', async () => {
    let called = false;
    server.use(
      http.post('*/api/projects/7/dpus/discover-all', () => {
        called = true;
        return HttpResponse.json({ enqueued: 3, max_parallel: 10, tasks: [] }, { status: 202 });
      }),
    );
    const { result } = renderHook(() => useDiscoverAllDpus(PROJECT), {
      wrapper: createWrapper(),
    });
    result.current.mutate();
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(called).toBe(true);
    expect(result.current.data?.enqueued).toBe(3);
  });

  it('useProbeDpuOs POSTs to /dpus/:id/probe-dpu-os', async () => {
    server.use(
      http.post('*/api/projects/7/dpus/42/probe-dpu-os', () =>
        HttpResponse.json({ dpu_id: 42, task_id: 't-1', status: 'running' }, { status: 202 }),
      ),
    );
    const { result } = renderHook(() => useProbeDpuOs(PROJECT), {
      wrapper: createWrapper(),
    });
    result.current.mutate(42);
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.dpu_id).toBe(42);
  });

  it('useProbeDpuOsAll POSTs to /dpus/probe-dpu-os-all', async () => {
    server.use(
      http.post('*/api/projects/7/dpus/probe-dpu-os-all', () =>
        HttpResponse.json({ enqueued: 2, max_parallel: 10, tasks: [] }, { status: 202 }),
      ),
    );
    const { result } = renderHook(() => useProbeDpuOsAll(PROJECT), {
      wrapper: createWrapper(),
    });
    result.current.mutate();
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.enqueued).toBe(2);
  });
});

describe('useDpuSettings / useUpdateDpuSettings', () => {
  it('fetches settings and PATCHes changes', async () => {
    server.use(
      http.get('*/api/projects/7/dpu-settings', () => HttpResponse.json(sampleSettings)),
    );
    const { result } = renderHook(() => useDpuSettings(PROJECT), {
      wrapper: createWrapper(),
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.dns_servers).toEqual(['1.1.1.1', '8.8.8.8']);
  });

  it('PATCH preserves partial update semantics', async () => {
    let captured: unknown = null;
    server.use(
      http.patch('*/api/projects/7/dpu-settings', async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json({ ...sampleSettings, high_speed_mtu: 9000 });
      }),
    );
    const { result } = renderHook(() => useUpdateDpuSettings(PROJECT), {
      wrapper: createWrapper(),
    });
    result.current.mutate({ high_speed_mtu: 9000 });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(captured).toEqual({ high_speed_mtu: 9000 });
  });
});
