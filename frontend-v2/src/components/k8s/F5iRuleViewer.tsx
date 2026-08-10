/**
 * F5 iRule Code Viewer
 *
 * Full-screen dialog with Monaco editor showing TCL iRule code.
 * Extracted from spec.iRule field of F5BigCneIrule CRDs.
 * Read-only with syntax highlighting, line numbers, and copy support.
 */

import { useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Editor } from '@monaco-editor/react';
import { Card } from '@/components/ui/card';
import { Copy, Check, Code, CheckCircle2, XCircle, AlertCircle, Clock } from 'lucide-react';
import { formatAge } from '@/lib/time-utils';
// Monaco editor theme is a data-viz/syntax-highlighter allowlist exemption
// (D-020 ADR) so we still need isDark for that one prop. Chrome surfaces use
// tokens only.
import { useTheme } from '@/context/ThemeContext';
import { notify } from '@/lib/notify';
import type { K8sResource, K8sCondition } from '@/types';

interface F5iRuleViewerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  resource: K8sResource;
}

export function F5iRuleViewer({ open, onOpenChange, resource }: F5iRuleViewerProps) {
  const { isDark } = useTheme();
  const [copied, setCopied] = useState(false);

  if (!resource) return null;

  const name = resource.metadata?.name || 'Unknown';
  const namespace = resource.metadata?.namespace || 'N/A';
  const age = formatAge(resource.metadata?.creationTimestamp);

  // Extract iRule TCL code from spec
  const iRuleCode = resource.spec?.iRule || resource.spec?.irule || '';
  const conditions = resource.status?.conditions || [];

  // Get overall status
  const accepted = conditions.find((c: K8sCondition) => c.type === 'Accepted');
  const programmed = conditions.find((c: K8sCondition) => c.type === 'Programmed');

  const getConditionBadge = (condition: K8sCondition | undefined, label: string) => {
    if (!condition) return <Badge variant="outline" className="text-xs">{label}: N/A</Badge>;
    if (condition.status === 'True') {
      return (
        <Badge variant="success" className="text-xs">
          <CheckCircle2 className="h-3 w-3 mr-1" />
          {label}
        </Badge>
      );
    }
    if (condition.status === 'False') {
      return (
        <Badge variant="destructive" className="text-xs">
          <XCircle className="h-3 w-3 mr-1" />
          {label} Failed
        </Badge>
      );
    }
    return (
      <Badge variant="warning" className="text-xs">
        <AlertCircle className="h-3 w-3 mr-1" />
        {label} Unknown
      </Badge>
    );
  };

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(iRuleCode);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      notify.error('Failed to copy to clipboard', undefined, { category: 'cluster' });
    }
  };

  // Count lines for stats
  const lineCount = iRuleCode ? iRuleCode.split('\n').length : 0;

  // Extract event handlers for display
  const eventHandlers = iRuleCode
    ? Array.from(iRuleCode.matchAll(/when\s+(\w+)/g) as IterableIterator<RegExpMatchArray>).map((m) => m[1])
    : [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-5xl max-h-[90vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-3">
            <Code className="h-5 w-5 text-primary" />
            <span>{name}</span>
          </DialogTitle>
        </DialogHeader>

        {/* Metadata bar */}
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline" className="text-xs">
            {namespace}
          </Badge>
          <Badge variant="outline" className="text-xs">
            <Clock className="h-3 w-3 mr-1" />
            {age}
          </Badge>
          {getConditionBadge(accepted, 'Accepted')}
          {getConditionBadge(programmed, 'Programmed')}
          <Badge variant="outline" className="text-xs">
            {lineCount} lines
          </Badge>
          {eventHandlers.length > 0 && (
            <Badge variant="outline" className="text-xs">
              Events: {eventHandlers.join(', ')}
            </Badge>
          )}
        </div>

        {/* Condition messages (if any failures) */}
        {conditions.filter((c: K8sCondition) => c.status === 'False' && c.message).map((c: K8sCondition, idx: number) => (
          <div
            key={idx}
            className="text-xs p-2 rounded border bg-destructive/10 border-destructive/20 text-destructive"
          >
            <span className="font-medium">{c.type}:</span> {c.message}
          </div>
        ))}

        {/* Code editor */}
        <div className="flex-1 min-h-0">
          {iRuleCode ? (
            <Card className="overflow-hidden h-full">
              <Editor
                height="500px"
                defaultLanguage="tcl"
                value={iRuleCode}
                options={{
                  readOnly: true,
                  minimap: { enabled: lineCount > 50 },
                  scrollBeyondLastLine: false,
                  fontSize: 13,
                  lineNumbers: 'on',
                  folding: true,
                  wordWrap: 'on',
                  automaticLayout: true,
                  tabSize: 4,
                  renderWhitespace: 'selection',
                  bracketPairColorization: {
                    enabled: true,
                  },
                }}
                theme={isDark ? 'vs-dark' : 'vs'}
              />
            </Card>
          ) : (
            <div className="h-64 flex items-center justify-center rounded-lg border bg-muted/50 border-border">
              <div className="text-center">
                <Code className="h-8 w-8 mx-auto mb-2 opacity-50" />
                <p className="text-sm text-muted-foreground">No iRule code found in this resource</p>
                <p className="text-xs text-muted-foreground mt-1">Expected spec.iRule field</p>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex justify-end gap-2 pt-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handleCopy}
            disabled={!iRuleCode}
          >
            {copied ? (
              <><Check className="h-4 w-4 mr-2" />Copied</>
            ) : (
              <><Copy className="h-4 w-4 mr-2" />Copy Code</>
            )}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => onOpenChange(false)}
          >
            Close
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
