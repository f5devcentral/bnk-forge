import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { PresetSelector, EnvironmentPresetSelector } from '../PresetSelector';
import type { PresetCategory } from '@/lib/presets';

const mockCategory: PresetCategory = {
  label: 'Network Size',
  description: 'Choose your network configuration',
  options: [
    {
      id: 'small',
      label: 'Small',
      description: '1 subnet, minimal NAT',
      estimatedCost: '$50/mo',
      variables: {},
      flatVariables: { cidr_block: '10.0.0.0/24', nat_count: '1' },
    },
    {
      id: 'large',
      label: 'Large',
      description: 'Multi-AZ, HA NAT',
      estimatedCost: '$200/mo',
      variables: {},
      flatVariables: { cidr_block: '10.0.0.0/16', nat_count: '3' },
    },
  ],
};

describe('PresetSelector', () => {
  it('renders category label, description, and preset options', () => {
    render(
      <PresetSelector
        category={mockCategory}
        selectedPresetId={null}
        onSelect={() => {}}
        currentValues={{}}
        onFieldChange={() => {}}
      />
    );
    expect(screen.getByText('Network Size')).toBeInTheDocument();
    expect(screen.getByText('Choose your network configuration')).toBeInTheDocument();
    expect(screen.getByText('Small')).toBeInTheDocument();
    expect(screen.getByText('Large')).toBeInTheDocument();
    expect(screen.getByText('Custom')).toBeInTheDocument();
  });

  it('calls onSelect when a preset card is clicked', async () => {
    const onSelect = vi.fn();
    const user = userEvent.setup();

    render(
      <PresetSelector
        category={mockCategory}
        selectedPresetId={null}
        onSelect={onSelect}
        currentValues={{}}
        onFieldChange={() => {}}
      />
    );

    await user.click(screen.getByText('Small'));
    expect(onSelect).toHaveBeenCalledWith('small');
  });

  it('shows Modified badge when preset values differ from current values', () => {
    render(
      <PresetSelector
        category={mockCategory}
        selectedPresetId="small"
        onSelect={() => {}}
        currentValues={{ cidr_block: '10.99.0.0/24', nat_count: '1' }}
        onFieldChange={() => {}}
      />
    );
    expect(screen.getByText('Modified')).toBeInTheDocument();
  });
});

describe('EnvironmentPresetSelector', () => {
  const presets = [
    { id: 'dev', label: 'Development', description: 'Low-cost dev', estimatedCost: '$50/mo', estimatedTime: '10 min' },
    { id: 'prod', label: 'Production', description: 'Full HA', estimatedCost: '$500/mo', estimatedTime: '30 min' },
  ];

  it('renders environment presets with cost and time', () => {
    render(
      <EnvironmentPresetSelector
        selectedId={null}
        onSelect={() => {}}
        presets={presets}
      />
    );
    expect(screen.getByText('Environment Size')).toBeInTheDocument();
    expect(screen.getByText('Development')).toBeInTheDocument();
    expect(screen.getByText('$50/mo')).toBeInTheDocument();
    expect(screen.getByText('10 min')).toBeInTheDocument();
    expect(screen.getByText('Production')).toBeInTheDocument();
    expect(screen.getByText('$500/mo')).toBeInTheDocument();
  });
});
