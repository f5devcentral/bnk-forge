import React from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

vi.mock('@/lib/notify', () => ({
  notifyError: vi.fn(),
}));

import { notifyError } from '@/lib/notify';

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

describe('useAppMutation', () => {
  beforeEach(() => {
    vi.mocked(notifyError).mockClear();
  });

  it('runs the mutation on success and does not call notifyError', async () => {
    const { result } = renderHook(
      () =>
        useAppMutation({
          mutationFn: async (n: number) => n * 2,
        }),
      { wrapper: createWrapper() },
    );

    result.current.mutate(7);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBe(14);
    expect(notifyError).not.toHaveBeenCalled();
  });

  it('calls notifyError by default when the mutation fails', async () => {
    const err = new Error('boom');
    const { result } = renderHook(
      () =>
        useAppMutation({
          mutationFn: async () => {
            throw err;
          },
        }),
      { wrapper: createWrapper() },
    );

    result.current.mutate();

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(notifyError).toHaveBeenCalledTimes(1);
    expect(vi.mocked(notifyError).mock.calls[0][0]).toBe(err);
  });

  it('forwards errorContext to notifyError', async () => {
    const err = new Error('with-context');
    const errorContext = { resourceType: 'pods' } as never;
    const { result } = renderHook(
      () =>
        useAppMutation({
          mutationFn: async () => {
            throw err;
          },
          errorContext,
        }),
      { wrapper: createWrapper() },
    );

    result.current.mutate();

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(notifyError).toHaveBeenCalledTimes(1);
    const call = vi.mocked(notifyError).mock.calls[0];
    expect(call[0]).toBe(err);
    expect(call[2]).toBe(errorContext);
  });

  it('caller-provided onError overrides the default and suppresses notifyError', async () => {
    const onError = vi.fn();
    const err = new Error('caller-handled');
    const { result } = renderHook(
      () =>
        useAppMutation({
          mutationFn: async () => {
            throw err;
          },
          onError,
        }),
      { wrapper: createWrapper() },
    );

    result.current.mutate();

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0]).toBe(err);
    expect(notifyError).not.toHaveBeenCalled();
  });

  it('silent: true suppresses notifyError when no onError is provided', async () => {
    const { result } = renderHook(
      () =>
        useAppMutation({
          mutationFn: async () => {
            throw new Error('quiet');
          },
          silent: true,
        }),
      { wrapper: createWrapper() },
    );

    result.current.mutate();

    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(notifyError).not.toHaveBeenCalled();
  });

  it('still runs onSuccess when provided', async () => {
    const onSuccess = vi.fn();
    const { result } = renderHook(
      () =>
        useAppMutation({
          mutationFn: async (s: string) => s.toUpperCase(),
          onSuccess,
        }),
      { wrapper: createWrapper() },
    );

    result.current.mutate('hi');

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(onSuccess).toHaveBeenCalledTimes(1);
    expect(onSuccess.mock.calls[0][0]).toBe('HI');
    expect(onSuccess.mock.calls[0][1]).toBe('hi');
  });
});
