/**
 * Tests for SystemDefaults component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import SystemDefaults from '../SystemDefaults';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('SystemDefaults', () => {
  it('renders section headings after loading', async () => {
    server.use(
      http.get('*/api/system/defaults', () =>
        HttpResponse.json({
          project: {
            default_type: { key: 'project.default_type', raw_value: 'cloud-ibm' },
          },
          cloud: {
            aws_region: { key: 'cloud.aws.default_region', raw_value: 'ap-southeast-2' },
            azure_region: { key: 'cloud.azure.default_region', raw_value: '' },
            gcp_region: { key: 'cloud.gcp.default_region', raw_value: '' },
            ibm_region: { key: 'cloud.ibm.default_region', raw_value: 'us-south' },
          },
          opentofu: {
            init_timeout: { key: 'opentofu.timeout.init', raw_value: '300' },
            plan_timeout: { key: 'opentofu.timeout.plan', raw_value: '600' },
            apply_timeout: { key: 'opentofu.timeout.apply', raw_value: '1800' },
            destroy_timeout: { key: 'opentofu.timeout.destroy', raw_value: '1800' },
          },
          execution: {
            max_retries: { key: 'execution.max_retries', raw_value: '3' },
            retry_delay: { key: 'execution.retry_delay', raw_value: '5' },
          },
        })
      ),
    );

    render(<SystemDefaults />);

    await waitFor(() => {
      expect(screen.getByText('System Defaults')).toBeInTheDocument();
    });
    expect(screen.getByText('Project defaults')).toBeInTheDocument();
    expect(screen.getByText('Cloud provider defaults')).toBeInTheDocument();
    expect(screen.getByText('OpenTofu timeouts')).toBeInTheDocument();
  });

  it('shows save button as disabled when no changes', async () => {
    server.use(
      http.get('*/api/system/defaults', () =>
        HttpResponse.json({
          project: {
            default_type: { key: 'project.default_type', raw_value: 'cloud-ibm' },
          },
        })
      ),
    );

    render(<SystemDefaults />);

    await waitFor(() => {
      expect(screen.getByText('Save Changes')).toBeInTheDocument();
    });

    const saveBtn = screen.getByText('Save Changes').closest('button');
    expect(saveBtn).toBeDisabled();
  });

  it('renders execution settings section', async () => {
    server.use(
      http.get('*/api/system/defaults', () =>
        HttpResponse.json({
          project: { default_type: { key: 'project.default_type', raw_value: 'cloud-ibm' } },
          execution: {
            max_retries: { key: 'execution.max_retries', raw_value: '3' },
            retry_delay: { key: 'execution.retry_delay', raw_value: '5' },
          },
        })
      ),
    );

    render(<SystemDefaults />);

    await waitFor(() => {
      expect(screen.getByText('Execution settings')).toBeInTheDocument();
    });
    expect(screen.getByText('Max Retries')).toBeInTheDocument();
    expect(screen.getByText('Retry Delay')).toBeInTheDocument();
  });
});
