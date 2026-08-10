/**
 * K8sHelmChartBrowser — Chart browsing within the K8s resource explorer
 * K8S-UX-004: Helm chart browser moved from standalone /helm page into K8s page
 *
 * Browse and search Helm charts from configured repositories.
 * Selecting a chart opens the install dialog with the selected cluster pre-filled.
 */

import { useState, useMemo, memo, useCallback } from 'react';
import { cn } from '@/lib/utils';
import { useThemeClasses } from '@/context/ThemeContext';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { EmptyState } from '@/components/ui/empty-state';
import { useDebounce } from '@/hooks/useDebounce';
import { DEBOUNCE_MS } from '@/lib/constants';
import {
  useHelmRepositories,
  useHelmChartBrowse,
  useHelmChartSearch,
} from '@/hooks/useHelm';
import {
  Search,
  Package,
  Library,
  Download,
  FolderOpen,
  ChevronRight,
} from 'lucide-react';
import type { HelmChart, HelmRepository } from '@/types';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface K8sHelmChartBrowserProps {
  onInstallChart: (chart: { name: string; version: string }) => void;
  onManageRepos: () => void;
}

// ---------------------------------------------------------------------------
// Memoized Chart Card
// ---------------------------------------------------------------------------

interface HelmChartCardProps {
  chart: HelmChart;
  onInstall: (chart: HelmChart) => void;
}

const HelmChartCard = memo(function HelmChartCard({ chart, onInstall }: HelmChartCardProps) {
  const { borderDefault } = useThemeClasses();

  return (
    <div
      className={cn(
        'border rounded-lg p-4 transition-colors bg-card hover:bg-muted/50',
        borderDefault
      )}
    >
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <Package className="h-5 w-5 flex-shrink-0 text-primary" />
          <span className="font-medium text-sm truncate text-foreground">
            {chart.name.includes('/') ? chart.name.split('/')[1] : chart.name}
          </span>
        </div>
        <Badge variant="outline" className="text-xs flex-shrink-0 ml-2">
          v{chart.version}
        </Badge>
      </div>
      <p className="text-xs line-clamp-2 mb-3 text-muted-foreground">
        {chart.description || 'No description available'}
      </p>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {chart.app_version && (
            <span className="text-xs text-muted-foreground">
              App: {chart.app_version}
            </span>
          )}
        </div>
        <Button
          size="sm"
          onClick={() => onInstall(chart)}
          className="h-7 text-xs"
        >
          <Download className="h-3 w-3 mr-1" />
          Install
        </Button>
      </div>
    </div>
  );
});

// ---------------------------------------------------------------------------
// Chart Browser Component
// ---------------------------------------------------------------------------

const INITIAL_CHARTS_LIMIT = 50;

export function K8sHelmChartBrowser({ onInstallChart, onManageRepos }: K8sHelmChartBrowserProps) {
  const { borderDefault } = useThemeClasses();

  // Repository selection
  const [selectedRepo, setSelectedRepo] = useState<string | null>(null);
  const [chartSearchQuery, setChartSearchQuery] = useState('');
  const debouncedChartSearch = useDebounce(chartSearchQuery, DEBOUNCE_MS.SEARCH);
  const [showAllCharts, setShowAllCharts] = useState(false);

  // Data fetching
  const { data: reposData, isLoading: isLoadingRepos } = useHelmRepositories();
  const repositories = reposData?.repositories || [];

  const { data: browseData, isLoading: isLoadingCharts } = useHelmChartBrowse(selectedRepo, {
    enabled: !!selectedRepo,
    maxResults: 200,
  });

  const { data: searchData, isLoading: isSearchingCharts } = useHelmChartSearch(
    debouncedChartSearch,
    selectedRepo || undefined,
    { enabled: debouncedChartSearch.length >= 3 }
  );

  // Charts to display
  const displayCharts = useMemo(() => {
    if (debouncedChartSearch.length >= 3 && searchData?.charts) {
      return searchData.charts;
    }
    return browseData?.charts || [];
  }, [browseData, searchData, debouncedChartSearch]);

  const limitedCharts = showAllCharts ? displayCharts : displayCharts.slice(0, INITIAL_CHARTS_LIMIT);
  const hasMoreCharts = displayCharts.length > INITIAL_CHARTS_LIMIT;

  const handleInstallChart = useCallback(
    (chart: HelmChart) => {
      onInstallChart({ name: chart.name, version: chart.version });
    },
    [onInstallChart]
  );

  const handleSelectRepo = (repoName: string) => {
    setSelectedRepo(repoName);
    setChartSearchQuery('');
    setShowAllCharts(false);
  };

  return (
    <div className="flex-1 flex overflow-hidden">
      {/* Repo sidebar */}
      <div
        className={cn(
          'w-52 flex-shrink-0 border-r overflow-auto p-3 bg-muted/30',
          borderDefault
        )}
      >
        <div className="flex items-center justify-between mb-3">
          <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground/70">
            Repositories
          </span>
          <Button variant="ghost" size="sm" className="h-6 text-xs px-2" onClick={onManageRepos}>
            Manage
          </Button>
        </div>

        {isLoadingRepos ? (
          <div className="space-y-2">
            {[...Array(3)].map((_, i) => (
              <Skeleton key={i} className="h-9 w-full rounded-lg" />
            ))}
          </div>
        ) : repositories.length === 0 ? (
          <div className="text-center py-4">
            <Library className="h-8 w-8 mx-auto mb-2 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">No repositories</p>
            <Button size="sm" variant="link" onClick={onManageRepos} className="mt-1">
              Add repository
            </Button>
          </div>
        ) : (
          repositories.map((repo: HelmRepository) => {
            const isSelected = selectedRepo === repo.name;
            return (
              <button
                key={repo.name}
                onClick={() => handleSelectRepo(repo.name)}
                className={cn(
                  'w-full flex items-center justify-between px-3 py-2 rounded-md text-sm transition-colors mb-0.5 border-l-2',
                  isSelected
                    ? 'bg-primary/10 text-primary font-medium border-l-primary'
                    : 'text-muted-foreground hover:text-foreground hover:bg-muted/50 border-l-transparent'
                )}
              >
                <div className="flex items-center gap-2 min-w-0 flex-1">
                  <Library className={cn('h-4 w-4 flex-shrink-0', isSelected ? 'text-primary' : 'text-muted-foreground')} />
                  <span className="truncate">{repo.name}</span>
                </div>
                <ChevronRight className={cn('h-4 w-4 flex-shrink-0', isSelected ? 'text-primary' : 'text-muted-foreground')} />
              </button>
            );
          })
        )}
      </div>

      {/* Chart grid */}
      <div className="flex-1 overflow-auto">
        {!selectedRepo ? (
          <EmptyState
            icon={FolderOpen}
            title="Select a repository"
            description="Choose a repository from the left to browse available charts"
          />
        ) : (
          <div className="p-4">
            {/* Search within repo */}
            <div className="flex items-center gap-3 mb-4">
              <div className="relative flex-1 max-w-md">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder={`Search in ${selectedRepo}...`}
                  aria-label={`Search charts in ${selectedRepo}`}
                  value={chartSearchQuery}
                  onChange={(e) => setChartSearchQuery(e.target.value)}
                  className="pl-9 h-9"
                />
              </div>
              <span className="text-sm text-muted-foreground">
                {debouncedChartSearch.length >= 3
                  ? `Found ${displayCharts.length} charts`
                  : `${displayCharts.length} charts in ${selectedRepo}`}
              </span>
            </div>

            {isLoadingCharts || isSearchingCharts ? (
              <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-3">
                {[...Array(6)].map((_, i) => (
                  <Skeleton key={i} className="h-28 w-full rounded-lg" />
                ))}
              </div>
            ) : displayCharts.length === 0 ? (
              <EmptyState
                icon={Package}
                title="No charts found"
                description={chartSearchQuery ? `No charts match "${chartSearchQuery}"` : `No charts found in ${selectedRepo}`}
              />
            ) : (
              <>
                <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-3">
                  {limitedCharts.map((chart, index) => (
                    <HelmChartCard
                      key={`${chart.name}-${chart.version}-${index}`}
                      chart={chart}
                      onInstall={handleInstallChart}
                    />
                  ))}
                </div>

                {!showAllCharts && hasMoreCharts && (
                  <Button variant="outline" className="w-full mt-4" onClick={() => setShowAllCharts(true)}>
                    Show {displayCharts.length - INITIAL_CHARTS_LIMIT} more charts
                  </Button>
                )}
                {showAllCharts && hasMoreCharts && (
                  <Button variant="ghost" className="w-full mt-4" onClick={() => setShowAllCharts(false)}>
                    Show less
                  </Button>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
