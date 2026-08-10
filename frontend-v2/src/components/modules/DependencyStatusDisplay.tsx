import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { CheckCircle2, AlertTriangle, XCircle, Link2, Loader2 } from 'lucide-react';
import type { ProjectModule } from '@/types';

interface DependencyStatusDisplayProps {
  module: ProjectModule;
  allModules: ProjectModule[];
}

export function DependencyStatusDisplay({ module, allModules }: DependencyStatusDisplayProps) {
  // If no dependencies, return null
  if (!module.dependencies || module.dependencies.length === 0) {
    return null;
  }

  // Find dependency modules
  const dependencyModules = module.dependencies
    .map((depId) => allModules.find((m) => m.id === depId))
    .filter((m) => m !== undefined) as ProjectModule[];

  // Check if all dependencies are satisfied
  const allDependenciesSatisfied = dependencyModules.every(
    (dep) => dep.status === 'applied' && dep.outputs && Object.keys(dep.outputs).length > 0
  );

  // Check for unapplied dependencies
  const unappliedDependencies = dependencyModules.filter(
    (dep) => dep.status !== 'applied'
  );

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Link2 className="h-4 w-4 text-muted-foreground" />
        <span className="text-sm font-medium">Dependencies</span>
        <Badge variant="outline" className="text-xs">
          {dependencyModules.length}
        </Badge>
      </div>

      {allDependenciesSatisfied ? (
        <Alert className="bg-success/10 border-success/20">
          <CheckCircle2 className="h-4 w-4 text-success" />
          <AlertDescription className="text-sm text-success">
            All dependencies are satisfied and ready
          </AlertDescription>
        </Alert>
      ) : (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription className="text-sm">
            {unappliedDependencies.length > 0 ? (
              <>Dependencies not deployed yet. Please deploy the following modules first:</>
            ) : (
              <>Some dependencies are missing outputs:</>
            )}
          </AlertDescription>
        </Alert>
      )}

      <div className="space-y-1">
        {dependencyModules.map((dep) => {
          const isApplied = dep.status === 'applied';
          const hasOutputs = dep.outputs && Object.keys(dep.outputs).length > 0;
          const isDeploying = ['initializing', 'planning', 'applying'].includes(dep.status);

          return (
            <div
              key={dep.id}
              className="flex items-center justify-between p-2 rounded-md border bg-muted/30"
            >
              <div className="flex items-center gap-2">
                {isApplied && hasOutputs ? (
                  <CheckCircle2 className="h-4 w-4 text-success flex-shrink-0" />
                ) : isDeploying ? (
                  <Loader2 className="h-4 w-4 text-info animate-spin flex-shrink-0" />
                ) : (
                  <XCircle className="h-4 w-4 text-warning flex-shrink-0" />
                )}
                <div className="flex flex-col">
                  <span className="text-sm font-medium">
                    {dep.library_module?.name || dep.module_name}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {dep.path_in_project}
                  </span>
                </div>
              </div>
              <div className="flex items-center gap-2">
                {isApplied && hasOutputs ? (
                  <Badge variant="success" className="text-xs">
                    Ready
                  </Badge>
                ) : isDeploying ? (
                  <Badge variant="secondary" className="text-xs">
                    Deploying...
                  </Badge>
                ) : isApplied && !hasOutputs ? (
                  <Badge variant="warning" className="text-xs">
                    No outputs
                  </Badge>
                ) : (
                  <Badge variant="destructive" className="text-xs">
                    Not deployed
                  </Badge>
                )}
                {isApplied && hasOutputs && (
                  <span className="text-xs text-muted-foreground">
                    {Object.keys(dep.outputs || {}).length} outputs
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
