import { describe, it, expect, vi } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { ValidatedInput } from '../validated-input';

describe('ValidatedInput', () => {
  it('renders label, input, and description', () => {
    render(
      <ValidatedInput
        id="test-input"
        label="Project Name"
        description="Enter a unique name"
        value=""
        onChange={() => {}}
      />
    );
    expect(screen.getByText('Project Name')).toBeInTheDocument();
    expect(screen.getByText('Enter a unique name')).toBeInTheDocument();
  });

  it('shows required indicator when required prop is true', () => {
    render(
      <ValidatedInput
        id="test-input"
        label="Name"
        required
        value=""
        onChange={() => {}}
      />
    );
    expect(screen.getByText('*')).toBeInTheDocument();
  });

  it('shows validation error after blur when validator returns error', async () => {
    const user = userEvent.setup();
    const validator = (val: string) =>
      val.length < 3
        ? { isValid: false, error: 'Must be at least 3 characters' }
        : { isValid: true, error: undefined };

    const onChange = vi.fn();

    render(
      <ValidatedInput
        id="test-input"
        label="Name"
        value="ab"
        onChange={onChange}
        validator={validator}
        validateOnBlur
      />
    );

    const input = screen.getByRole('textbox');
    // Focus and blur to trigger validation
    await user.click(input);
    await user.tab();

    await waitFor(() => {
      expect(screen.getByText('Must be at least 3 characters')).toBeInTheDocument();
    });
  });
});
