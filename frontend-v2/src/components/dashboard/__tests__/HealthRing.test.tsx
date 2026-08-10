/**
 * Tests for HealthRing component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { HealthRing } from '../HealthRing';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('HealthRing', () => {
  it('renders the score number', () => {
    render(<HealthRing score={85} />);
    expect(screen.getByText('85')).toBeInTheDocument();
    expect(screen.getByText('Health')).toBeInTheDocument();
  });

  it('renders SVG circle elements', () => {
    const { container } = render(<HealthRing score={70} />);
    const circles = container.querySelectorAll('circle');
    expect(circles.length).toBe(2); // background + progress
  });

  it('uses success token stroke for high scores', () => {
    // D-020: stroke now reads from the --success CSS variable instead of a hex.
    const { container } = render(<HealthRing score={90} />);
    const progressCircle = container.querySelectorAll('circle')[1];
    expect(progressCircle?.getAttribute('stroke')).toBe('hsl(var(--success))');
  });

  it('uses destructive token stroke for low scores', () => {
    // D-020: stroke now reads from the --destructive CSS variable instead of a hex.
    const { container } = render(<HealthRing score={40} />);
    const progressCircle = container.querySelectorAll('circle')[1];
    expect(progressCircle?.getAttribute('stroke')).toBe('hsl(var(--destructive))');
  });
});
