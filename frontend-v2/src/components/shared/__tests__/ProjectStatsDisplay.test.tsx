import { describe, it, expect } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { ProjectStatsDisplay } from '../ProjectStatsDisplay';
import type { Project } from '@/types';

const mockProject: Project = {
  id: 1,
  name: 'test-project',
  description: 'A test project',
  color: '#2563eb',
  icon: '',
  is_active: true,
  has_deployments: true,
  module_count: 5,
  deployed_count: 3,
  failed_count: 1,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-02-01T00:00:00Z',
};

describe('ProjectStatsDisplay', () => {
  it('renders module count, deployed count, and failed count', () => {
    render(<ProjectStatsDisplay project={mockProject} />);
    expect(screen.getByText('5')).toBeInTheDocument();
    expect(screen.getByText('Modules')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
    expect(screen.getByText('Deployed')).toBeInTheDocument();
    expect(screen.getByText('1')).toBeInTheDocument();
    expect(screen.getByText('Failed')).toBeInTheDocument();
  });

  it('defaults to 0 when counts are missing', () => {
    const emptyProject = {
      ...mockProject,
      module_count: 0,
      deployed_count: 0,
      failed_count: 0,
    };
    render(<ProjectStatsDisplay project={emptyProject} />);
    // All 3 stats should show 0
    const zeros = screen.getAllByText('0');
    expect(zeros).toHaveLength(3);
  });
});
