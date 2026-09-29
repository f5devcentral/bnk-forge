import { useEffect, useState } from 'react';

interface LinkedClusterOptions {
  allClusters: { id: number; project_id: number }[];
  allClustersLoaded: boolean;
  visibleClusters: { id: number }[];
  selectedProject: number | null;
  setSelectedProject: (id: number) => void;
}

/**
 * A `?cluster=` deep link must land on that cluster's project, not the project
 * restored from localStorage. Switches the project to the linked cluster's and
 * reports `pending` until the cluster is listed for it, so the page's
 * "cluster not in project" reset waits instead of replacing the linked cluster.
 */
export function useLinkedClusterProject(
  initialLinkedCluster: number | null,
  { allClusters, allClustersLoaded, visibleClusters, selectedProject, setSelectedProject }: LinkedClusterOptions,
) {
  const [linkedCluster, setLinkedCluster] = useState<number | null>(initialLinkedCluster);

  useEffect(() => {
    if (linkedCluster === null || !allClustersLoaded) return;
    const match = allClusters.find((c) => c.id === linkedCluster);
    if (!match?.project_id) {
      setLinkedCluster(null);
    } else if (match.project_id !== selectedProject) {
      setSelectedProject(match.project_id);
    } else if (visibleClusters.some((c) => c.id === linkedCluster)) {
      setLinkedCluster(null);
    }
  }, [linkedCluster, allClusters, allClustersLoaded, visibleClusters, selectedProject, setSelectedProject]);

  return { linkPending: linkedCluster !== null, setLinkedCluster };
}
