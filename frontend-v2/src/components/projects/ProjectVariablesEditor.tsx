import { useState, useEffect } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Switch } from '@/components/ui/switch';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { 
  Plus, Trash2, Save, Info, Zap, Settings2, 
  ChevronDown, ChevronRight, RotateCcw 
} from 'lucide-react';
import { useProjectVariables, useUpdateProjectVariables } from '@/hooks/useProjects';
import { cn } from '@/lib/utils';
import type { JsonValue } from '@/types';

interface ProjectVariablesEditorProps {
  projectId: number;
}

interface ConfigurableVar {
  type: string;
  description: string;
  default: JsonValue;
  required: boolean;
  sensitive: boolean;
  used_by: string[];
  modules_count: number;
  current_value: JsonValue;
  value_source: 'user' | 'discovered' | 'default' | 'unset';
}

export function ProjectVariablesEditor({ projectId }: ProjectVariablesEditorProps) {
  const { data, isLoading } = useProjectVariables(projectId);
  const updateMutation = useUpdateProjectVariables();

  const [variables, setVariables] = useState<Record<string, JsonValue>>({});
  const [newVarKey, setNewVarKey] = useState('');
  const [newVarValue, setNewVarValue] = useState('');
  const [hasChanges, setHasChanges] = useState(false);
  const [discoveredOpen, setDiscoveredOpen] = useState(false);
  const [showAllVars, setShowAllVars] = useState(false);
  
  // Performance: Limit initial render to prevent UI freeze with many variables
  const INITIAL_VARS_LIMIT = 20;

  // Load variables when data is fetched
  useEffect(() => {
    if (data?.variables) {
      setVariables(data.variables);
      setHasChanges(false);
    }
  }, [data]);

  const handleVariableChange = (key: string, value: JsonValue) => {
    setVariables({ ...variables, [key]: value });
    setHasChanges(true);
  };

  const handleResetToDefault = (key: string) => {
    const newVars = { ...variables };
    delete newVars[key]; // Remove user override, will use default
    setVariables(newVars);
    setHasChanges(true);
  };

  const handleAddVariable = () => {
    if (newVarKey && newVarValue) {
      let parsedValue: JsonValue = newVarValue;
      try {
        parsedValue = JSON.parse(newVarValue);
      } catch {
        // Keep as string if not valid JSON
      }
      setVariables({ ...variables, [newVarKey]: parsedValue });
      setNewVarKey('');
      setNewVarValue('');
      setHasChanges(true);
    }
  };

  const handleDeleteVariable = (key: string) => {
    const newVars = { ...variables };
    delete newVars[key];
    setVariables(newVars);
    setHasChanges(true);
  };

  const handleSave = async () => {
    await updateMutation.mutateAsync({
      projectId,
      variables,
    });
    setHasChanges(false);
  };

  const formatValue = (value: JsonValue): string => {
    if (value === null || value === undefined) return 'null';
    if (typeof value === 'object') {
      const str = JSON.stringify(value);
      return str.length > 50 ? str.substring(0, 50) + '...' : str;
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

  const discoveredVars = data?.discovered || {};
  const configurableVars = data?.configurable || {};
  const hasDiscovered = Object.keys(discoveredVars).length > 0;
  const hasConfigurable = Object.keys(configurableVars).length > 0;

  // Filter configurable vars to show important ones first
  const importantVars = Object.entries(configurableVars as Record<string, ConfigurableVar>)
    .filter(([, v]) => v.modules_count > 0)
    .sort((a, b) => {
      // Sort by: user-set first, then by modules_count, then alphabetically
      const aSource = a[1].value_source === 'user' ? 0 : 1;
      const bSource = b[1].value_source === 'user' ? 0 : 1;
      if (aSource !== bSource) return aSource - bSource;
      if (b[1].modules_count !== a[1].modules_count) return b[1].modules_count - a[1].modules_count;
      return a[0].localeCompare(b[0]);
    });

  // Render input based on variable type
  const renderVariableInput = (key: string, varInfo: ConfigurableVar) => {
    const currentValue = variables[key] ?? varInfo.current_value;

    if (varInfo.type === 'bool') {
      return (
        <div className="flex items-center gap-2">
          <Switch
            checked={currentValue === true}
            onCheckedChange={(checked) => handleVariableChange(key, checked)}
          />
          <span className="text-sm text-muted-foreground">
            {currentValue === true ? 'true' : 'false'}
          </span>
        </div>
      );
    }

    if (varInfo.type === 'number') {
      return (
        <Input
          type="number"
          value={typeof currentValue === 'number' ? currentValue : (currentValue != null ? String(currentValue) : '')}
          onChange={(e) => handleVariableChange(key, e.target.value ? Number(e.target.value) : null)}
          className="h-8 w-32"
        />
      );
    }

    // String or other types
    return (
      <Input
        value={typeof currentValue === 'object' ? JSON.stringify(currentValue) : (currentValue != null ? String(currentValue) : '')}
        onChange={(e) => {
          let val: JsonValue = e.target.value;
          // Try to parse as JSON for objects/arrays
          if (varInfo.type === 'list' || varInfo.type === 'map' || varInfo.type === 'object') {
            try {
              val = JSON.parse(e.target.value);
            } catch {
              // Keep as string while editing
            }
          }
          handleVariableChange(key, val);
        }}
        className="h-8"
        placeholder={varInfo.default !== null ? String(varInfo.default) : 'Enter value...'}
      />
    );
  };

  return (
    <div className="space-y-4">
      {/* Info Alert */}
      <Alert>
        <Info className="h-4 w-4" />
        <AlertDescription>
          Configure project-wide variables that apply to all module deployments. 
          Auto-discovered values from deployed modules are shown below for reference.
        </AlertDescription>
      </Alert>

      {/* Configurable Variables (Editable) */}
      {hasConfigurable && (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="text-lg flex items-center gap-2">
                  <Settings2 className="h-4 w-4 text-primary" />
                  Configurable Variables
                </CardTitle>
                <CardDescription>
                  Variables you can adjust before deployment. These are injected into all modules.
                </CardDescription>
              </div>
              {hasChanges && (
                <Badge variant="secondary">Unsaved Changes</Badge>
              )}
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            {importantVars.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-4">
                No configurable variables found in project modules.
              </p>
            ) : (
              <div className="space-y-2 max-h-[500px] overflow-y-auto">
                {/* Performance: Only render limited vars initially, show more on demand */}
                {(showAllVars ? importantVars : importantVars.slice(0, INITIAL_VARS_LIMIT)).map(([key, varInfo]) => {
                  const isUserSet = key in variables;
                  const hasDefault = varInfo.default !== null && varInfo.default !== undefined;
                  
                  return (
                    <div 
                      key={key} 
                      className={cn(
                        "p-3 rounded-md border",
                        isUserSet
                          ? "bg-info/10 border-info/20"
                          : "bg-muted/50 border-border"
                      )}
                    >
                      <div className="flex items-start gap-3">
                        {/* Variable name and description */}
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <code className="text-sm font-mono font-semibold">{key}</code>
                            <Badge variant="outline" className="text-xs">
                              {varInfo.type}
                            </Badge>
                            {varInfo.modules_count > 1 && (
                              <Badge variant="secondary" className="text-xs">
                                {varInfo.modules_count} modules
                              </Badge>
                            )}
                            {isUserSet && (
                              <Badge variant="info" className="text-xs">modified</Badge>
                            )}
                          </div>
                          {varInfo.description && (
                            <p className="text-xs text-muted-foreground mt-1 truncate">
                              {varInfo.description}
                            </p>
                          )}
                          {hasDefault && !isUserSet && (
                            <p className="text-xs text-muted-foreground mt-0.5">
                              Default: <code className="text-xs">{formatValue(varInfo.default)}</code>
                            </p>
                          )}
                        </div>

                        {/* Input */}
                        <div className="flex-shrink-0 w-48">
                          {renderVariableInput(key, varInfo)}
                        </div>

                        {/* Reset button */}
                        {isUserSet && hasDefault && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => handleResetToDefault(key)}
                            title="Reset to default"
                            className="flex-shrink-0"
                          >
                            <RotateCcw className="h-4 w-4" />
                          </Button>
                        )}
                      </div>
                    </div>
                  );
                })}
                
                {/* Show more button when there are more variables */}
                {!showAllVars && importantVars.length > INITIAL_VARS_LIMIT && (
                  <Button 
                    variant="outline" 
                    className="w-full mt-2"
                    onClick={() => setShowAllVars(true)}
                  >
                    Show {importantVars.length - INITIAL_VARS_LIMIT} more variables
                  </Button>
                )}
                {showAllVars && importantVars.length > INITIAL_VARS_LIMIT && (
                  <Button 
                    variant="ghost" 
                    className="w-full mt-2"
                    onClick={() => setShowAllVars(false)}
                  >
                    Show less
                  </Button>
                )}
              </div>
            )}

            {/* Save Button */}
            <div className="flex gap-2 pt-4 border-t">
              <Button
                onClick={handleSave}
                disabled={!hasChanges || updateMutation.isPending}
              >
                <Save className="h-4 w-4 mr-2" />
                {updateMutation.isPending ? 'Saving...' : 'Save Variables'}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Auto-Discovered Variables (Collapsed by default) */}
      {hasDiscovered && (
        <Collapsible open={discoveredOpen} onOpenChange={setDiscoveredOpen}>
          <Card>
            <CollapsibleTrigger asChild>
              <CardHeader className="cursor-pointer hover:bg-muted/50 transition-colors">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    {discoveredOpen ? (
                      <ChevronDown className="h-4 w-4" />
                    ) : (
                      <ChevronRight className="h-4 w-4" />
                    )}
                    <Zap className="h-4 w-4 text-warning" />
                    <CardTitle className="text-lg">Auto-Discovered Dependencies</CardTitle>
                    <Badge variant="outline" className="ml-2">
                      {Object.keys(discoveredVars).length} values
                    </Badge>
                  </div>
                </div>
                <CardDescription className="ml-8">
                  Read-only values automatically wired from deployed module outputs.
                </CardDescription>
              </CardHeader>
            </CollapsibleTrigger>
            <CollapsibleContent>
              <CardContent>
                <div className="space-y-2 max-h-96 overflow-y-auto">
                  {Object.entries(discoveredVars).map(([key, info]) => (
                    <div 
                      key={key} 
                      className="flex items-center gap-3 p-2 bg-warning/10 border border-warning/20 rounded-md"
                    >
                      <div className="flex-1 min-w-0">
                        <code className="text-sm font-mono">{key}</code>
                      </div>
                      <div className="flex-1 min-w-0">
                        <code className="text-xs font-mono text-muted-foreground truncate block">
                          {info.is_truncated ? String(info.display_value ?? '') : formatValue(info.value)}
                        </code>
                      </div>
                      <Badge variant="outline" className="text-xs flex-shrink-0">
                        {info.type}
                      </Badge>
                      <Badge variant="outline" className="text-xs flex-shrink-0 max-w-32 truncate">
                        {info.source_module || info.source}
                      </Badge>
                    </div>
                  ))}
                </div>
              </CardContent>
            </CollapsibleContent>
          </Card>
        </Collapsible>
      )}

      {/* Custom Variables (for advanced users) */}
      <Collapsible>
        <Card>
          <CollapsibleTrigger asChild>
            <CardHeader className="cursor-pointer hover:bg-muted/50 transition-colors">
              <div className="flex items-center gap-2">
                <ChevronRight className="h-4 w-4" />
                <CardTitle className="text-lg">Custom Variables</CardTitle>
              </div>
              <CardDescription className="ml-6">
                Add custom key-value pairs for variables not defined in modules.
              </CardDescription>
            </CardHeader>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <CardContent className="space-y-4">
              {/* Add New Variable Form */}
              <div className="flex gap-2">
                <div className="flex-1 space-y-1">
                  <Label htmlFor="var-key" className="text-xs">Variable Name</Label>
                  <Input
                    id="var-key"
                    placeholder="e.g., custom_setting"
                    value={newVarKey}
                    onChange={(e) => setNewVarKey(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') handleAddVariable();
                    }}
                  />
                </div>
                <div className="flex-1 space-y-1">
                  <Label htmlFor="var-value" className="text-xs">Value</Label>
                  <Input
                    id="var-value"
                    placeholder="e.g., my-value"
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

              {/* Custom variables that aren't in configurable list */}
              {Object.entries(variables)
                .filter(([key]) => !(key in configurableVars))
                .length > 0 && (
                <div className="space-y-2">
                  {Object.entries(variables)
                    .filter(([key]) => !(key in configurableVars))
                    .map(([key, value]) => (
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
                          aria-label={`Delete ${key}`}
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    ))}
                </div>
              )}
            </CardContent>
          </CollapsibleContent>
        </Card>
      </Collapsible>
    </div>
  );
}
