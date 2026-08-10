import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { F5iRuleViewer } from '../F5iRuleViewer';

// Mock Monaco editor
vi.mock('@monaco-editor/react', () => ({
  Editor: ({ value }: { value: string }) => (
    <pre data-testid="mock-editor">{value}</pre>
  ),
}));

const mockResource = {
  kind: 'F5BigCneIrule',
  apiVersion: 'k8s.f5net.com/v1',
  metadata: {
    name: 'my-irule',
    namespace: 'bnk-demo',
    uid: '123',
    creationTimestamp: '2026-01-01T00:00:00Z',
  },
  spec: {
    iRule: 'when HTTP_REQUEST {\n  log local0. "Request received"\n  pool /Common/web_pool\n}\nwhen HTTP_RESPONSE {\n  HTTP::header remove Server\n}',
  },
  status: {
    conditions: [
      { type: 'Accepted', status: 'True', reason: 'Accepted' },
      { type: 'Programmed', status: 'True', reason: 'Programmed' },
    ],
  },
};

describe('F5iRuleViewer', () => {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    resource: mockResource,
  };

  it('renders iRule name and namespace', () => {
    render(<F5iRuleViewer {...defaultProps} />);
    expect(screen.getByText('my-irule')).toBeInTheDocument();
    expect(screen.getByText('bnk-demo')).toBeInTheDocument();
  });

  it('shows line count and event handlers', () => {
    render(<F5iRuleViewer {...defaultProps} />);
    expect(screen.getByText('7 lines')).toBeInTheDocument();
    expect(screen.getByText('Events: HTTP_REQUEST, HTTP_RESPONSE')).toBeInTheDocument();
  });

  it('shows empty state when no iRule code found', () => {
    const emptyResource = {
      ...mockResource,
      spec: {},
    };
    render(<F5iRuleViewer {...defaultProps} resource={emptyResource} />);
    expect(screen.getByText('No iRule code found in this resource')).toBeInTheDocument();
  });
});
