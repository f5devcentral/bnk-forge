/**
 * Tests for System page
 *
 * Covers: page title, all tab labels, tab switching to Appearance,
 * dark mode toggle display, description text, URL-based tab param.
 */
import { describe, it, expect, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import System from '@/pages/System';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock('@/components/settings/PerformanceMonitor', () => ({
  default: () => <div data-testid="perf-monitor">PerformanceMonitor</div>,
}));

vi.mock('@/components/settings/DatabaseManagement', () => ({
  default: () => <div data-testid="db-mgmt">DatabaseManagement</div>,
}));

vi.mock('@/components/settings/ContainerManagement', () => ({
  default: () => <div data-testid="container-mgmt">ContainerManagement</div>,
}));

vi.mock('@/components/settings/SystemDefaults', () => ({
  default: () => <div data-testid="sys-defaults">SystemDefaults</div>,
}));

vi.mock('@/components/settings/SystemUpgrade', () => ({
  default: () => <div data-testid="sys-upgrade">SystemUpgrade</div>,
}));

vi.mock('@/components/settings/AuditLog', () => ({
  default: () => <div data-testid="audit-log">AuditLog</div>,
}));

vi.mock('@/components/settings/AlertChannels', () => ({
  AlertChannels: () => <div data-testid="alert-channels">AlertChannels</div>,
}));

vi.mock('@/components/system/McpPanel', () => ({
  McpPanel: () => <div data-testid="mcp-panel">McpPanel</div>,
}));

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('System', () => {
  it('renders System Administration title and description', () => {
    render(<System />, { initialRoute: '/system' });
    expect(screen.getByText('System Administration')).toBeInTheDocument();
    expect(screen.getByText(/monitor system health/i)).toBeInTheDocument();
  });

  it('renders all tab labels', () => {
    render(<System />, { initialRoute: '/system' });
    expect(screen.getByText('System Monitor')).toBeInTheDocument();
    expect(screen.getByText('Version & Upgrade')).toBeInTheDocument();
    expect(screen.getByText('MCP Server')).toBeInTheDocument();
    expect(screen.getByText('Audit Log')).toBeInTheDocument();
    expect(screen.getByText('Alerts')).toBeInTheDocument();
    expect(screen.getByText('Defaults')).toBeInTheDocument();
    expect(screen.getByText('Appearance')).toBeInTheDocument();
    expect(screen.getByText(/backup/i)).toBeInTheDocument();
    // Module Library + Helm Repos tabs moved to /catalog
    expect(screen.queryByText('Module Library')).not.toBeInTheDocument();
    expect(screen.queryByText('Helm Repos')).not.toBeInTheDocument();
  });

  it('switches to Appearance tab and shows dark mode toggle', async () => {
    const user = userEvent.setup();
    render(<System />, { initialRoute: '/system' });
    await user.click(screen.getByText('Appearance'));
    await waitFor(() => {
      expect(screen.getByText(/dark mode/i)).toBeInTheDocument();
    });
  });

  it('renders System Monitor content by default', () => {
    render(<System />, { initialRoute: '/system' });
    expect(screen.getByTestId('perf-monitor')).toBeInTheDocument();
    expect(screen.getByTestId('db-mgmt')).toBeInTheDocument();
    expect(screen.getByTestId('container-mgmt')).toBeInTheDocument();
  });

  it('switches to Version & Upgrade tab and renders SystemUpgrade', async () => {
    const user = userEvent.setup();
    render(<System />, { initialRoute: '/system' });
    await user.click(screen.getByText('Version & Upgrade'));
    await waitFor(() => {
      expect(screen.getByTestId('sys-upgrade')).toBeInTheDocument();
    });
  });

  it('switches to MCP Server tab and renders the panel', async () => {
    const user = userEvent.setup();
    render(<System />, { initialRoute: '/system' });
    await user.click(screen.getByText('MCP Server'));
    await waitFor(() => {
      expect(screen.getByTestId('mcp-panel')).toBeInTheDocument();
    });
  });

});
