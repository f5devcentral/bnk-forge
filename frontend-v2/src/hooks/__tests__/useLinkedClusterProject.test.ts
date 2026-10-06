import { describe, it, expect } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useState } from 'react';
import { useLinkedClusterProject } from '../useLinkedClusterProject';

const allClusters = [
  { id: 1, project_id: 10 },
  { id: 2, project_id: 20 },
];

function setup(linked: number | null, storedProject: number | null) {
  return renderHook(
    ({ visible }: { visible: { id: number }[] }) => {
      const [selectedProject, setSelectedProject] = useState<number | null>(storedProject);
      const link = useLinkedClusterProject(linked, {
        allClusters,
        allClustersLoaded: true,
        visibleClusters: visible,
        selectedProject,
        setSelectedProject,
      });
      return { selectedProject, ...link };
    },
    { initialProps: { visible: [{ id: 1 }] } },
  );
}

describe('useLinkedClusterProject', () => {
  it("switches to the linked cluster's project and stays pending until it is listed", () => {
    const { result, rerender } = setup(2, 10);
    expect(result.current.selectedProject).toBe(20);
    expect(result.current.linkPending).toBe(true);

    // Placeholder data for the previous project must not end the link.
    rerender({ visible: [{ id: 1 }] });
    expect(result.current.linkPending).toBe(true);

    rerender({ visible: [{ id: 2 }] });
    expect(result.current.linkPending).toBe(false);
    expect(result.current.selectedProject).toBe(20);
  });

  it('is not pending without a linked cluster', () => {
    const { result } = setup(null, 10);
    expect(result.current.linkPending).toBe(false);
    expect(result.current.selectedProject).toBe(10);
  });

  it('drops a link to an unknown cluster', () => {
    const { result } = setup(99, 10);
    expect(result.current.linkPending).toBe(false);
  });

  it('can be re-armed for an in-page navigation', () => {
    const { result, rerender } = setup(null, 10);
    act(() => result.current.setLinkedCluster(2));
    expect(result.current.selectedProject).toBe(20);
    rerender({ visible: [{ id: 2 }] });
    expect(result.current.linkPending).toBe(false);
  });
});
