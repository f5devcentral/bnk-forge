/**
 * Tests for ActivityItem component
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { ActivityItem } from '../ActivityItem';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ActivityItem', () => {
  it('renders activity details for success status', () => {
    render(
      <ActivityItem
        action="Deployed"
        module="bnk-vpc"
        project="Production"
        time="5m ago"
        status="success"
      />
    );
    expect(screen.getByText('Deployed')).toBeInTheDocument();
    expect(screen.getByText('bnk-vpc')).toBeInTheDocument();
    expect(screen.getByText('Production')).toBeInTheDocument();
    expect(screen.getByText('5m ago')).toBeInTheDocument();
  });

  it('renders activity details for failed status', () => {
    render(
      <ActivityItem
        action="Failed"
        module="bnk-eks"
        project="Staging"
        time="10m ago"
        status="failed"
      />
    );
    expect(screen.getByText('Failed')).toBeInTheDocument();
    expect(screen.getByText('bnk-eks')).toBeInTheDocument();
  });

  it('renders activity details for progress status', () => {
    render(
      <ActivityItem
        action="Deploying"
        module="bnk-rds"
        project="Dev"
        time="1m ago"
        status="progress"
      />
    );
    expect(screen.getByText('Deploying')).toBeInTheDocument();
    expect(screen.getByText('bnk-rds')).toBeInTheDocument();
  });
});
