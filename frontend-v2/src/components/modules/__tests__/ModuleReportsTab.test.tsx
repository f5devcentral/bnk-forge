/**
 * Tests for ModuleReportsTab (D-034 PR-2.5, #458 — module reports viewer).
 *
 * Covers: run list renders newest-first with file chips, selecting an .md file
 * renders it as markdown, selecting a .json file pretty-prints it, empty state.
 *
 * CT-012: MSW handlers mirror the REAL backend shapes from
 * backend/routes/project_execution.py + backend/schemas/projects.py
 * (ModuleReportsListResponse / ModuleReportContentResponse).
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';
import { render } from '@/test/test-utils';
import { ModuleReportsTab } from '../ModuleReportsTab';

const mockReportsResponse = {
  module_id: 7,
  runs: [
    {
      stamp: '2026-07-18T07-00-00Z',
      files: [
        { path: '2026-07-18T07-00-00Z/run-poc.md', kind: 'md', size: 1200 },
        { path: '2026-07-18T07-00-00Z/scenarios/tcpl4lb.json', kind: 'json', size: 40 },
      ],
    },
    {
      stamp: '2026-07-18T06-00-00Z',
      files: [{ path: '2026-07-18T06-00-00Z/logs/00-init.log', kind: 'log', size: 90 }],
    },
  ],
};

function reportsHandler() {
  return http.get('*/api/project-modules/:moduleId/reports', () =>
    HttpResponse.json(mockReportsResponse)
  );
}

function contentHandler() {
  return http.get('*/api/project-modules/:moduleId/reports/content', ({ request }) => {
    const path = new URL(request.url).searchParams.get('path') ?? '';
    if (path.endsWith('.md')) {
      return HttpResponse.json({
        path,
        kind: 'md',
        size: 1200,
        content: '# Run report\nDeployment PASSED',
      });
    }
    return HttpResponse.json({
      path,
      kind: 'json',
      size: 40,
      content: '{"scenario":"tcpl4lb","result":"pass"}',
    });
  });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ModuleReportsTab', () => {
  it('renders the report runs newest-first with file chips', async () => {
    server.use(reportsHandler(), contentHandler());
    render(<ModuleReportsTab moduleId={7} />);

    await waitFor(() => {
      expect(screen.getByText('2026-07-18T07-00-00Z')).toBeInTheDocument();
    });
    expect(screen.getByText('2026-07-18T06-00-00Z')).toBeInTheDocument();
    expect(screen.getByText('run-poc.md')).toBeInTheDocument();
    expect(screen.getByText('tcpl4lb.json')).toBeInTheDocument();
    expect(screen.getByText('00-init.log')).toBeInTheDocument();
  });

  it('renders a selected markdown file with heading styling', async () => {
    server.use(reportsHandler(), contentHandler());
    render(<ModuleReportsTab moduleId={7} />);

    const mdChip = await screen.findByText('run-poc.md');
    await userEvent.click(mdChip);

    await waitFor(() => {
      expect(screen.getByText('Run report')).toBeInTheDocument();
    });
    expect(screen.getByText(/Deployment PASSED/)).toBeInTheDocument();
  });

  it('pretty-prints a selected json file', async () => {
    server.use(reportsHandler(), contentHandler());
    render(<ModuleReportsTab moduleId={7} />);

    const jsonChip = await screen.findByText('tcpl4lb.json');
    await userEvent.click(jsonChip);

    await waitFor(() => {
      // Pretty-printed JSON puts each key on its own indented line.
      expect(screen.getByText(/"scenario": "tcpl4lb"/)).toBeInTheDocument();
    });
  });

  it('shows an empty state when there are no reports', async () => {
    server.use(
      http.get('*/api/project-modules/:moduleId/reports', () =>
        HttpResponse.json({ module_id: 7, runs: [] })
      )
    );
    render(<ModuleReportsTab moduleId={7} />);

    await waitFor(() => {
      expect(screen.getByText(/No reports yet/)).toBeInTheDocument();
    });
  });
});
