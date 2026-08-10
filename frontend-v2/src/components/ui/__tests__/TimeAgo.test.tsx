import { describe, it, expect } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { TimeAgo } from '../TimeAgo';

describe('TimeAgo', () => {
  it('renders fallback text when dateStr is null', () => {
    render(<TimeAgo dateStr={null} />);
    expect(screen.getByText('--')).toBeInTheDocument();
  });

  it('renders custom fallback when dateStr is undefined', () => {
    render(<TimeAgo dateStr={undefined} fallback="Never" />);
    expect(screen.getByText('Never')).toBeInTheDocument();
  });

  it('renders relative time for a valid date string', () => {
    // Use a date far in the past to get a stable "ago" string
    render(<TimeAgo dateStr="2020-01-01T00:00:00Z" />);
    // formatTimeAgo should produce some string — just verify something renders (not fallback)
    const span = screen.queryByText('--');
    expect(span).not.toBeInTheDocument();
  });

  it('sets title attribute to full date string', () => {
    render(<TimeAgo dateStr="2026-02-18T10:00:00Z" />);
    const el = document.querySelector('span[title]');
    expect(el).toBeTruthy();
    // The title should contain the formatted local date
    expect(el?.getAttribute('title')).toContain('2026');
  });
});
