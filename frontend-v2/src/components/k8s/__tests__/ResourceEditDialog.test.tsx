/**
 * Tests for ResourceEditDialog component
 *
 * Mocks Monaco editor and js-yaml to isolate component behavior.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { ResourceEditDialog } from '../ResourceEditDialog';

// Mock Monaco editor
vi.mock('@monaco-editor/react', () => ({
  Editor: ({ value, onChange }: { value: string; onChange?: (v: string) => void }) => (
    <textarea
      data-testid="mock-editor"
      value={value}
      onChange={(e) => onChange?.(e.target.value)}
    />
  ),
}));

// Mock js-yaml
vi.mock('js-yaml', () => ({
  default: {
    dump: (obj: unknown) => JSON.stringify(obj, null, 2),
    load: (str: string) => JSON.parse(str),
  },
}));

const mockResource = {
  kind: 'ConfigMap',
  apiVersion: 'v1',
  metadata: { name: 'my-config', namespace: 'default', uid: '123' },
  spec: {},
  status: {},
};

describe('ResourceEditDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    resource: mockResource,
    onSubmit: vi.fn(),
    isLoading: false,
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders dialog title with resource kind and name', () => {
    render(<ResourceEditDialog {...defaultProps} />);
    expect(screen.getByText('Edit ConfigMap: my-config')).toBeInTheDocument();
  });

  it('calls onSubmit with yaml and dryRun flag', async () => {
    const user = userEvent.setup();
    render(<ResourceEditDialog {...defaultProps} />);

    const saveBtn = screen.getByRole('button', { name: /Save Changes/i });
    await user.click(saveBtn);

    expect(defaultProps.onSubmit).toHaveBeenCalledWith(expect.any(String), false);
  });

  it('returns null when resource is null', () => {
    const { container } = render(<ResourceEditDialog {...defaultProps} resource={null} />);
    expect(container.innerHTML).toBe('');
  });
});
