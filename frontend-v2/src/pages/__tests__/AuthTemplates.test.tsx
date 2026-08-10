/**
 * Tests for AuthTemplates page
 *
 * Covers: renders page title, description, credential templates section,
 * usage instructions.
 */
import { describe, it, expect } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import { render } from '@/test/test-utils';
import AuthTemplates from '@/pages/AuthTemplates';

describe('AuthTemplates', () => {
  it('renders the Access Methods title', async () => {
    render(<AuthTemplates />);
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /access methods/i })).toBeInTheDocument();
    });
  });

  it('renders the description text', async () => {
    // D-020: subtitle reworded — drops "Manage", uses
    // "Cloud-provider credentials, SSH jumphost configurations, and
    // single-node SSH access".
    render(<AuthTemplates />);
    await waitFor(() => {
      expect(
        screen.getByText(
          /cloud-provider credentials, ssh jumphost configurations, and single-node ssh access/i,
        ),
      ).toBeInTheDocument();
    });
  });

  it('renders the usage instructions section', async () => {
    render(<AuthTemplates />);
    await waitFor(() => {
      expect(screen.getByText(/using credential templates/i)).toBeInTheDocument();
    });
  });

  it('renders step-by-step instructions', async () => {
    render(<AuthTemplates />);
    await waitFor(() => {
      expect(screen.getByText(/go to/i)).toBeInTheDocument();
      expect(screen.getByText(/click on a project/i)).toBeInTheDocument();
    });
  });
});
