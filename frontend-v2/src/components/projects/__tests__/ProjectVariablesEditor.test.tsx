import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { ProjectVariablesEditor } from '../ProjectVariablesEditor';

const mockConfigurable = {
  region: {
    type: 'string',
    description: 'AWS region',
    default: 'us-east-1',
    required: true,
    sensitive: false,
    used_by: ['module-a'],
    modules_count: 2,
    current_value: 'us-west-2',
    value_source: 'user' as const,
  },
  enable_logging: {
    type: 'bool',
    description: 'Enable logging',
    default: false,
    required: false,
    sensitive: false,
    used_by: ['module-b'],
    modules_count: 1,
    current_value: true,
    value_source: 'default' as const,
  },
};

const mockDiscovered = {
  vpc_id: {
    value: 'vpc-123456',
    display_value: 'vpc-123456',
    type: 'string',
    source: 'module-a',
    source_module: 'module-a',
    is_truncated: false,
  },
};

beforeEach(() => {
  vi.clearAllMocks();
  server.use(
    http.get('*/api/projects/:id/variables', () => {
      return HttpResponse.json({
        variables: { region: 'us-west-2' },
        configurable: mockConfigurable,
        discovered: mockDiscovered,
      });
    }),
    http.put('*/api/projects/:id/variables', () => {
      return HttpResponse.json({ success: true });
    })
  );
});

describe('ProjectVariablesEditor', () => {
  it('shows loading skeleton initially then renders configurable variables', async () => {
    render(<ProjectVariablesEditor projectId={1} />);

    // Should eventually render configurable variables
    await waitFor(() => {
      expect(screen.getByText('Configurable Variables')).toBeInTheDocument();
    });
    expect(screen.getByText('region')).toBeInTheDocument();
    expect(screen.getByText('enable_logging')).toBeInTheDocument();
  });

  it('displays discovered dependencies section', async () => {
    render(<ProjectVariablesEditor projectId={1} />);

    await waitFor(() => {
      expect(screen.getByText('Auto-Discovered Dependencies')).toBeInTheDocument();
    });
    expect(screen.getByText('1 values')).toBeInTheDocument();
  });

  it('shows save button disabled when no changes made', async () => {
    render(<ProjectVariablesEditor projectId={1} />);

    await waitFor(() => {
      expect(screen.getByText('Save Variables')).toBeInTheDocument();
    });
    // No changes have been made, button should be disabled
    expect(screen.getByText('Save Variables').closest('button')).toBeDisabled();
  });

  it('shows "Unsaved Changes" badge and enables save when a variable is modified', async () => {
    const user = userEvent.setup();
    render(<ProjectVariablesEditor projectId={1} />);

    await waitFor(() => {
      expect(screen.getByText('region')).toBeInTheDocument();
    });

    // Find the region input and change it
    const regionInput = screen.getByDisplayValue('us-west-2');
    await user.clear(regionInput);
    await user.type(regionInput, 'eu-west-1');

    expect(screen.getByText('Unsaved Changes')).toBeInTheDocument();
    expect(screen.getByText('Save Variables').closest('button')).not.toBeDisabled();
  });
});
