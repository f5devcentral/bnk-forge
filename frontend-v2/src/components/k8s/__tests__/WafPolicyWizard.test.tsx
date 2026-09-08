/**
 * Tests for WafPolicyWizard — step navigation, validation gating, and submit.
 * Hooks are mocked since useWafPolicies is already tested separately.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { WafPolicyWizard } from '../WafPolicyWizard';
import {
  useCreateWafPolicy,
  useCreateWafLogConf,
  useDeleteWafPolicy,
  useWafPolicies,
  useWafLogConfs,
} from '@/hooks/useWafPolicies';

vi.mock('@/hooks/useWafPolicies', () => ({
  useCreateWafPolicy:  vi.fn(),
  useCreateWafLogConf: vi.fn(),
  useDeleteWafPolicy:  vi.fn(),
  useWafPolicies:      vi.fn(),
  useWafLogConfs:      vi.fn(),
}));

function mockMutation(overrides: Record<string, unknown> = {}) {
  return { mutate: vi.fn(), mutateAsync: vi.fn().mockResolvedValue({}), isPending: false, ...overrides };
}

function mockQuery<T>(data: T) {
  return { data, isLoading: false, isError: false, error: null };
}

describe('WafPolicyWizard', () => {
  beforeEach(() => {
    vi.mocked(useCreateWafPolicy).mockReturnValue(mockMutation() as never);
    vi.mocked(useCreateWafLogConf).mockReturnValue(mockMutation() as never);
    vi.mocked(useDeleteWafPolicy).mockReturnValue(mockMutation() as never);
    vi.mocked(useWafPolicies).mockReturnValue(mockQuery({ policies: [], count: 0 }) as never);
    vi.mocked(useWafLogConfs).mockReturnValue(mockQuery({ log_confs: [], count: 0 }) as never);
  });

  it('starts on the Basics step', () => {
    render(<WafPolicyWizard clusterId={1} />);
    expect(screen.getByRole('textbox', { name: /policy name/i })).toBeInTheDocument();
  });

  it('blocks Next until name is filled in', async () => {
    const user = userEvent.setup();
    render(<WafPolicyWizard clusterId={1} />);
    const nextButton = screen.getByRole('button', { name: /next/i });
    expect(nextButton).toBeDisabled();
    await user.type(screen.getByRole('textbox', { name: /policy name/i }), 'my-policy');
    await waitFor(() => expect(nextButton).not.toBeDisabled());
  });

  it('shows conflict warning when policy name already exists', async () => {
    vi.mocked(useWafPolicies).mockReturnValue(
      mockQuery({ policies: [{ metadata: { name: 'existing-policy', namespace: 'default' } }], count: 1 }) as never
    );
    const user = userEvent.setup();
    render(<WafPolicyWizard clusterId={1} />);
    await user.type(screen.getByRole('textbox', { name: /policy name/i }), 'existing-policy');
    await waitFor(() => expect(screen.getByText(/already exists/)).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /next/i })).toBeDisabled();
  });

  it('advances through steps to Review and shows CR preview', async () => {
    const user = userEvent.setup();
    render(<WafPolicyWizard clusterId={1} />);
    await user.type(screen.getByRole('textbox', { name: /policy name/i }), 'my-policy');
    await user.click(screen.getByRole('button', { name: /next/i })); // -> policy
    await user.click(screen.getByRole('button', { name: /next/i })); // -> logging
    await user.click(screen.getByRole('button', { name: /next/i })); // -> review
    expect(screen.getByText('Review')).toBeInTheDocument();
    expect(screen.getByText(/"kind": "APPolicy"/)).toBeInTheDocument();
  });

  it('calls onClose when Cancel is clicked', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<WafPolicyWizard clusterId={1} onClose={onClose} />);
    await user.click(screen.getByRole('button', { name: /cancel/i }));
    expect(onClose).toHaveBeenCalled();
  });

  it('submits createPolicy on Create', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const createPolicy = mockMutation();
    vi.mocked(useCreateWafPolicy).mockReturnValue(createPolicy as never);
    render(<WafPolicyWizard clusterId={1} onClose={onClose} />);
    await user.type(screen.getByRole('textbox', { name: /policy name/i }), 'my-policy');
    await user.click(screen.getByRole('button', { name: /next/i }));
    await user.click(screen.getByRole('button', { name: /next/i }));
    await user.click(screen.getByRole('button', { name: /next/i }));
    await user.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(createPolicy.mutateAsync).toHaveBeenCalledWith({
      name: 'my-policy', namespace: 'default',
      spec: { policy: { name: 'my-policy' } },
    }));
    expect(onClose).toHaveBeenCalled();
  });
});
