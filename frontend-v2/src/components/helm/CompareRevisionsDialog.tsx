/**
 * Compare Revisions Dialog
 *
 * Dialog for comparing two Helm release revisions side-by-side
 */

import { useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useCompareRevisions } from '@/hooks/useHelm';
import { Loader2, GitCompare } from 'lucide-react';
import { cn } from '@/lib/utils';
import yaml from 'js-yaml';
import { diffLines, Change } from 'diff';

interface CompareRevisionsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  clusterId: number;
  releaseName: string;
  namespace: string;
  revision1: number;
  revision2: number;
}

export function CompareRevisionsDialog({
  open,
  onOpenChange,
  clusterId,
  releaseName,
  namespace,
  revision1,
  revision2,
}: CompareRevisionsDialogProps) {
  const [activeTab, setActiveTab] = useState<'values' | 'manifest'>('values');

  const { data, isLoading, error } = useCompareRevisions(
    clusterId,
    releaseName,
    revision1,
    revision2,
    namespace,
    { enabled: open }
  );

  const renderDiff = (oldText: string, newText: string) => {
    const diff = diffLines(oldText, newText);

    return (
      <div className="grid grid-cols-2 gap-4">
        {/* Left side - Revision 1 (Old) */}
        <div className="space-y-1">
          <div className="px-3 py-2 text-xs font-semibold border-b border-border bg-muted sticky top-0 z-10">
            Revision {revision1} (Old)
          </div>
          <div className="font-mono text-xs overflow-x-auto bg-card">
            {diff.map((part: Change, index: number) => {
              if (part.added) {
                // Skip added lines on the left side
                return null;
              }

              const lines = part.value.split('\n');
              return lines.map((line, lineIndex) => {
                if (lineIndex === lines.length - 1 && line === '') return null;

                return (
                  <div
                    key={`left-${index}-${lineIndex}`}
                    className={cn(
                      'px-3 py-0.5 min-h-[20px]',
                      part.removed && 'bg-destructive/10 text-destructive'
                    )}
                  >
                    <span className="inline-block w-10 text-right mr-3 text-muted-foreground">
                      {part.removed ? '-' : ' '}
                    </span>
                    {line || ' '}
                  </div>
                );
              });
            })}
          </div>
        </div>

        {/* Right side - Revision 2 (New) */}
        <div className="space-y-1">
          <div className="px-3 py-2 text-xs font-semibold border-b border-border bg-muted sticky top-0 z-10">
            Revision {revision2} (New)
          </div>
          <div className="font-mono text-xs overflow-x-auto bg-card">
            {diff.map((part: Change, index: number) => {
              if (part.removed) {
                // Skip removed lines on the right side
                return null;
              }

              const lines = part.value.split('\n');
              return lines.map((line, lineIndex) => {
                if (lineIndex === lines.length - 1 && line === '') return null;

                return (
                  <div
                    key={`right-${index}-${lineIndex}`}
                    className={cn(
                      'px-3 py-0.5 min-h-[20px]',
                      part.added && 'bg-success/10 text-success'
                    )}
                  >
                    <span className="inline-block w-10 text-right mr-3 text-muted-foreground">
                      {part.added ? '+' : ' '}
                    </span>
                    {line || ' '}
                  </div>
                );
              });
            })}
          </div>
        </div>
      </div>
    );
  };

  const valuesDiff = data
    ? renderDiff(
        yaml.dump(data.revision1.values || {}, { indent: 2 }),
        yaml.dump(data.revision2.values || {}, { indent: 2 })
      )
    : null;

  const manifestDiff = data
    ? renderDiff(
        data.revision1.manifest || '',
        data.revision2.manifest || ''
      )
    : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-7xl max-h-[90vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <GitCompare className="h-5 w-5" />
            Compare Revisions: {releaseName}
          </DialogTitle>
          <DialogDescription>
            Comparing revision {revision1} (old) with revision {revision2} (new) in namespace{' '}
            <code className="text-xs font-mono bg-muted px-1 py-0.5 rounded">{namespace}</code>
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 overflow-hidden">
          {isLoading ? (
            <div className="flex items-center justify-center h-64">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : error ? (
            <div className="p-8 rounded-lg border border-destructive/20 bg-destructive/10 text-center">
              <p className="text-sm text-destructive">
                Failed to load comparison data: {(error as Error).message}
              </p>
            </div>
          ) : data ? (
            <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as 'values' | 'manifest')} className="h-full flex flex-col">
              <TabsList className="grid w-full grid-cols-2">
                <TabsTrigger value="values">Values Diff</TabsTrigger>
                <TabsTrigger value="manifest">Manifest Diff</TabsTrigger>
              </TabsList>

              <div className="flex-1 overflow-auto mt-2">
                <TabsContent value="values" className="m-0 h-full">
                  <div className="rounded-lg border border-border overflow-hidden">
                    {Object.keys(data.revision1.values || {}).length === 0 &&
                     Object.keys(data.revision2.values || {}).length === 0 ? (
                      <div className="p-8 text-center bg-muted/50">
                        <p className="text-sm text-muted-foreground">
                          No custom values in either revision
                        </p>
                      </div>
                    ) : (
                      <div className="max-h-[60vh] overflow-auto">
                        {valuesDiff}
                      </div>
                    )}
                  </div>
                </TabsContent>

                <TabsContent value="manifest" className="m-0 h-full">
                  <div className="rounded-lg border border-border overflow-hidden">
                    <div className="max-h-[60vh] overflow-auto">
                      {manifestDiff}
                    </div>
                  </div>
                </TabsContent>
              </div>
            </Tabs>
          ) : null}
        </div>

        {/* Legend */}
        {data && (
          <div className="flex items-center gap-4 px-4 py-2 rounded-lg text-xs border-t border-border bg-muted/50">
            <span className="font-semibold">Legend:</span>
            <div className="flex items-center gap-1.5">
              <div className="w-4 h-4 rounded bg-destructive/10 border border-destructive/20" />
              <span>Removed</span>
            </div>
            <div className="flex items-center gap-1.5">
              <div className="w-4 h-4 rounded bg-success/10 border border-success/20" />
              <span>Added</span>
            </div>
            <div className="flex items-center gap-1.5">
              <div className="w-4 h-4 rounded bg-card border border-border" />
              <span>Unchanged</span>
            </div>
          </div>
        )}

        <div className="flex justify-end pt-4 border-t border-border">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
