import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@/test/test-utils';
import { ProjectCard } from '../ProjectCard';
import type { Project } from '@/types';

beforeEach(() => {
  vi.clearAllMocks();
});

const baseProject: Project = {
  id: 1,
  name: 'My Project',
  description: 'A test project description',
  project_type: 'cloud-aws',
  color: '#2563eb',
  icon: '',
  is_active: true,
  has_deployments: true,
  module_count: 3,
  deployed_count: 2,
  failed_count: 0,
  region: 'us-east-1',
  created_at: '2026-01-15T00:00:00Z',
  updated_at: '2026-02-01T00:00:00Z',
};

describe('ProjectCard', () => {
  it('renders project name, description, and link', () => {
    render(<ProjectCard project={baseProject} />);

    expect(screen.getByText('My Project')).toBeInTheDocument();
    expect(screen.getByText('A test project description')).toBeInTheDocument();
    // Has a link to the project detail page
    expect(screen.getByRole('link', { name: /view project my project/i })).toHaveAttribute(
      'href',
      '/projects/1'
    );
  });

  it('shows Deployed badge when project has deployments', () => {
    render(<ProjectCard project={baseProject} />);

    // "Deployed" appears both as a badge and in the stats display
    const deployed = screen.getAllByText('Deployed');
    expect(deployed.length).toBeGreaterThanOrEqual(1);
  });
});
