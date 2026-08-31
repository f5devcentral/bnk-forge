/**
 * Tests for SystemDefaults component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@/test/test-utils';
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

  it('renders free-text region inputs and accepts custom regions', async () => {
    server.use(
      http.get('*/api/system/defaults', () =>
        HttpResponse.json({
          project: {
            default_type: { key: 'project.default_type', raw_value: 'cloud-aws' },
          },
          cloud: {
            aws_region: { key: 'cloud.aws.default_region', raw_value: '' },
            azure_region: { key: 'cloud.azure.default_region', raw_value: '' },
            gcp_region: { key: 'cloud.gcp.default_region', raw_value: '' },
            ibm_region: { key: 'cloud.ibm.default_region', raw_value: '' },
          },
        })
      ),
    );

    render(<SystemDefaults />);

    await waitFor(() => {
      expect(screen.getByLabelText(/AWS Region/i)).toBeInTheDocument();
    });

    const awsInput = screen.getByLabelText(/AWS Region/i) as HTMLInputElement;
    const azureInput = screen.getByLabelText(/Azure Default Region/i) as HTMLInputElement;
    const gcpInput = screen.getByLabelText(/GCP Default Region/i) as HTMLInputElement;
    const ibmInput = screen.getByLabelText(/IBM Default Region/i) as HTMLInputElement;

    expect(awsInput.tagName).toBe('INPUT');
    expect(azureInput.tagName).toBe('INPUT');
    expect(gcpInput.tagName).toBe('INPUT');
    expect(ibmInput.tagName).toBe('INPUT');

    // Custom regions such as eu-fr2 should be accepted in any provider field.
    fireEvent.change(awsInput, { target: { value: 'eu-fr2' } });
    fireEvent.change(azureInput, { target: { value: 'eu-fr2' } });
    fireEvent.change(gcpInput, { target: { value: 'eu-fr2' } });
    fireEvent.change(ibmInput, { target: { value: 'eu-fr2' } });

    expect(awsInput).toHaveValue('eu-fr2');
    expect(azureInput).toHaveValue('eu-fr2');
    expect(gcpInput).toHaveValue('eu-fr2');
    expect(ibmInput).toHaveValue('eu-fr2');
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
