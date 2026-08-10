/**
 * Install Chart Dialog
 *
 * Dialog for installing Helm charts with customizable values
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
import { useInstallHelmChart } from '@/hooks/useHelm';
import { notify, notifyError } from '@/lib/notify';
import { Package, Loader2 } from 'lucide-react';
import yaml from 'js-yaml';
import type { HelmChart, JsonValue } from '@/types';

interface InstallChartDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  clusterId: number;
  defaultNamespace?: string;
  defaultChart?: string;
  selectedChart?: Pick<HelmChart, 'name' | 'version'>; // Pre-fill from chart search
}

export function InstallChartDialog({
  open,
  onOpenChange,
  clusterId,
  defaultNamespace = 'default',
  defaultChart = '',
  selectedChart,
}: InstallChartDialogProps) {
  const installMutation = useInstallHelmChart();

  // Form state
  const [releaseName, setReleaseName] = useState('');
  const [chart, setChart] = useState(selectedChart?.name || defaultChart);
  const [namespace, setNamespace] = useState(defaultNamespace);
  const [version, setVersion] = useState('');
  const [valuesFormat, setValuesFormat] = useState<'json' | 'yaml'>('yaml');
  const [valuesText, setValuesText] = useState('');
  const [createNamespace, setCreateNamespace] = useState(true);
  const [wait, setWait] = useState(true);
  const [timeout, setTimeout] = useState('5m');

  // Update chart when selectedChart changes
  useEffect(() => {
    if (selectedChart) {
      setChart(selectedChart.name);
      if (selectedChart.version) {
        setVersion(selectedChart.version);
      }
    }
  }, [selectedChart]);

  // Reset form when dialog opens
  const handleOpenChange = (newOpen: boolean) => {
    if (!newOpen) {
      // Reset form
      setReleaseName('');
      setChart(defaultChart);
      setNamespace(defaultNamespace);
      setVersion('');
      setValuesText('');
      setCreateNamespace(true);
      setWait(true);
      setTimeout('5m');
    }
    onOpenChange(newOpen);
  };

  const handleInstall = async () => {
    // Validation
    if (!releaseName.trim()) {
      notify.error('Release name is required');
      return;
    }

    if (!chart.trim()) {
      notify.error('Chart reference is required');
      return;
    }

    // Parse values if provided
    let values: Record<string, JsonValue> | undefined;
    if (valuesText.trim()) {
      try {
        if (valuesFormat === 'json') {
          values = JSON.parse(valuesText);
        } else {
          // Parse YAML to JSON
          values = yaml.load(valuesText) as Record<string, JsonValue>;
        }
      } catch (error) {
        notify.error(`Invalid ${valuesFormat.toUpperCase()} format: ${(error as Error).message}`);
        return;
      }
    }

    try {
      await installMutation.mutateAsync({
        clusterId,
        request: {
          release_name: releaseName,
          chart,
          namespace,
          values,
          version: version || undefined,
          create_namespace: createNamespace,
          wait,
          timeout,
        },
      });

      notify.success(`Install of ${chart} queued — refresh the Releases tab in ~30s to confirm`, undefined, { category: 'deployment' });
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
            <Package className="h-5 w-5" />
            Install Helm Chart
          </DialogTitle>
          <DialogDescription>
            Install a Helm chart from a repository. Specify the chart reference (e.g., bitnami/nginx) and configure deployment options.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          {/* Release Name */}
          <div className="space-y-2">
            <Label htmlFor="release-name">Release Name *</Label>
            <Input
              id="release-name"
              placeholder="my-release"
              value={releaseName}
              onChange={(e) => setReleaseName(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              Unique name for this Helm release
            </p>
          </div>

          {/* Chart Reference */}
          <div className="space-y-2">
            <Label htmlFor="chart">Chart Reference *</Label>
            <Input
              id="chart"
              placeholder="bitnami/nginx or https://charts.example.com/nginx-1.0.0.tgz"
              value={chart}
              onChange={(e) => setChart(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">
              Repository/chart name (e.g., bitnami/nginx) or chart URL
            </p>
          </div>

          {/* Namespace and Version */}
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="namespace">Namespace</Label>
              <Input
                id="namespace"
                placeholder="default"
                value={namespace}
                onChange={(e) => setNamespace(e.target.value)}
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="version">Version (optional)</Label>
              <Input
                id="version"
                placeholder="1.0.0"
                value={version}
                onChange={(e) => setVersion(e.target.value)}
              />
            </div>
          </div>

          {/* Custom Values */}
          <div className="space-y-2">
            <Label>Custom Values (optional)</Label>
            <Tabs value={valuesFormat} onValueChange={(v) => setValuesFormat(v as 'json' | 'yaml')}>
              <TabsList className="grid w-full grid-cols-2">
                <TabsTrigger value="yaml">YAML</TabsTrigger>
                <TabsTrigger value="json">JSON</TabsTrigger>
              </TabsList>
              <TabsContent value="yaml" className="mt-2">
                <Textarea
                  placeholder="replicaCount: 3&#10;image:&#10;  tag: latest"
                  value={valuesText}
                  onChange={(e) => setValuesText(e.target.value)}
                  rows={8}
                  className="font-mono text-xs"
                />
              </TabsContent>
              <TabsContent value="json" className="mt-2">
                <Textarea
                  placeholder='{"replicaCount": 3, "image": {"tag": "latest"}}'
                  value={valuesText}
                  onChange={(e) => setValuesText(e.target.value)}
                  rows={8}
                  className="font-mono text-xs"
                />
              </TabsContent>
            </Tabs>
          </div>

          {/* Options */}
          <div className="space-y-3">
            <Label>Options</Label>

            <div className="flex items-center space-x-2">
              <Checkbox
                id="create-namespace"
                checked={createNamespace}
                onCheckedChange={(checked) => setCreateNamespace(checked as boolean)}
              />
              <label
                htmlFor="create-namespace"
                className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70 cursor-pointer"
              >
                Create namespace if it doesn't exist
              </label>
            </div>

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
            disabled={installMutation.isPending}
          >
            Cancel
          </Button>
          <Button
            onClick={handleInstall}
            disabled={installMutation.isPending || !releaseName.trim() || !chart.trim()}
          >
            {installMutation.isPending ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Installing...
              </>
            ) : (
              'Install'
            )}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
