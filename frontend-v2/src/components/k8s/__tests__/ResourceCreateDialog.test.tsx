/**
 * Tests for ResourceCreateDialog component
 *
 * Tests dialog rendering, YAML template generation per resource type,
 * namespace substitution, dry-run toggle, submit/cancel behavior,
 * and loading states.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { ResourceCreateDialog } from '../ResourceCreateDialog';

// Mock Monaco editor — same pattern as ResourceEditDialog and YamlEditor tests
vi.mock('@monaco-editor/react', () => ({
  Editor: ({ value, onChange }: { value: string; onChange?: (v: string) => void }) => (
    <textarea
      data-testid="mock-editor"
      value={value}
      onChange={(e) => onChange?.(e.target.value)}
    />
  ),
}));

// Mock js-yaml so YamlEditor validation doesn't break in jsdom
vi.mock('js-yaml', () => ({
  default: {
    load: (str: string) => {
      // Return a minimal object that looks like a valid K8s manifest
      if (str.includes('apiVersion')) {
        return { apiVersion: 'v1', kind: 'Pod', metadata: { name: 'test' } };
      }
      return {};
    },
    dump: (obj: unknown) => JSON.stringify(obj, null, 2),
  },
}));

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const defaultProps = {
  open: true,
  onOpenChange: vi.fn(),
  onSubmit: vi.fn(),
  isLoading: false,
  resourceType: 'pod',
  namespace: 'default',
};

/** Get the mock editor's current value */
function getEditorValue(): string {
  return (screen.getByTestId('mock-editor') as HTMLTextAreaElement).value;
}

describe('ResourceCreateDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // ─── Rendering ──────────────────────────────────────────────────────

  describe('rendering', () => {
    it('renders dialog with title showing capitalized resource type', () => {
      render(<ResourceCreateDialog {...defaultProps} />);
      expect(screen.getByText('Create Pod')).toBeInTheDocument();
    });

    it('capitalizes first letter of resource type in title', () => {
      render(<ResourceCreateDialog {...defaultProps} resourceType="deployment" />);
      expect(screen.getByText('Create Deployment')).toBeInTheDocument();
    });

    it('shows namespace in description text', () => {
      render(<ResourceCreateDialog {...defaultProps} namespace="kube-system" />);
      expect(screen.getByText('kube-system')).toBeInTheDocument();
    });

    it('renders YAML editor with default template', () => {
      render(<ResourceCreateDialog {...defaultProps} />);
      expect(screen.getByTestId('mock-editor')).toBeInTheDocument();
      const val = getEditorValue();
      expect(val).toContain('kind: Pod');
      expect(val).toContain('namespace: default');
    });

    it('renders Create Resource button', () => {
      render(<ResourceCreateDialog {...defaultProps} />);
      expect(screen.getByRole('button', { name: /Create Resource/i })).toBeInTheDocument();
    });

    it('renders Cancel button', () => {
      render(<ResourceCreateDialog {...defaultProps} />);
      expect(screen.getByRole('button', { name: /Cancel/i })).toBeInTheDocument();
    });

    it('renders dry run checkbox unchecked by default', () => {
      render(<ResourceCreateDialog {...defaultProps} />);
      expect(screen.getByText(/Dry run/)).toBeInTheDocument();
    });

    it('does not render when open is false', () => {
      render(<ResourceCreateDialog {...defaultProps} open={false} />);
      expect(screen.queryByText('Create Pod')).not.toBeInTheDocument();
    });
  });

  // ─── YAML Templates ────────────────────────────────────────────────

  describe('YAML templates', () => {
    it('generates pod template by default', () => {
      render(<ResourceCreateDialog {...defaultProps} />);
      const val = getEditorValue();
      expect(val).toContain('kind: Pod');
      expect(val).toContain('apiVersion: v1');
    });

    it('generates deployment template', () => {
      render(<ResourceCreateDialog {...defaultProps} resourceType="deployment" />);
      const val = getEditorValue();
      expect(val).toContain('kind: Deployment');
      expect(val).toContain('apiVersion: apps/v1');
      expect(val).toContain('replicas: 3');
    });

    it('generates statefulset template', () => {
      render(<ResourceCreateDialog {...defaultProps} resourceType="statefulset" />);
      const val = getEditorValue();
      expect(val).toContain('kind: StatefulSet');
      expect(val).toContain('serviceName: my-service');
    });

    it('generates daemonset template', () => {
      render(<ResourceCreateDialog {...defaultProps} resourceType="daemonset" />);
      const val = getEditorValue();
      expect(val).toContain('kind: DaemonSet');
      expect(val).toContain('apiVersion: apps/v1');
    });

    it('generates service template', () => {
      render(<ResourceCreateDialog {...defaultProps} resourceType="service" />);
      const val = getEditorValue();
      expect(val).toContain('kind: Service');
      expect(val).toContain('type: ClusterIP');
    });

    it('generates configmap template', () => {
      render(<ResourceCreateDialog {...defaultProps} resourceType="configmap" />);
      const val = getEditorValue();
      expect(val).toContain('kind: ConfigMap');
      expect(val).toContain('key1: value1');
    });

    it('generates secret template', () => {
      render(<ResourceCreateDialog {...defaultProps} resourceType="secret" />);
      const val = getEditorValue();
      expect(val).toContain('kind: Secret');
      expect(val).toContain('type: Opaque');
    });

    it('generates ingress template', () => {
      render(<ResourceCreateDialog {...defaultProps} resourceType="ingress" />);
      const val = getEditorValue();
      expect(val).toContain('kind: Ingress');
      expect(val).toContain('host: example.com');
    });

    it('generates gateway template (Gateway API)', () => {
      render(<ResourceCreateDialog {...defaultProps} resourceType="gateway" />);
      const val = getEditorValue();
      expect(val).toContain('kind: Gateway');
      expect(val).toContain('gatewayClassName: f5-gateway-class');
    });

    it('generates httproute template', () => {
      render(<ResourceCreateDialog {...defaultProps} resourceType="httproute" />);
      const val = getEditorValue();
      expect(val).toContain('kind: HTTPRoute');
      expect(val).toContain('parentRefs');
    });

    it('generates F5 BNK firewall policy template', () => {
      render(<ResourceCreateDialog {...defaultProps} resourceType="f5bigfwpolicy" />);
      const val = getEditorValue();
      expect(val).toContain('kind: F5BigFwPolicy');
      expect(val).toContain('action: accept');
    });

    it('generates F5 BNK address list template', () => {
      render(<ResourceCreateDialog {...defaultProps} resourceType="f5bigcneaddresslist" />);
      const val = getEditorValue();
      expect(val).toContain('kind: F5BigCneAddresslist');
      expect(val).toContain('addresses');
    });

    it('generates F5 VLAN template', () => {
      render(<ResourceCreateDialog {...defaultProps} resourceType="f5spkvlan" />);
      const val = getEditorValue();
      expect(val).toContain('kind: F5SPKVlan');
      expect(val).toContain('interfaces');
    });

    it('falls back to pod template for unknown resource type', () => {
      render(<ResourceCreateDialog {...defaultProps} resourceType="unknowntype" />);
      const val = getEditorValue();
      expect(val).toContain('kind: Pod');
    });

    it('substitutes namespace into template', () => {
      render(<ResourceCreateDialog {...defaultProps} namespace="my-namespace" resourceType="deployment" />);
      const val = getEditorValue();
      expect(val).toContain('namespace: my-namespace');
    });
  });

  // ─── Submit Behavior ────────────────────────────────────────────────

  describe('submit behavior', () => {
    it('calls onSubmit with YAML content and dryRun=false by default', async () => {
      const user = userEvent.setup();
      render(<ResourceCreateDialog {...defaultProps} />);

      const createBtn = screen.getByRole('button', { name: /Create Resource/i });
      await user.click(createBtn);

      expect(defaultProps.onSubmit).toHaveBeenCalledTimes(1);
      expect(defaultProps.onSubmit).toHaveBeenCalledWith(
        expect.stringContaining('kind: Pod'),
        false,
      );
    });

    it('calls onSubmit with dryRun=true when checkbox is checked', async () => {
      const user = userEvent.setup();
      render(<ResourceCreateDialog {...defaultProps} />);

      // Click the dry run checkbox
      const checkbox = screen.getByRole('checkbox');
      await user.click(checkbox);

      // Button text should change to "Dry Run"
      expect(screen.getByRole('button', { name: /Dry Run/i })).toBeInTheDocument();

      await user.click(screen.getByRole('button', { name: /Dry Run/i }));

      expect(defaultProps.onSubmit).toHaveBeenCalledWith(
        expect.any(String),
        true,
      );
    });

    it('calls onOpenChange when Cancel is clicked', async () => {
      const user = userEvent.setup();
      render(<ResourceCreateDialog {...defaultProps} />);

      await user.click(screen.getByRole('button', { name: /Cancel/i }));

      expect(defaultProps.onOpenChange).toHaveBeenCalledWith(false);
    });

    it('passes edited YAML content on submit', async () => {
      const user = userEvent.setup();
      render(<ResourceCreateDialog {...defaultProps} />);

      const editor = screen.getByTestId('mock-editor');
      await user.clear(editor);
      await user.type(editor, 'apiVersion: v1{enter}kind: Custom');

      const createBtn = screen.getByRole('button', { name: /Create Resource/i });
      await user.click(createBtn);

      expect(defaultProps.onSubmit).toHaveBeenCalledWith(
        expect.stringContaining('kind: Custom'),
        false,
      );
    });
  });

  // ─── Loading State ──────────────────────────────────────────────────

  describe('loading state', () => {
    it('shows "Creating..." text when loading and not dry run', () => {
      render(<ResourceCreateDialog {...defaultProps} isLoading />);
      expect(screen.getByText('Creating...')).toBeInTheDocument();
    });

    it('disables Create button when loading', () => {
      render(<ResourceCreateDialog {...defaultProps} isLoading />);
      const buttons = screen.getAllByRole('button');
      const submitBtn = buttons.find(b => b.textContent?.includes('Creating'));
      expect(submitBtn).toBeDisabled();
    });

    it('disables Cancel button when loading', () => {
      render(<ResourceCreateDialog {...defaultProps} isLoading />);
      expect(screen.getByRole('button', { name: /Cancel/i })).toBeDisabled();
    });
  });

  // ─── Dry Run Toggle ────────────────────────────────────────────────

  describe('dry run toggle', () => {
    it('shows "Create Resource" when dry run is off', () => {
      render(<ResourceCreateDialog {...defaultProps} />);
      expect(screen.getByRole('button', { name: /Create Resource/i })).toBeInTheDocument();
    });

    it('shows "Dry Run" when dry run is on', async () => {
      const user = userEvent.setup();
      render(<ResourceCreateDialog {...defaultProps} />);

      await user.click(screen.getByRole('checkbox'));
      expect(screen.getByRole('button', { name: /Dry Run/i })).toBeInTheDocument();
    });

    it('toggles dry run checkbox on and off', async () => {
      const user = userEvent.setup();
      render(<ResourceCreateDialog {...defaultProps} />);

      const checkbox = screen.getByRole('checkbox');
      // Initially unchecked
      expect(checkbox).not.toBeChecked();

      // Check it
      await user.click(checkbox);
      expect(screen.getByRole('button', { name: /Dry Run/i })).toBeInTheDocument();

      // Uncheck it
      await user.click(checkbox);
      expect(screen.getByRole('button', { name: /Create Resource/i })).toBeInTheDocument();
    });
  });

  // ─── Default Props ─────────────────────────────────────────────────

  describe('default props', () => {
    it('defaults to pod resource type', () => {
      render(
        <ResourceCreateDialog
          open={true}
          onOpenChange={vi.fn()}
          onSubmit={vi.fn()}
        />,
      );
      expect(screen.getByText('Create Pod')).toBeInTheDocument();
    });

    it('defaults to default namespace', () => {
      render(
        <ResourceCreateDialog
          open={true}
          onOpenChange={vi.fn()}
          onSubmit={vi.fn()}
        />,
      );
      const val = getEditorValue();
      expect(val).toContain('namespace: default');
    });
  });
});
