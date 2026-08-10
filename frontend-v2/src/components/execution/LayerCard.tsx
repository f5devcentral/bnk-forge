/**
 * LayerCard Component
 *
 * Displays a single execution layer with its modules.
 * Shows modules that will execute in parallel and their estimated times.
 */

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import type { ExecutionLayer, LayerProgress } from '@/types';
import { Layers, Clock, CheckCircle2, XCircle, Loader2 } from 'lucide-react';

/** LayerCard accepts both old ExecutionLayer and new LayerProgress shapes.
 *  Cast to ExecutionLayer internally — fields absent in LayerProgress
 *  (can_run_parallel, module_count, estimated_time_minutes, modules)
 *  are optional in ExecutionLayer and will be undefined/falsy safely.
 */
type AnyLayer = ExecutionLayer | LayerProgress;

interface LayerCardProps {
  layer: AnyLayer;
  showStatus?: boolean;
}

export function LayerCard({ layer: layerProp, showStatus = false }: LayerCardProps) {
  // Cast to ExecutionLayer — fields absent in LayerProgress are optional and
  // will be undefined/falsy at runtime, which the guards below handle safely.
  const layer = layerProp as ExecutionLayer;
  const getStatusIcon = (status?: string) => {
    switch (status) {
      case 'completed':
        return <CheckCircle2 className="h-4 w-4 text-success" />;
      case 'failed':
        return <XCircle className="h-4 w-4 text-destructive" />;
      case 'in_progress':
        return <Loader2 className="h-4 w-4 text-primary animate-spin" />;
      default:
        return null;
    }
  };

  const getStatusBorder = (status?: string) => {
    switch (status) {
      case 'completed':
        return 'border-success/40 bg-success/5';
      case 'failed':
        return 'border-destructive/40 bg-destructive/5';
      case 'in_progress':
        return 'border-primary/40 bg-primary/5';
      default:
        return 'border-border';
    }
  };

  return (
    <Card className={`${showStatus ? getStatusBorder(layer.status) : ''}`}>
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Layers className="h-5 w-5 text-muted-foreground" />
            <CardTitle className="text-base">
              Layer {layer.layer_index}
              {showStatus && layer.status && (
                <span className="ml-2">{getStatusIcon(layer.status)}</span>
              )}
            </CardTitle>
          </div>
          <div className="flex items-center gap-3">
            {layer.can_run_parallel && (
              <Badge variant="secondary" className="text-xs">
                {layer.module_count} parallel
              </Badge>
            )}
            <div className="flex items-center gap-1 text-sm text-muted-foreground">
              <Clock className="h-4 w-4" />
              <span>{layer.estimated_time_minutes} min</span>
            </div>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          {/* Support both formats: modules array (execution plan) or module_ids/module_names (parallel execution status) */}
          {layer.modules ? (
            layer.modules.map((module) => (
              <div
                key={module.id}
                className="flex items-center justify-between p-2 rounded-md bg-muted/50 hover:bg-muted transition-colors"
              >
                <div className="flex items-center gap-2">
                  <div className="flex flex-col">
                    <span className="text-sm font-medium text-foreground">
                      {module.name}
                    </span>
                    <span className="text-xs text-muted-foreground">{module.path}</span>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  {showStatus && module.status && (
                    <Badge
                      variant={
                        module.status === 'applied' || module.status === 'completed'
                          ? 'success'
                          : module.status === 'failed' || module.status === 'apply_failed'
                          ? 'destructive'
                          : 'secondary'
                      }
                      className="text-xs"
                    >
                      {module.status}
                    </Badge>
                  )}
                  <span className="text-xs text-muted-foreground">
                    ~{module.estimated_time_minutes} min
                  </span>
                </div>
              </div>
            ))
          ) : layer.module_names ? (
            layer.module_names.map((name: string, idx: number) => {
              const moduleId = layer.module_ids?.[idx];
              // results may be keyed by number (ExecutionLayer) or string (LayerProgress JSON).
              // Try both to handle either backend serialization.
              const results = layer.results as Record<string | number, { success: boolean } | undefined> | undefined;
              const result = moduleId !== undefined && results
                ? (results[moduleId] ?? results[String(moduleId)])
                : undefined;
              const moduleStatus = result ? (result.success ? 'completed' : 'failed') : undefined;

              return (
                <div
                  key={moduleId || idx}
                  className="flex items-center justify-between p-2 rounded-md bg-muted/50 hover:bg-muted transition-colors"
                >
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-foreground">{name}</span>
                  </div>
                  <div className="flex items-center gap-3">
                    {showStatus && moduleStatus && (
                      <Badge
                        variant={moduleStatus === 'completed' ? 'success' : 'destructive'}
                        className="text-xs"
                      >
                        {moduleStatus}
                      </Badge>
                    )}
                    {showStatus && !moduleStatus && layer.status === 'in_progress' && (
                      <Badge variant="secondary" className="text-xs">
                        <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                        running
                      </Badge>
                    )}
                  </div>
                </div>
              );
            })
          ) : (
            <div className="text-sm text-muted-foreground">No modules in this layer</div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
