/**
 * Tests for CollapsibleSection component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@/test/test-utils';
import userEvent from '@testing-library/user-event';
import { Activity } from 'lucide-react';
import { CollapsibleSection } from '../CollapsibleSection';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('CollapsibleSection', () => {
  it('renders title and is collapsed by default', () => {
    render(
      <CollapsibleSection title="Test Section" icon={Activity}>
        <div>Section Content</div>
      </CollapsibleSection>
    );
    expect(screen.getByText('Test Section')).toBeInTheDocument();
    expect(screen.queryByText('Section Content')).not.toBeInTheDocument();
  });

  it('renders children when defaultOpen is true', () => {
    render(
      <CollapsibleSection title="Open Section" icon={Activity} defaultOpen>
        <div>Visible Content</div>
      </CollapsibleSection>
    );
    expect(screen.getByText('Visible Content')).toBeInTheDocument();
  });

  it('toggles content on click and calls onOpenChange', async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(
      <CollapsibleSection title="Toggle Section" icon={Activity} onOpenChange={onOpenChange}>
        <div>Hidden Content</div>
      </CollapsibleSection>
    );

    expect(screen.queryByText('Hidden Content')).not.toBeInTheDocument();

    await user.click(screen.getByText('Toggle Section'));
    expect(screen.getByText('Hidden Content')).toBeInTheDocument();
    expect(onOpenChange).toHaveBeenCalledWith(true);

    await user.click(screen.getByText('Toggle Section'));
    expect(screen.queryByText('Hidden Content')).not.toBeInTheDocument();
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
