import { Editor } from '@monaco-editor/react';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Check, X, AlertTriangle } from 'lucide-react';
import { useState, useEffect } from 'react';
import yaml from 'js-yaml';

interface YamlEditorProps {
  value: string;
  onChange: (value: string) => void;
  height?: string;
  readOnly?: boolean;
  showValidation?: boolean;
}

interface ValidationResult {
  isValid: boolean;
  error?: string;
  warning?: string;
}

export function YamlEditor({
  value,
  onChange,
  height = '400px',
  readOnly = false,
  showValidation = true,
}: YamlEditorProps) {
  const [validation, setValidation] = useState<ValidationResult>({ isValid: true });

  useEffect(() => {
    if (!showValidation || !value) {
      setValidation({ isValid: true });
      return;
    }

    try {
      const parsed = yaml.load(value);

      if (!parsed || typeof parsed !== 'object') {
        setValidation({
          isValid: false,
          error: 'Invalid YAML: Must be an object',
        });
        return;
      }

      // Check for required Kubernetes fields
      // Type guard: check if parsed object has expected K8s structure
      const hasApiVersion = (obj: unknown): obj is { apiVersion: string } =>
        typeof obj === 'object' && obj !== null && 'apiVersion' in obj && typeof (obj as { apiVersion: unknown }).apiVersion === 'string';
      const hasKind = (obj: unknown): obj is { kind: string } =>
        typeof obj === 'object' && obj !== null && 'kind' in obj && typeof (obj as { kind: unknown }).kind === 'string';
      const hasMetadata = (obj: unknown): obj is { metadata: { name: string } } =>
        typeof obj === 'object' && obj !== null && 'metadata' in obj &&
        typeof (obj as { metadata: unknown }).metadata === 'object' && (obj as { metadata: { name?: unknown } }).metadata !== null &&
        'name' in (obj as { metadata: { name?: unknown } }).metadata;

      if (!hasApiVersion(parsed)) {
        setValidation({
          isValid: true,
          warning: 'Missing apiVersion field',
        });
        return;
      }

      if (!hasKind(parsed)) {
        setValidation({
          isValid: true,
          warning: 'Missing kind field',
        });
        return;
      }

      if (!hasMetadata(parsed)) {
        setValidation({
          isValid: true,
          warning: 'Missing metadata.name field',
        });
        return;
      }

      setValidation({ isValid: true });
    } catch (error) {
      setValidation({
        isValid: false,
        error: error instanceof Error ? error.message : 'Invalid YAML syntax',
      });
    }
  }, [value, showValidation]);

  const handleEditorChange = (newValue: string | undefined) => {
    onChange(newValue || '');
  };

  return (
    <div className="space-y-2">
      {showValidation && (
        <div className="flex items-center gap-2">
          {validation.isValid && !validation.warning && (
            <Badge variant="outline" className="gap-1">
              <Check className="h-3 w-3" />
              Valid YAML
            </Badge>
          )}
          {validation.warning && (
            <Badge variant="secondary" className="gap-1">
              <AlertTriangle className="h-3 w-3" />
              {validation.warning}
            </Badge>
          )}
          {!validation.isValid && validation.error && (
            <Badge variant="destructive" className="gap-1">
              <X className="h-3 w-3" />
              {validation.error}
            </Badge>
          )}
        </div>
      )}

      <Card className="overflow-hidden">
        <Editor
          height={height}
          defaultLanguage="yaml"
          value={value}
          onChange={handleEditorChange}
          options={{
            readOnly,
            minimap: { enabled: false },
            scrollBeyondLastLine: false,
            fontSize: 13,
            lineNumbers: 'on',
            folding: true,
            wordWrap: 'on',
            automaticLayout: true,
            tabSize: 2,
            renderWhitespace: 'selection',
            bracketPairColorization: {
              enabled: true,
            },
          }}
          theme="vs-dark"
        />
      </Card>
    </div>
  );
}
