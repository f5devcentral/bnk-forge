/**
 * Tests for ModuleVariableEditor component (in modules/__tests__)
 *
 * Covers: renders variable inputs by type (string, number, bool, list/map),
 * empty state, validation, sensitive toggle, onChange callbacks,
 * auto-wired and project-sourced variable sections.
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

  it('renders empty state when no variables provided', () => {
    render(<ModuleVariableEditor variables={[]} values={{}} onChange={mockOnChange} />);

    expect(screen.getByText('No variables to configure')).toBeInTheDocument();
    expect(screen.getByText('This module uses default values')).toBeInTheDocument();
  });

  it('renders string input with label and type badge', () => {
    const variables = [
      { name: 'region', type: 'string', required: false, description: 'AWS region' },
    ];
    render(<ModuleVariableEditor variables={variables} values={{}} onChange={mockOnChange} />);

    expect(screen.getByText('region')).toBeInTheDocument();
    expect(screen.getByText('string')).toBeInTheDocument();
    expect(screen.getByText('AWS region')).toBeInTheDocument();
  });

  it('renders number input for number type variables', () => {
    const variables = [
      { name: 'instance_count', type: 'number', required: false },
    ];
    render(<ModuleVariableEditor variables={variables} values={{}} onChange={mockOnChange} />);

    const input = screen.getByRole('spinbutton');
    expect(input).toBeInTheDocument();
  });

  it('renders checkbox for boolean type variables', () => {
    const variables = [
      { name: 'enable_logging', type: 'bool', required: false },
    ];
    render(<ModuleVariableEditor variables={variables} values={{ enable_logging: true }} onChange={mockOnChange} />);

    const checkbox = screen.getByRole('checkbox');
    expect(checkbox).toBeInTheDocument();
    expect(checkbox).toBeChecked();
  });

  it('calls onChange with toggled value when checkbox clicked', async () => {
    const user = userEvent.setup();
    const variables = [
      { name: 'enable_dns', type: 'bool', required: false },
    ];
    render(<ModuleVariableEditor variables={variables} values={{ enable_dns: false }} onChange={mockOnChange} />);

    await user.click(screen.getByRole('checkbox'));

    expect(mockOnChange).toHaveBeenCalledWith({ enable_dns: true });
  });

  it('renders textarea for list type with JSON placeholder', () => {
    const variables = [
      { name: 'subnets', type: 'list', required: false },
    ];
    render(<ModuleVariableEditor variables={variables} values={{}} onChange={mockOnChange} />);

    expect(screen.getByPlaceholderText('["item1", "item2"]')).toBeInTheDocument();
  });

  it('renders textarea for map type with JSON placeholder', () => {
    const variables = [
      { name: 'tags', type: 'map', required: false },
    ];
    render(<ModuleVariableEditor variables={variables} values={{}} onChange={mockOnChange} />);

    expect(screen.getByPlaceholderText('{"key": "value"}')).toBeInTheDocument();
  });

  it('shows sensitive badge and password input for sensitive variables', () => {
    const variables = [
      { name: 'db_password', type: 'string', sensitive: true, required: false },
    ];
    render(<ModuleVariableEditor variables={variables} values={{}} onChange={mockOnChange} />);

    expect(screen.getByText('sensitive')).toBeInTheDocument();
    // Input type is password by default for sensitive fields
    const input = document.querySelector('input[type="password"]');
    expect(input).toBeInTheDocument();
  });

  it('separates required and optional variables into sections', () => {
    const variables = [
      { name: 'cluster_name', type: 'string', required: true },
      { name: 'description', type: 'string', required: false },
    ];
    render(<ModuleVariableEditor variables={variables} values={{}} onChange={mockOnChange} />);

    expect(screen.getByText('Required Variables')).toBeInTheDocument();
    expect(screen.getByText('Optional Variables')).toBeInTheDocument();
  });

  it('shows required marker (asterisk) for required variables', () => {
    const variables = [
      { name: 'name', type: 'string', required: true },
    ];
    render(<ModuleVariableEditor variables={variables} values={{}} onChange={mockOnChange} />);

    const asterisks = document.querySelectorAll('.text-destructive');
    expect(asterisks.length).toBeGreaterThan(0);
  });

  it('calls onChange when text input value changes', async () => {
    const user = userEvent.setup();
    const variables = [
      { name: 'vpc_name', type: 'string', required: false },
    ];
    render(<ModuleVariableEditor variables={variables} values={{}} onChange={mockOnChange} />);

    const input = screen.getByPlaceholderText(/enter vpc_name/i);
    await user.type(input, 'my-vpc');

    expect(mockOnChange).toHaveBeenCalled();
  });

  it('renders auto-wired section for module-sourced variables', () => {
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

  it('renders project variables section for project-sourced variables', () => {
    const variables = [
      {
        name: 'aws_region',
        type: 'string',
        source: 'project' as const,
        description: 'Region from project settings',
      },
    ];
    render(<ModuleVariableEditor variables={variables} values={{}} onChange={mockOnChange} />);

    expect(screen.getByText('Project Variables')).toBeInTheDocument();
  });

  it('shows default value as placeholder text', () => {
    const variables = [
      { name: 'instance_type', type: 'string', default: 't3.medium', required: false },
    ];
    render(<ModuleVariableEditor variables={variables} values={{}} onChange={mockOnChange} />);

    expect(screen.getByPlaceholderText('Default: t3.medium')).toBeInTheDocument();
  });
});
