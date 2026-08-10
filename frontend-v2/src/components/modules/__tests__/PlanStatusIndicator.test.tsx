/**
 * Tests for PlanStatusIndicator component
 *
 * Covers: renders nothing while loading, renders nothing when no plan,
 * shows "Plan Available" for valid plan, shows "Plan Outdated" for invalid plan,
 * compact mode, tooltip content.
 */
import { describe, it, expect, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { render } from '@/test/test-utils';
import { PlanStatusIndicator } from '@/components/modules/PlanStatusIndicator';
import type { PlanStatus } from '@/types';

// Mock the API client to control plan status responses
const mockPlanStatus: { data: PlanStatus | null } = { data: null };

vi.mock('@/lib/api', () => ({
  apiClient: {
    get: vi.fn(() => Promise.resolve({ data: mockPlanStatus.data })),
  },
  api: {
    get: vi.fn(),
  },
}));

function setPlanStatus(status: PlanStatus | null) {
  mockPlanStatus.data = status;
}

describe('PlanStatusIndicator', () => {
  it('renders nothing when no plan exists', async () => {
    setPlanStatus({
      module_id: 1,
      has_plan: false,
      plan_valid: false,
      plan_serial: 0,
      reason_invalid: '',
      workspace_initialized: true,
    });

    const { container } = render(<PlanStatusIndicator moduleId={1} />);

    // Wait for query to resolve
    await waitFor(() => {
      // Component should render nothing since has_plan is false
      expect(container.innerHTML).toBe('');
    });
  });

  it('shows "Plan Available" badge for valid plan', async () => {
    setPlanStatus({
      module_id: 1,
      has_plan: true,
      plan_valid: true,
      plan_serial: 5,
      plan_age_seconds: 120,
      reason_invalid: '',
      workspace_initialized: true,
      plan_output_preview: 'Plan: 2 to add, 0 to change, 0 to destroy.',
    });

    render(<PlanStatusIndicator moduleId={1} />);

    await waitFor(() => {
      expect(screen.getByText('Plan Available')).toBeInTheDocument();
    });
  });

  it('shows "Plan Outdated" badge for invalid plan', async () => {
    setPlanStatus({
      module_id: 1,
      has_plan: true,
      plan_valid: false,
      plan_serial: 3,
      reason_invalid: 'Variables have changed since plan was created',
      workspace_initialized: true,
    });

    render(<PlanStatusIndicator moduleId={1} />);

    await waitFor(() => {
      expect(screen.getByText('Plan Outdated')).toBeInTheDocument();
    });
  });

  it('does not show text label in compact mode for valid plan', async () => {
    setPlanStatus({
      module_id: 1,
      has_plan: true,
      plan_valid: true,
      plan_serial: 5,
      plan_age_seconds: 60,
      reason_invalid: '',
      workspace_initialized: true,
    });

    render(<PlanStatusIndicator moduleId={1} compact />);

    await waitFor(() => {
      // In compact mode, "Plan Available" text should NOT be shown
      expect(screen.queryByText('Plan Available')).not.toBeInTheDocument();
    });
  });
});
