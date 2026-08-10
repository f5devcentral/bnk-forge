import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  FileOutput,
  Copy,
  Eye,
  EyeOff,
  Download,
  CheckCircle2,
  AlertTriangle,
  RefreshCw,
} from 'lucide-react';
import { useState, useEffect, useCallback } from 'react';
import { api } from '@/lib/api';
import type { ProjectModule, JsonValue } from '@/types';
import { logger } from '@/lib/logger';

interface ModuleOutput {
  name: string;
  value: JsonValue;
  type?: string;
  sensitive: boolean;
  description?: string;
}

interface ModuleOutputs {
  [moduleId: number]: {
    outputs: Record<string, ModuleOutput>;
    exists: boolean;
    plannedOutputs?: string[]; // Output names from plan
  };
}

interface OutputsViewerProps {
  modules: ProjectModule[];
}

export function OutputsViewer({ modules }: OutputsViewerProps) {
  const [showSensitive, setShowSensitive] = useState<Record<string, boolean>>({});
  const [copiedField, setCopiedField] = useState<string | null>(null);
  const [moduleOutputs, setModuleOutputs] = useState<ModuleOutputs>({});
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadOutputs = useCallback(async () => {
    if (!modules || modules.length === 0) return;

    setIsLoading(true);
    setError(null);

    try {
      const outputsData: ModuleOutputs = {};

      await Promise.all(
        modules.map(async (module) => {
          try {
            // Load applied outputs from state file
            const response = await api.getModuleOutputs(module.id);
            const appliedOutputs = response.outputs || {};
            const exists = response.exists || false;

            // Also load planned outputs from latest plan task
            let plannedOutputs: string[] = [];
            try {
              const tasksResponse = await api.getTasks({ module_id: module.id, task_type: 'plan' });
              if (tasksResponse.tasks.length > 0) {
                const latestPlanTask = await api.getTask(tasksResponse.tasks[0].id);
                if (latestPlanTask.logs) {
                  const outputSection = latestPlanTask.logs.match(/Changes to Outputs:([\s\S]*?)(?:\n\n|───|$)/);
                  if (outputSection) {
                    const outputMatches = outputSection[1].match(/\s+[+~]\s+(\w+)\s+=/g);
                    if (outputMatches) {
                      plannedOutputs = outputMatches.map(match => match.match(/(\w+)\s+=/)?.[1] || '').filter(Boolean);
                    }
                  }
                }
              }
            } catch (planErr) {
              logger.error(`Failed to load plan outputs for module ${module.id}:`, planErr);
            }

            outputsData[module.id] = {
              outputs: appliedOutputs,
              exists,
              plannedOutputs,
            };
          } catch (err) {
            logger.error(`Failed to load outputs for module ${module.id}:`, err);
            outputsData[module.id] = { outputs: {}, exists: false, plannedOutputs: [] };
          }
        })
      );

      setModuleOutputs(outputsData);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load outputs';
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [modules]);

  useEffect(() => {
    loadOutputs();
  }, [loadOutputs]);

  const toggleSensitive = (key: string) => {
    setShowSensitive((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  const copyToClipboard = (name: string, value: JsonValue) => {
    const textValue = typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value);
    navigator.clipboard.writeText(textValue);
    setCopiedField(name);
    setTimeout(() => setCopiedField(null), 2000);
  };

  const downloadOutputs = () => {
    const allOutputs = modules.flatMap((module) => {
      const moduleData = moduleOutputs[module.id];
      if (!moduleData || !moduleData.exists) return [];

      return Object.entries(moduleData.outputs).map(([name, output]) => ({
        module: module.library_module?.name || 'unknown',
        output: name,
        value: output.sensitive ? '<sensitive>' : output.value,
        type: output.type || 'unknown',
      }));
    });

    const json = JSON.stringify(allOutputs, null, 2);
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `outputs-${Date.now()}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const getValueDisplay = (output: ModuleOutput, showSensitiveValue: boolean) => {
    if (output.sensitive && !showSensitiveValue) {
      return '<sensitive>';
    }

    if (typeof output.value === 'object') {
      return JSON.stringify(output.value, null, 2);
    }

    return String(output.value);
  };

  const hasAnyOutputs = modules.some((m) => {
    const moduleData = moduleOutputs[m.id];
    return moduleData && (
      (moduleData.exists && Object.keys(moduleData.outputs).length > 0) ||
      (moduleData.plannedOutputs && moduleData.plannedOutputs.length > 0)
    );
  });

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              <FileOutput className="h-5 w-5" />
              Module Outputs
            </CardTitle>
            <CardDescription>
              Output values from deployed modules
            </CardDescription>
          </div>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" onClick={loadOutputs} disabled={isLoading}>
              <RefreshCw className={`h-4 w-4 mr-2 ${isLoading ? 'animate-spin' : ''}`} />
              Refresh
            </Button>
            {hasAnyOutputs && (
              <Button size="sm" variant="outline" onClick={downloadOutputs}>
                <Download className="h-4 w-4 mr-2" />
                Export JSON
              </Button>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="text-center py-12">
            <RefreshCw className="h-12 w-12 animate-spin mx-auto mb-4 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">Loading outputs...</p>
          </div>
        ) : error ? (
          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : !hasAnyOutputs ? (
          <Alert>
            <AlertTriangle className="h-4 w-4" />
            <AlertDescription>
              No outputs available. Deploy modules with outputs to see them here.
            </AlertDescription>
          </Alert>
        ) : (
          <ScrollArea className="h-[500px]">
            <div className="space-y-6">
              {modules.map((module) => {
                const moduleData = moduleOutputs[module.id];
                if (!moduleData) return null;

                const hasAppliedOutputs = moduleData.exists && Object.keys(moduleData.outputs).length > 0;
                const hasPlannedOutputs = moduleData.plannedOutputs && moduleData.plannedOutputs.length > 0;

                if (!hasAppliedOutputs && !hasPlannedOutputs) {
                  return null;
                }

                const outputs = Object.entries(moduleData.outputs);

                return (
                  <div key={module.id} className="border rounded-lg p-4">
                    <h4 className="font-semibold mb-3 flex items-center gap-2">
                      {module.library_module?.name || 'Module'}
                      <Badge variant="outline" className="text-xs">
                        {hasAppliedOutputs ? outputs.length : hasPlannedOutputs ? (moduleData.plannedOutputs?.length ?? 0) : 0} output{(hasAppliedOutputs ? outputs.length : hasPlannedOutputs ? (moduleData.plannedOutputs?.length ?? 0) : 0) !== 1 ? 's' : ''}
                      </Badge>
                    </h4>

                    {/* Applied Outputs */}
                    {hasAppliedOutputs && (
                      <div className="space-y-3 mb-4">
                        <div className="flex items-center gap-2">
                          <CheckCircle2 className="h-4 w-4 text-success" />
                          <span className="text-sm font-semibold">Applied outputs</span>
                        </div>
                      {outputs.map(([outputName, output]) => {
                        const key = `${module.id}-${outputName}`;
                        const showValue = showSensitive[key] || false;

                        return (
                          <div
                            key={key}
                            className="border rounded-lg p-3 bg-muted/30 hover:bg-muted/50 transition-colors"
                          >
                            <div className="flex items-start justify-between mb-2">
                              <div className="flex-1">
                                <div className="flex items-center gap-2">
                                  <span className="font-mono text-sm font-medium">
                                    {outputName}
                                  </span>
                                  {output.type && (
                                    <Badge variant="secondary" className="text-xs">
                                      {output.type}
                                    </Badge>
                                  )}
                                  {output.sensitive && (
                                    <Badge variant="destructive" className="text-xs">
                                      sensitive
                                    </Badge>
                                  )}
                                </div>
                                {output.description && (
                                  <p className="text-xs text-muted-foreground mt-1">
                                    {output.description}
                                  </p>
                                )}
                              </div>

                              <div className="flex gap-1">
                                {output.sensitive && (
                                  <Button
                                    size="sm"
                                    variant="ghost"
                                    onClick={() => toggleSensitive(key)}
                                  >
                                    {showValue ? (
                                      <EyeOff className="h-4 w-4" />
                                    ) : (
                                      <Eye className="h-4 w-4" />
                                    )}
                                  </Button>
                                )}
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onClick={() => copyToClipboard(outputName, output.value)}
                                >
                                  {copiedField === outputName ? (
                                    <CheckCircle2 className="h-4 w-4 text-success" />
                                  ) : (
                                    <Copy className="h-4 w-4" />
                                  )}
                                </Button>
                              </div>
                            </div>

                            <div className="font-mono text-sm bg-muted text-foreground p-2 rounded max-h-64 overflow-auto">
                              <pre className="whitespace-pre-wrap break-all">{getValueDisplay(output, showValue)}</pre>
                            </div>
                          </div>
                        );
                      })}
                      </div>
                    )}

                    {/* Planned Outputs */}
                    {hasPlannedOutputs && !hasAppliedOutputs && (
                      <div className="space-y-3">
                        <div className="flex items-center gap-2">
                          <AlertTriangle className="h-4 w-4 text-warning" />
                          <span className="text-sm font-semibold">Planned outputs</span>
                        </div>
                        <Alert>
                          <AlertTriangle className="h-4 w-4" />
                          <AlertDescription>
                            These outputs will be available after running tofu apply.
                          </AlertDescription>
                        </Alert>
                        <div className="space-y-2">
                          {moduleData.plannedOutputs?.map((outputName) => (
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
                  </div>
                );
              })}
            </div>
          </ScrollArea>
        )}

        <div className="mt-4 text-xs text-muted-foreground">
          <p>
            <strong>Note:</strong> Outputs are populated after a successful <code>tofu apply</code>.
            Sensitive outputs are masked by default.
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
