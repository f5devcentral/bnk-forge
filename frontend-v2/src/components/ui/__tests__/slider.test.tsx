import { describe, it, expect } from 'vitest';
import { render } from '@/test/test-utils';
import { Slider } from '../slider';

describe('Slider', () => {
  it('renders without crashing and applies custom className', () => {
    const { container } = render(
      <Slider defaultValue={[50]} max={100} step={1} className="custom-class" />
    );
    const slider = container.querySelector('[class*="custom-class"]');
    expect(slider).toBeTruthy();
  });
});
