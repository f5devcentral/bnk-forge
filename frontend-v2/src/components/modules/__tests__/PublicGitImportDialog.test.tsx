/**
 * Tests for PublicGitImportDialog component.
 *
 * Covers: form validation (URL pattern, required fields), submit calls
 * /api/registry/import-git with the typed body, success notifies + closes.
 */
import { describe, it, expect, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import { PublicGitImportDialog } from '../PublicGitImportDialog';

describe('PublicGitImportDialog', () => {
  it('disables submit until URL and ref are filled', async () => {
    const user = userEvent.setup();
    render(<PublicGitImportDialog open={true} onOpenChange={vi.fn()} />);

    const submit = await screen.findByRole('button', { name: /import module/i });
    expect(submit).toBeDisabled();

    await user.type(screen.getByLabelText('Git URL *'), 'https://github.com/cloudposse/terraform-null-label');
    expect(submit).toBeDisabled();

    await user.type(screen.getByLabelText(/^Ref \(/), '0.25.0');
    expect(submit).toBeEnabled();
  });

  it('flags invalid URL format', async () => {
    const user = userEvent.setup();
    render(<PublicGitImportDialog open={true} onOpenChange={vi.fn()} />);

    await user.type(screen.getByLabelText('Git URL *'), 'not-a-url');
    expect(await screen.findByText(/should look like https/i)).toBeInTheDocument();
  });

  it('submits payload with the right shape on import', async () => {
    const user = userEvent.setup();
    let captured: Record<string, unknown> | null = null;
    server.use(
      http.post('*/api/registry/import-git', async ({ request }) => {
        captured = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({
          success: true,
          message: 'Module imported from git',
          module: { id: 5005, name: 'terraform-null-label', category: 'infra', provider: 'git', version: '0.25.0' },
        });
      }),
    );

    const onImported = vi.fn();
    const onOpenChange = vi.fn();
    render(<PublicGitImportDialog open={true} onOpenChange={onOpenChange} onImported={onImported} />);

    await user.type(screen.getByLabelText('Git URL *'), 'https://github.com/cloudposse/terraform-null-label');
    await user.type(screen.getByLabelText(/^Ref \(/), '0.25.0');
    await user.type(screen.getByLabelText('Subpath (optional)'), 'modules/label');
    await user.click(await screen.findByRole('button', { name: /import module/i }));

    await waitFor(() => expect(captured).not.toBeNull());
    expect(captured).toMatchObject({
      url: 'https://github.com/cloudposse/terraform-null-label',
      ref: '0.25.0',
      subpath: 'modules/label',
    });
    await waitFor(() => expect(onImported).toHaveBeenCalledWith(
      expect.objectContaining({ id: 5005 }),
    ));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
