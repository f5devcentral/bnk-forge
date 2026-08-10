import type { Project } from '@/types';

export function getEligibleExistingClusterProjects(projects: Project[] | undefined): Project[] {
  if (!projects) return [];
  return projects.filter((project) => (project.cluster_count ?? 0) > 0);
}
