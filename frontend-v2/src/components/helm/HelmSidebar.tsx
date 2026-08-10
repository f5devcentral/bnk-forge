/**
 * Helm Sidebar - Grouped releases by cluster/namespace + chart sources
 */

import { cn } from '@/lib/utils';
import { Package, Library, Globe, Upload, Copy, ChevronRight, ChevronDown } from 'lucide-react';
import { ClusterGroup } from './ClusterGroup';
import { EmptyState } from '@/components/ui/empty-state';
import type { ClusterWithReleases, HelmReleaseWithCluster } from '@/lib/helm-grouping';

interface ChartSource {
  key: string;
  label: string;
  icon: typeof Globe;
  description: string;
}

const chartSources: ChartSource[] = [
  { key: 'artifact-hub', label: 'Artifact Hub', icon: Globe, description: 'Official Helm charts' },
  { key: 'bitnami', label: 'Bitnami', icon: Package, description: 'Production-ready charts' },
  { key: 'uploaded', label: 'Uploaded Charts', icon: Upload, description: 'Your custom charts' },
  { key: 'cloned', label: 'Cloned Charts', icon: Copy, description: 'Modified copies' },
];

interface HelmSidebarProps {
  releases: ClusterWithReleases[];
  selectedRelease: HelmReleaseWithCluster | null;
  onSelectRelease: (release: HelmReleaseWithCluster) => void;
  selectedChartSource: string | null;
  onSelectChartSource: (source: string) => void;
  expandedClusters: Set<number>;
  expandedNamespaces: Set<string>;
  onToggleCluster: (clusterId: number) => void;
  onToggleNamespace: (key: string) => void;
  expandedSections: Set<string>;
  onToggleSection: (section: string) => void;
}

export function HelmSidebar({
  releases,
  selectedRelease,
  onSelectRelease,
  selectedChartSource,
  onSelectChartSource,
  expandedClusters,
  expandedNamespaces,
  onToggleCluster,
  onToggleNamespace,
  expandedSections,
  onToggleSection,
}: HelmSidebarProps) {
  const isReleasesExpanded = expandedSections.has('releases');
  const isChartsExpanded = expandedSections.has('charts');

  return (
    <div className="w-80 border-r flex flex-col h-full">
      {/* Releases Section */}
      <div className="border-b">
        <button
          onClick={() => onToggleSection('releases')}
          className="w-full px-4 py-3 flex items-center gap-2 hover:bg-accent/50 transition-colors"
          aria-expanded={isReleasesExpanded}
          aria-controls="releases-list"
        >
          {isReleasesExpanded ? (
            <ChevronDown className="w-4 h-4 transition-transform duration-200" aria-hidden="true" />
          ) : (
            <ChevronRight className="w-4 h-4 transition-transform duration-200" aria-hidden="true" />
          )}
          <Package className="w-4 h-4" aria-hidden="true" />
          <h3 className="font-semibold text-sm">Releases</h3>
        </button>

        {/* Releases List (scrollable) */}
        {isReleasesExpanded && (
          <div id="releases-list" className="flex-1 overflow-y-auto max-h-[60vh]">
            {releases.length === 0 ? (
              <div className="p-4">
                <EmptyState
                  icon={Package}
                  title="No Helm Releases"
                  description="Install your first Helm chart to get started."
                />
              </div>
            ) : (
              releases.map((cluster) => (
                <ClusterGroup
                  key={cluster.id}
                  cluster={cluster}
                  expanded={expandedClusters.has(cluster.id)}
                  onToggle={() => onToggleCluster(cluster.id)}
                  selectedRelease={selectedRelease}
                  onSelectRelease={onSelectRelease}
                  expandedNamespaces={expandedNamespaces}
                  onToggleNamespace={onToggleNamespace}
                />
              ))
            )}
          </div>
        )}
      </div>

      {/* Charts Section */}
      <div className="border-t">
        <button
          onClick={() => onToggleSection('charts')}
          className="w-full px-4 py-3 flex items-center gap-2 hover:bg-accent/50 transition-colors"
          aria-expanded={isChartsExpanded}
          aria-controls="charts-list"
        >
          {isChartsExpanded ? (
            <ChevronDown className="w-4 h-4 transition-transform duration-200" aria-hidden="true" />
          ) : (
            <ChevronRight className="w-4 h-4 transition-transform duration-200" aria-hidden="true" />
          )}
          <Library className="w-4 h-4" aria-hidden="true" />
          <h3 className="font-semibold text-sm">Charts</h3>
        </button>

        {isChartsExpanded && (
          <div id="charts-list" className="p-2">
            {chartSources.map((source) => (
              <button
                key={source.key}
                onClick={() => onSelectChartSource(source.key)}
                className={cn(
                  'w-full flex items-center gap-2 px-3 py-2 rounded-lg transition-colors',
                  'text-sm text-left',
                  selectedChartSource === source.key
                    ? 'bg-accent'
                    : 'hover:bg-accent/50'
                )}
                title={source.description}
              >
                <source.icon className="w-4 h-4 flex-shrink-0" />
                <span className="flex-1 truncate">{source.label}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
