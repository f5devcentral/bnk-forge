/**
 * Upgrade Release Dialog
 *
 * Dialog for upgrading existing Helm releases with new values or version
 */

import { useState, useEffect } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Checkbox } from '@/components/ui/checkbox';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useUpgradeHelmRelease, useHelmValues } from '@/hooks/useHelm';
import { notify, notifyError } from '@/lib/notify';
import { Loader2, RefreshCcw } from 'lucide-react';
import yaml from 'js-yaml';
import type { JsonValue } from '@/types';
import { logger } from '@/lib/logger';

interface UpgradeReleaseDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  clusterId: number;
  releaseName: string;
  releaseNamespace: string;
  currentChart: string;
  currentVersion?: string;
}

export function UpgradeReleaseDialog({
  open,
  onOpenChange,
  clusterId,
  releaseName,
  releaseNamespace,
  currentChart,
  currentVersion,
}: UpgradeReleaseDialogProps) {
  const upgradeMutation = useUpgradeHelmRelease();

  // Fetch current values
  const { data: valuesData, isLoading: valuesLoading } = useHelmValues(
    clusterId,
    releaseName,
    releaseNamespace,
    false, // user-supplied values only
    { enabled: open }
  );

  // Form state
  const [chart, setChart] = useState(currentChart);
  const [version, setVersion] = useState(currentVersion || '');
  const [valuesFormat, setValuesFormat] = useState<'json' | 'yaml'>('yaml');
  const [valuesText, setValuesText] = useState('');
  const [wait, setWait] = useState(true);
  const [timeout, setTimeout] = useState('5m');
  const [reuseValues, setReuseValues] = useState(false);

  // Load current values when they're fetched
  useEffect(() => {
    if (valuesData?.values && Object.keys(valuesData.values).length > 0) {
      try {
        if (valuesFormat === 'yaml') {
          setValuesText(yaml.dump(valuesData.values));
        } else {
          setValuesText(JSON.stringify(valuesData.values, null, 2));
        }
      } catch (error) {
        logger.error('Failed to format values:', error);
      }
    }
  }, [valuesData, valuesFormat]);

  // Handle format toggle with conversion
  const handleFormatChange = (newFormat: 'json' | 'yaml') => {
    if (!valuesText.trim()) {
      setValuesFormat(newFormat);
      return;
    }

    try {
      // Parse current format
      let parsedValues;
      if (valuesFormat === 'json') {
        parsedValues = JSON.parse(valuesText);
      } else {
        parsedValues = yaml.load(valuesText);
      }

      // Convert to new format
      if (newFormat === 'json') {
        setValuesText(JSON.stringify(parsedValues, null, 2));
      } else {
        setValuesText(yaml.dump(parsedValues));
      }

      setValuesFormat(newFormat);
    } catch (error) {
      notifyError(error);
    }
  };

  // Reset form when dialog closes
  const handleOpenChange = (newOpen: boolean) => {
    if (!newOpen) {
      setChart(currentChart);
      setVersion(currentVersion || '');
      setValuesText('');
      setReuseValues(false);
      setWait(true);
      setTimeout('5m');
    }
    onOpenChange(newOpen);
  };

  const handleUpgrade = async () => {
    // Parse values if provided
    let values: Record<string, JsonValue> | undefined;
    if (valuesText.trim() && !reuseValues) {
      try {
        if (valuesFormat === 'json') {
          values = JSON.parse(valuesText);
        } else {
          values = yaml.load(valuesText) as Record<string, JsonValue>;
        }
      } catch (error) {
        notify.error(`Invalid ${valuesFormat.toUpperCase()} format: ${(error as Error).message}`);
        return;
      }
    }

    try {
      await upgradeMutation.mutateAsync({
        clusterId,
        releaseName,
        namespace: releaseNamespace,
        request: {
          chart,
          values,
          version: version || undefined,
          // Note: reuse_values not supported by backend schema
          wait,
          timeout,
        },
      });

      notify.success(`Upgrade of ${releaseName} queued — refresh the Releases tab in ~30s to confirm`, undefined, { category: 'deployment' });
      handleOpenChange(false);
    } catch (error) {
      notifyError(error);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <RefreshCcw className="h-5 w-5" />
            Upgrade Helm Release
          </DialogTitle>
          <DialogDescription>
            Upgrade <code className="text-xs font-mono bg-muted px-1 py-0.5 rounded">{releaseName}</code> to a new version or with updated values.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          {/* Chart Reference */}
          <div className="space-y-2">
            <Label htmlFor="chart">Chart Reference</Label>
            <Input
              id="chart"
              placeholder="bitnami/nginx"
              value={chart}
              onChange={(e) => setChart(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              Repository/chart name (defaults to current: {currentChart})
            </p>
          </div>

          {/* Version */}
          <div className="space-y-2">
            <Label htmlFor="version">Version (optional)</Label>
            <Input
              id="version"
              placeholder={currentVersion || 'Latest'}
              value={version}
              onChange={(e) => setVersion(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              Leave empty to use latest version
              {currentVersion && ` (currently: ${currentVersion})`}
            </p>
          </div>

          {/* Reuse Values Option */}
          <div className="flex items-center space-x-2 p-3 rounded-lg border border-border bg-muted/50">
            <Checkbox
              id="reuse-values"
              checked={reuseValues}
              onCheckedChange={(checked) => setReuseValues(checked as boolean)}
            />
            <label
              htmlFor="reuse-values"
              className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70 cursor-pointer"
            >
              Reuse existing values (ignore custom values below)
            </label>
          </div>

          {/* Custom Values */}
          {!reuseValues && (
            <div className="space-y-2">
              <Label>Custom Values (optional)</Label>
              {valuesLoading ? (
                <div className="flex items-center justify-center h-32 border border-border rounded-lg">
                  <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                </div>
              ) : (
                <Tabs value={valuesFormat} onValueChange={(v) => handleFormatChange(v as 'json' | 'yaml')}>
                  <TabsList className="grid w-full grid-cols-2">
                    <TabsTrigger value="yaml">YAML</TabsTrigger>
                    <TabsTrigger value="json">JSON</TabsTrigger>
                  </TabsList>
                  <TabsContent value="yaml" className="mt-2">
                    <Textarea
                      placeholder="replicaCount: 3&#10;image:&#10;  tag: latest"
                      value={valuesText}
                      onChange={(e) => setValuesText(e.target.value)}
                      rows={10}
                      className="font-mono text-xs"
                    />
                  </TabsContent>
                  <TabsContent value="json" className="mt-2">
                    <Textarea
                      placeholder='{"replicaCount": 3, "image": {"tag": "latest"}}'
                      value={valuesText}
                      onChange={(e) => setValuesText(e.target.value)}
                      rows={10}
                      className="font-mono text-xs"
                    />
                  </TabsContent>
                </Tabs>
              )}
              <p className="text-xs text-muted-foreground">
                Modify values or leave empty to keep current values
              </p>
            </div>
          )}

          {/* Options */}
          <div className="space-y-3">
            <Label>Options</Label>

            <div className="flex items-center space-x-2">
              <Checkbox
                id="wait"
                checked={wait}
                onCheckedChange={(checked) => setWait(checked as boolean)}
              />
              <label
                htmlFor="wait"
                className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70 cursor-pointer"
              >
                Wait for resources to be ready
              </label>
            </div>

            <div className="flex items-center gap-4">
              <Label htmlFor="timeout" className="whitespace-nowrap">Timeout:</Label>
              <Select value={timeout} onValueChange={setTimeout}>
                <SelectTrigger id="timeout" className="w-32">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="1m">1 minute</SelectItem>
                  <SelectItem value="5m">5 minutes</SelectItem>
                  <SelectItem value="10m">10 minutes</SelectItem>
                  <SelectItem value="15m">15 minutes</SelectItem>
                  <SelectItem value="30m">30 minutes</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        </div>

        <div className="flex justify-end gap-3">
          <Button
            variant="outline"
            onClick={() => handleOpenChange(false)}
            disabled={upgradeMutation.isPending}
          >
            Cancel
          </Button>
          <Button
            onClick={handleUpgrade}
            disabled={upgradeMutation.isPending || !chart.trim()}
          >
            {upgradeMutation.isPending ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Upgrading...
              </>
            ) : (
              'Upgrade'
            )}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
