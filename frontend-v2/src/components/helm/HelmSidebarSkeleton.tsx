/**
 * Loading skeleton for Helm Sidebar
 */

import { Package, Library, ChevronDown } from 'lucide-react';

export function HelmSidebarSkeleton() {
  return (
    <div className="w-80 border-r flex flex-col h-full">
      {/* Releases Section Header */}
      <div className="border-b">
        <div className="px-4 py-3 flex items-center gap-2">
          <ChevronDown className="w-4 h-4" />
          <Package className="w-4 h-4" />
          <h3 className="font-semibold text-sm">Releases</h3>
        </div>

        {/* Loading Skeletons */}
        <div className="overflow-y-auto p-2 space-y-1 max-h-[60vh]">
          {/* Cluster 1 */}
          <div className="space-y-1">
            <div className="h-9 bg-accent/50 rounded-lg animate-pulse" />
            <div className="ml-6 space-y-1">
              <div className="h-8 bg-accent/30 rounded-lg animate-pulse" />
              <div className="ml-8 space-y-1">
                <div className="h-8 bg-accent/20 rounded-lg animate-pulse" />
                <div className="h-8 bg-accent/20 rounded-lg animate-pulse" />
                <div className="h-8 bg-accent/20 rounded-lg animate-pulse" />
              </div>
            </div>
          </div>

          {/* Cluster 2 */}
          <div className="space-y-1">
            <div className="h-9 bg-accent/50 rounded-lg animate-pulse" />
            <div className="ml-6 space-y-1">
              <div className="h-8 bg-accent/30 rounded-lg animate-pulse" />
              <div className="ml-8 space-y-1">
                <div className="h-8 bg-accent/20 rounded-lg animate-pulse" />
                <div className="h-8 bg-accent/20 rounded-lg animate-pulse" />
              </div>
            </div>
          </div>

          {/* Cluster 3 */}
          <div className="h-9 bg-accent/50 rounded-lg animate-pulse" />
        </div>
      </div>

      {/* Charts Section */}
      <div className="border-t">
        <div className="px-4 py-3 flex items-center gap-2">
          <ChevronDown className="w-4 h-4" />
          <Library className="w-4 h-4" />
          <h3 className="font-semibold text-sm">Charts</h3>
        </div>
        <div className="p-2 space-y-1">
          <div className="h-9 bg-accent/30 rounded-lg animate-pulse" />
          <div className="h-9 bg-accent/30 rounded-lg animate-pulse" />
          <div className="h-9 bg-accent/30 rounded-lg animate-pulse" />
          <div className="h-9 bg-accent/30 rounded-lg animate-pulse" />
        </div>
      </div>
    </div>
  );
}
