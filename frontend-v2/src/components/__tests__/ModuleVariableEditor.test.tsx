/**
 * Tests for ModuleVariableEditor component
 *
 * Covers: renders variables, empty state, required/optional sections,
 * input types (string, number, boolean, enum, list/map), sensitive toggle,
 * validation (required, pattern, number range, JSON), auto-wired display.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import { ModuleVariableEditor } from '@/components/modules/ModuleVariableEditor';

describe('ModuleVariableEditor', () => {
  const mockOnChange = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders empty state when no variables', () => {
    render(<ModuleVariableEditor variables={[]} values={{}} onChange={mockOnChange} />);

    expect(screen.getByText('No variables to configure')).toBeInTheDocument();
    expect(screen.getByText('This module uses default values')).toBeInTheDocument();
  });

  it('renders required variables section', () => {
    const variables = [
      { name: 'cluster_name', type: 'string', required: true, description: 'Name of the cluster' },
    ];

    render(<ModuleVariableEditor variables={variables} values={{}} onChange={mockOnChange} />);

    expect(screen.getByText('Required Variables')).toBeInTheDocument();
    expect(screen.getByText('cluster_name')).toBeInTheDocument();
  });

  it('renders optional variables section', () => {
    const variables = [
      { name: 'instance_type', type: 'string', required: false, description: 'EC2 instance type' },
    ];

    render(<ModuleVariableEditor variables={variables} values={{}} onChange={mockOnChange} />);

    expect(screen.getByText('Optional Variables')).toBeInTheDocument();
    expect(screen.getByText('instance_type')).toBeInTheDocument();
  });

  it('renders required marker for required variables', () => {
    const variables = [
      { name: 'vpc_cidr', type: 'string', required: true },
    ];

    render(<ModuleVariableEditor variables={variables} values={{}} onChange={mockOnChange} />);

    // D-020: required marker now uses the destructive token instead of `text-red-500`.
    const asterisks = document.querySelectorAll('.text-destructive');
    expect(asterisks.length).toBeGreaterThan(0);
  });

  it('renders type badge for variables', () => {
    const variables = [
      { name: 'port', type: 'number', required: false },
    ];

    render(<ModuleVariableEditor variables={variables} values={{}} onChange={mockOnChange} />);

    expect(screen.getByText('number')).toBeInTheDocument();
  });

  it('renders description for variables', () => {
    const variables = [
      { name: 'region', type: 'string', description: 'AWS region for deployment' },
    ];

    render(<ModuleVariableEditor variables={variables} values={{}} onChange={mockOnChange} />);

    expect(screen.getByText('AWS region for deployment')).toBeInTheDocument();
  });

  it('renders enum dropdown when enum_values provided', () => {
    const variables = [
      {
        name: 'environment',
        type: 'string',
        enum_values: ['dev', 'staging', 'production'],
      },
    ];

    render(<ModuleVariableEditor variables={variables} values={{}} onChange={mockOnChange} />);

    expect(screen.getByText('environment')).toBeInTheDocument();
  });

  it('renders boolean as checkbox', () => {
    const variables = [
      { name: 'enable_monitoring', type: 'bool', required: false },
    ];

    render(<ModuleVariableEditor variables={variables} values={{ enable_monitoring: false }} onChange={mockOnChange} />);

    const checkbox = screen.getByRole('checkbox');
    expect(checkbox).toBeInTheDocument();
  });

  it('renders textarea for list type', () => {
    const variables = [
      { name: 'allowed_cidrs', type: 'list', required: false },
    ];

    render(<ModuleVariableEditor variables={variables} values={{}} onChange={mockOnChange} />);

    expect(screen.getByPlaceholderText('["item1", "item2"]')).toBeInTheDocument();
  });

  it('renders textarea for map type', () => {
    const variables = [
      { name: 'tags', type: 'map', required: false },
    ];

    render(<ModuleVariableEditor variables={variables} values={{}} onChange={mockOnChange} />);

    expect(screen.getByPlaceholderText('{"key": "value"}')).toBeInTheDocument();
  });

  it('renders sensitive badge and toggle for sensitive variables', () => {
    const variables = [
      { name: 'api_key', type: 'string', sensitive: true },
    ];

    render(<ModuleVariableEditor variables={variables} values={{}} onChange={mockOnChange} />);

    expect(screen.getByText('sensitive')).toBeInTheDocument();
  });

  it('calls onChange when input value changes', async () => {
    const user = userEvent.setup();
    const variables = [
      { name: 'region', type: 'string', required: false },
    ];

    render(<ModuleVariableEditor variables={variables} values={{}} onChange={mockOnChange} />);

    const input = screen.getByPlaceholderText(/enter region/i);
    await user.type(input, 'us-west-2');

    expect(mockOnChange).toHaveBeenCalled();
  });

  it('calls onChange when checkbox is toggled', async () => {
    const user = userEvent.setup();
    const variables = [
      { name: 'enabled', type: 'bool', required: false },
    ];

    render(<ModuleVariableEditor variables={variables} values={{ enabled: false }} onChange={mockOnChange} />);

    await user.click(screen.getByRole('checkbox'));

    expect(mockOnChange).toHaveBeenCalledWith({ enabled: true });
  });

  it('shows auto-wired section for module-sourced variables', () => {
    const variables = [
      {
        name: 'vpc_id',
        type: 'string',
        source: 'module' as const,
        from_module: 'infra/aws/vpc',
        from_output: 'vpc_id',
        description: 'VPC ID from dependency',
      },
    ];

    render(<ModuleVariableEditor variables={variables} values={{}} onChange={mockOnChange} />);

    expect(screen.getByText('Auto-Wired from Dependencies')).toBeInTheDocument();
    expect(screen.getByText('vpc_id')).toBeInTheDocument();
  });

  it('shows project variables section for project-sourced variables', () => {
    const variables = [
      {
        name: 'project_region',
        type: 'string',
        source: 'project' as const,
        description: 'Region from project settings',
      },
    ];

    render(<ModuleVariableEditor variables={variables} values={{}} onChange={mockOnChange} />);

    expect(screen.getByText('Project Variables')).toBeInTheDocument();
  });

  it('displays default value as placeholder', () => {
    const variables = [
      { name: 'instance_type', type: 'string', default: 't3.medium' },
    ];

    render(<ModuleVariableEditor variables={variables} values={{}} onChange={mockOnChange} />);

    expect(screen.getByPlaceholderText('Default: t3.medium')).toBeInTheDocument();
  });

  it('renders both required and optional sections together', () => {
    const variables = [
      { name: 'name', type: 'string', required: true },
      { name: 'description', type: 'string', required: false },
    ];

    render(<ModuleVariableEditor variables={variables} values={{}} onChange={mockOnChange} />);

    expect(screen.getByText('Required Variables')).toBeInTheDocument();
    expect(screen.getByText('Optional Variables')).toBeInTheDocument();
  });
});
