/**
 * Tests for RunModuleActionDialog (D-034 — module test actions).
 *
 * Covers: green action happy path (submit + task status), amber warning gate
 * blocks submit until confirmed, enum input renders the manifest's choices,
 * empty actions list message.
 *
 * CT-012: MSW handlers mirror the REAL backend shapes from
 * backend/routes/project_execution.py + backend/schemas/projects.py
 * (ModuleActionsListResponse / ModuleActionRequest / ModuleActionSubmitResponse).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import { RunModuleActionDialog } from '../RunModuleActionDialog';
import type { ProjectModule } from '@/types';

// Radix Select needs pointer-capture APIs that jsdom lacks
// (same polyfill as ContainerRegistries.test.tsx / TMMDebugPanel.test.tsx)
if (!HTMLElement.prototype.hasPointerCapture) {
  HTMLElement.prototype.hasPointerCapture = () => false;
  HTMLElement.prototype.releasePointerCapture = () => {};
}

const mockModule = {
  id: 7,
  project_id: 1,
  module_name: 'ocibnkctl-cluster',
  path_in_project: 'oci/cluster',
  deployment_order: 0,
  enabled: true,
  status: 'applied',
  library_module: {
    id: 9,
    name: 'ocibnkctl',
    category: 'bnk',
    version: '0.9.0',
  },
} as unknown as ProjectModule;

// Real ModuleActionsListResponse shape (ProjectModuleService.list_module_actions)
const mockActionsResponse = {
  module_id: 7,
  actions: [
    {
      name: 'scenario-run',
      title: 'Run scenario',
      description: 'Run one functional scenario against the deployed cluster',
      rating: 'green',
      inputs: [
        {
          name: 'scenario',
          type: 'string',
          source: null,
          default: 'tcpl4lb',
          description: 'Scenario to run',
          choices: ['tcpl4lb', 'udpl4lb', 'ai-inference-e2e'],
        },
      ],
    },
    {
      name: 'ai-scenarios',
      title: 'AI scenarios',
      description: 'needs AI model resources (GPU pool)',
      rating: 'amber',
      inputs: [],
    },
  ],
  total: 2,
};

const submitResponse = (action: string) => ({
  success: true,
  message: `Action '${action}' queued`,
  action,
  task_id: 42,
  celery_task_id: 'ce1e12-abc',
  status: 'queued',
});

describe('RunModuleActionDialog', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    module: mockModule,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    server.use(
      http.get('*/api/project-modules/:moduleId/actions', () => {
        return HttpResponse.json(mockActionsResponse);
      }),
      http.get('*/api/tasks/:id', ({ request }) => {
        const url = new URL(request.url);
        if (url.pathname.split('/').length > 4) return;
        return HttpResponse.json({
          id: 42,
          celery_task_id: 'ce1e12-abc',
          task_type: 'action',
          status: 'in_progress',
          project_id: 1,
          module_id: 7,
          created_at: '2026-07-18T00:00:00Z',
        });
      })
    );
  });

  async function selectAction(user: ReturnType<typeof userEvent.setup>, title: string) {
    await user.click(await screen.findByRole('combobox', { name: 'Action' }));
    await user.click(await screen.findByRole('option', { name: new RegExp(title, 'i') }));
  }

  it('runs a green action with the prefilled enum default and shows task status', async () => {
    let capturedBody: unknown = null;
    server.use(
      http.post('*/api/project-modules/:moduleId/actions/:actionName', async ({ request, params }) => {
        capturedBody = await request.json();
        return HttpResponse.json(submitResponse(String(params.actionName)));
      })
    );

    const user = userEvent.setup();
    render(<RunModuleActionDialog {...defaultProps} />);

    await selectAction(user, 'Run scenario');

    // Green action: description shown, no amber gate
    expect(
      screen.getByText('Run one functional scenario against the deployed cluster')
    ).toBeInTheDocument();
    expect(screen.queryByText(/amber-rated/)).not.toBeInTheDocument();

    const runButton = screen.getByRole('button', { name: 'Run' });
    expect(runButton).toBeEnabled();
    await user.click(runButton);

    // Payload matches the backend's ModuleActionRequest with the prefilled default
    await waitFor(() => {
      expect(capturedBody).toEqual({ inputs: { scenario: 'tcpl4lb' } });
    });

    // Task status is shown via the existing task polling + a logs link
    expect(await screen.findByText(/Action task #42/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'View logs' })).toHaveAttribute('href', '/tasks');
  });

  it('renders the enum input choices from the manifest', async () => {
    const user = userEvent.setup();
    render(<RunModuleActionDialog {...defaultProps} />);

    await selectAction(user, 'Run scenario');

    await user.click(screen.getByRole('combobox', { name: 'scenario' }));

    expect(await screen.findByRole('option', { name: 'udpl4lb' })).toBeInTheDocument();
    expect(screen.getByRole('option', { name: 'ai-inference-e2e' })).toBeInTheDocument();
    expect(screen.getAllByRole('option', { name: 'tcpl4lb' }).length).toBeGreaterThan(0);
  });

  it('blocks amber actions behind an explicit confirmation naming the reason', async () => {
    let posted = false;
    server.use(
      http.post('*/api/project-modules/:moduleId/actions/:actionName', ({ params }) => {
        posted = true;
        return HttpResponse.json(submitResponse(String(params.actionName)));
      })
    );

    const user = userEvent.setup();
    render(<RunModuleActionDialog {...defaultProps} />);

    await selectAction(user, 'AI scenarios');

    // Warning names why caution is needed (the description carries the reason)
    expect(screen.getByText(/amber-rated — extra caution is needed/)).toBeInTheDocument();
    expect(screen.getByText(/needs AI model resources \(GPU pool\)/)).toBeInTheDocument();

    // Submit is blocked until the confirmation checkbox is ticked
    const runButton = screen.getByRole('button', { name: 'Run' });
    expect(runButton).toBeDisabled();
    await user.click(runButton);
    expect(posted).toBe(false);

    await user.click(screen.getByRole('checkbox', { name: /I understand/ }));
    expect(runButton).toBeEnabled();
    await user.click(runButton);

    await waitFor(() => {
      expect(posted).toBe(true);
    });
  });

  it('gates a mixed-case "Amber" rating (client check is the sole amber safeguard)', async () => {
    // #468 review: the backend amber ack is UI-only, so the client rating check is
    // the entire safeguard — a non-lowercase rating must not slip through ungated.
    server.use(
      http.get('*/api/project-modules/:moduleId/actions', () => {
        return HttpResponse.json({
          module_id: 7,
          total: 1,
          actions: [
            { name: 'ai-scenarios', title: 'AI scenarios', description: 'GPU pool', rating: 'Amber', inputs: [] },
          ],
        });
      })
    );

    const user = userEvent.setup();
    render(<RunModuleActionDialog {...defaultProps} />);

    await selectAction(user, 'AI scenarios');

    expect(screen.getByText(/amber-rated — extra caution is needed/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Run' })).toBeDisabled();
  });

  it('renders a hostile manifest cleanup_note as literal text (escaping regression guard)', async () => {
    // cleanup_note is authored in the artifact manifest -- untrusted content.
    // It must render as text, never as HTML (#99).
    const hostile = 'Remove the PoC dir <img src=x onerror="window.__pwned = 3"> <b>now</b>';
    server.use(
      http.get('*/api/project-modules/:moduleId/actions', () =>
        HttpResponse.json({
          module_id: 7,
          actions: [{ name: 'cleanup', title: 'Clean up', description: 'Clean up the PoC',
                      rating: 'green', inputs: [], cleanup_note: hostile }],
        })
      ),
      http.post('*/api/project-modules/:moduleId/actions/:actionName', () =>
        HttpResponse.json({ task_id: 901, message: 'queued' })
      ),
      http.get('*/api/tasks/901', () =>
        HttpResponse.json({ id: 901, celery_task_id: 'c901', task_type: 'action',
                            status: 'completed', project_id: 1, module_id: 7,
                            created_at: '2026-07-18T00:00:00Z' })
      ),
    );
    const user = userEvent.setup();
    render(<RunModuleActionDialog {...defaultProps} />);

    await selectAction(user, 'Clean up');
    await user.click(screen.getByRole('button', { name: 'Run' }));

    await waitFor(() => {
      expect(screen.getByText(/Remove the PoC dir/)).toBeInTheDocument();
    });
    // Markup is visible as literal characters, and created no elements.
    expect(screen.getByText(/<img src=x onerror=/)).toBeInTheDocument();
    expect(document.querySelector('img[src="x"]')).toBeNull();
    expect(document.querySelector('b')).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it('ignores a second submit while the first is still in flight (re-guard)', async () => {
    let posts = 0;
    server.use(
      http.post('*/api/project-modules/:moduleId/actions/:actionName', async () => {
        posts += 1;
        await new Promise((r) => setTimeout(r, 150)); // hold the first request open
        return HttpResponse.json(submitResponse('scenario-run'));
      })
    );
    const user = userEvent.setup();
    render(<RunModuleActionDialog {...defaultProps} />);

    await selectAction(user, 'Run scenario');
    const run = screen.getByRole('button', { name: 'Run' });
    // Two clicks inside the window before isPending disables the button.
    await Promise.all([user.click(run), user.click(run)]);

    await waitFor(() => {
      expect(screen.getByText(/Action task #42/)).toBeInTheDocument();
    });
    expect(posts).toBe(1);
  });

  it('shows a message and keeps Run disabled when no actions are declared', async () => {
    server.use(
      http.get('*/api/project-modules/:moduleId/actions', () => {
        return HttpResponse.json({ module_id: 7, actions: [], total: 0 });
      })
    );

    render(<RunModuleActionDialog {...defaultProps} />);

    expect(
      await screen.findByText(/declares no actions/)
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Run' })).toBeDisabled();
  });
});
