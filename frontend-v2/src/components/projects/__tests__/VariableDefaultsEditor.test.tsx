import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { VariableDefaultsEditor } from '../VariableDefaultsEditor';

beforeEach(() => {
  vi.clearAllMocks();
  server.use(
    http.get('*/api/projects/:id/variable-defaults', () => {
      return HttpResponse.json({
        common_defaults: {
          vpc_cidr: '10.0.0.0/16',
          instance_type: 't3.medium',
        },
        custom_defaults: {
          my_var: 'my-value',
        },
      });
    }),
    http.put('*/api/projects/:id/variable-defaults', () => {
      return HttpResponse.json({ success: true });
    }),
    http.post('*/api/projects/:id/variable-defaults/reset', () => {
      return HttpResponse.json({ success: true });
    })
  );
});

describe('VariableDefaultsEditor', () => {
  it('renders common defaults as read-only', async () => {
    render(<VariableDefaultsEditor projectId={1} />);

    await waitFor(() => {
      expect(screen.getByText('Common Defaults')).toBeInTheDocument();
    });
    expect(screen.getByText('vpc_cidr')).toBeInTheDocument();
    expect(screen.getByText('10.0.0.0/16')).toBeInTheDocument();
    expect(screen.getByText('instance_type')).toBeInTheDocument();
    expect(screen.getByText('t3.medium')).toBeInTheDocument();
  });

  it('renders existing custom defaults', async () => {
    render(<VariableDefaultsEditor projectId={1} />);

    await waitFor(() => {
      expect(screen.getByText('Custom Defaults')).toBeInTheDocument();
    });
    expect(screen.getByText('my_var')).toBeInTheDocument();
    expect(screen.getByText('my-value')).toBeInTheDocument();
  });

  it('shows empty state when no custom defaults', async () => {
    server.use(
      http.get('*/api/projects/:id/variable-defaults', () => {
        return HttpResponse.json({
          common_defaults: { vpc_cidr: '10.0.0.0/16' },
          custom_defaults: {},
        });
      })
    );

    render(<VariableDefaultsEditor projectId={1} />);

    await waitFor(() => {
      expect(screen.getByText('No custom defaults defined')).toBeInTheDocument();
    });
  });
});
