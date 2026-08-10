/**
 * Tests for ValueJourneyBanner — GAP-004
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import { ValueJourneyBanner } from '../ValueJourneyBanner';
import { startValueJourney, loadValueJourney } from '@/lib/value-journey';

describe('ValueJourneyBanner', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('renders nothing when no journey is active', () => {
    const { container } = render(<ValueJourneyBanner />);
    expect(container.firstChild).toBeNull();
  });

  it('renders journey banner when a journey is active', () => {
    startValueJourney({
      projectId: 1,
      blueprintSlug: 'aws-k8s-foundation',
      blueprintName: 'AWS EKS Foundation',
    });

    render(<ValueJourneyBanner />);

    expect(screen.getByText(/Value Journey/i)).toBeInTheDocument();
    expect(screen.getByText(/AWS EKS Foundation/i)).toBeInTheDocument();
  });

  it('shows step progress count', () => {
    startValueJourney({
      projectId: 1,
      blueprintSlug: 'test',
      blueprintName: 'Test',
    });

    render(<ValueJourneyBanner />);

    // 1/5 — deploy is auto-completed on start
    expect(screen.getByText('1/5')).toBeInTheDocument();
  });

  it('shows "Next: Validate" as next action after deploy', () => {
    startValueJourney({
      projectId: 1,
      blueprintSlug: 'test',
      blueprintName: 'Test',
    });

    render(<ValueJourneyBanner />);

    expect(screen.getByText(/Next: Validate/i)).toBeInTheDocument();
  });

  it('dismisses the banner and clears journey on close', async () => {
    const user = userEvent.setup();

    startValueJourney({
      projectId: 1,
      blueprintSlug: 'test',
      blueprintName: 'Test',
    });

    render(<ValueJourneyBanner />);

    // Should be visible
    expect(screen.getByText(/Value Journey/i)).toBeInTheDocument();

    // Click dismiss
    const dismissButton = screen.getByLabelText(/dismiss/i);
    await user.click(dismissButton);

    // Banner should be gone
    expect(screen.queryByText(/Value Journey/i)).not.toBeInTheDocument();
    // Journey should be cleared from storage
    expect(loadValueJourney()).toBeNull();
  });
});
