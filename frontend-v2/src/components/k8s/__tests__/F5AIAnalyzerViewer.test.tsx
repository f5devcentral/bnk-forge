import { describe, it, expect, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@/test/test-utils';
import { F5AIAnalyzerViewer } from '../F5AIAnalyzerViewer';
import { http, HttpResponse } from 'msw';
import { server } from '@/test/mocks/server';

// Minimal F5BigAnalyzer CR with no status conditions (the common real-world case
// where the controller never writes .status.conditions).
const analyzerNoConditions = {
  metadata: { name: 'no-cond-analyzer', namespace: 'bnk-demo', uid: 'uid-nc', creationTimestamp: '2026-01-01T00:00:00Z' },
  spec: {
    schedule: '15m',
    applications: [],
    dataSources: [],
    script: { type: 'builtin', builtin: { name: 'roundrobin' } },
  },
  status: {},
};

describe('F5AIAnalyzerViewer', () => {
  beforeEach(() => {
    // Default: return empty analyzers
    server.use(
      http.get('*/api/k8s/clusters/:id/resources/:type', ({ params }) => {
        if (params.type === 'f5biganalyzer') {
          return HttpResponse.json({ resources: [] });
        }
        return HttpResponse.json({ resources: [] });
      })
    );
  });

  it('shows empty state (no synthetic card) when no CRs exist', async () => {
    render(<F5AIAnalyzerViewer clusterId={1} />);

    await waitFor(() => {
      expect(screen.getByText(/no analyzer CR found/i)).toBeInTheDocument();
    });
    // Must NOT render a synthetic "preview (no analyzer CR)" card — that was
    // a prior placeholder that read like a stale CR.
    expect(screen.queryByText(/preview \(no analyzer CR\)/i)).not.toBeInTheDocument();
    expect(screen.getByText(/No F5BigAnalyzer resources in this cluster yet/i)).toBeInTheDocument();
  });

  it('shows analyzer card with name and metadata when data returned', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:id/resources/:type', () => {
        return HttpResponse.json({
          resources: [
            {
              metadata: { name: 'test-analyzer', namespace: 'bnk-demo', uid: 'uid-1', creationTimestamp: '2026-01-01T00:00:00Z' },
              spec: {
                schedule: '15m',
                applications: [{ kind: 'HTTPRoute', group: 'gateway.networking.k8s.io', name: 'my-route', namespace: 'bnk-demo' }],
                dataSources: [{ name: 'prometheus', type: 'prometheus', endpoint: 'http://prom:9090' }],
                script: { type: 'builtin', builtin: { name: 'roundrobin' } },
              },
              status: { conditions: [{ type: 'Programmed', status: 'True' }] },
            },
          ],
        });
      })
    );

    render(<F5AIAnalyzerViewer clusterId={1} />);

    await waitFor(() => {
      expect(screen.getByText('test-analyzer')).toBeInTheDocument();
    });
    expect(screen.getByText('Every 15 minutes')).toBeInTheDocument();
    expect(screen.getByText('1 application')).toBeInTheDocument();
    expect(screen.getByText('1 data source')).toBeInTheDocument();
  });

  it('shows error state on API failure', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:id/resources/:type', () => {
        return HttpResponse.json({ error: 'Forbidden' }, { status: 403 });
      })
    );

    render(<F5AIAnalyzerViewer clusterId={1} />);

    await waitFor(() => {
      expect(screen.getByText('Permission Denied')).toBeInTheDocument();
    });
  });

  it('shows Active (not Pending) when CR has no status conditions but backend health is available', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:id/resources/:type', () => {
        return HttpResponse.json({ resources: [analyzerNoConditions] });
      }),
      http.get('*/api/k8s/clusters/:id/analyzers/:ns/:name/backend-health', () => {
        return HttpResponse.json({
          available: true,
          backends: [
            {
              pod_name: 'llm-pod-0',
              namespace: 'bnk-demo',
              backend_kind: 'vllm',
              model: 'llama3',
              status: 'healthy',
              kv_cache_used_pct: 20,
              running: 2,
              waiting: 0,
              ttft_p95_ms: 120,
              itl_p95_ms: 10,
              output_tps: 50,
              gpu_util_pct: null,
              vram_used_gb: null,
              vram_total_gb: null,
              gpu_temp_c: null,
            },
          ],
          errors: {},
          updated_at: '2026-01-01T00:00:00Z',
        });
      }),
    );

    render(<F5AIAnalyzerViewer clusterId={1} />);

    await waitFor(() => {
      expect(screen.getByText('Active')).toBeInTheDocument();
    });
    expect(screen.queryByText('Pending')).not.toBeInTheDocument();
  });

  it('shows Pending when CR has no status conditions and backend health data is absent', async () => {
    server.use(
      http.get('*/api/k8s/clusters/:id/resources/:type', () => {
        return HttpResponse.json({ resources: [analyzerNoConditions] });
      }),
      http.get('*/api/k8s/clusters/:id/analyzers/:ns/:name/backend-health', () => {
        return HttpResponse.json({
          available: false,
          reason: 'no resolvable backend pods',
          backends: [],
          errors: {},
          updated_at: '2026-01-01T00:00:00Z',
        });
      }),
    );

    render(<F5AIAnalyzerViewer clusterId={1} />);

    await waitFor(() => {
      expect(screen.getByText('Pending')).toBeInTheDocument();
    });
    expect(screen.queryByText('Active')).not.toBeInTheDocument();
  });
});
