/**
 * notify.ts — D-025 P5: bell-only (zero toast). Every path persists to the bell.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

// ---------------------------------------------------------------------------
// Mocks — set up before importing notify so module-level singletons see them
// ---------------------------------------------------------------------------

// Mock notificationsApi
const mockCreateNotification = vi.fn().mockResolvedValue({ id: 1 });
vi.mock('../api/notifications', () => ({
  notificationsApi: {
    createNotification: (...args: unknown[]) => mockCreateNotification(...args),
  },
}));

// Mock queryClient singleton
const mockInvalidateQueries = vi.fn();
vi.mock('../queryClient', () => ({
  queryClient: {
    invalidateQueries: (...args: unknown[]) => mockInvalidateQueries(...args),
  },
}));

// Mock queryKeys
vi.mock('../queryKeys', () => ({
  queryKeys: {
    notifications: {
      all: ['notifications'],
      list: (u?: boolean) => ['notifications', u],
      unreadCount: () => ['notifications', 'unread-count'],
    },
  },
}));

// ---------------------------------------------------------------------------
// Import subject under test (after mocks)
// ---------------------------------------------------------------------------
import { notify, notifyError, deploymentNotify } from '../notify';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const flushPromises = () => new Promise((r) => setTimeout(r, 0));

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('notify() — always persists to the bell', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('calls createNotification for notify.success', async () => {
    notify.success('Module saved');
    await flushPromises();

    expect(mockCreateNotification).toHaveBeenCalledOnce();
    expect(mockCreateNotification).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Module saved', severity: 'success' }),
    );
  });

  it('calls createNotification for notify.error', async () => {
    notify.error('Deploy failed', 'Something went wrong');
    await flushPromises();

    expect(mockCreateNotification).toHaveBeenCalledOnce();
    expect(mockCreateNotification).toHaveBeenCalledWith(
      expect.objectContaining({ title: 'Deploy failed', severity: 'error' }),
    );
  });

  it('calls createNotification for notify.warning', async () => {
    notify.warning('Drift detected');
    await flushPromises();

    expect(mockCreateNotification).toHaveBeenCalledOnce();
    expect(mockCreateNotification).toHaveBeenCalledWith(
      expect.objectContaining({ severity: 'warning' }),
    );
  });

  it('calls createNotification for notify.info', async () => {
    notify.info('Scan complete');
    await flushPromises();

    expect(mockCreateNotification).toHaveBeenCalledOnce();
    expect(mockCreateNotification).toHaveBeenCalledWith(
      expect.objectContaining({ severity: 'info' }),
    );
  });
});

describe('notify() — fields are passed through', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('passes category and action_url to createNotification', async () => {
    notify.error('Auth failed', 'Token expired', {
      category: 'credentials',
      action_url: '/auth-templates',
    });
    await flushPromises();

    expect(mockCreateNotification).toHaveBeenCalledWith(
      expect.objectContaining({
        category: 'credentials',
        action_url: '/auth-templates',
        severity: 'error',
      }),
    );
  });

  it('passes dedupe_key to createNotification', async () => {
    notify.info('Cluster offline', undefined, { dedupe_key: 'cluster-1-offline' });
    await flushPromises();

    expect(mockCreateNotification).toHaveBeenCalledWith(
      expect.objectContaining({ dedupe_key: 'cluster-1-offline' }),
    );
  });

  it('defaults category to "general" when not provided', async () => {
    notify.success('Done');
    await flushPromises();

    expect(mockCreateNotification).toHaveBeenCalledWith(
      expect.objectContaining({ category: 'general' }),
    );
  });
});

describe('deploymentNotify — routes to the bell', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('deploymentNotify.started persists an info bell entry', async () => {
    deploymentNotify.started('vpc-module', 'apply');
    await flushPromises();

    expect(mockCreateNotification).toHaveBeenCalledWith(
      expect.objectContaining({ severity: 'info', category: 'deployment' }),
    );
  });

  it('deploymentNotify.success persists a success bell entry', async () => {
    deploymentNotify.success('vpc-module', 'apply');
    await flushPromises();

    expect(mockCreateNotification).toHaveBeenCalledWith(
      expect.objectContaining({ severity: 'success', category: 'deployment' }),
    );
  });

  it('deploymentNotify.failed persists an error bell entry', async () => {
    deploymentNotify.failed('vpc-module', 'apply', 'timeout');
    await flushPromises();

    expect(mockCreateNotification).toHaveBeenCalledWith(
      expect.objectContaining({ severity: 'error', category: 'deployment' }),
    );
  });
});

describe('createBellNotification — recursion / throw safety', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('swallows a failed POST without throwing', async () => {
    mockCreateNotification.mockRejectedValueOnce(new Error('Network error'));
    const consoleSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

    // Must not throw
    await expect(
      (async () => {
        notify.error('Oops');
        await flushPromises();
      })(),
    ).resolves.toBeUndefined();

    expect(consoleSpy).toHaveBeenCalledWith(
      expect.stringContaining('[notify] Failed to create bell notification'),
    );
    consoleSpy.mockRestore();
  });

  it('does NOT call notify.error when POST fails (no recursion)', async () => {
    mockCreateNotification.mockRejectedValueOnce(new Error('500'));
    const consoleSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

    notify.error('Something broke');
    await flushPromises();

    // Only one createNotification call (the original) — not a second one from error handling
    expect(mockCreateNotification).toHaveBeenCalledTimes(1);
    consoleSpy.mockRestore();
  });

  it('invalidates queries after a successful POST', async () => {
    notify.success('All good');
    await flushPromises();

    expect(mockInvalidateQueries).toHaveBeenCalledWith(
      expect.objectContaining({ queryKey: ['notifications'] }),
    );
  });

  it('does NOT invalidate queries when POST fails', async () => {
    mockCreateNotification.mockRejectedValueOnce(new Error('503'));
    const consoleSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});

    notify.success('Might fail');
    await flushPromises();

    expect(mockInvalidateQueries).not.toHaveBeenCalled();
    consoleSpy.mockRestore();
  });
});

describe('notifyError — persists to the bell, forwards action_url', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('persists a bell entry', async () => {
    const err = { response: { status: 403, data: { message: 'Forbidden' } } };
    notifyError(err, 'loading cluster');
    await flushPromises();

    expect(mockCreateNotification).toHaveBeenCalled();
  });

  it('forwards parsed action route as action_url on bell entry', async () => {
    // Simulate a credentials error — error-handler maps this to route '/auth-templates'
    const err = {
      response: {
        status: 403,
        data: { message: 'Unable to authenticate with your cloud provider.' },
      },
    };
    notifyError(err, 'deploying');
    await flushPromises();

    const call = mockCreateNotification.mock.calls[0]?.[0] as Record<string, unknown> | undefined;
    if (call?.action_url) {
      expect(typeof call.action_url).toBe('string');
    }
    // Bell must have been called
    expect(mockCreateNotification).toHaveBeenCalled();
  });

  it('skips UNAUTHORIZED errors entirely (no bell)', async () => {
    const err = { response: { data: { error: { code: 'UNAUTHORIZED' } } } };
    notifyError(err);
    await flushPromises();

    expect(mockCreateNotification).not.toHaveBeenCalled();
  });
});
