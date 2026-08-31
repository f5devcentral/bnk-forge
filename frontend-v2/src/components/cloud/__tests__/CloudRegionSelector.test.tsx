import { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@/test/test-utils';
import { CloudRegionSelector, type CloudRegionSelectorProps } from '../CloudRegionSelector';

function StatefulCloudRegionSelector(props: Omit<CloudRegionSelectorProps, 'value' | 'onValueChange'>) {
  const [value, setValue] = useState('');
  return <CloudRegionSelector {...props} value={value} onValueChange={setValue} />;
}

describe('CloudRegionSelector', () => {
  it('renders a free-text input for AWS with known region datalist', () => {
    render(<StatefulCloudRegionSelector provider="aws" />);

    const input = screen.getByPlaceholderText('Enter region') as HTMLInputElement;
    expect(input.tagName).toBe('INPUT');
    // AWS delegates to RegionSelector; the datalist uses the parent id.
    expect(document.getElementById('cloud-region-input-suggestions')).toBeInTheDocument();
  });

  it('renders a free-text input for IBM with suggestions', () => {
    const options = [
      { value: 'us-south', label: 'US South (Dallas)' },
      { value: 'eu-de', label: 'EU Germany (Frankfurt)' },
    ];
    render(<StatefulCloudRegionSelector provider="ibm" options={options} />);

    const input = screen.getByPlaceholderText('Enter region') as HTMLInputElement;
    expect(input.tagName).toBe('INPUT');

    const datalist = document.getElementById('cloud-region-input-ibm-suggestions') as HTMLDataListElement;
    expect(datalist).toBeInTheDocument();
    expect(datalist.options.length).toBe(2);
  });

  it('accepts arbitrary region input for IBM', () => {
    render(<StatefulCloudRegionSelector provider="ibm" />);

    const input = screen.getByPlaceholderText('Enter region') as HTMLInputElement;
    fireEvent.change(input, { target: { value: 'eu-fr2' } });
    expect(input).toHaveValue('eu-fr2');
  });

  it('renders a free-text input for Azure and GCP', () => {
    const { rerender } = render(<CloudRegionSelector provider="azure" value="" onValueChange={vi.fn()} />);
    expect(screen.getByPlaceholderText('Enter region').tagName).toBe('INPUT');

    rerender(<CloudRegionSelector provider="gcp" value="" onValueChange={vi.fn()} />);
    expect(screen.getByPlaceholderText('Enter region').tagName).toBe('INPUT');
  });
});
