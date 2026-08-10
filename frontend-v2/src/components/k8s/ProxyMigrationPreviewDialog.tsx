/**
 * Proxy Migration Preview Dialog (D-021 P2)
 *
 * Shows the BNK Gateway API manifests that would result from migrating a
 * detected existing proxy.  Read-only preview only — no apply/cutover.
 *
 * Displays:
 *   - Combined YAML with tabs (GatewayClass / Gateway / HTTPRoute / Combined)
 *   - Copy-to-clipboard button
 *   - Unmapped/lossy constructs list (D-017: never silently dropped)
 */

import { useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  AlertTriangle,
  CheckCircle2,
  ClipboardCopy,
  Loader2,
  Network,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import type { ProxyTranslateResponse, ProxyTranslateUnmapped } from '@/hooks/k8s/useResources';

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

interface ProxyMigrationPreviewDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** null while the mutation is in-flight */
  result: ProxyTranslateResponse | null;
  isLoading: boolean;
  error: string | null;
  proxyDisplayName: string;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function YamlBlock({ yaml }: { yaml: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(yaml);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="relative">
      <Button
        variant="ghost"
        size="sm"
        className="absolute top-2 right-2 z-10 h-7 text-xs gap-1"
        onClick={handleCopy}
      >
        {copied ? (
          <>
            <CheckCircle2 className="h-3 w-3 text-success" />
            Copied
          </>
        ) : (
          <>
            <ClipboardCopy className="h-3 w-3" />
            Copy
          </>
        )}
      </Button>
      <ScrollArea className="h-64 rounded-md border">
        <pre className="p-4 text-xs font-mono leading-relaxed text-foreground bg-muted/30">
          {yaml}
        </pre>
      </ScrollArea>
    </div>
  );
}

function UnmappedList({ unmapped }: { unmapped: ProxyTranslateUnmapped[] }) {
  if (unmapped.length === 0) {
    return (
      <div className="flex items-center gap-2 p-3 rounded-lg bg-success/10 border border-success/20">
        <CheckCircle2 className="h-4 w-4 text-success flex-shrink-0" />
        <span className="text-sm text-success">All constructs mapped successfully.</span>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <p className="text-xs font-medium text-muted-foreground">
        {unmapped.length} unmapped construct{unmapped.length !== 1 ? 's' : ''} — review before applying:
      </p>
      <ScrollArea className="h-48">
        <div className="space-y-2 pr-2">
          {unmapped.map((u, idx) => (
            <div
              key={idx}
              className="rounded-lg border p-3 text-xs border-warning/30 bg-warning/5"
            >
              <div className="flex items-start gap-2">
                <AlertTriangle className="h-3.5 w-3.5 text-warning flex-shrink-0 mt-0.5" />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5 flex-wrap mb-1">
                    <Badge variant="outline" className="text-[10px] font-mono h-4">
                      {u.construct_type}
                    </Badge>
                    <span className="font-mono truncate text-muted-foreground">
                      {u.source_kind}/{u.source_namespace}/{u.source_name}
                    </span>
                  </div>
                  <p className="font-mono text-[11px] mb-1 text-warning">
                    {u.detail}
                  </p>
                  <p className="text-[11px] text-muted-foreground">
                    {u.reason}
                  </p>
                </div>
              </div>
            </div>
          ))}
        </div>
      </ScrollArea>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main dialog
// ---------------------------------------------------------------------------

export function ProxyMigrationPreviewDialog({
  open,
  onOpenChange,
  result,
  isLoading,
  error,
  proxyDisplayName,
}: ProxyMigrationPreviewDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className={cn('max-w-3xl', 'bg-card border-border')}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Network className="h-5 w-5 text-primary" />
            BNK Migration Preview: {proxyDisplayName}
          </DialogTitle>
          <DialogDescription>
            Preview-only — no cluster changes are applied.
            Copy the YAML and apply it manually when ready.
          </DialogDescription>
        </DialogHeader>

        {isLoading && (
          <div className="flex items-center justify-center py-12 gap-3">
            <Loader2 className="h-6 w-6 animate-spin text-primary" />
            <span className="text-sm text-muted-foreground">
              Fetching live cluster objects and generating manifests…
            </span>
          </div>
        )}

        {error && !isLoading && (
          <div className="rounded-lg border p-4 text-sm bg-destructive/10 border-destructive/20 text-destructive">
            <AlertTriangle className="h-4 w-4 inline mr-2" />
            {error}
          </div>
        )}

        {result && !isLoading && (
          <div className="space-y-4">
            {/* Unmapped warnings — always shown first so user doesn't miss them */}
            <UnmappedList unmapped={result.unmapped ?? []} />

            {/* YAML manifests */}
            <Tabs defaultValue="combined">
              <TabsList className="w-full">
                <TabsTrigger value="combined" className="flex-1">Combined</TabsTrigger>
                <TabsTrigger value="gatewayclass" className="flex-1">GatewayClass</TabsTrigger>
                <TabsTrigger value="gateway" className="flex-1">Gateway</TabsTrigger>
                <TabsTrigger value="httproute" className="flex-1">HTTPRoute</TabsTrigger>
              </TabsList>

              <TabsContent value="combined" className="mt-3">
                <YamlBlock yaml={result.combined_yaml ?? ''} />
              </TabsContent>

              <TabsContent value="gatewayclass" className="mt-3">
                <YamlBlock yaml={result.gatewayclass_yaml ?? ''} />
              </TabsContent>

              <TabsContent value="gateway" className="mt-3">
                <YamlBlock yaml={result.gateway_yaml ?? ''} />
              </TabsContent>

              <TabsContent value="httproute" className="mt-3">
                <YamlBlock yaml={result.httproute_yaml ?? ''} />
              </TabsContent>
            </Tabs>

            {/* Source metadata footer */}
            {result.source && Object.keys(result.source).length > 0 && (
              <p className="text-[11px] font-mono text-muted-foreground">
                Source: {result.proxy_type} · {result.source_kind}/{result.class_name}
                {(result.source.ingress_count !== undefined) && ` · ${result.source.ingress_count as number} Ingress(es)`}
                {(result.source.httproute_count !== undefined) && ` · ${result.source.httproute_count as number} HTTPRoute(s)`}
              </p>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
