/**
 * Tests for notify.ts notification utility
 *
 * Covers: notify.success/error/warning/info persists to the bell (POST /api/notifications),
 * notifyError routes errors to the bell with action_url for route-based actions,
 * and deploymentNotify persists deployment events to the bell.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { notify, notifyError, deploymentNotify } from '@/lib/notify';

// Mock the notifications API module — this is what notificationsApi.createNotification calls
vi.mock('@/lib/api/notifications', () => ({
  notificationsApi: {
    createNotification: vi.fn(() => Promise.resolve({ data: {} })),
  },
}));

// Mock queryClient so cache invalidation doesn't error
vi.mock('@/lib/queryClient', () => ({
  queryClient: {
    invalidateQueries: vi.fn(),
  },
}));

// Import the mocked notificationsApi AFTER vi.mock is hoisted
import { notificationsApi } from '@/lib/api/notifications';

describe('notify', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('notify.success posts to the bell with correct payload', () => {
    notify.success('Project created', 'Your project is ready');

    expect(notificationsApi.createNotification).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'Project created',
        message: 'Your project is ready',
        severity: 'success',
        type: 'success',
      })
    );
  });

  it('notify.error posts to the bell with correct payload', () => {
    notify.error('Deployment failed', 'Module VPC could not be applied');

    expect(notificationsApi.createNotification).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'Deployment failed',
        message: 'Module VPC could not be applied',
        severity: 'error',
        type: 'error',
      })
    );
  });

  it('notify.warning posts to the bell with correct payload', () => {
    notify.warning('Configuration issue', 'Missing credentials');

    expect(notificationsApi.createNotification).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'Configuration issue',
        message: 'Missing credentials',
        severity: 'warning',
        type: 'warning',
      })
    );
  });

  it('notify.info posts to the bell with correct payload', () => {
    notify.info('Sync started', '5 modules being updated');

    expect(notificationsApi.createNotification).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'Sync started',
        message: '5 modules being updated',
        severity: 'info',
        type: 'info',
      })
    );
  });

  it('bell notification includes category field', () => {
    notify({ title: 'Short', message: 'OK', severity: 'success' });
    expect(notificationsApi.createNotification).toHaveBeenCalledWith(
      expect.objectContaining({ category: 'general' })
    );

    vi.clearAllMocks();

    notify({ title: 'Note', message: 'OK', severity: 'info' });
    expect(notificationsApi.createNotification).toHaveBeenCalledWith(
      expect.objectContaining({ severity: 'info', title: 'Note' })
    );

    vi.clearAllMocks();

    notify({ title: 'Err', message: 'Something broke', severity: 'error' });
    expect(notificationsApi.createNotification).toHaveBeenCalledWith(
      expect.objectContaining({ severity: 'error', title: 'Err' })
    );
  });

  it('bell notification carries action_url when provided', () => {
    notify({
      title: 'Error',
      message: 'Something failed',
      severity: 'error',
      action_url: '/settings',
    });

    expect(notificationsApi.createNotification).toHaveBeenCalledWith(
      expect.objectContaining({
        title: 'Error',
        severity: 'error',
        action_url: '/settings',
      })
    );
  });
});

describe('notifyError', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('parses and sends API error to the bell', () => {
    const error = {
      isAxiosError: true,
      response: {
        status: 500,
        data: { error: { message: 'Internal server error' } },
      },
      message: 'Request failed',
    };

    notifyError(error);

    expect(notificationsApi.createNotification).toHaveBeenCalled();
  });

  it('sends 401 error with action_url to the bell', () => {
    const error = {
      isAxiosError: true,
      response: {
        status: 401,
        data: {},
      },
      message: 'Unauthorized',
    };

    notifyError(error);

    // Bell notification should be created (401 with action route goes to bell)
    expect(notificationsApi.createNotification).toHaveBeenCalledWith(
      expect.objectContaining({
        severity: expect.stringMatching(/warning|error/),
      })
    );
  });

  it('handles plain string errors via the bell', () => {
    notifyError('Something broke');

    expect(notificationsApi.createNotification).toHaveBeenCalled();
  });
});

describe('deploymentNotify', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('started notification persists an info bell entry with module name', () => {
    deploymentNotify.started('vpc', 'apply');

    expect(notificationsApi.createNotification).toHaveBeenCalledWith(
      expect.objectContaining({
        severity: 'info',
        category: 'deployment',
        message: expect.stringContaining('vpc'),
      })
    );
  });

  it('success notification includes resource changes', () => {
    deploymentNotify.success('vpc', 'apply', { add: 3, change: 1, destroy: 0 });

    expect(notificationsApi.createNotification).toHaveBeenCalledWith(
      expect.objectContaining({
        severity: 'success',
        category: 'deployment',
        message: expect.stringContaining('+3 added'),
      })
    );
  });

  it('failed notification shows error message', () => {
    deploymentNotify.failed('vpc', 'apply', 'Terraform plan failed');

    expect(notificationsApi.createNotification).toHaveBeenCalledWith(
      expect.objectContaining({
        severity: 'error',
        category: 'deployment',
        title: expect.stringContaining('Failed'),
        message: expect.stringContaining('vpc'),
      })
    );
  });
});
