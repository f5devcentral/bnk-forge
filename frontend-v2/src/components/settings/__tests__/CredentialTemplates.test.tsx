/**
 * Tests for CredentialTemplates component
 */
import { describe, it, expect, vi, beforeEach, beforeAll } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import CredentialTemplates from '../CredentialTemplates';

vi.mock('@/lib/ibm-cloud', () => ({
  loadIbmRegionsFromApiKey: vi.fn(async () => [
    { value: 'us-south', label: 'US South (Dallas)' },
    { value: 'eu-de', label: 'EU Germany (Frankfurt)' },
  ]),
}));

beforeEach(() => {
  vi.clearAllMocks();
});

beforeAll(() => {
  if (!HTMLElement.prototype.hasPointerCapture) {
    Object.defineProperty(HTMLElement.prototype, 'hasPointerCapture', {
      configurable: true,
      value: () => false,
    });
  }
  if (!HTMLElement.prototype.setPointerCapture) {
    Object.defineProperty(HTMLElement.prototype, 'setPointerCapture', {
      configurable: true,
      value: () => undefined,
    });
  }
  if (!HTMLElement.prototype.releasePointerCapture) {
    Object.defineProperty(HTMLElement.prototype, 'releasePointerCapture', {
      configurable: true,
      value: () => undefined,
    });
  }
});

describe('CredentialTemplates', () => {
  it('renders credential template list', async () => {
    server.use(
      http.get('*/api/credential-templates', () =>
        HttpResponse.json([
          {
            id: 1,
            name: 'AWS Production',
            provider: 'aws',
            aws_auth_method: 'access_keys',
            description: 'Production AWS credentials',
            region: 'us-east-1',
            is_default: true,
            has_aws_secret_access_key: true,
            aws_sso_enabled: false,
            projects_count: 2,
            created_at: '2026-01-10T00:00:00Z',
          },
        ])
      ),
    );

    render(<CredentialTemplates />);

    await waitFor(() => {
      expect(screen.getByText('AWS Production')).toBeInTheDocument();
    });
    expect(screen.getByText('Default')).toBeInTheDocument();
    expect(screen.getByText('Used by 2 project(s)')).toBeInTheDocument();
  });

  it('shows empty state when no templates exist', async () => {
    server.use(
      http.get('*/api/credential-templates', () => HttpResponse.json([])),
    );

    render(<CredentialTemplates />);

    await waitFor(() => {
      expect(screen.getByText('No credential templates found')).toBeInTheDocument();
    });
  });

  it('opens create dialog when New Template button is clicked', async () => {
    const user = userEvent.setup();
    server.use(
      http.get('*/api/credential-templates', () => HttpResponse.json([])),
    );

    render(<CredentialTemplates />);

    await waitFor(() => {
      expect(screen.getByText('New Template')).toBeInTheDocument();
    });

    await user.click(screen.getByText('New Template'));

    await waitFor(() => {
      expect(screen.getByText('Create Credential Template')).toBeInTheDocument();
    });
    expect(screen.getByLabelText('Template Name *')).toBeInTheDocument();
  });

  it('shows IBM Cloud as a provider option and renders IBM fields', async () => {
    const user = userEvent.setup();
    server.use(http.get('*/api/credential-templates', () => HttpResponse.json([])));

    render(<CredentialTemplates />);

    await waitFor(() => {
      expect(screen.getByText('New Template')).toBeInTheDocument();
    });

    await user.click(screen.getByText('New Template'));

    await waitFor(() => {
      expect(screen.getByText('Create Credential Template')).toBeInTheDocument();
    });

    const dialog = screen.getByRole('dialog');
    await user.click(within(dialog).getAllByRole('combobox')[0]);
    await user.click(screen.getByRole('option', { name: 'IBM Cloud' }));

    expect(screen.getByLabelText(/IBM Cloud API Key/i)).toBeInTheDocument();
  });

  it('loads IBM regions from IBM Cloud when requested', async () => {
    const user = userEvent.setup();
    server.use(
      http.get('*/api/credential-templates', () => HttpResponse.json([])),
    );

    render(<CredentialTemplates />);

    await waitFor(() => {
      expect(screen.getByText('New Template')).toBeInTheDocument();
    });

    await user.click(screen.getByText('New Template'));
    await waitFor(() => {
      expect(screen.getByText('Create Credential Template')).toBeInTheDocument();
    });

    const dialog = screen.getByRole('dialog');
    await user.click(within(dialog).getAllByRole('combobox')[0]);
    await user.click(screen.getByRole('option', { name: 'IBM Cloud' }));

    await user.type(screen.getByLabelText(/IBM Cloud API Key/i), 'ibm-secret-key');
    await user.click(screen.getByRole('button', { name: /load regions/i }));

    await waitFor(() => {
      expect(screen.queryByText(/enter an ibm cloud api key first/i)).not.toBeInTheDocument();
    });
  });

  it('loads IBM COS instances for IBM templates', async () => {
    const user = userEvent.setup();
    server.use(
      http.get('*/api/credential-templates', () => HttpResponse.json([])),
      http.post('*/api/cloud-auth/ibm/cos-instances/query', async () =>
        HttpResponse.json({
          instances: [
            { value: 'cos-prod', label: 'cos-prod', crn: 'crn:v1:bluemix:public:cloud-object-storage:global:a/x:y::' },
          ],
        })
      ),
    );

    render(<CredentialTemplates />);

    await waitFor(() => {
      expect(screen.getByText('New Template')).toBeInTheDocument();
    });

    await user.click(screen.getByText('New Template'));
    await waitFor(() => {
      expect(screen.getByText('Create Credential Template')).toBeInTheDocument();
    });

    const dialog = screen.getByRole('dialog');
    await user.click(within(dialog).getAllByRole('combobox')[0]);
    await user.click(screen.getByRole('option', { name: 'IBM Cloud' }));

    await user.type(screen.getByLabelText(/IBM Cloud API Key/i), 'ibm-real-key');
    await user.click(screen.getByRole('button', { name: /load cos instances/i }));

    await waitFor(() => {
      expect(screen.queryByText(/enter an ibm cloud api key first/i)).not.toBeInTheDocument();
    });
  });

  describe('Authenticate SSO menu item visibility', () => {
    it('shows Authenticate SSO when aws_auth_method is sso and aws_sso_enabled is true', async () => {
      const user = userEvent.setup();
      server.use(
        http.get('*/api/credential-templates', () =>
          HttpResponse.json([
            {
              id: 1,
              name: 'AWS SSO Template',
              provider: 'aws',
              aws_auth_method: 'sso',
              aws_sso_enabled: true,
              aws_sso_start_url: 'https://my-company.awsapps.com/start',
              aws_sso_region: 'us-east-1',
              aws_sso_account_id: '123456789012',
              aws_sso_role_name: 'AdministratorAccess',
              is_default: false,
              projects_count: 0,
              created_at: '2026-01-10T00:00:00Z',
            },
          ])
        ),
      );

      render(<CredentialTemplates />);
      await waitFor(() => expect(screen.getByText('AWS SSO Template')).toBeInTheDocument());

      await user.click(screen.getByRole('button', { name: 'Template actions' }));
      await waitFor(() => expect(screen.getByText('Authenticate SSO')).toBeInTheDocument());
    });

    it('shows Authenticate SSO when aws_auth_method is access_keys but complete SSO config exists', async () => {
      const user = userEvent.setup();
      server.use(
        http.get('*/api/credential-templates', () =>
          HttpResponse.json([
            {
              id: 1,
              name: 'AWS Production',
              provider: 'aws',
              aws_auth_method: 'access_keys',
              aws_sso_enabled: false,
              aws_sso_start_url: 'https://my-company.awsapps.com/start',
              aws_sso_region: 'us-east-1',
              aws_sso_account_id: '123456789012',
              aws_sso_role_name: 'AdministratorAccess',
              is_default: true,
              has_aws_secret_access_key: true,
              projects_count: 2,
              created_at: '2026-01-10T00:00:00Z',
            },
          ])
        ),
      );

      render(<CredentialTemplates />);
      await waitFor(() => expect(screen.getByText('AWS Production')).toBeInTheDocument());

      await user.click(screen.getByRole('button', { name: 'Template actions' }));
      await waitFor(() => expect(screen.getByText('Authenticate SSO')).toBeInTheDocument());
    });

    it('does not show Authenticate SSO when SSO config is incomplete', async () => {
      const user = userEvent.setup();
      server.use(
        http.get('*/api/credential-templates', () =>
          HttpResponse.json([
            {
              id: 1,
              name: 'AWS Production',
              provider: 'aws',
              aws_auth_method: 'access_keys',
              aws_sso_enabled: false,
              aws_sso_start_url: '',
              aws_sso_region: '',
              aws_sso_account_id: '',
              aws_sso_role_name: '',
              is_default: true,
              has_aws_secret_access_key: true,
              projects_count: 0,
              created_at: '2026-01-10T00:00:00Z',
            },
          ])
        ),
      );

      render(<CredentialTemplates />);
      await waitFor(() => expect(screen.getByText('AWS Production')).toBeInTheDocument());

      await user.click(screen.getByRole('button', { name: 'Template actions' }));
      await waitFor(() => expect(screen.queryByText('Authenticate SSO')).not.toBeInTheDocument());
    });

    it('does not show Authenticate SSO for non-AWS providers', async () => {
      const user = userEvent.setup();
      server.use(
        http.get('*/api/credential-templates', () =>
          HttpResponse.json([
            {
              id: 2,
              name: 'IBM Production',
              provider: 'ibm',
              aws_sso_enabled: false,
              is_default: false,
              has_ibmcloud_api_key: true,
              projects_count: 0,
              created_at: '2026-01-10T00:00:00Z',
            },
          ])
        ),
      );

      render(<CredentialTemplates />);
      await waitFor(() => expect(screen.getByText('IBM Production')).toBeInTheDocument());

      await user.click(screen.getByRole('button', { name: 'Template actions' }));
      await waitFor(() => expect(screen.queryByText('Authenticate SSO')).not.toBeInTheDocument());
    });
  });

  it('renders IBM template details in the list', async () => {
    server.use(
      http.get('*/api/credential-templates', () =>
        HttpResponse.json([
          {
            id: 2,
            name: 'IBM Production',
            provider: 'ibm',
            description: 'Production IBM credentials',
            region: 'us-south',
            is_default: false,
            has_aws_secret_access_key: false,
            has_aws_session_token: false,
            aws_sso_enabled: false,
            has_gcp_credentials: false,
            has_azure_credentials: false,
            has_ibmcloud_api_key: true,
            has_ssh_password: false,
            has_ssh_key: false,
            projects_count: 1,
            created_at: '2026-01-10T00:00:00Z',
            updated_at: '2026-01-10T00:00:00Z',
          },
        ])
      ),
    );

    render(<CredentialTemplates />);

    await waitFor(() => {
      expect(screen.getByText('IBM Production')).toBeInTheDocument();
    });

    expect(screen.getByText('IBM')).toBeInTheDocument();
    expect(screen.getByText('Region: us-south')).toBeInTheDocument();
    expect(screen.getByText('Credentials Configured')).toBeInTheDocument();
  });
});
