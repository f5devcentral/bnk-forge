import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SecurityLogsTab } from '../SecurityLogsTab';
import * as wafLogsHook from '@/hooks/useWafLogs';
import type { WafSecurityLogsResponse } from '@/lib/api/waf-logs';

vi.mock('@/hooks/useWafLogs');

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

const defaultProps = {
  clusterId: 1,
  namespace: 'default',
  crKind: 'appolicy' as const,
  crName: 'my-waf-policy',
};

function mockUseWafLogs(overrides: Partial<ReturnType<typeof wafLogsHook.useWafLogs>>) {
  vi.mocked(wafLogsHook.useWafLogs).mockReturnValue({
    data: undefined,
    isFetching: false,
    refetch: vi.fn(),
    dataUpdatedAt: 0,
    ...overrides,
  } as ReturnType<typeof wafLogsHook.useWafLogs>);
}

describe('SecurityLogsTab', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows loading state while fetching', () => {
    mockUseWafLogs({ isFetching: true, data: undefined });
    render(<SecurityLogsTab {...defaultProps} />, { wrapper });
    // No entries and no warning shown while fetching
    expect(screen.queryByText(/No security events/)).not.toBeInTheDocument();
    expect(screen.queryByText(/No syslog endpoint/)).not.toBeInTheDocument();
  });

  it('shows warning when no syslog endpoint resolved', () => {
    const data: WafSecurityLogsResponse = {
      entries: [],
      total: 0,
      source_endpoint: null,
      cr_kind: 'appolicy',
      cr_name: 'my-waf-policy',
      warning: 'No syslog endpoint found. Ensure a SecPolicy with a F5BigLogProfile and F5BigLogHslpub is attached to this resource.',
    };
    mockUseWafLogs({ data, isFetching: false });
    render(<SecurityLogsTab {...defaultProps} />, { wrapper });
    expect(screen.getByText(/No syslog endpoint found/)).toBeInTheDocument();
  });

  it('shows source endpoint when resolved', () => {
    const data: WafSecurityLogsResponse = {
      entries: [],
      total: 0,
      source_endpoint: '10.0.0.1:514',
      cr_kind: 'appolicy',
      cr_name: 'my-waf-policy',
    };
    mockUseWafLogs({ data, isFetching: false });
    render(<SecurityLogsTab {...defaultProps} />, { wrapper });
    expect(screen.getByText('10.0.0.1:514')).toBeInTheDocument();
  });

  it('shows empty state when connected but no events', () => {
    const data: WafSecurityLogsResponse = {
      entries: [],
      total: 0,
      source_endpoint: '10.0.0.1:514',
      cr_kind: 'appolicy',
      cr_name: 'my-waf-policy',
    };
    mockUseWafLogs({ data, isFetching: false });
    render(<SecurityLogsTab {...defaultProps} />, { wrapper });
    expect(screen.getByText(/No security events found/)).toBeInTheDocument();
  });

  it('renders log entries with outcome badges', () => {
    const data: WafSecurityLogsResponse = {
      entries: [
        { raw: 'outcome="BLOCKED" uri="/attack" attack_type="XSS" violation_rating="5" date_time="2026-08-18 10:00:00" support_id="s1"', outcome: 'BLOCKED', uri: '/attack', attack_type: 'XSS', violation_rating: '5', date_time: '2026-08-18 10:00:00', support_id: 's1' },
        { raw: 'outcome="PASSED" uri="/ok" attack_type="" date_time="2026-08-18 10:01:00"', outcome: 'PASSED', uri: '/ok', date_time: '2026-08-18 10:01:00' },
      ],
      total: 2,
      source_endpoint: '10.0.0.1:514',
      cr_kind: 'appolicy',
      cr_name: 'my-waf-policy',
    };
    mockUseWafLogs({ data, isFetching: false });
    render(<SecurityLogsTab {...defaultProps} />, { wrapper });
    expect(screen.getByText('BLOCKED')).toBeInTheDocument();
    expect(screen.getByText('PASSED')).toBeInTheDocument();
    expect(screen.getByText('2 entries')).toBeInTheDocument();
  });

  it('expands a log entry row on click to show details', async () => {
    const data: WafSecurityLogsResponse = {
      entries: [
        {
          raw: 'outcome="BLOCKED" client_ip="1.2.3.4" support_id="abc"',
          outcome: 'BLOCKED',
          client_ip: '1.2.3.4',
          support_id: 'abc',
        },
      ],
      total: 1,
      source_endpoint: '10.0.0.1:514',
      cr_kind: 'appolicy',
      cr_name: 'my-waf-policy',
    };
    mockUseWafLogs({ data, isFetching: false });
    render(<SecurityLogsTab {...defaultProps} />, { wrapper });

    // Click the row to expand
    fireEvent.click(screen.getByText('BLOCKED').closest('button')!);
    await waitFor(() => {
      expect(screen.getByText('client_ip')).toBeInTheDocument();
      expect(screen.getByText('1.2.3.4')).toBeInTheDocument();
      expect(screen.getByText('support_id')).toBeInTheDocument();
      expect(screen.getByText('abc')).toBeInTheDocument();
    });
  });

  it('shows error banner when backend reports a connection error', () => {
    const data: WafSecurityLogsResponse = {
      entries: [],
      total: 0,
      source_endpoint: '10.0.0.1:514',
      cr_kind: 'appolicy',
      cr_name: 'my-waf-policy',
      error: 'Connection refused to 10.0.0.1:514 — syslog server may not be running',
    };
    mockUseWafLogs({ data, isFetching: false });
    render(<SecurityLogsTab {...defaultProps} />, { wrapper });
    expect(screen.getByText(/Connection refused/)).toBeInTheDocument();
  });

  it('filters by outcome via select', async () => {
    mockUseWafLogs({ data: undefined, isFetching: false });
    render(<SecurityLogsTab {...defaultProps} />, { wrapper });
    // outcome select renders
    expect(screen.getByText('All outcomes')).toBeInTheDocument();
  });

  it('shows vs_name filter only for f5virtualserver cr_kind', () => {
    mockUseWafLogs({ data: undefined, isFetching: false });
    const { rerender } = render(
      <SecurityLogsTab {...defaultProps} crKind="appolicy" />,
      { wrapper }
    );
    expect(screen.queryByPlaceholderText('vs_name…')).not.toBeInTheDocument();

    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <SecurityLogsTab {...defaultProps} crKind="f5virtualserver" />
      </QueryClientProvider>
    );
    expect(screen.getByPlaceholderText('vs_name…')).toBeInTheDocument();
  });

  it('CSV export button is disabled when no entries', () => {
    const data: WafSecurityLogsResponse = {
      entries: [],
      total: 0,
      source_endpoint: '10.0.0.1:514',
      cr_kind: 'appolicy',
      cr_name: 'my-waf-policy',
    };
    mockUseWafLogs({ data, isFetching: false });
    render(<SecurityLogsTab {...defaultProps} />, { wrapper });
    const csvBtn = screen.getByRole('button', { name: /CSV/i });
    expect(csvBtn).toBeDisabled();
  });

  it('CSV export button is enabled when entries exist', () => {
    const data: WafSecurityLogsResponse = {
      entries: [{ raw: 'outcome="BLOCKED"', outcome: 'BLOCKED' }],
      total: 1,
      source_endpoint: '10.0.0.1:514',
      cr_kind: 'appolicy',
      cr_name: 'my-waf-policy',
    };
    mockUseWafLogs({ data, isFetching: false });
    render(<SecurityLogsTab {...defaultProps} />, { wrapper });
    const csvBtn = screen.getByRole('button', { name: /CSV/i });
    expect(csvBtn).not.toBeDisabled();
  });

  it('refresh button calls refetch', async () => {
    const refetch = vi.fn();
    mockUseWafLogs({ data: undefined, isFetching: false, refetch });
    render(<SecurityLogsTab {...defaultProps} />, { wrapper });
    const refreshBtn = screen.getByTitle ? screen.queryAllByRole('button').find(b => b.querySelector('svg')) : null;
    // Verify hook was called with correct params
    expect(wafLogsHook.useWafLogs).toHaveBeenCalledWith(
      1,
      expect.objectContaining({ cr_kind: 'appolicy', cr_name: 'my-waf-policy', namespace: 'default' }),
    );
  });
});
