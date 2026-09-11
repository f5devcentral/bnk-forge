/**
 * Tests for HealthDetailCard component
 *
 * Tests severity rendering, expansion, pod details table, placement chips,
 * and remediation actions.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@/test/test-utils';
import _userEvent from '@testing-library/user-event';
import { HealthDetailCard } from '../HealthDetailCard';
import type { HealthPodDetail, HealthRemediationAction } from '@/types';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const basePods: HealthPodDetail[] = [
  {
    podName: 'f5-tmm-abc12',
    namespace: 'f5-bnk',
    nodeName: 'worker-1',
    nodeZone: 'us-east-1a',
    nodeInstanceType: 'm5.large',
    phase: 'Running',
    restartCount: 0,
    containersReady: '2/2',
    issue: '',
  },
  {
    podName: 'f5-tmm-def34',
    namespace: 'f5-bnk',
    nodeName: 'worker-2',
    nodeZone: 'us-east-1b',
    nodeInstanceType: 'm5.large',
    phase: 'Running',
    restartCount: 1,
    containersReady: '2/2',
    issue: '',
  },
];

const unhealthyPods: HealthPodDetail[] = [
  {
    podName: 'f5-tmm-crash',
    namespace: 'f5-bnk',
    nodeName: 'worker-3',
    nodeZone: 'us-east-1c',
    nodeInstanceType: 'm5.xlarge',
    phase: 'CrashLoopBackOff',
    restartCount: 12,
    containersReady: '0/2',
    issue: 'CrashLoopBackOff — 12 restarts',
  },
];

const actions: HealthRemediationAction[] = [
  { label: 'View Logs', action: 'view_logs', target: 'f5-tmm-abc12', namespace: 'f5-bnk' },
];

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('HealthDetailCard', () => {
  it('renders collapsed header with name and severity', () => {
    render(
      <HealthDetailCard
        name="TMM (Data Plane)"
        severity="healthy"
        summary="2/2 pods running"
        explanation="Data plane explanation"
        podDetails={basePods}
        remediationActions={actions}
        namespaces={['f5-bnk']}
        zones={['us-east-1a', 'us-east-1b']}
        nodes={['worker-1', 'worker-2']}
        clusterId={1}
      />,
    );

    expect(screen.getByText('TMM (Data Plane)')).toBeInTheDocument();
    expect(screen.getByText('2/2 pods running')).toBeInTheDocument();
    expect(screen.getByText('Healthy')).toBeInTheDocument();
  });

  it('auto-expands when severity is critical', () => {
    render(
      <HealthDetailCard
        name="TMM (Data Plane)"
        severity="critical"
        summary="0/2 pods running"
        explanation="Data plane explanation"
        podDetails={unhealthyPods}
        remediationActions={actions}
        namespaces={['f5-bnk']}
        zones={['us-east-1c']}
        nodes={['worker-3']}
        clusterId={1}
      />,
    );

    expect(screen.getByText('Why this matters')).toBeInTheDocument();
    expect(screen.getByText('1 issue detected')).toBeInTheDocument();
  });

  it('shows pod details table with zone and instance type', async () => {
    const user = _userEvent.setup();
    render(
      <HealthDetailCard
        name="TMM (Data Plane)"
        severity="healthy"
        summary="2/2 pods running"
        explanation="Data plane explanation"
        podDetails={basePods}
        remediationActions={[]}
        namespaces={['f5-bnk']}
        zones={['us-east-1a', 'us-east-1b']}
        nodes={['worker-1', 'worker-2']}
        clusterId={1}
      />,
    );

    const header = screen.getByText('TMM (Data Plane)');
    await user.click(header);

    expect(screen.getByText('Pod Details')).toBeInTheDocument();
    expect(screen.getByText('f5-tmm-abc12')).toBeInTheDocument();
    // Zone/instance type appear in both pod table rows and zone chips.
    expect(screen.getAllByText('us-east-1a').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('us-east-1b').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('m5.large').length).toBeGreaterThanOrEqual(1);
  });

  it('shows placement chips for namespaces, zones, and nodes', async () => {
    const user = _userEvent.setup();
    render(
      <HealthDetailCard
        name="TMM (Data Plane)"
        severity="healthy"
        summary="2/2 pods running"
        explanation="Data plane explanation"
        podDetails={basePods}
        remediationActions={[]}
        namespaces={['f5-bnk']}
        zones={['us-east-1a', 'us-east-1b']}
        nodes={['worker-1', 'worker-2']}
        clusterId={1}
      />,
    );

    const header = screen.getByText('TMM (Data Plane)');
    await user.click(header);

    expect(screen.getByText('Namespaces')).toBeInTheDocument();
    expect(screen.getByText('Availability Zones')).toBeInTheDocument();
    expect(screen.getByText('Nodes')).toBeInTheDocument();
    expect(screen.getByText('f5-bnk')).toBeInTheDocument();
    // worker names also appear in the pod details table; chips make ≥2 matches.
    expect(screen.getAllByText('worker-1').length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText('worker-2').length).toBeGreaterThanOrEqual(1);
  });

  it('hides placement section when no placement data exists', async () => {
    const user = _userEvent.setup();
    render(
      <HealthDetailCard
        name="Gateways"
        severity="healthy"
        summary="1/1 programmed"
        explanation="Gateway explanation"
        podDetails={[]}
        remediationActions={[]}
        namespaces={[]}
        zones={[]}
        nodes={[]}
        clusterId={1}
      />,
    );

    const header = screen.getByText('Gateways');
    await user.click(header);

    expect(screen.queryByText('Namespaces')).not.toBeInTheDocument();
    expect(screen.queryByText('Availability Zones')).not.toBeInTheDocument();
    expect(screen.queryByText('Nodes')).not.toBeInTheDocument();
  });

  it('calls onViewLogs when View Logs action clicked', async () => {
    const user = _userEvent.setup();
    const onViewLogs = vi.fn();
    render(
      <HealthDetailCard
        name="TMM (Data Plane)"
        severity="healthy"
        summary="2/2 pods running"
        explanation="Data plane explanation"
        podDetails={basePods}
        remediationActions={actions}
        namespaces={['f5-bnk']}
        zones={['us-east-1a']}
        nodes={['worker-1']}
        clusterId={1}
        onViewLogs={onViewLogs}
      />,
    );

    const header = screen.getByText('TMM (Data Plane)');
    await user.click(header);

    const viewLogsButton = screen.getByRole('button', { name: /View Logs/i });
    await user.click(viewLogsButton);

    expect(onViewLogs).toHaveBeenCalledWith('f5-tmm-abc12', 'f5-bnk');
  });
});
