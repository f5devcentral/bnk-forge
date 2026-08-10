import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { DriftDetailPanel } from '../DriftDetailPanel';
import type { DriftCheck } from '@/types';

// Mock useTheme to control dark/light mode
vi.mock('@/context/ThemeContext', () => ({
  useTheme: () => ({ isDark: false, theme: 'light', setTheme: vi.fn() }),
  ThemeProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

beforeEach(() => {
  vi.clearAllMocks();
});

const mockDriftCheck: DriftCheck = {
  id: 1,
  project_id: 1,
  module_id: 10,
  module_name: 'vpc-module',
  schedule_enabled: true,
  last_check_at: '2026-02-18T09:00:00Z',
  drift_detected: true,
  drift_summary: '+ aws_subnet.new\n- aws_route.old\n~ aws_security_group.main',
  drift_details: {
    drift_detected: true,
    resource_changes: { add: 1, change: 1, destroy: 1 },
    changed_resources: [
      { address: 'aws_subnet.new', action: 'create' },
      { address: 'aws_security_group.main', action: 'update' },
      { address: 'aws_route.old', action: 'delete' },
    ],
    summary: 'Plan: 1 to add, 1 to change, 1 to destroy',
  },
  status: 'completed',
  created_at: '2026-02-18T08:50:00Z',
  updated_at: '2026-02-18T09:00:00Z',
};

describe('DriftDetailPanel', () => {
  it('shows "No drift check selected" when no data provided', () => {
    render(<DriftDetailPanel />);

    expect(screen.getByText('No drift check selected')).toBeInTheDocument();
  });

  it('renders drift check details with module name and severity badge', () => {
    render(
      <DriftDetailPanel
        driftCheck={mockDriftCheck}
        projectId={1}
        moduleId={10}
      />
    );

    expect(screen.getByText('vpc-module')).toBeInTheDocument();
    expect(screen.getByText('Drift Detected')).toBeInTheDocument();
    expect(screen.getByText('Medium Severity')).toBeInTheDocument();
  });

  it('renders resource changes with correct counts', () => {
    render(
      <DriftDetailPanel
        driftCheck={mockDriftCheck}
        projectId={1}
        moduleId={10}
      />
    );

    expect(screen.getByText('3 resources affected')).toBeInTheDocument();
    expect(screen.getByText('+1 to add')).toBeInTheDocument();
    expect(screen.getByText('~1 to change')).toBeInTheDocument();
    expect(screen.getByText('-1 to destroy')).toBeInTheDocument();
  });
});
