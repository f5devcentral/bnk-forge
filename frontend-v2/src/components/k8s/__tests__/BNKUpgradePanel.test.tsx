/**
 * Tests for BNKUpgradePanel component
 *
 * Tests current version display, version picker, loading/error states,
 * and upgrade workflow elements.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { BNKUpgradePanel } from '../BNKUpgradePanel';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const mockCurrentVersion = {
  status: 'installed',
  health: 'healthy',
  flo_version: '2.2.0-0.0.11',
  ga_label: 'BNK 2.2',
  helm_release: { name: 'f5-bnk', namespace: 'f5-operator', version: '2.2.0', status: 'deployed' },
  tmm_pods: { pods: 2, running: 2, containers: { total_containers: 4, ready_containers: 4, containers: '4/4 ready' } },
  vlans: [
    { name: 'external', interfaces: ['ens5'], self_ips: ['10.1.1.10'], mtu: null, programmed: true },
    { name: 'internal', interfaces: ['ens6'], self_ips: ['10.2.1.10'], mtu: null, programmed: true },
  ],
};

const mockVersions = {
  current_version: '2.2.0-0.0.11',
  available_versions: [
    { version: '2.2.0-0.0.11', label: 'BNK 2.2 GA', notes: 'Current stable release' },
    { version: '2.3.0-0.0.5', label: 'BNK 2.3 RC1', notes: 'Release candidate' },
    { version: '2.4.0-0.0.1', label: 'BNK 2.4 Beta', notes: 'Beta release' },
  ],
  registry_available: true,
  registry_error: null,
};

const mockHistory = {
  upgrades: [
    {
      id: 1,
      cluster_id: 1,
      from_version: '2.1.0-0.0.8',
      to_version: '2.2.0-0.0.11',
      status: 'completed',
      pre_check_passed: true,
      total_steps: 5,
      current_step: 5,
      rollback_available: false,
      duration_seconds: 180,
      created_at: '2026-02-15T10:00:00Z',
    },
  ],
  total: 1,
};

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();

  server.use(
    // Match full path for BNK upgrade endpoints
    http.get(/\/api\/k8s\/clusters\/\d+\/bnk\/upgrade\/current$/, () => {
      return HttpResponse.json(mockCurrentVersion);
    }),
    http.get(/\/api\/k8s\/clusters\/\d+\/bnk\/upgrade\/versions/, () => {
      return HttpResponse.json(mockVersions);
    }),
    http.get(/\/api\/k8s\/clusters\/\d+\/bnk\/upgrade\/history/, () => {
      return HttpResponse.json(mockHistory);
    }),
  );
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('BNKUpgradePanel', () => {
  it('renders current installation card with health status and version info', async () => {
    render(<BNKUpgradePanel clusterId={1} />);

    // Wait for data to load
    await waitFor(
      () => {
        expect(screen.getByText('Current Installation')).toBeInTheDocument();
      },
      { timeout: 3000 },
    );

    // The health badge shows "healthy"
    expect(screen.getByText('healthy')).toBeInTheDocument();
    // FLO version is displayed
    expect(screen.getByText('2.2.0-0.0.11')).toBeInTheDocument();
  });

  it('shows TMM pods and VLANs in current version card', async () => {
    render(<BNKUpgradePanel clusterId={1} />);

    await waitFor(
      () => {
        expect(screen.getByText('TMM Pods')).toBeInTheDocument();
      },
      { timeout: 3000 },
    );

    expect(screen.getByText(/2\/2 running/)).toBeInTheDocument();
    expect(screen.getByText('VLANs')).toBeInTheDocument();
    expect(screen.getByText(/2\/2 programmed/)).toBeInTheDocument();
  });

  it('renders upgrade target section with version selector and validate button', async () => {
    render(<BNKUpgradePanel clusterId={1} />);

    await waitFor(
      () => {
        // Wait for version data to load
        expect(screen.getByText('Upgrade Target')).toBeInTheDocument();
        expect(screen.getByText('Validate & Plan')).toBeInTheDocument();
      },
      { timeout: 3000 },
    );

    expect(screen.getByText('Select target version...')).toBeInTheDocument();
  });

  it('shows placeholder text when no active upgrade', async () => {
    render(<BNKUpgradePanel clusterId={1} />);

    await waitFor(() => {
      expect(screen.getByText(/Select a target version/)).toBeInTheDocument();
    });
  });

  it('shows loading state while detecting BNK installation', () => {
    server.use(
      http.get(/\/api\/k8s\/clusters\/\d+\/bnk\/upgrade\/current$/, async () => {
        await new Promise((resolve) => setTimeout(resolve, 10000));
        return HttpResponse.json(mockCurrentVersion);
      }),
    );

    render(<BNKUpgradePanel clusterId={1} />);

    expect(screen.getByText('Detecting BNK installation...')).toBeInTheDocument();
  });

  it('shows error state when current version fetch fails', async () => {
    server.use(
      http.get(/\/api\/k8s\/clusters\/\d+\/bnk\/upgrade\/current$/, () => {
        return HttpResponse.json(
          { error: 'Connection failed' },
          { status: 500 },
        );
      }),
    );

    render(<BNKUpgradePanel clusterId={1} />);

    await waitFor(() => {
      expect(screen.getByText(/Failed to detect BNK installation/)).toBeInTheDocument();
    });
  });

  it('shows upgrade history section', async () => {
    render(<BNKUpgradePanel clusterId={1} />);

    await waitFor(() => {
      expect(screen.getByText(/Upgrade History/)).toBeInTheDocument();
    });
  });
});
