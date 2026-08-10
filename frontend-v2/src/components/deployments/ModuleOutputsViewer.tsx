import { useState, useEffect, useCallback } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Alert, AlertDescription } from '@/components/ui/alert';
import {
  FileOutput,
  Copy,
  CheckCircle2,
  AlertTriangle,
  RefreshCw,
  Eye,
  EyeOff,
  Loader2,
  AlertCircle,
} from 'lucide-react';
import { isAxiosError } from 'axios';
import { api } from '@/lib/api';
import type { ProjectModule } from '@/types';

interface ModuleOutputsViewerProps {
  module: ProjectModule;
}

export function ModuleOutputsViewer({ module }: ModuleOutputsViewerProps) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [stateOutputs, setStateOutputs] = useState<Record<string, any> | null>(null);
  const [planOutputs, setPlanOutputs] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copiedField, setCopiedField] = useState<string | null>(null);
  const [showSensitive, setShowSensitive] = useState<Record<string, boolean>>({});

  const loadOutputs = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      // v2: Outputs are stored directly in the module's outputs field in the database
      // This is populated after successful apply by the execution engine
      if (module.outputs && Object.keys(module.outputs).length > 0) {
        setStateOutputs(module.outputs);
      } else {
        // Fallback: Try to load from state file (v1 compatibility)
        try {
          const stateData = await api.getModuleState(module.id);
          if (stateData.exists && stateData.outputs) {
            setStateOutputs(stateData.outputs);
          } else {
            setStateOutputs(null);
          }
        } catch (stateErr: unknown) {
          // If 404, state doesn't exist yet - that's okay, not an error
          if (
            (isAxiosError(stateErr) && stateErr.response?.status === 404) ||
            (stateErr instanceof Error && stateErr.message?.includes('404'))
          ) {
            setStateOutputs(null);
          } else {
            // Other errors should be shown
            throw stateErr;
          }
        }
      }

      // Also load plan outputs (what will be created)
      const tasksResponse = await api.getTasks({ module_id: module.id, task_type: 'plan' });
      if (tasksResponse.tasks.length > 0) {
        // Fetch full task details to get logs
        const latestPlanTask = await api.getTask(tasksResponse.tasks[0].id);
        if (latestPlanTask.logs) {
          // Parse plan logs to extract output names from "Changes to Outputs:" section
          const outputSection = latestPlanTask.logs.match(/Changes to Outputs:([\s\S]*?)(?:\n\n|───|$)/);
          if (outputSection) {
            // Only match top-level outputs (exactly 2 spaces, +/~, space, name)
            // This excludes nested object keys which have more indentation
            const outputMatches = outputSection[1].match(/^ {2}[+~]\s+(\w+)\s+=/gm);
            if (outputMatches) {
              const outputNames = outputMatches.map(match => match.match(/(\w+)\s+=/)?.[1] || '').filter(Boolean);
              setPlanOutputs(outputNames);
            }
          }
        }
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load outputs';
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [module.id, module.outputs]);

  useEffect(() => {
    loadOutputs();
  }, [loadOutputs]); // Re-load when module.outputs changes

  const copyToClipboard = (text: string, label: string) => {
    navigator.clipboard.writeText(text);
    setCopiedField(label);
    setTimeout(() => setCopiedField(null), 2000);
  };

  const toggleSensitive = (key: string) => {
    setShowSensitive(prev => ({ ...prev, [key]: !prev[key] }));
  };

  if (isLoading) {
    return (
      <Card>
        <CardContent className="pt-6 text-center py-12">
          <Loader2 className="h-12 w-12 animate-spin mx-auto mb-4 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">Loading outputs...</p>
        </CardContent>
      </Card>
    );
  }

  if (error) {
    return (
      <Card>
        <CardContent className="pt-6">
          <Alert variant="destructive">
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
          <div className="mt-4 flex justify-end">
            <Button onClick={loadOutputs}>
              <RefreshCw className="h-4 w-4 mr-2" />
              Retry
            </Button>
          </div>
        </CardContent>
      </Card>
    );
  }

  const hasStateOutputs = stateOutputs && Object.keys(stateOutputs).length > 0;
  const hasPlanOutputs = planOutputs.length > 0;

  if (!hasStateOutputs && !hasPlanOutputs) {
    return (
      <Card>
        <CardContent className="pt-6 text-center py-12">
          <FileOutput className="h-16 w-16 mx-auto mb-4 text-muted-foreground opacity-50" />
          <h3 className="text-lg font-semibold mb-2">No Outputs Available</h3>
          <p className="text-muted-foreground">
            Module not applied. No outputs exist yet.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h3 className="text-lg font-semibold flex items-center gap-2">
              <FileOutput className="h-5 w-5" />
              Module Outputs
            </h3>
            <p className="text-sm text-muted-foreground">
              OpenTofu outputs from this module
            </p>
          </div>
          <Button size="sm" variant="outline" onClick={loadOutputs}>
            <RefreshCw className="h-4 w-4 mr-2" />
            Refresh
          </Button>
        </div>

        {/* Applied Outputs (from state) */}
        {hasStateOutputs && (
          <div className="mb-6">
            <div className="flex items-center gap-2 mb-3">
              <CheckCircle2 className="h-4 w-4 text-success" />
              <h4 className="font-semibold">Applied Outputs</h4>
              <Badge variant="secondary">{Object.keys(stateOutputs).length} outputs</Badge>
            </div>
            <ScrollArea className="h-[400px]">
              <div className="space-y-3">
                {Object.entries(stateOutputs).map(([key, output]) => {
                  // Handle both v1 format (with .value) and v2 format (plain values)
                  const outputValue = typeof output === 'object' && 'value' in output ? output.value : output;
                  const isSensitive = typeof output === 'object' && 'sensitive' in output ? output.sensitive : false;

                  return (
                    <div key={key} className="border rounded-lg p-4 bg-muted/30">
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-2">
                          <span className="font-mono text-sm font-medium">{key}</span>
                          {isSensitive && (
                            <Badge variant="destructive" className="text-xs">
                              sensitive
                            </Badge>
                          )}
                        </div>
                        <div className="flex gap-2">
                          {isSensitive && (
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => toggleSensitive(key)}
                            >
                              {showSensitive[key] ? (
                                <EyeOff className="h-4 w-4" />
                              ) : (
                                <Eye className="h-4 w-4" />
                              )}
                            </Button>
                          )}
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() =>
                              copyToClipboard(
                                typeof outputValue === 'object'
                                  ? JSON.stringify(outputValue, null, 2)
                                  : String(outputValue),
                                key
                              )
                            }
                          >
                            {copiedField === key ? (
                              <CheckCircle2 className="h-4 w-4 text-success" />
                            ) : (
                              <Copy className="h-4 w-4" />
                            )}
                          </Button>
                        </div>
                      </div>
                      <div className="font-mono text-sm bg-muted text-foreground p-2 rounded overflow-x-auto">
                        <pre>
                          {isSensitive && !showSensitive[key]
                            ? '***SENSITIVE***'
                            : typeof outputValue === 'object'
                            ? JSON.stringify(outputValue, null, 2)
                            : String(outputValue)}
                        </pre>
                      </div>
                    </div>
                  );
                })}
              </div>
            </ScrollArea>
          </div>
        )}

        {/* Planned Outputs (from plan) */}
        {hasPlanOutputs && !hasStateOutputs && (
          <div>
            <div className="flex items-center gap-2 mb-3">
              <AlertTriangle className="h-4 w-4 text-warning" />
              <h4 className="font-semibold">Planned Outputs</h4>
              <Badge variant="secondary">{planOutputs.length} outputs</Badge>
            </div>
            <Alert>
              <AlertTriangle className="h-4 w-4" />
              <AlertDescription>
                These outputs will be available after running tofu apply.
                The following outputs are defined in the module:
              </AlertDescription>
            </Alert>
            <div className="mt-4 space-y-2">
              {planOutputs.map((outputName) => (
                <div key={outputName} className="border rounded-lg p-3 bg-muted/30">
                  <span className="font-mono text-sm">{outputName}</span>
                  <span className="text-xs text-muted-foreground ml-2">
                    (value will be available after apply)
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Both available */}
        {hasPlanOutputs && hasStateOutputs && planOutputs.some(name => !stateOutputs[name]) && (
          <div className="mt-6 pt-6 border-t">
            <div className="flex items-center gap-2 mb-3">
              <AlertTriangle className="h-4 w-4 text-warning" />
              <h4 className="font-semibold">Additional Planned Outputs</h4>
            </div>
            <Alert>
              <AlertTriangle className="h-4 w-4" />
              <AlertDescription>
                These outputs are defined in the latest plan but not yet applied:
              </AlertDescription>
            </Alert>
            <div className="mt-4 space-y-2">
              {planOutputs
                .filter(name => !stateOutputs[name])
                .map((outputName) => (
                  <div key={outputName} className="border rounded-lg p-3 bg-muted/30">
                    <span className="font-mono text-sm">{outputName}</span>
                    <span className="text-xs text-muted-foreground ml-2">
                      (pending apply)
                    </span>
                  </div>
                ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
