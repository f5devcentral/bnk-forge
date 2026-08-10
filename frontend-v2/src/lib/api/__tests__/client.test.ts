/**
 * FU-017: lib/api/client — API client config, interceptors, retry logic
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { apiClient } from '../client';
import { STORAGE_KEYS } from '../../storage-keys';

describe('apiClient', () => {
  beforeEach(() => {
    localStorage.clear();
    // Ensure window.location.href is a valid absolute URL for MSW URL resolution
    Object.defineProperty(window, 'location', {
      value: { href: 'http://localhost:3000/' },
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => {
    localStorage.clear();
  });

  it('has correct default Content-Type header', () => {
    expect(apiClient.defaults.headers['Content-Type']).toBe('application/json');
  });

  it('attaches auth token from localStorage', async () => {
    localStorage.setItem(STORAGE_KEYS.AUTH_TOKEN, 'test-token-123');

    let capturedAuth = '';
    server.use(
      http.get('*/api/test-auth', ({ request }) => {
        capturedAuth = request.headers.get('Authorization') || '';
        return HttpResponse.json({ ok: true });
      })
    );

    await apiClient.get('/api/test-auth');
    expect(capturedAuth).toBe('Bearer test-token-123');
  });

  it('does not attach auth header when no token', async () => {
    let capturedAuth: string | null = '';
    server.use(
      http.get('*/api/test-no-auth', ({ request }) => {
        capturedAuth = request.headers.get('Authorization');
        return HttpResponse.json({ ok: true });
      })
    );

    await apiClient.get('/api/test-no-auth');
    expect(capturedAuth).toBeNull();
  });

  it('clears token on 401 for non-login requests', async () => {
    localStorage.setItem(STORAGE_KEYS.AUTH_TOKEN, 'expired-token');

    server.use(
      http.get('*/api/protected-resource', () => {
        return HttpResponse.json(
          { error: { code: 'UNAUTHORIZED', message: 'Token expired' } },
          { status: 401 }
        );
      })
    );

    await expect(apiClient.get('/api/protected-resource')).rejects.toThrow();
    expect(localStorage.getItem(STORAGE_KEYS.AUTH_TOKEN)).toBeNull();
  });

  it('does not clear token on 401 for login requests', async () => {
    server.use(
      http.post('*/api/auth/login', () => {
        return HttpResponse.json(
          { error: { code: 'UNAUTHORIZED', message: 'Bad credentials' } },
          { status: 401 }
        );
      })
    );

    localStorage.setItem(STORAGE_KEYS.AUTH_TOKEN, 'some-token');
    await expect(apiClient.post('/api/auth/login', { username: 'bad', password: 'bad' })).rejects.toThrow();
    expect(localStorage.getItem(STORAGE_KEYS.AUTH_TOKEN)).toBe('some-token');
  });

  it('does not clear token on non-auth 401 responses', async () => {
    localStorage.setItem(STORAGE_KEYS.AUTH_TOKEN, 'still-valid-session-token');

    server.use(
      http.get('*/api/k8s/clusters/5/resources/pod', () => {
        return HttpResponse.json(
          { error: { code: 'K8S_API_ERROR', message: 'Unauthorized' } },
          { status: 401 }
        );
      })
    );

    await expect(apiClient.get('/api/k8s/clusters/5/resources/pod')).rejects.toThrow();
    expect(localStorage.getItem(STORAGE_KEYS.AUTH_TOKEN)).toBe('still-valid-session-token');
  });

  it('returns data on successful request', async () => {
    server.use(
      http.get('*/api/test-success', () => {
        return HttpResponse.json({ message: 'hello' });
      })
    );

    const response = await apiClient.get('/api/test-success');
    expect(response.data).toEqual({ message: 'hello' });
  });

  // ---------------------------------------------------------------------------
  // Structured-error predicate: these reject immediately, no timer manipulation needed
  // ---------------------------------------------------------------------------

  it('502 with structured ApiError body skips retry immediately', async () => {
    let callCount = 0;
    server.use(
      http.get('*/api/retry-test-skip', () => {
        callCount += 1;
        return HttpResponse.json(
          { error: { code: 'INVALID_INPUT', message: 'bad request' } },
          { status: 502 }
        );
      })
    );

    await expect(apiClient.get('/api/retry-test-skip')).rejects.toThrow();
    // Should have only been called once — no retries fired.
    expect(callCount).toBe(1);
  });

  it('500 with structured ApiError body skips retry immediately', async () => {
    let callCount = 0;
    server.use(
      http.get('*/api/retry-test-500-skip', () => {
        callCount += 1;
        return HttpResponse.json(
          { error: { code: 'INTERNAL_ERROR', message: 'something went wrong' } },
          { status: 500 }
        );
      })
    );

    await expect(apiClient.get('/api/retry-test-500-skip')).rejects.toThrow();
    // 500 is not in RETRYABLE_STATUS_CODES; predicate path not exercised,
    // but no retry should occur regardless.
    expect(callCount).toBe(1);
  });

  it('network error (no response object) flows through existing retry path', async () => {
    server.use(
      http.get('*/api/retry-test-network', () => {
        return HttpResponse.error();
      })
    );

    // Network errors have no response object; the predicate must not crash or
    // accidentally short-circuit on undefined — the error should propagate cleanly.
    await expect(apiClient.get('/api/retry-test-network')).rejects.toThrow();
  });

  // ---------------------------------------------------------------------------
  // Retry path: use fake timers to advance exponential-backoff delays
  // ---------------------------------------------------------------------------

  describe('retry with backoff', () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it('502 with no body still retries', async () => {
      let callCount = 0;
      server.use(
        http.get('*/api/retry-test-nodata', () => {
          callCount += 1;
          if (callCount === 1) {
            return new HttpResponse(null, { status: 502 });
          }
          return HttpResponse.json({ ok: true });
        })
      );

      // Drive the exponential-backoff setTimeout so the retry fires.
      const promise = apiClient.get('/api/retry-test-nodata');
      await vi.runAllTimersAsync();
      const response = await promise;

      expect(response.data).toEqual({ ok: true });
      expect(callCount).toBeGreaterThanOrEqual(2);
    });

    it('502 with empty error.code still retries', async () => {
      let callCount = 0;
      server.use(
        http.get('*/api/retry-test-emptycode', () => {
          callCount += 1;
          if (callCount === 1) {
            return HttpResponse.json(
              { error: { code: '', message: 'something broke' } },
              { status: 502 }
            );
          }
          return HttpResponse.json({ ok: true });
        })
      );

      const promise = apiClient.get('/api/retry-test-emptycode');
      await vi.runAllTimersAsync();
      const response = await promise;

      expect(response.data).toEqual({ ok: true });
      expect(callCount).toBeGreaterThanOrEqual(2);
    });
  });
});
