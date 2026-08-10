import React, { useRef } from 'react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Search, RefreshCw, LayoutGrid } from 'lucide-react';
import { useKeyboardShortcuts } from '@/hooks/useKeyboardShortcuts';

interface Cluster {
  id: number;
  name: string;
  status?: string;
}

interface Project {
  id: number;
  name: string;
}

interface ResourcePageHeaderProps {
  title?: string;
  subtitle?: React.ReactNode;
  projects?: Project[];
  selectedProjectId?: number | null;
  onProjectChange?: (id: number) => void;
  clusters: Cluster[];
  selectedClusterId: number | null;
  onClusterChange: (id: number) => void;
  namespaces: { name: string }[] | string[];
  selectedNamespace: string;
  onNamespaceChange: (ns: string) => void;
  searchQuery: string;
  onSearchChange: (query: string) => void;
  onRefresh?: () => void;
  isRefreshing?: boolean;
  children?: React.ReactNode;
  className?: string;
}

export function ResourcePageHeader({
  title,
  subtitle,
  projects,
  selectedProjectId,
  onProjectChange,
  clusters,
  selectedClusterId,
  onClusterChange,
  namespaces,
  selectedNamespace,
  onNamespaceChange,
  searchQuery,
  onSearchChange,
  onRefresh,
  isRefreshing,
  children,
  className,
}: ResourcePageHeaderProps) {
  const searchInputRef = useRef<HTMLInputElement>(null);

  useKeyboardShortcuts([
    {
      key: '/',
      action: (e) => {
        e.preventDefault();
        searchInputRef.current?.focus();
      },
      allowInInput: true,
    },
  ]);

  // Normalize namespaces to strings
  const namespaceList = namespaces.map(ns => typeof ns === 'string' ? ns : ns.name);

  return (
    <div className={cn(
      'border-b border-border bg-card/95 backdrop-blur supports-[backdrop-filter]:bg-card/75 px-6 py-4 flex flex-col gap-4 sticky top-0 z-20',
      className
    )}>
      {/* Bold page title */}
      {title && (
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">{title}</h1>
          {subtitle && (
            <p className="text-sm text-muted-foreground mt-1">{subtitle}</p>
          )}
        </div>
      )}

      {/* Selector + search + actions row */}
      <div className="flex items-center gap-3 flex-wrap">
        {/* Optional Project Selector */}
        {projects && onProjectChange && (
          <div className="flex items-center gap-2">
            <Select
              value={selectedProjectId?.toString() || ''}
              onValueChange={(value) => onProjectChange(parseInt(value))}
            >
              <SelectTrigger className="w-[180px] h-9">
                <div className="flex items-center gap-2 min-w-0">
                  <LayoutGrid className="h-4 w-4 text-muted-foreground flex-shrink-0" />
                  <span className="min-w-0 flex-1 truncate text-left">
                    <SelectValue placeholder="Select Project" />
                  </span>
                </div>
              </SelectTrigger>
              <SelectContent>
                {projects.map((project) => (
                  <SelectItem key={project.id} value={project.id.toString()}>
                    {project.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <span className="text-lg font-light text-muted-foreground/50">/</span>
          </div>
        )}

        {/* Cluster Selector */}
        <Select
          value={selectedClusterId?.toString() || ''}
          onValueChange={(value) => onClusterChange(parseInt(value))}
          disabled={!clusters.length || (projects && !selectedProjectId)}
        >
          <SelectTrigger className="w-[200px] h-9">
            <SelectValue placeholder="Select Cluster" />
          </SelectTrigger>
          <SelectContent>
            {clusters.length === 0 ? (
               <div className="p-2 text-xs text-muted-foreground text-center">No clusters found</div>
            ) : (
              clusters.map((cluster) => (
                <SelectItem key={cluster.id} value={cluster.id.toString()}>
                  <div className="flex items-center gap-2 min-w-0">
                    <span className={cn(
                      'h-2 w-2 rounded-full flex-shrink-0',
                      cluster.status === 'active' ? 'bg-success' : 'bg-muted-foreground'
                    )} />
                    <span className="truncate">{cluster.name}</span>
                  </div>
                </SelectItem>
              ))
            )}
          </SelectContent>
        </Select>

        <span className="text-lg font-light text-muted-foreground/50">/</span>

        {/* Namespace Selector */}
        <Select
          value={selectedNamespace}
          onValueChange={onNamespaceChange}
          disabled={!selectedClusterId}
        >
          <SelectTrigger className="w-[180px] h-9 font-medium">
             <span className="truncate">
                {selectedNamespace === 'all' ? 'All Namespaces' : selectedNamespace}
             </span>
          </SelectTrigger>
          <SelectContent>
            {/* "All Namespaces" removed from the dropdown intentionally — it's
                an expensive cluster-wide fetch and should be opt-in via the
                explicit "Load all namespaces" button in the K8s sidebar. The
                currently-active "all" state is still displayed if set. */}
            {selectedNamespace === 'all' && (
              <SelectItem value="all" className="font-semibold text-warning">
                All Namespaces (cluster-wide)
              </SelectItem>
            )}
            {namespaceList.map((ns) => (
              <SelectItem key={ns} value={ns}>
                {ns}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {/* Search Input */}
        <div className="flex-1 relative min-w-[200px]">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            ref={searchInputRef}
            placeholder="Search... (/)"
            aria-label="Search resources"
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            className="pl-9 h-9 transition-all focus:w-full"
          />
        </div>

        {/* Actions Area */}
        <div className="flex items-center gap-2">
          {children}

          {onRefresh && (
            <Button
              variant="outline"
              size="sm"
              className="h-9 w-9 p-0"
              onClick={onRefresh}
              disabled={isRefreshing || !selectedClusterId}
              title="Refresh Resources"
              aria-label="Refresh resources"
            >
              <RefreshCw className={cn('h-4 w-4', isRefreshing && 'animate-spin')} aria-hidden="true" />
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
