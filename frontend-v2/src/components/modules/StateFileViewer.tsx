import { useState, useEffect, useCallback } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Alert, AlertDescription } from '@/components/ui/alert';
import {
  RefreshCw,
  AlertTriangle,
  Database,
  Copy,
  CheckCircle2,
} from 'lucide-react';
import type { ProjectModule, JsonValue } from '@/types';

interface StateData {
  exists: boolean;
  resources: Array<{ type: string; name: string; provider: string }>;
  outputs: Record<string, JsonValue>;
  metadata: {
    version?: number;
    terraform_version?: string;
    serial?: number;
    lineage?: string;
  };
}

interface StateFileViewerProps {
  module: ProjectModule;
}

export function StateFileViewer({ module }: StateFileViewerProps) {
  const [stateData, setStateData] = useState<StateData | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copiedField, setCopiedField] = useState<string | null>(null);

  const loadStateData = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      // v2: We don't expose state files via API for security
      // Instead, show outputs from the database (captured after apply)
      if (module.outputs && Object.keys(module.outputs).length > 0) {
        setStateData({
          exists: true,
          resources: [], // Not available in v2 (state files are local)
          outputs: module.outputs,
          metadata: {
            terraform_version: 'OpenTofu 1.6.2',
          }
        });
      } else {
        // No outputs yet (module not applied)
        setStateData({ exists: false, resources: [], outputs: {}, metadata: {} });
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Failed to load state data';
      setError(message);
    } finally {
      setIsLoading(false);
    }
  }, [module.outputs]);

  useEffect(() => {
    loadStateData();
  }, [loadStateData]);

  const copyToClipboard = (text: string, label: string) => {
    navigator.clipboard.writeText(text);
    setCopiedField(label);
    setTimeout(() => setCopiedField(null), 2000);
  };

  if (isLoading) {
    return (
      <Card>
        <CardContent className="pt-6 text-center py-12">
          <RefreshCw className="h-12 w-12 animate-spin mx-auto mb-4 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">Loading state file...</p>
        </CardContent>
      </Card>
    );
  }

  if (error) {
    return (
      <Card>
        <CardContent className="pt-6">
          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
          <div className="mt-4 flex justify-end">
            <Button onClick={loadStateData}>
              <RefreshCw className="h-4 w-4 mr-2" />
              Retry
            </Button>
          </div>
        </CardContent>
      </Card>
    );
  }

  if (!stateData?.exists) {
    return (
      <Card>
        <CardContent className="pt-6 text-center py-12">
          <Database className="h-16 w-16 mx-auto mb-4 text-muted-foreground opacity-50" />
          <h3 className="text-lg font-semibold mb-2">State Not Available</h3>
          <p className="text-muted-foreground">
            Module not applied. No state file exists yet.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent className="pt-6">
        <Alert className="mb-4">
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>
            <strong>v2 Architecture:</strong> State files are stored locally for security.
            Only outputs are accessible via GUI. For full state access, use container exec.
          </AlertDescription>
        </Alert>

        {/* Show Outputs */}
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-lg font-semibold">Module Outputs</h3>
              <p className="text-sm text-muted-foreground">
                {Object.keys(stateData.outputs).length} outputs from database
              </p>
            </div>
            <Button size="sm" variant="outline" onClick={loadStateData}>
              <RefreshCw className="h-4 w-4 mr-2" />
              Refresh
            </Button>
          </div>

          {Object.keys(stateData.outputs).length > 0 ? (
            <ScrollArea className="h-[400px]">
              <div className="space-y-2">
                {Object.entries(stateData.outputs).map(([key, value]) => (
                  <div key={key} className="border rounded-lg p-3 bg-muted/50">
                    <div className="flex items-start justify-between">
                      <div className="flex-1">
                        <div className="font-mono text-sm font-semibold text-info mb-1">
                          {key}
                        </div>
                        <div className="font-mono text-sm bg-background rounded px-2 py-1 break-all">
                          {typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)}
                        </div>
                      </div>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => {
                          const valueStr = typeof value === 'object' ? JSON.stringify(value) : String(value);
                          copyToClipboard(valueStr, key);
                        }}
                      >
                        {copiedField === key ? (
                          <CheckCircle2 className="h-4 w-4 text-success" />
                        ) : (
                          <Copy className="h-4 w-4" />
                        )}
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            </ScrollArea>
          ) : (
            <div className="text-center py-8 text-muted-foreground">
              No outputs available
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
