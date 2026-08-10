import { useState } from 'react';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { AlertCircle, HelpCircle, Eye, EyeOff, CheckCircle2, XCircle, Link2, Info } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { ListenerBuilder, type Listener } from '@/components/bnk/ListenerBuilder';
import { RoutingRulesBuilder, type RoutingRule } from '@/components/bnk/RoutingRulesBuilder';
import { DocaReleasePicker } from '@/components/modules/DocaPackagePicker';

// Enum value can be a simple string or a label/value pair
type EnumValue = string | { label: string; value: string };

interface ModuleVariable {
  name: string;
  type?: string;
  description?: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  default?: any;
  example?: string;  // Example value to show as placeholder
  required?: boolean;
  sensitive?: boolean;
  validation?: {
    condition?: string;
    error_message?: string;
    pattern?: string;  // Regex pattern for validation
    type?: 'file_path' | 'regex' | 'range';  // Validation type hint
  };
  enum_values?: EnumValue[];  // Supports both ["val"] and [{label, value}] formats
  min?: number;
  max?: number;
  pattern?: string;  // Legacy: pattern at top level
  // v2 metadata fields
  source?: "user" | "module" | "project" | "computed";
  from_module?: string;
  from_output?: string;
}

interface ModuleVariableEditorProps {
  variables: ModuleVariable[];
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  values: Record<string, any>;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  onChange: (values: Record<string, any>) => void;
}

export function ModuleVariableEditor({
  variables,
  values,
  onChange,
}: ModuleVariableEditorProps) {
  const [showSensitive, setShowSensitive] = useState<Record<string, boolean>>({});
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({});

  // Extract user-friendly name from module path
  const getModuleName = (modulePath: string): string => {
    // Extract name from path (e.g., "infra/aws/vpc" -> "vpc")
    const pathParts = modulePath.split('/');
    const moduleName = pathParts[pathParts.length - 1];

    // Capitalize and format (e.g., "vpc" -> "VPC", "eks" -> "EKS")
    return moduleName.toUpperCase();
  };

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const handleChange = (name: string, value: any) => {
    const newValues = {
      ...values,
      [name]: value,
    };
    onChange(newValues);

    // Validate the input
    const variable = variables.find(v => v.name === name);
    if (variable) {
      validateVariable(variable, value);
    }
  };

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const validateVariable = (variable: ModuleVariable, value: any): boolean => {
    const errors = { ...validationErrors };

    // Required check
    if (variable.required && (!value || value === '')) {
      errors[variable.name] = 'This field is required';
      setValidationErrors(errors);
      return false;
    }

    // Type-specific validation
    if (variable.type === 'number') {
      const numValue = Number(value);
      if (isNaN(numValue)) {
        errors[variable.name] = 'Must be a valid number';
        setValidationErrors(errors);
        return false;
      }
      if (variable.min !== undefined && numValue < variable.min) {
        errors[variable.name] = `Must be at least ${variable.min}`;
        setValidationErrors(errors);
        return false;
      }
      if (variable.max !== undefined && numValue > variable.max) {
        errors[variable.name] = `Must be at most ${variable.max}`;
        setValidationErrors(errors);
        return false;
      }
    }

    // Pattern validation (check both legacy and new validation object)
    const pattern = variable.validation?.pattern || variable.pattern;
    if (pattern && value) {
      try {
        const regex = new RegExp(pattern);
        if (!regex.test(value)) {
          errors[variable.name] = variable.validation?.error_message || 'Invalid format';
          setValidationErrors(errors);
          return false;
        }
      } catch (_e) {
        // Invalid regex, skip validation
      }
    }

    // JSON validation for list/map types
    if ((variable.type === 'list' || variable.type === 'map') && value) {
      try {
        JSON.parse(value);
      } catch (_e) {
        errors[variable.name] = 'Must be valid JSON';
        setValidationErrors(errors);
        return false;
      }
    }

    // Clear error if validation passed
    delete errors[variable.name];
    setValidationErrors(errors);
    return true;
  };

  const toggleSensitive = (name: string) => {
    setShowSensitive(prev => ({
      ...prev,
      [name]: !prev[name],
    }));
  };

  const getInputType = (variable: ModuleVariable) => {
    if (variable.sensitive && !showSensitive[variable.name]) {
      return 'password';
    }
    switch (variable.type?.toLowerCase()) {
      case 'number':
        return 'number';
      case 'bool':
      case 'boolean':
        return 'checkbox';
      default:
        return 'text';
    }
  };

  const renderValidationState = (variableName: string) => {
    const value = values[variableName];
    const error = validationErrors[variableName];
    const variable = variables.find(v => v.name === variableName);

    if (error) {
      return (
        <div className="flex items-center gap-1 text-xs text-destructive mt-1">
          <XCircle className="h-3 w-3" />
          {error}
        </div>
      );
    }

    if (value && variable?.required) {
      return (
        <div className="flex items-center gap-1 text-xs text-success mt-1">
          <CheckCircle2 className="h-3 w-3" />
          Valid
        </div>
      );
    }

    return null;
  };

  const renderInput = (variable: ModuleVariable) => {
    const value = values[variable.name] ?? variable.default ?? '';
    const hasError = !!validationErrors[variable.name];

    // Enum dropdown - supports both string[] and {label, value}[] formats
    if (variable.enum_values && variable.enum_values.length > 0) {
      // Normalize enum values to {label, value} format
      const normalizedEnums = variable.enum_values.map((enumVal) => {
        if (typeof enumVal === 'string') {
          return { label: enumVal, value: enumVal };
        }
        return enumVal as { label: string; value: string };
      });

      return (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label htmlFor={variable.name}>
              {variable.name}
              {variable.required && <span className="text-destructive ml-1">*</span>}
            </Label>
            <Badge variant="outline" className="text-xs">
              {variable.type || 'enum'}
            </Badge>
          </div>
          <Select
            value={value}
            onValueChange={(val: string) => handleChange(variable.name, val)}
          >
            <SelectTrigger id={variable.name} className={hasError ? 'border-destructive' : ''}>
              <SelectValue placeholder="Select value..." />
            </SelectTrigger>
            <SelectContent>
              {normalizedEnums.map((enumVal) => (
                <SelectItem key={enumVal.value} value={enumVal.value}>
                  {enumVal.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {variable.description && (
            <p className="text-xs text-muted-foreground flex items-start gap-1">
              <HelpCircle className="h-3 w-3 mt-0.5 flex-shrink-0" />
              {variable.description}
            </p>
          )}
          {renderValidationState(variable.name)}
        </div>
      );
    }

    // Boolean checkbox
    if (getInputType(variable) === 'checkbox') {
      return (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              id={variable.name}
              checked={Boolean(value)}
              onChange={(e) => handleChange(variable.name, e.target.checked)}
              className="rounded border-input"
            />
            <Label htmlFor={variable.name} className="cursor-pointer">
              {variable.required ? (
                <span>
                  {variable.name} <span className="text-destructive">*</span>
                </span>
              ) : (
                variable.name
              )}
            </Label>
            <Badge variant="outline" className="text-xs">
              boolean
            </Badge>
          </div>
          {variable.description && (
            <p className="text-xs text-muted-foreground flex items-start gap-1 ml-6">
              <HelpCircle className="h-3 w-3 mt-0.5 flex-shrink-0" />
              {variable.description}
            </p>
          )}
        </div>
      );
    }

    // Special: DOCA Release Picker
    if (variable.name === 'doca_release_id') {
      return (
        <DocaReleasePicker
          variable={variable}
          value={value}
          onChange={(val) => handleChange(variable.name, val)}
          hasError={hasError}
          renderValidationState={() => renderValidationState(variable.name)}
        />
      );
    }

    // Special: Gateway Listeners Builder
    if (variable.name === 'listeners' && variable.type?.includes('list')) {
      const listenersValue: Listener[] = typeof value === 'string'
        ? (value ? JSON.parse(value) : [])
        : (Array.isArray(value) ? value : []);

      return (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label htmlFor={variable.name}>
              {variable.name}
              {variable.required && <span className="text-destructive ml-1">*</span>}
            </Label>
            <Badge variant="info" className="text-xs">
              Gateway Listeners
            </Badge>
          </div>
          {variable.description && (
            <p className="text-xs text-muted-foreground flex items-start gap-1 mb-2">
              <Info className="h-3 w-3 mt-0.5 flex-shrink-0" />
              {variable.description}
            </p>
          )}
          <ListenerBuilder
            value={listenersValue}
            onChange={(listeners) => handleChange(variable.name, listeners)}
          />
          {renderValidationState(variable.name)}
        </div>
      );
    }

    // Special: HTTPRoute Routing Rules Builder
    if (variable.name === 'routing_rules' && variable.type?.includes('list')) {
      const rulesValue: RoutingRule[] = typeof value === 'string'
        ? (value ? JSON.parse(value) : [])
        : (Array.isArray(value) ? value : []);

      return (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label htmlFor={variable.name}>
              {variable.name}
              {variable.required && <span className="text-destructive ml-1">*</span>}
            </Label>
            <Badge variant="secondary" className="text-xs">
              Routing Rules
            </Badge>
          </div>
          {variable.description && (
            <p className="text-xs text-muted-foreground flex items-start gap-1 mb-2">
              <Info className="h-3 w-3 mt-0.5 flex-shrink-0" />
              {variable.description}
            </p>
          )}
          <RoutingRulesBuilder
            value={rulesValue}
            onChange={(rules) => handleChange(variable.name, rules)}
          />
          {renderValidationState(variable.name)}
        </div>
      );
    }

    // List/Map JSON textarea (fallback for other list types)
    if (variable.type === 'list' || variable.type === 'map') {
      return (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label htmlFor={variable.name}>
              {variable.name}
              {variable.required && <span className="text-destructive ml-1">*</span>}
            </Label>
            <Badge variant="outline" className="text-xs">
              {variable.type}
            </Badge>
          </div>
          <Textarea
            id={variable.name}
            value={value}
            onChange={(e) => handleChange(variable.name, e.target.value)}
            placeholder={variable.type === 'list' ? '["item1", "item2"]' : '{"key": "value"}'}
            rows={4}
            className={`font-mono text-sm ${hasError ? 'border-destructive' : ''}`}
          />
          {variable.description && (
            <p className="text-xs text-muted-foreground flex items-start gap-1">
              <HelpCircle className="h-3 w-3 mt-0.5 flex-shrink-0" />
              {variable.description}
            </p>
          )}
          {renderValidationState(variable.name)}
        </div>
      );
    }

    // Standard text/number/password input
    return (
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label htmlFor={variable.name}>
            {variable.name}
            {variable.required && <span className="text-destructive ml-1">*</span>}
            {variable.sensitive && (
              <Badge variant="secondary" className="text-xs ml-2">
                sensitive
              </Badge>
            )}
          </Label>
          <Badge variant="outline" className="text-xs">
            {variable.type || 'string'}
          </Badge>
        </div>
        <div className="relative">
          <Input
            id={variable.name}
            type={getInputType(variable)}
            value={value}
            onChange={(e) => handleChange(variable.name, e.target.value)}
            placeholder={
              variable.default
                ? `Default: ${variable.default}`
                : variable.example
                  ? `Example: ${variable.example}`
                  : `Enter ${variable.name}...`
            }
            min={variable.min}
            max={variable.max}
            className={`${hasError ? 'border-destructive' : ''} ${variable.sensitive ? 'pr-10' : ''}`}
          />
          {variable.sensitive && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="absolute right-0 top-0 h-full px-3 hover:bg-transparent"
              onClick={() => toggleSensitive(variable.name)}
            >
              {showSensitive[variable.name] ? (
                <EyeOff className="h-4 w-4 text-muted-foreground" />
              ) : (
                <Eye className="h-4 w-4 text-muted-foreground" />
              )}
            </Button>
          )}
        </div>
        {variable.description && (
          <p className="text-xs text-muted-foreground flex items-start gap-1">
            <HelpCircle className="h-3 w-3 mt-0.5 flex-shrink-0" />
            {variable.description}
          </p>
        )}
        {/* Show format hint if pattern validation exists */}
        {(variable.validation?.pattern || variable.pattern) && variable.example && !validationErrors[variable.name] && (
          <p className="text-xs text-warning flex items-start gap-1">
            <AlertCircle className="h-3 w-3 mt-0.5 flex-shrink-0" />
            Required format: {variable.example}
          </p>
        )}
        {renderValidationState(variable.name)}
      </div>
    );
  };

  if (!variables || variables.length === 0) {
    return (
      <div className="text-center py-8 text-muted-foreground">
        <AlertCircle className="h-12 w-12 mx-auto mb-2 opacity-50" />
        <p>No variables to configure</p>
        <p className="text-sm mt-1">This module uses default values</p>
      </div>
    );
  }

  // Separate variables by source
  const moduleSourcedVars = variables.filter((v) => v.source === 'module');
  const projectSourcedVars = variables.filter((v) => v.source === 'project');
  const userVars = variables.filter((v) => !v.source || v.source === 'user');

  const requiredUserVars = userVars.filter((v) => v.required);
  const optionalUserVars = userVars.filter((v) => !v.required);

  return (
    <div className="space-y-6 max-h-[500px] overflow-y-auto pr-2">
      {/* Module-sourced variables (auto-wired from dependencies) */}
      {moduleSourcedVars.length > 0 && (
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <Link2 className="h-4 w-4 text-info" />
            <h4 className="font-medium">Auto-Wired from Dependencies</h4>
            <Badge variant="info" className="text-xs">
              {moduleSourcedVars.length}
            </Badge>
          </div>
          <Alert className="bg-info/10 border-info/20">
            <Info className="h-4 w-4 text-info" />
            <AlertDescription className="text-sm text-info">
              These variables are automatically populated from dependency module outputs.
            </AlertDescription>
          </Alert>
          {moduleSourcedVars.map((variable) => (
            <div key={variable.name} className="border rounded-lg p-3 bg-muted/30">
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-sm font-medium">{variable.name}</span>
                  <Badge variant="outline" className="text-xs">
                    {variable.type || 'string'}
                  </Badge>
                  <Badge variant="secondary" className="text-xs flex items-center gap-1">
                    <Link2 className="h-3 w-3" />
                    from {variable.from_module ? getModuleName(variable.from_module) : 'dependency'}
                  </Badge>
                </div>
              </div>
              {variable.description && (
                <p className="text-xs text-muted-foreground flex items-start gap-1 mb-2">
                  <HelpCircle className="h-3 w-3 mt-0.5 flex-shrink-0" />
                  {variable.description}
                </p>
              )}
              <div className="text-xs text-muted-foreground bg-muted p-2 rounded">
                <div className="font-medium mb-1">Auto-wired from dependency:</div>
                <div className="font-mono text-info">
                  {variable.from_module ? getModuleName(variable.from_module) : variable.from_module}.outputs.{variable.from_output || variable.name}
                </div>
                <div className="text-[10px] text-muted-foreground mt-1">
                  Module path: {variable.from_module}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Project-sourced variables (auto-populated from project settings) */}
      {projectSourcedVars.length > 0 && (
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <h4 className="font-medium">Project Variables</h4>
            <Badge variant="secondary" className="text-xs">
              {projectSourcedVars.length}
            </Badge>
          </div>
          <Alert className="bg-muted border-border">
            <Info className="h-4 w-4 text-muted-foreground" />
            <AlertDescription className="text-sm">
              These variables are automatically set from project configuration.
            </AlertDescription>
          </Alert>
          {projectSourcedVars.map((variable) => (
            <div key={variable.name} className="border rounded-lg p-3 bg-muted/30">
              <div className="flex items-center gap-2 mb-1">
                <span className="font-mono text-sm font-medium">{variable.name}</span>
                <Badge variant="outline" className="text-xs">
                  {variable.type || 'string'}
                </Badge>
              </div>
              {variable.description && (
                <p className="text-xs text-muted-foreground flex items-start gap-1">
                  <HelpCircle className="h-3 w-3 mt-0.5 flex-shrink-0" />
                  {variable.description}
                </p>
              )}
            </div>
          ))}
        </div>
      )}

      {/* User-provided variables (editable) */}
      {requiredUserVars.length > 0 && (
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <h4 className="font-medium">Required Variables</h4>
            <Badge variant="destructive" className="text-xs">
              {requiredUserVars.length}
            </Badge>
          </div>
          {requiredUserVars.map((variable) => (
            <div key={variable.name}>{renderInput(variable)}</div>
          ))}
        </div>
      )}

      {optionalUserVars.length > 0 && (
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <h4 className="font-medium">Optional Variables</h4>
            <Badge variant="secondary" className="text-xs">
              {optionalUserVars.length}
            </Badge>
          </div>
          {optionalUserVars.map((variable) => (
            <div key={variable.name}>{renderInput(variable)}</div>
          ))}
        </div>
      )}
    </div>
  );
}
