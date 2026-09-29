import { useState } from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@/test/test-utils';
import { RegionSelector } from '../RegionSelector';

function StatefulRegionSelector() {
  const [value, setValue] = useState('');
  return <RegionSelector value={value} onValueChange={setValue} />;
}

describe('RegionSelector', () => {
  it('renders a text input that accepts custom regions', () => {
    render(<StatefulRegionSelector />);

    const input = screen.getByPlaceholderText('e.g. us-east-1') as HTMLInputElement;
    expect(input.tagName).toBe('INPUT');

    fireEvent.change(input, { target: { value: 'eu-fr2' } });
    expect(input).toHaveValue('eu-fr2');
  });

  it('renders a datalist with known AWS regions', () => {
    render(<RegionSelector value="" onValueChange={vi.fn()} />);

    const listId = screen.getByPlaceholderText('e.g. us-east-1').getAttribute('list')!;
    const datalist = document.getElementById(listId) as HTMLDataListElement;
    expect(datalist).toBeInTheDocument();
    expect(datalist.options.length).toBeGreaterThan(20);
  });

  it('gives each instance its own input and datalist ids', () => {
    render(
      <>
        <RegionSelector value="" onValueChange={vi.fn()} />
        <RegionSelector value="" onValueChange={vi.fn()} />
      </>,
    );

    const [a, b] = screen.getAllByPlaceholderText('e.g. us-east-1');
    expect(a.id).not.toBe(b.id);
    expect(a.getAttribute('list')).not.toBe(b.getAttribute('list'));
  });
});
