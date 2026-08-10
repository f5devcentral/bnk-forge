/**
 * AddClusterFlowDialog — Multi-step dialog for adding a cluster from pages
 * that don't have a project context (Fleet, Dashboard).
 *
 * Step 1: Pick a project (or create one via CreateProjectDialog)
 * Step 2: Opens ClusterConfigDialog with the selected projectId
 *
 * Reuses existing CreateProjectDialog and ClusterConfigDialog — no new forms.
 */

import { useState, useCallback } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { useProjects } from '@/hooks/useProjects';
import { useThemeClasses } from '@/context/ThemeContext';
import { cn } from '@/lib/utils';
import { FolderOpen, Plus, ChevronRight } from 'lucide-react';
import { ClusterConfigDialog } from './ClusterConfigDialog';
import { CreateProjectDialog } from '@/components/projects/CreateProjectDialog';
import { Skeleton } from '@/components/ui/skeleton';
import type { Project } from '@/types';

interface AddClusterFlowDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function AddClusterFlowDialog({ open, onOpenChange }: AddClusterFlowDialogProps) {
  const tc = useThemeClasses();
  const { data: projects, isLoading } = useProjects();
  const [selectedProjectId, setSelectedProjectId] = useState<number | null>(null);
  const [showClusterConfig, setShowClusterConfig] = useState(false);
  const [showCreateProject, setShowCreateProject] = useState(false);

  // Find the selected project to pass SSH credential if available
  const selectedProject = projects?.find((p: Project) => p.id === selectedProjectId);

  const handleSelectProject = useCallback((projectId: number) => {
    setSelectedProjectId(projectId);
    setShowClusterConfig(true);
  }, []);

  const handleClusterConfigClose = useCallback((isOpen: boolean) => {
    if (!isOpen) {
      setShowClusterConfig(false);
      setSelectedProjectId(null);
      onOpenChange(false);
    }
  }, [onOpenChange]);

  const handleClose = useCallback(() => {
    setSelectedProjectId(null);
    setShowClusterConfig(false);
    setShowCreateProject(false);
    onOpenChange(false);
  }, [onOpenChange]);

  // Step 2b: CreateProjectDialog is open — it invalidates useProjects on success,
  // so when it closes the project list refreshes and the user can pick the new project.
  if (showCreateProject) {
    return (
      <CreateProjectDialog
        open={true}
        onOpenChange={(isOpen) => {
          if (!isOpen) setShowCreateProject(false);
        }}
      />
    );
  }

  // Step 2a: ClusterConfigDialog is active
  if (showClusterConfig && selectedProjectId) {
    return (
      <ClusterConfigDialog
        open={true}
        onOpenChange={handleClusterConfigClose}
        projectId={selectedProjectId}
        projectSshCredentialId={selectedProject?.ssh_credential_id}
      />
    );
  }

  const activeProjects = projects?.filter((p: Project) => p.is_active) ?? [];

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className={cn('sm:max-w-md', tc.bgCard)}>
        <DialogHeader>
          <DialogTitle className={cn('text-xl font-bold', tc.textPrimary)}>
            Add Cluster
          </DialogTitle>
          <DialogDescription className={tc.textMuted}>
            Select a project to add the cluster to. Clusters are scoped to projects for organization and access control.
          </DialogDescription>
        </DialogHeader>

        <div className="mt-2 space-y-2 max-h-[320px] overflow-y-auto">
          {isLoading && (
            <div className="space-y-2">
              {Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className={cn('flex items-center gap-3 rounded-lg border p-3', tc.borderDefault)}>
                  <Skeleton className="h-8 w-8 rounded-md" />
                  <div className="flex-1 space-y-1">
                    <Skeleton className="h-4 w-32" />
                    <Skeleton className="h-3 w-48" />
                  </div>
                </div>
              ))}
            </div>
          )}

          {!isLoading && activeProjects.length === 0 && (
            <div className={cn('rounded-lg border p-6 text-center', tc.borderDefault)}>
              <FolderOpen className={cn('h-10 w-10 mx-auto mb-3', tc.textMuted)} />
              <p className={cn('text-sm font-medium mb-1', tc.textPrimary)}>No projects yet</p>
              <p className={cn('text-xs mb-4', tc.textMuted)}>Create a project first, then add clusters to it.</p>
              <Button size="sm" onClick={() => setShowCreateProject(true)}>
                <Plus className="h-3.5 w-3.5 mr-1" />
                Create Project
              </Button>
            </div>
          )}

          {!isLoading && activeProjects.map((project: Project) => (
            <button
              key={project.id}
              type="button"
              className={cn(
                'w-full flex items-center gap-3 rounded-lg border p-3 text-left transition-colors',
                'hover:border-primary/50 hover:bg-primary/5',
                tc.borderDefault
              )}
              onClick={() => handleSelectProject(project.id)}
            >
              {/* Project color dot */}
              <div
                className="h-8 w-8 rounded-md flex items-center justify-center text-white text-sm font-bold shrink-0"
                style={{ backgroundColor: project.color || '#3b82f6' }}
              >
                {project.icon || project.name.charAt(0).toUpperCase()}
              </div>
              <div className="flex-1 min-w-0">
                <p className={cn('text-sm font-medium truncate', tc.textPrimary)}>{project.name}</p>
                <p className={cn('text-xs truncate', tc.textMuted)}>
                  {project.cluster_count ?? 0} cluster{(project.cluster_count ?? 0) !== 1 ? 's' : ''}
                  {project.environment && <> · {project.environment}</>}
                </p>
              </div>
              <ChevronRight className={cn('h-4 w-4 shrink-0', tc.textMuted)} />
            </button>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
