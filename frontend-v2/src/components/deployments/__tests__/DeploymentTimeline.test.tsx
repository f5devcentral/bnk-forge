/**
 * Tests for DeploymentTimeline component
 *
 * Covers: empty state, renders events with module names and statuses,
 * shows timestamps and optional duration/user, renders correct status badges.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import { render } from '@/test/test-utils';
import { DeploymentTimeline } from '../DeploymentTimeline';

const sampleEvents = [
  {
    id: 1,
    moduleName: 'vpc',
    status: 'deployed' as const,
    timestamp: '2026-02-18 10:05',
    duration: '4m 59s',
    user: 'admin',
  },
  {
    id: 2,
    moduleName: 'eks',
    status: 'failed' as const,
    timestamp: '2026-02-18 10:10',
    duration: '2m 30s',
  },
  {
    id: 3,
    moduleName: 'rds',
    status: 'deploying' as const,
    timestamp: '2026-02-18 10:15',
  },
  {
    id: 4,
    moduleName: 'iam',
    status: 'pending' as const,
    timestamp: '2026-02-18 10:20',
  },
];

describe('DeploymentTimeline', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows empty state when no events are provided', () => {
    render(<DeploymentTimeline />);

    expect(screen.getByText('No deployment history yet')).toBeInTheDocument();
  });

  it('renders the card title and description', () => {
    render(<DeploymentTimeline events={sampleEvents} />);

    expect(screen.getByText('Deployment timeline')).toBeInTheDocument();
    expect(
      screen.getByText('Recent deployment activity across all modules.')
    ).toBeInTheDocument();
  });

  it('renders all events with module names, statuses, and timestamps', () => {
    render(<DeploymentTimeline events={sampleEvents} />);

    // Module names
    expect(screen.getByText('vpc')).toBeInTheDocument();
    expect(screen.getByText('eks')).toBeInTheDocument();
    expect(screen.getByText('rds')).toBeInTheDocument();
    expect(screen.getByText('iam')).toBeInTheDocument();

    // Status badges
    expect(screen.getByText('deployed')).toBeInTheDocument();
    expect(screen.getByText('failed')).toBeInTheDocument();
    expect(screen.getByText('deploying')).toBeInTheDocument();
    expect(screen.getByText('pending')).toBeInTheDocument();

    // Timestamps
    expect(screen.getByText('2026-02-18 10:05')).toBeInTheDocument();
    expect(screen.getByText('2026-02-18 10:10')).toBeInTheDocument();
  });

  it('shows duration and user when provided', () => {
    render(<DeploymentTimeline events={sampleEvents} />);

    // Duration shown for first two events
    expect(screen.getByText('Duration: 4m 59s')).toBeInTheDocument();
    expect(screen.getByText('Duration: 2m 30s')).toBeInTheDocument();

    // User shown only for first event
    expect(screen.getByText('by admin')).toBeInTheDocument();
  });

  it('renders with an empty array (shows empty state)', () => {
    render(<DeploymentTimeline events={[]} />);

    expect(screen.getByText('No deployment history yet')).toBeInTheDocument();
  });
});
