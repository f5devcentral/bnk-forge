import { useState, useEffect } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useProjects } from '@/hooks/useProjects';
import { X, Plus, GitBranch } from 'lucide-react';
import type { Project, ProjectDependency } from '@/types';
import { notify } from '@/lib/notify';
import { api } from '@/lib/api';

interface ProjectDependenciesDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  project: Project;
  onUpdate: () => void;
}

export function ProjectDependenciesDialog({
  open,
  onOpenChange,
  project,
  onUpdate,
}: ProjectDependenciesDialogProps) {
  const { data: allProjects } = useProjects();
  const [dependencies, setDependencies] = useState<ProjectDependency[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string>('');
  const [outputInput, setOutputInput] = useState('');
  const [isSaving, setIsSaving] = useState(false);

  // Initialize dependencies from project
  useEffect(() => {
    if (project.dependent_projects) {
      setDependencies(project.dependent_projects);
    } else {
      setDependencies([]);
    }
  }, [project]);

  // Filter out current project and projects already added
  const availableProjects = allProjects?.filter(
    (p) => p.id !== project.id && !dependencies.some((d) => d.project_id === p.id)
  ) || [];

  const handleAddDependency = () => {
    if (!selectedProjectId) {
      notify.error('Please select a project');
      return;
    }

    const newDep: ProjectDependency = {
      project_id: parseInt(selectedProjectId),
      outputs: [],
    };

    setDependencies([...dependencies, newDep]);
    setSelectedProjectId('');
  };

  const handleRemoveDependency = (projectId: number) => {
    setDependencies(dependencies.filter((d) => d.project_id !== projectId));
  };

  const handleAddOutput = (projectId: number) => {
    if (!outputInput.trim()) {
      notify.error('Please enter an output name');
      return;
    }

    setDependencies(
      dependencies.map((d) =>
        d.project_id === projectId
          ? { ...d, outputs: [...d.outputs, outputInput.trim()] }
          : d
      )
    );
    setOutputInput('');
  };

  const handleRemoveOutput = (projectId: number, output: string) => {
    setDependencies(
      dependencies.map((d) =>
        d.project_id === projectId
          ? { ...d, outputs: d.outputs.filter((o) => o !== output) }
          : d
      )
    );
  };

  const handleSave = async () => {
    setIsSaving(true);
    try {
      await api.updateProjectDependencies(project.id, dependencies);
      onUpdate();
      onOpenChange(false);
    } catch {
      notify.error('Failed to update project dependencies');
    } finally {
      setIsSaving(false);
    }
  };

  const getDependentProjectName = (projectId: number) => {
    return allProjects?.find((p) => p.id === projectId)?.name || `Project ${projectId}`;
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[700px] max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <GitBranch className="h-5 w-5" />
            Cross-Project Dependencies
          </DialogTitle>
          <DialogDescription>
            Configure which projects this project depends on. This allows modules to reference
            outputs from other projects (e.g., EKS cluster info from a base infrastructure project).
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-6 py-4">
          {/* Add New Dependency */}
          <div className="space-y-3">
            <Label>Add Dependency</Label>
            <div className="flex gap-2">
              <Select value={selectedProjectId} onValueChange={setSelectedProjectId}>
                <SelectTrigger className="flex-1">
                  <SelectValue placeholder="Select a project to depend on..." />
                </SelectTrigger>
                <SelectContent>
                  {availableProjects.map((p) => (
                    <SelectItem key={p.id} value={p.id.toString()}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button
                onClick={handleAddDependency}
                disabled={!selectedProjectId || availableProjects.length === 0}
              >
                <Plus className="h-4 w-4 mr-2" />
                Add
              </Button>
            </div>
            {availableProjects.length === 0 && (
              <p className="text-sm text-muted-foreground">
                No other projects available. Create another project first.
              </p>
            )}
          </div>

          {/* Existing Dependencies */}
          {dependencies.length > 0 && (
            <div className="space-y-4">
              <Label>Current Dependencies</Label>
              {dependencies.map((dep) => (
                <div key={dep.project_id} className="border rounded-lg p-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <GitBranch className="h-4 w-4 text-muted-foreground" />
                      <span className="font-medium">{getDependentProjectName(dep.project_id)}</span>
                    </div>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleRemoveDependency(dep.project_id)}
                    >
                      <X className="h-4 w-4" />
                    </Button>
                  </div>

                  {/* Outputs for this dependency */}
                  <div className="space-y-2">
                    <Label className="text-sm">Required Outputs</Label>
                    <div className="flex gap-2">
                      <Input
                        placeholder="e.g., eks_cluster_endpoint"
                        value={outputInput}
                        onChange={(e) => setOutputInput(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') {
                            e.preventDefault();
                            handleAddOutput(dep.project_id);
                          }
                        }}
                      />
                      <Button
                        size="sm"
                        variant="secondary"
                        onClick={() => handleAddOutput(dep.project_id)}
                        disabled={!outputInput.trim()}
                      >
                        <Plus className="h-4 w-4" />
                      </Button>
                    </div>

                    {/* Output badges */}
                    {dep.outputs.length > 0 && (
                      <div className="flex flex-wrap gap-2 mt-2">
                        {dep.outputs.map((output) => (
                          <Badge key={output} variant="secondary" className="gap-1">
                            {output}
                            <button
                              onClick={() => handleRemoveOutput(dep.project_id, output)}
                              className="ml-1 hover:bg-destructive/20 rounded-full"
                            >
                              <X className="h-3 w-3" />
                            </button>
                          </Badge>
                        ))}
                      </div>
                    )}

                    {dep.outputs.length === 0 && (
                      <p className="text-xs text-muted-foreground">
                        Add output names that modules will reference (e.g., vpc_id, eks_cluster_endpoint)
                      </p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}

          {dependencies.length === 0 && (
            <div className="text-center py-8 text-muted-foreground">
              <GitBranch className="h-12 w-12 mx-auto mb-3 opacity-50" />
              <p>No cross-project dependencies configured</p>
              <p className="text-sm mt-1">
                Add a dependency to reference outputs from another project
              </p>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isSaving}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={isSaving}>
            {isSaving ? 'Saving...' : 'Save Dependencies'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
