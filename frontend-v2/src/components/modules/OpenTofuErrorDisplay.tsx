import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  AlertTriangle,
  XCircle,
  Info,
  Lightbulb,
  Copy,
  ExternalLink,
} from 'lucide-react';

interface OpenTofuError {
  severity: 'error' | 'warning' | 'info';
  summary: string;
  detail: string;
  resource?: string;
  line?: number;
  suggestion?: string;
  docsUrl?: string;
}

interface OpenTofuErrorDisplayProps {
  error: string;
  onRetry?: () => void;
}

export function OpenTofuErrorDisplay({ error, onRetry }: OpenTofuErrorDisplayProps) {
  const parsedErrors = parseOpenTofuError(error);

  const copyError = () => {
    navigator.clipboard.writeText(error);
  };

  return (
    <div className="space-y-4">
      {parsedErrors.map((err, index) => (
        <Card key={index} className="border-destructive/50">
          <CardContent className="pt-6">
            <div className="flex items-start gap-3">
              <div className="mt-0.5">
                {err.severity === 'error' && <XCircle className="h-5 w-5 text-destructive" />}
                {err.severity === 'warning' && <AlertTriangle className="h-5 w-5 text-warning" />}
                {err.severity === 'info' && <Info className="h-5 w-5 text-info" />}
              </div>

              <div className="flex-1 space-y-2">
                <div className="flex items-center gap-2">
                  <h4 className="font-semibold text-destructive">{err.summary}</h4>
                  <Badge variant="destructive" className="text-xs">
                    {err.severity}
                  </Badge>
                  {err.resource && (
                    <Badge variant="outline" className="text-xs font-mono">
                      {err.resource}
                    </Badge>
                  )}
                  {err.line && (
                    <Badge variant="secondary" className="text-xs">
                      Line {err.line}
                    </Badge>
                  )}
                </div>

                {err.detail && (
                  <div className="text-sm text-muted-foreground bg-muted p-3 rounded-lg font-mono text-xs overflow-x-auto">
                    <pre className="whitespace-pre-wrap">{err.detail}</pre>
                  </div>
                )}

                {err.suggestion && (
                  <Alert>
                    <Lightbulb className="h-4 w-4" />
                    <AlertTitle>Suggestion</AlertTitle>
                    <AlertDescription>{err.suggestion}</AlertDescription>
                  </Alert>
                )}

                {err.docsUrl && (
                  <Button variant="link" size="sm" onClick={() => window.open(err.docsUrl, '_blank')}>
                    <ExternalLink className="h-4 w-4 mr-2" />
                    View Documentation
                  </Button>
                )}
              </div>
            </div>
          </CardContent>
        </Card>
      ))}

      {/* Raw Error Output */}
      <Card>
        <CardContent className="pt-6">
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-sm font-medium">Full Error Output</h4>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={copyError}>
                <Copy className="h-4 w-4 mr-2" />
                Copy
              </Button>
              {onRetry && (
                <Button size="sm" onClick={onRetry}>
                  Retry
                </Button>
              )}
            </div>
          </div>
          <ScrollArea className="h-[200px]">
            <pre className="text-xs font-mono bg-muted text-destructive p-4 rounded-lg whitespace-pre-wrap">
              {error}
            </pre>
          </ScrollArea>
        </CardContent>
      </Card>
    </div>
  );
}

function parseOpenTofuError(errorText: string): OpenTofuError[] {
  const errors: OpenTofuError[] = [];

  // Check for credential errors
  if (errorText.match(/no valid credential|InvalidClientTokenId|credential/i)) {
    errors.push({
      severity: 'error',
      summary: 'Cloud Credentials Not Configured',
      detail: 'No valid credentials found or credentials are invalid.',
      suggestion: 'Configure credentials in Project Settings → Cloud Credentials.',
      docsUrl: 'https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-files.html',
    });
  }

  // Check for provider configuration errors
  if (errorText.match(/provider.*not found|provider configuration/i)) {
    errors.push({
      severity: 'error',
      summary: 'Provider Configuration Error',
      detail: errorText.substring(0, 500),
      suggestion: 'Ensure the required provider is configured in your OpenTofu configuration.',
    });
  }

  // Check for resource not found errors
  if (errorText.match(/resource.*not found|does not exist/i)) {
    const resourceMatch = errorText.match(/resource\s+"([^"]+)"/);
    errors.push({
      severity: 'error',
      summary: 'Resource Not Found',
      detail: errorText.substring(0, 500),
      resource: resourceMatch ? resourceMatch[1] : undefined,
      suggestion: 'The resource may have been deleted outside of OpenTofu. Run "tofu refresh" or remove it from the state.',
    });
  }

  // Check for syntax errors
  if (errorText.match(/syntax error|unexpected token|parse error/i)) {
    const lineMatch = errorText.match(/line (\d+)/i);
    errors.push({
      severity: 'error',
      summary: 'OpenTofu Syntax Error',
      detail: errorText.substring(0, 500),
      line: lineMatch ? parseInt(lineMatch[1]) : undefined,
      suggestion: 'Check your OpenTofu configuration files for syntax errors.',
    });
  }

  // Check for variable errors
  if (errorText.match(/variable.*not defined|no value for required variable/i)) {
    errors.push({
      severity: 'error',
      summary: 'Missing Required Variable',
      detail: errorText.substring(0, 500),
      suggestion: 'Define all required variables in the module configuration.',
    });
  }

  // Check for dependency errors
  if (errorText.match(/dependency.*not met|cycle in resource dependencies/i)) {
    errors.push({
      severity: 'error',
      summary: 'Dependency Error',
      detail: errorText.substring(0, 500),
      suggestion: 'Check module dependencies and ensure there are no circular references.',
    });
  }

  // Check for state lock errors
  if (errorText.match(/state.*locked|lock.*held/i)) {
    errors.push({
      severity: 'warning',
      summary: 'State File Locked',
      detail: 'Another operation is currently holding the state lock.',
      suggestion: 'Wait for the other operation to complete, or force-unlock if the process crashed.',
    });
  }

  // If no specific errors parsed, show generic error
  if (errors.length === 0) {
    errors.push({
      severity: 'error',
      summary: 'OpenTofu Operation Failed',
      detail: errorText.substring(0, 1000),
      suggestion: 'Check the full error output below for details.',
    });
  }

  return errors;
}
