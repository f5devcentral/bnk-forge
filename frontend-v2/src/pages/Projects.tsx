/**
 * Projects page — D-020 redesign.
 *
 * One coherent surface, bnkhealth shape: bold heading, chip-filter row,
 * single SectionCard holding a flat table. No in-page sidebar, no per-status
 * color tints — status conveyed by small Badge variants only.
 */

import { useState, useMemo, useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { SectionCard } from '@/components/ui/section-card';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { CreateProjectDialog } from '@/components/projects/CreateProjectDialog';
import { EmptyState } from '@/components/ui/empty-state';
import { ErrorState } from '@/components/ui/error-state';
import { useProjects } from '@/hooks/useProjects';
import { PageHeader } from '@/components/layout/PageHeader';
import { usePageRefresh } from '@/hooks/usePageRefresh';
import { useProjectDriftCounts } from '@/hooks/useDrift';
import { cn } from '@/lib/utils';
import {
  Plus,
  FolderGit2,
  Search,
  MoreVertical,
  Eye,
  Settings,
  Clock,
} from 'lucide-react';
import type { Project } from '@/types';
import { getProjectLocationInfo } from '@/lib/aws-regions';
import { formatTimeAgo } from '@/lib/time-utils';
import { useAuthStore } from '@/stores/authStore';
import {
  getPlatformProfileLabel,
  getProjectTargetPlatform,
} from '@/lib/platform-context';

const INITIAL_PROJECTS_LIMIT = 20;

type ProjectFilter = 'all' | 'mine' | 'active' | 'inactive' | 'has_failed';

const FILTER_CONFIG: Record<ProjectFilter, { label: string }> = {
  all: { label: 'All' },
  mine: { label: 'Mine' },
  active: { label: 'Active' },
  inactive: { label: 'Inactive' },
  has_failed: { label: 'Has failures' },
};

function projectTypeLabel(project: Project): string {
  if (project.project_type === 'kubernetes') return 'Kubernetes';
  if (project.project_type === 'cloud-aws') return 'AWS';
  if (project.project_type === 'cloud-azure') return 'Azure';
  if (project.project_type === 'cloud-gcp') return 'GCP';
  if (project.project_type === 'cloud-ibm') return 'IBM';
  if (project.credential_template?.provider === 'ssh') return 'SSH';
  if (project.credential_template) return project.credential_template.name;
  return '—';
}

export default function Projects() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const currentUser = useAuthStore((s) => s.user);
  const { data: projects, isLoading, isError, error, refetch } = useProjects();
  const projectDriftCounts = useProjectDriftCounts(20);

  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [activeFilter, setActiveFilter] = useState<ProjectFilter>('all');
  const [showAll, setShowAll] = useState(false);

  // Open create dialog when navigated with ?action=create (Command Palette path).
  useEffect(() => {
    if (searchParams.get('action') === 'create') {
      setCreateDialogOpen(true);
      setSearchParams({}, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  const isActive = (p: Project) =>
    p.has_deployments || (p.module_count || 0) > 0 || (p.cluster_count || 0) > 0;

  const filteredProjects = useMemo(() => {
    return (projects || [])
      .filter((p) => {
        if (searchQuery) {
          const q = searchQuery.toLowerCase();
          if (!p.name.toLowerCase().includes(q) && !p.description?.toLowerCase().includes(q)) {
            return false;
          }
        }
        switch (activeFilter) {
          case 'mine':
            return p.user_id === currentUser?.id;
          case 'active':
            return isActive(p);
          case 'inactive':
            return !isActive(p);
          case 'has_failed':
            return (p.failed_count || 0) > 0;
          default:
            return true;
        }
      })
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [projects, searchQuery, activeFilter, currentUser?.id]);

  const counts = useMemo(() => {
    const c: Record<ProjectFilter, number> = {
      all: projects?.length || 0,
      mine: 0,
      active: 0,
      inactive: 0,
      has_failed: 0,
    };
    projects?.forEach((p) => {
      if (p.user_id === currentUser?.id) c.mine++;
      if (isActive(p)) c.active++;
      else c.inactive++;
      if ((p.failed_count || 0) > 0) c.has_failed++;
    });
    return c;
  }, [projects, currentUser?.id]);

  const limited = showAll ? filteredProjects : filteredProjects.slice(0, INITIAL_PROJECTS_LIMIT);
  const hasMore = filteredProjects.length > INITIAL_PROJECTS_LIMIT;

  const handleView = (project: Project) => navigate(`/projects/${project.id}`);

  // Visible chips: skip 0-count to keep the row calm.
  const chipFilters: ProjectFilter[] = (
    ['all', 'mine', 'active', 'inactive', 'has_failed'] as ProjectFilter[]
  ).filter((k) => k === 'all' || (counts[k] || 0) > 0);

  // Header subtitle — quiet roll-up across all projects.
  const totalModules = projects?.reduce((sum, p) => sum + (p.module_count || 0), 0) || 0;
  const totalDeployed = projects?.reduce((sum, p) => sum + (p.deployed_count || 0), 0) || 0;
  const { refresh, isRefreshing } = usePageRefresh();

  const subtitle =
    (projects?.length || 0) > 0
      ? `${projects?.length || 0} ${projects?.length === 1 ? 'project' : 'projects'} · ${totalModules} module${totalModules === 1 ? '' : 's'} · ${totalDeployed} deployed`
      : 'Deploy infrastructure, platforms, and applications grouped by project.';

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <PageHeader
        title="Projects"
        subtitle={subtitle}
        onRefresh={refresh}
        isRefreshing={isRefreshing}
        actions={
          <Button
            variant="outline"
            onClick={() => setCreateDialogOpen(true)}
            size="sm"
            className="shrink-0"
          >
            <Plus className="h-4 w-4 mr-1.5" />
            New project
          </Button>
        }
      />

      {/* Filter chips + search */}
      <div className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          {chipFilters.map((key) => {
            const selected = activeFilter === key;
            return (
              <button
                key={key}
                type="button"
                onClick={() => {
                  setActiveFilter(key);
                  setShowAll(false);
                }}
                className={cn(
                  'inline-flex items-center gap-2 rounded-full border px-3 py-1 text-sm transition-colors',
                  selected
                    ? 'bg-foreground text-background border-foreground'
                    : 'bg-card text-muted-foreground border-border hover:text-foreground',
                )}
                aria-pressed={selected}
              >
                <span>{FILTER_CONFIG[key].label}</span>
                <span
                  className={cn(
                    'text-xs tabular-nums',
                    selected ? 'text-background/70' : 'text-muted-foreground/70',
                  )}
                >
                  {counts[key]}
                </span>
              </button>
            );
          })}
        </div>

        <div className="relative max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search projects…"
            aria-label="Search projects"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-9 h-9"
          />
        </div>
      </div>

      {/* Content */}
      {isError ? (
        <ErrorState error={error} onRetry={refetch} />
      ) : isLoading ? (
        <SectionCard compact>
          <div className="space-y-2">
            {[...Array(6)].map((_, i) => (
              <Skeleton key={i} className="h-14 w-full rounded-md" />
            ))}
          </div>
        </SectionCard>
      ) : filteredProjects.length === 0 ? (
        <EmptyState
          icon={FolderGit2}
          title="No projects found"
          description={
            searchQuery
              ? `No projects match "${searchQuery}"`
              : 'Create your first project to get started'
          }
          action={{ label: 'Create Project', onClick: () => setCreateDialogOpen(true) }}
        />
      ) : (
        <SectionCard
          title={`${filteredProjects.length} ${filteredProjects.length === 1 ? 'project' : 'projects'}`}
          compact
        >
          <div className="overflow-x-auto -mx-4">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs uppercase tracking-wider text-muted-foreground">
                  <th scope="col" className="text-left font-medium px-4 py-2">Project</th>
                  <th scope="col" className="text-left font-medium px-4 py-2">Status</th>
                  <th scope="col" className="text-left font-medium px-4 py-2">Platform</th>
                  <th scope="col" className="text-left font-medium px-4 py-2">Modules</th>
                  <th scope="col" className="text-left font-medium px-4 py-2">Last activity</th>
                  <th scope="col" className="text-right font-medium px-4 py-2 w-16">{/* actions */}</th>
                </tr>
              </thead>
              <tbody>
                {limited.map((project) => {
                  const active = isActive(project);
                  const failed = (project.failed_count || 0) > 0;
                  const statusVariant: 'success' | 'destructive' | 'muted' = failed
                    ? 'destructive'
                    : active
                    ? 'success'
                    : 'muted';
                  const statusLabel = failed ? 'Has failures' : active ? 'Active' : 'Inactive';
                  const location = getProjectLocationInfo(
                    project.cloud_provider,
                    project.region,
                    project.credential_template?.provider,
                    project.project_type,
                  );
                  const driftCount = projectDriftCounts[project.id] || 0;

                  return (
                    <tr
                      key={project.id}
                      data-testid="project-card"
                      className="border-t border-border cursor-pointer hover:bg-muted/40 transition-colors"
                      onClick={() => handleView(project)}
                    >
                      <td className="px-4 py-3">
                        <div className="flex flex-col">
                          <span className="font-medium text-foreground">{project.name}</span>
                          {project.description && (
                            <span className="text-xs text-muted-foreground line-clamp-1 mt-0.5">
                              {project.description}
                            </span>
                          )}
                          {project.owner_username && project.user_id !== currentUser?.id && (
                            <span className="text-xs text-muted-foreground mt-0.5">
                              Owner: {project.owner_username}
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <Badge variant={statusVariant} className="text-xs">
                          {statusLabel}
                        </Badge>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex flex-col gap-0.5">
                          <span className="text-foreground/80">{projectTypeLabel(project)}</span>
                          {location && (
                            <span className="text-xs text-muted-foreground">
                              {location.flag} {location.label}
                            </span>
                          )}
                          <span className="text-xs text-muted-foreground">
                            Target: {getPlatformProfileLabel(getProjectTargetPlatform(project))}
                          </span>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex flex-wrap items-center gap-1.5">
                          <span className="text-foreground/80 tabular-nums">
                            {project.module_count || 0}
                          </span>
                          {(project.deployed_count || 0) > 0 && (
                            <Badge variant="success" className="text-[10px]">
                              {project.deployed_count} deployed
                            </Badge>
                          )}
                          {failed && (
                            <Badge variant="destructive" className="text-[10px]">
                              {project.failed_count} failed
                            </Badge>
                          )}
                          {driftCount > 0 && (
                            <Badge variant="warning" className="text-[10px]">
                              {driftCount} drifted
                            </Badge>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1.5 text-muted-foreground">
                          <Clock className="h-3.5 w-3.5" />
                          <span className="text-xs">
                            {formatTimeAgo(project.updated_at || project.created_at) || 'Never'}
                          </span>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-right">
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild onClick={(e) => e.stopPropagation()}>
                            <Button
                              variant="ghost"
                              size="sm"
                              className="h-8 w-8 p-0"
                              aria-label="Project actions"
                            >
                              <MoreVertical className="h-4 w-4" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem
                              onClick={(e) => {
                                e.stopPropagation();
                                handleView(project);
                              }}
                            >
                              <Eye className="h-4 w-4 mr-2" />
                              View Project
                            </DropdownMenuItem>
                            <DropdownMenuItem
                              onClick={(e) => {
                                e.stopPropagation();
                                navigate(`/projects/${project.id}?tab=settings`);
                              }}
                            >
                              <Settings className="h-4 w-4 mr-2" />
                              Settings
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {!showAll && hasMore && (
            <div className="mt-4 flex justify-center">
              <Button variant="outline" size="sm" onClick={() => setShowAll(true)}>
                Show {filteredProjects.length - INITIAL_PROJECTS_LIMIT} more
              </Button>
            </div>
          )}
          {showAll && hasMore && (
            <div className="mt-4 flex justify-center">
              <Button variant="ghost" size="sm" onClick={() => setShowAll(false)}>
                Show less
              </Button>
            </div>
          )}
        </SectionCard>
      )}

      <CreateProjectDialog open={createDialogOpen} onOpenChange={setCreateDialogOpen} />
    </div>
  );
}
