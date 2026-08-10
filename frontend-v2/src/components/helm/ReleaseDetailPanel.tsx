/**
 * Release Detail Panel
 *
 * Comprehensive detail view for Helm releases with tabs for info, values, manifest, and history
 */

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { useHelmRelease, useHelmValues, useHelmHistory } from '@/hooks/useHelm';
import { CompareRevisionsDialog } from '@/components/helm/CompareRevisionsDialog';
import { cn } from '@/lib/utils';
import { formatTimeAgo, formatDateTime } from '@/lib/time-utils';
import { getResourceStatusColor } from '@/lib/status-colors';
import {
  Package,
  X,
  Loader2,
  RefreshCcw,
  Undo2,
  Trash2,
  Calendar,
  Tag,
  GitBranch,
  FileCode,
  GitCompare,
} from 'lucide-react';
import yaml from 'js-yaml';

interface ReleaseDetailPanelProps {
  clusterId: number;
  releaseName: string;
  releaseNamespace: string;
  onClose: () => void;
  onUpgrade?: () => void;
  onRollback?: () => void;
  onUninstall?: () => void;
}

export function ReleaseDetailPanel({
  clusterId,
  releaseName,
  releaseNamespace,
  onClose,
  onUpgrade,
  onRollback,
  onUninstall,
}: ReleaseDetailPanelProps) {
  const [activeTab, setActiveTab] = useState('info');
  const [compareDialogOpen, setCompareDialogOpen] = useState(false);
  const [selectedRev1, setSelectedRev1] = useState<string>('');
  const [selectedRev2, setSelectedRev2] = useState<string>('');

  // Fetch release details
  const { data: releaseData, isLoading: releaseLoading } = useHelmRelease(
    clusterId,
    releaseName,
    releaseNamespace
  );

  // Fetch values
  const { data: valuesData, isLoading: valuesLoading } = useHelmValues(
    clusterId,
    releaseName,
    releaseNamespace,
    false // user-supplied values only
  );

  // Fetch history
  const { data: historyData, isLoading: historyLoading } = useHelmHistory(
    clusterId,
    releaseName,
    releaseNamespace
  );

  const release = releaseData?.release;
  const values = valuesData?.values;
  const history = historyData?.history || [];

  // Use centralized time utilities
  const formatDate = (timestamp: string) => formatDateTime(timestamp) || 'Unknown';
  const calculateAge = formatTimeAgo;

  return (
    <div className="w-96 border-l border-border bg-card flex flex-col h-full">
      {/* Header */}
      <div className="px-4 py-3 border-b border-border">
        <div className="flex items-start justify-between mb-2">
          <div className="flex-1 min-w-0">
            <h3 className="font-semibold text-lg truncate mb-1">
              {releaseName}
            </h3>
            <p className="text-xs text-muted-foreground">
              <code className="font-mono">{releaseNamespace}</code> namespace
            </p>
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="h-8 w-8 p-0 flex-shrink-0"
            onClick={onClose}
          >
            <X className="h-4 w-4" />
            <span className="sr-only">Close</span>
          </Button>
        </div>

        {release && (
          <div className="flex items-center gap-2 mt-2">
            <Badge className={cn('text-[10px]', getResourceStatusColor(release.info.status))}>
              {release.info.status}
            </Badge>
            <span className="text-xs text-muted-foreground">
              Rev {release.version}
            </span>
          </div>
        )}
      </div>

      {/* Action Buttons */}
      <div className="px-4 py-2 border-b border-border flex gap-2">
        <Button
          size="sm"
          variant="outline"
          className="flex-1 h-8"
          onClick={onUpgrade}
          disabled={!release}
        >
          <RefreshCcw className="h-3.5 w-3.5 mr-1.5" />
          Upgrade
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="flex-1 h-8"
          onClick={onRollback}
          disabled={!release}
        >
          <Undo2 className="h-3.5 w-3.5 mr-1.5" />
          Rollback
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="h-8 text-destructive hover:text-destructive hover:bg-destructive/10"
          onClick={onUninstall}
          disabled={!release}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        {releaseLoading ? (
          <div className="flex items-center justify-center h-64">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : !release ? (
          <div className="p-4 text-center">
            <Package className="h-12 w-12 mx-auto mb-2 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              Failed to load release details
            </p>
          </div>
        ) : (
          <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
            <TabsList className="w-full grid grid-cols-4 sticky top-0 z-10 bg-card">
              <TabsTrigger value="info" className="text-xs">Info</TabsTrigger>
              <TabsTrigger value="values" className="text-xs">Values</TabsTrigger>
              <TabsTrigger value="manifest" className="text-xs">Manifest</TabsTrigger>
              <TabsTrigger value="history" className="text-xs">History</TabsTrigger>
            </TabsList>

            {/* Info Tab */}
            <TabsContent value="info" className="p-4 space-y-4 m-0">
              {/* Chart Info */}
              <div>
                <h4 className="text-xs font-semibold mb-2 text-muted-foreground uppercase tracking-wide">Chart</h4>
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    <Package className="h-4 w-4 text-muted-foreground" />
                    <code className="text-sm font-mono">{release?.chart?.metadata?.name || release?.name || 'Unknown'}</code>
                  </div>
                  <div className="flex items-center gap-2">
                    <Tag className="h-4 w-4 text-muted-foreground" />
                    <span className="text-sm">Version: {release?.chart?.metadata?.version || 'N/A'}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <GitBranch className="h-4 w-4 text-muted-foreground" />
                    <span className="text-sm">App Version: {release?.chart?.metadata?.app_version || 'N/A'}</span>
                  </div>
                </div>
              </div>

              {/* Description */}
              {release?.chart?.metadata?.description && (
                <div>
                  <h4 className="text-xs font-semibold mb-2 text-muted-foreground uppercase tracking-wide">Description</h4>
                  <p className="text-sm text-muted-foreground">{release?.chart?.metadata?.description}</p>
                </div>
              )}

              {/* Deployment Info */}
              <div>
                <h4 className="text-xs font-semibold mb-2 text-muted-foreground uppercase tracking-wide">Deployment</h4>
                <div className="space-y-2 text-sm">
                  <div className="flex items-start gap-2">
                    <Calendar className="h-4 w-4 text-muted-foreground flex-shrink-0 mt-0.5" />
                    <div className="flex-1 min-w-0">
                      <p className="text-xs text-muted-foreground">First Deployed</p>
                      <p className="text-sm truncate">{formatDate(release.info.first_deployed)}</p>
                    </div>
                  </div>
                  <div className="flex items-start gap-2">
                    <Calendar className="h-4 w-4 text-muted-foreground flex-shrink-0 mt-0.5" />
                    <div className="flex-1 min-w-0">
                      <p className="text-xs text-muted-foreground">Last Deployed</p>
                      <p className="text-sm truncate">{formatDate(release.info.last_deployed)}</p>
                      <p className="text-xs text-muted-foreground">{calculateAge(release.info.last_deployed)}</p>
                    </div>
                  </div>
                </div>
              </div>

              {/* Notes */}
              {release.info.notes && (
                <div>
                  <h4 className="text-xs font-semibold mb-2 text-muted-foreground uppercase tracking-wide">Release Notes</h4>
                  <div className="p-3 rounded-lg text-xs font-mono whitespace-pre-wrap overflow-x-auto bg-muted/50">
                    {release.info.notes}
                  </div>
                </div>
              )}
            </TabsContent>

            {/* Values Tab */}
            <TabsContent value="values" className="p-4 m-0">
              {valuesLoading ? (
                <div className="flex items-center justify-center h-32">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              ) : values && Object.keys(values).length > 0 ? (
                <div className="p-3 rounded-lg text-xs font-mono whitespace-pre overflow-x-auto bg-muted/50">
                  {yaml.dump(values)}
                </div>
              ) : (
                <div className="text-center py-8">
                  <FileCode className="h-12 w-12 mx-auto mb-2 text-muted-foreground" />
                  <p className="text-sm text-muted-foreground">
                    No custom values set
                  </p>
                </div>
              )}
            </TabsContent>

            {/* Manifest Tab */}
            <TabsContent value="manifest" className="p-4 m-0">
              {release.manifest ? (
                <div className="p-3 rounded-lg text-xs font-mono whitespace-pre overflow-x-auto bg-muted/50">
                  {release.manifest}
                </div>
              ) : (
                <div className="text-center py-8">
                  <FileCode className="h-12 w-12 mx-auto mb-2 text-muted-foreground" />
                  <p className="text-sm text-muted-foreground">
                    No manifest available
                  </p>
                </div>
              )}
            </TabsContent>

            {/* History Tab */}
            <TabsContent value="history" className="m-0">
              {historyLoading ? (
                <div className="flex items-center justify-center h-32">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              ) : history.length > 0 ? (
                <>
                  {/* Compare Controls */}
                  {history.length >= 2 && (
                    <div className="p-3 border-b border-border bg-muted/50 flex items-center gap-3">
                      <span className="text-xs font-medium">Compare:</span>
                      <Select value={selectedRev1} onValueChange={setSelectedRev1}>
                        <SelectTrigger className="w-28 h-8 text-xs">
                          <SelectValue placeholder="Rev 1" />
                        </SelectTrigger>
                        <SelectContent>
                          {history.map((rev) => (
                            <SelectItem key={rev.revision} value={rev.revision.toString()}>
                              Rev {rev.revision}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <span className="text-xs">vs</span>
                      <Select value={selectedRev2} onValueChange={setSelectedRev2}>
                        <SelectTrigger className="w-28 h-8 text-xs">
                          <SelectValue placeholder="Rev 2" />
                        </SelectTrigger>
                        <SelectContent>
                          {history.map((rev) => (
                            <SelectItem key={rev.revision} value={rev.revision.toString()}>
                              Rev {rev.revision}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <Button
                        size="sm"
                        className="h-8 ml-auto"
                        onClick={() => setCompareDialogOpen(true)}
                        disabled={!selectedRev1 || !selectedRev2 || selectedRev1 === selectedRev2}
                      >
                        <GitCompare className="h-3.5 w-3.5 mr-1.5" />
                        Compare
                      </Button>
                    </div>
                  )}

                  {/* Revision List */}
                  <div className="divide-y divide-border">
                    {history.map((rev) => (
                      <div key={rev.revision} className="p-4 hover:bg-muted/50 transition-colors">
                        <div className="flex items-center justify-between mb-2">
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-semibold">Rev {rev.revision}</span>
                            <Badge className={cn('text-[10px]', getResourceStatusColor(rev.status))}>
                              {rev.status}
                            </Badge>
                          </div>
                          <span className="text-xs text-muted-foreground">{calculateAge(rev.updated)}</span>
                        </div>
                        <div className="text-xs space-y-1">
                          <p className="text-muted-foreground">
                            <code className="font-mono">{rev.chart}</code>
                          </p>
                          <p className="text-muted-foreground">{rev.description || 'No description'}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                </>
              ) : (
                <div className="text-center py-8">
                  <GitBranch className="h-12 w-12 mx-auto mb-2 text-muted-foreground" />
                  <p className="text-sm text-muted-foreground">
                    No revision history available
                  </p>
                </div>
              )}
            </TabsContent>
          </Tabs>
        )}
      </div>

      {/* Compare Revisions Dialog */}
      {selectedRev1 && selectedRev2 && (
        <CompareRevisionsDialog
          open={compareDialogOpen}
          onOpenChange={setCompareDialogOpen}
          clusterId={clusterId}
          releaseName={releaseName}
          namespace={releaseNamespace}
          revision1={parseInt(selectedRev1)}
          revision2={parseInt(selectedRev2)}
        />
      )}
    </div>
  );
}
