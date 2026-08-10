/**
 * Chart Browser Panel
 *
 * Browse and search Helm charts from various sources
 */

import { useState } from 'react';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { EmptyState } from '@/components/ui/empty-state';
import { useHelmChartSearch } from '@/hooks/useHelm';
import { Search, X, Package, Download, ExternalLink } from 'lucide-react';
import { useDebounce } from '@/hooks/useDebounce';
import { DEBOUNCE_MS } from '@/lib/constants';
import type { HelmChart } from '@/types';

interface ChartBrowserPanelProps {
  source: string;
  onClose: () => void;
  onInstallChart: (chart: HelmChart) => void;
}

export function ChartBrowserPanel({ source, onClose, onInstallChart }: ChartBrowserPanelProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const debouncedSearch = useDebounce(searchQuery, DEBOUNCE_MS.SEARCH);

  // Fetch charts based on source
  const { data: chartsData, isLoading } = useHelmChartSearch(
    debouncedSearch || getDefaultSearchForSource(source),
    undefined, // repository parameter
    { enabled: true }
  );

  const charts = chartsData?.charts || [];

  // Get source display name
  const getSourceName = (source: string): string => {
    const names: Record<string, string> = {
      'artifact-hub': 'Artifact Hub',
      'bitnami': 'Bitnami',
      'uploaded': 'Uploaded Charts',
      'cloned': 'Cloned Charts',
    };
    return names[source] || source;
  };

  // Get default search term for source
  function getDefaultSearchForSource(source: string): string {
    if (source === 'bitnami') return 'bitnami/';
    return '';
  }

  return (
    <div className="flex flex-col h-full bg-card">
      {/* Header */}
      <div className="border-b border-border px-6 py-4">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-xl font-bold">Chart Browser</h2>
            <p className="text-sm text-muted-foreground mt-1">
              {getSourceName(source)}
            </p>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={onClose}
          >
            <X className="w-4 h-4" />
          </Button>
        </div>

        {/* Search Bar */}
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            placeholder="Search charts..."
            aria-label="Search Helm charts"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-10"
          />
        </div>
      </div>

      {/* Chart Grid */}
      <div className="flex-1 overflow-y-auto p-6">
        {isLoading ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {[...Array(6)].map((_, i) => (
              <ChartCardSkeleton key={i} />
            ))}
          </div>
        ) : charts.length === 0 ? (
          <EmptyState
            icon={Package}
            title="No Charts Found"
            description={searchQuery ? `No charts matching "${searchQuery}"` : 'No charts available in this source'}
          />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {charts.map((chart: HelmChart) => (
              <ChartCard
                key={chart.name}
                chart={chart}
                onInstall={() => onInstallChart(chart)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

interface ChartCardProps {
  chart: HelmChart;
  onInstall: () => void;
}

function ChartCard({ chart, onInstall }: ChartCardProps) {
  return (
    <div className="border border-border rounded-lg p-4 hover:border-foreground/20 hover:shadow-md transition-shadow">
      <div className="flex items-start justify-between mb-3">
        <div className="flex-1 min-w-0">
          <h3 className="font-semibold text-sm truncate mb-1">{chart.name}</h3>
          {chart.version && (
            <Badge variant="outline" className="text-[10px]">
              v{chart.version}
            </Badge>
          )}
        </div>
        <Package className="w-5 h-5 text-muted-foreground flex-shrink-0 ml-2" />
      </div>

      <p className="text-xs text-muted-foreground mb-4 line-clamp-2">
        {chart.description || 'No description available'}
      </p>

      <div className="flex items-center gap-2">
        <Button
          size="sm"
          className="flex-1"
          onClick={onInstall}
        >
          <Download className="w-3.5 h-3.5 mr-1.5" />
          Install
        </Button>
        {chart.home && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => window.open(chart.home, '_blank')}
          >
            <ExternalLink className="w-3.5 h-3.5" />
          </Button>
        )}
      </div>

      {chart.app_version && (
        <div className="mt-3 pt-3 border-t border-border text-[10px] text-muted-foreground">
          App Version: {chart.app_version}
        </div>
      )}
    </div>
  );
}

function ChartCardSkeleton() {
  return (
    <div className="border border-border rounded-lg p-4">
      <div className="flex items-start justify-between mb-3">
        <div className="flex-1">
          <Skeleton className="h-4 w-32 mb-2" />
          <Skeleton className="h-4 w-16" />
        </div>
        <Skeleton className="w-5 h-5 rounded" />
      </div>
      <Skeleton className="h-10 w-full mb-4" />
      <div className="flex gap-2">
        <Skeleton className="h-8 flex-1" />
        <Skeleton className="h-8 w-10" />
      </div>
    </div>
  );
}
