import { describe, it, expect } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { ConditionsList } from '../ConditionsList';

describe('ConditionsList', () => {
  it('renders empty text when no conditions provided', () => {
    render(<ConditionsList conditions={[]} />);
    expect(screen.getByText('No conditions available')).toBeInTheDocument();
  });

  it('renders condition type, status, reason and message', () => {
    render(
      <ConditionsList
        conditions={[
          { type: 'Accepted', status: 'True', reason: 'Accepted', message: 'Gateway accepted' },
        ]}
      />
    );
    expect(screen.getByText('Accepted')).toBeInTheDocument();
    expect(screen.getByText('True')).toBeInTheDocument();
    expect(screen.getByText(/Gateway accepted/)).toBeInTheDocument();
  });

  it('color-codes false conditions as destructive', () => {
    render(
      <ConditionsList
        conditions={[{ type: 'Programmed', status: 'False', message: 'address conflict' }]}
      />
    );
    expect(screen.getByText('Programmed')).toBeInTheDocument();
    expect(screen.getByText('False')).toBeInTheDocument();
    expect(screen.getByText(/address conflict/)).toBeInTheDocument();
  });

  it('treats status=False as healthy for negative-polarity types', () => {
    render(
      <ConditionsList
        conditions={[
          { type: 'Conflicted', status: 'False' },
          { type: 'ResolvedRefs', status: 'False' },
        ]}
      />
    );
    const [conflicted, resolvedRefs] = screen.getAllByText('False');
    expect(conflicted.className).toContain('success');
    expect(resolvedRefs.className).toContain('destructive');
  });

  it('keeps the card neutral and puts status color on small elements', () => {
    const { container } = render(
      <ConditionsList conditions={[{ type: 'Programmed', status: 'False' }]} />
    );
    const card = container.querySelector('.rounded-md.border') as HTMLElement;
    expect(card.className).not.toMatch(/destructive|success|warning/);
  });
});
