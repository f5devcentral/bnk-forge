import { useEffect } from 'react';

interface AutoSelectOptions {
  projects: { id: number }[] | undefined;
  allClusters: { id: number; project_id: number }[];
  visibleClusters: { id: number }[];
  selectedProject: number | null;
  selectedCluster: number | null;
  setSelectedProject: (id: number) => void;
  setSelectedCluster: (id: number) => void;
}

/**
 * Auto-selects the single project and cluster when exactly one of each exists
 * and nothing is currently selected. Used by KubernetesV2 and F5BNK pages.
 */
export function useAutoSelectProjectCluster({
  projects,
  allClusters,
  visibleClusters,
  selectedProject,
  selectedCluster,
  setSelectedProject,
  setSelectedCluster,
}: AutoSelectOptions) {
  useEffect(() => {
    if (!selectedProject && !selectedCluster && projects && projects.length === 1 && allClusters.length === 1) {
      setSelectedProject(projects[0].id);
    }
    if (selectedProject && !selectedCluster && allClusters.length === 1 && visibleClusters.length === 1) {
      setSelectedCluster(visibleClusters[0].id);
    }
  }, [projects, allClusters, visibleClusters, selectedProject, selectedCluster, setSelectedProject, setSelectedCluster]);
}
