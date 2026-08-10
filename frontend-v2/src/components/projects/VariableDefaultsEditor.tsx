import { useState, useEffect } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Plus, Trash2, RotateCcw, Save, Info } from 'lucide-react';
import { useVariableDefaults, useUpdateVariableDefaults, useResetVariableDefaults } from '@/hooks/useProjects';
import type { JsonValue } from '@/types';

interface VariableDefaultsEditorProps {
  projectId: number;
}

export function VariableDefaultsEditor({ projectId }: VariableDefaultsEditorProps) {
  const { data, isLoading } = useVariableDefaults(projectId);
  const updateMutation = useUpdateVariableDefaults();
  const resetMutation = useResetVariableDefaults();

  const [customDefaults, setCustomDefaults] = useState<Record<string, JsonValue>>({});
  const [newVarKey, setNewVarKey] = useState('');
  const [newVarValue, setNewVarValue] = useState('');
  const [hasChanges, setHasChanges] = useState(false);

  // Load custom defaults when data is fetched
  useEffect(() => {
    if (data?.custom_defaults) {
      setCustomDefaults(data.custom_defaults);
      setHasChanges(false);
    }
  }, [data]);

  const handleAddVariable = () => {
    if (newVarKey && newVarValue) {
      // Try to parse value as JSON for arrays/objects/numbers/booleans
      let parsedValue = newVarValue;
      try {
        // Try parsing as JSON
        const jsonValue = JSON.parse(newVarValue);
        parsedValue = jsonValue;
      } catch {
        // Keep as string if not valid JSON
        parsedValue = newVarValue;
      }

      setCustomDefaults({ ...customDefaults, [newVarKey]: parsedValue });
      setNewVarKey('');
      setNewVarValue('');
      setHasChanges(true);
    }
  };

  const handleDeleteVariable = (key: string) => {
    const newDefaults = { ...customDefaults };
    delete newDefaults[key];
    setCustomDefaults(newDefaults);
    setHasChanges(true);
  };

  const handleSave = async () => {
    await updateMutation.mutateAsync({
      projectId,
      defaults: customDefaults,
    });
    setHasChanges(false);
  };

  const handleReset = async () => {
    if (confirm('Reset all custom defaults to common defaults? This cannot be undone.')) {
      await resetMutation.mutateAsync(projectId);
      setCustomDefaults({});
      setHasChanges(false);
    }
  };

  const formatValue = (value: JsonValue): string => {
    if (typeof value === 'object') {
      return JSON.stringify(value);
    }
    return String(value);
  };

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-4 w-96" />
        </CardHeader>
        <CardContent>
          <Skeleton className="h-32 w-full" />
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      {/* Info Alert */}
      <Alert>
        <Info className="h-4 w-4" />
        <AlertDescription>
          Variable defaults are injected at runtime for new modules. These values apply to
          any module that requires these variables. Common defaults are provided by the system, but
          you can override them with custom values specific to this project.
        </AlertDescription>
      </Alert>

      {/* Common Defaults (Read-only) */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Common Defaults</CardTitle>
          <CardDescription>
            Built-in default values provided by BNK-Forge. These apply to all projects unless
            overridden below.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 max-h-96 overflow-y-auto">
            {data?.common_defaults && Object.entries(data.common_defaults).map(([key, value]) => (
              <div key={key} className="p-3 bg-muted/50 rounded-md">
                <code className="text-xs font-mono text-muted-foreground">{key}</code>
                <div className="mt-1">
                  <code className="text-sm font-mono">{formatValue(value)}</code>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Custom Defaults (Editable) */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="text-lg">Custom Defaults</CardTitle>
              <CardDescription>
                Override common defaults with project-specific values. These will be injected at runtime
                for modules in this project.
              </CardDescription>
            </div>
            <div className="flex gap-2">
              {hasChanges && (
                <Badge variant="secondary">Unsaved Changes</Badge>
              )}
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Add New Variable Form */}
          <div className="flex gap-2">
            <div className="flex-1 space-y-1">
              <Label htmlFor="var-key" className="text-xs">Variable Name</Label>
              <Input
                id="var-key"
                placeholder="e.g., vpc_cidr"
                value={newVarKey}
                onChange={(e) => setNewVarKey(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleAddVariable();
                }}
              />
            </div>
            <div className="flex-1 space-y-1">
              <Label htmlFor="var-value" className="text-xs">Default Value</Label>
              <Input
                id="var-value"
                placeholder="e.g., 10.0.0.0/16"
                value={newVarValue}
                onChange={(e) => setNewVarValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleAddVariable();
                }}
              />
            </div>
            <div className="space-y-1">
              <Label className="text-xs opacity-0">Action</Label>
              <Button onClick={handleAddVariable} disabled={!newVarKey || !newVarValue}>
                <Plus className="h-4 w-4 mr-2" />
                Add
              </Button>
            </div>
          </div>

          {/* Custom Defaults List */}
          {Object.keys(customDefaults).length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              <p className="text-sm">No custom defaults defined</p>
              <p className="text-xs mt-1">Add custom defaults to override common values</p>
            </div>
          ) : (
            <div className="space-y-2 max-h-96 overflow-y-auto">
              {Object.entries(customDefaults).map(([key, value]) => (
                <div key={key} className="flex items-center gap-3 p-3 bg-muted rounded-md">
                  <div className="flex-1">
                    <code className="text-sm font-mono font-semibold">{key}</code>
                  </div>
                  <div className="flex-1">
                    <code className="text-sm font-mono text-muted-foreground">
                      {formatValue(value)}
                    </code>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleDeleteVariable(key)}
                    aria-label={`Delete default value for ${key}`}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              ))}
            </div>
          )}

          {/* Action Buttons */}
          <div className="flex gap-2 pt-4 border-t">
            <Button
              onClick={handleSave}
              disabled={!hasChanges || updateMutation.isPending}
            >
              <Save className="h-4 w-4 mr-2" />
              {updateMutation.isPending ? 'Saving...' : 'Save Custom Defaults'}
            </Button>
            {Object.keys(customDefaults).length > 0 && (
              <Button
                variant="outline"
                onClick={handleReset}
                disabled={resetMutation.isPending}
              >
                <RotateCcw className="h-4 w-4 mr-2" />
                {resetMutation.isPending ? 'Resetting...' : 'Reset to Common Defaults'}
              </Button>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
