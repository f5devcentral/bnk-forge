/**
 * Upload Chart Dialog
 *
 * Dialog for uploading custom Helm chart .tgz files
 */

import { useState, useRef } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { useUploadHelmChart } from '@/hooks/useHelm';
import { notify, notifyError } from '@/lib/notify';
import { Upload, Loader2, FileArchive, X, CheckCircle2 } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { UploadedHelmChart } from '@/types';

interface UploadChartDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function UploadChartDialog({ open, onOpenChange }: UploadChartDialogProps) {
  const uploadMutation = useUploadHelmChart();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [dragActive, setDragActive] = useState(false);
  const [uploadedChart, setUploadedChart] = useState<UploadedHelmChart | null>(null);

  const handleReset = () => {
    setSelectedFile(null);
    setUploadedChart(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const handleClose = () => {
    handleReset();
    onOpenChange(false);
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      validateAndSetFile(file);
    }
  };

  const validateAndSetFile = (file: File) => {
    if (!file.name.endsWith('.tgz')) {
      notify.error('Invalid file type. Please upload a .tgz Helm chart package.');
      return;
    }

    // Check file size (max 50MB)
    const maxSize = 50 * 1024 * 1024; // 50MB
    if (file.size > maxSize) {
      notify.error('File too large. Maximum size is 50MB.');
      return;
    }

    setSelectedFile(file);
    setUploadedChart(null);
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);

    const file = e.dataTransfer.files?.[0];
    if (file) {
      validateAndSetFile(file);
    }
  };

  const handleUpload = async () => {
    if (!selectedFile) return;

    try {
      const result = await uploadMutation.mutateAsync(selectedFile);
      notify.success(`Successfully uploaded ${result.chart.name}:${result.chart.version}`, undefined, { category: 'system' });
      setUploadedChart(result.chart);
      setSelectedFile(null);
    } catch (error) {
      notifyError(error);
    }
  };

  const formatFileSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Upload className="h-5 w-5" />
            Upload Helm Chart
          </DialogTitle>
          <DialogDescription>
            Upload a custom Helm chart package (.tgz file) to install from your own charts.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          {!uploadedChart ? (
            <>
              {/* Drag and Drop Area */}
              <div
                className={cn(
                  'border-2 border-dashed rounded-lg p-8 text-center transition-colors cursor-pointer',
                  dragActive
                    ? 'border-primary bg-primary/5'
                    : 'border-border hover:border-muted-foreground bg-muted/30'
                )}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".tgz"
                  onChange={handleFileChange}
                  className="hidden"
                />

                {selectedFile ? (
                  <div className="space-y-3">
                    <FileArchive className="h-12 w-12 mx-auto text-muted-foreground" />
                    <div>
                      <p className="text-sm font-medium mb-1">{selectedFile.name}</p>
                      <p className="text-xs text-muted-foreground">
                        {formatFileSize(selectedFile.size)}
                      </p>
                    </div>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleReset();
                      }}
                      className="text-destructive hover:text-destructive hover:bg-destructive/10"
                    >
                      <X className="h-4 w-4 mr-1.5" />
                      Remove
                    </Button>
                  </div>
                ) : (
                  <div className="space-y-3">
                    <Upload className="h-12 w-12 mx-auto text-muted-foreground" />
                    <div>
                      <p className="text-sm font-medium mb-1">
                        Drag and drop your chart here
                      </p>
                      <p className="text-xs text-muted-foreground">
                        or click to browse
                      </p>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      Only .tgz files are supported (max 50MB)
                    </p>
                  </div>
                )}
              </div>

              {/* Info Box */}
              <div className="p-3 rounded-lg border border-info/30 bg-info/10 text-xs text-info">
                <p className="font-medium mb-1">Chart Requirements</p>
                <ul className="list-disc list-inside space-y-0.5 ml-2">
                  <li>Must be a valid Helm chart package (.tgz)</li>
                  <li>Must contain a valid Chart.yaml file</li>
                  <li>Chart name and version will be extracted automatically</li>
                  <li>Duplicate charts (same name:version) will be rejected</li>
                </ul>
              </div>
            </>
          ) : (
            /* Success State */
            <div className="p-6 rounded-lg border border-success/30 bg-success/10 text-center space-y-4">
              <CheckCircle2 className="h-16 w-16 mx-auto text-success" />
              <div>
                <p className="text-lg font-semibold mb-1">Upload Successful!</p>
                <p className="text-sm text-muted-foreground">
                  Chart uploaded and ready to install
                </p>
              </div>

              <div className="p-4 rounded-lg text-left bg-card border border-border">
                <div className="space-y-2 text-sm">
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Name:</span>
                    <code className="font-mono font-semibold">{uploadedChart.name}</code>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Version:</span>
                    <code className="font-mono">{uploadedChart.version}</code>
                  </div>
                  {uploadedChart.app_version && (
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">App Version:</span>
                      <code className="font-mono">{uploadedChart.app_version}</code>
                    </div>
                  )}
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Size:</span>
                    <span>{formatFileSize(uploadedChart.file_size)}</span>
                  </div>
                </div>
              </div>

              <p className="text-xs text-muted-foreground">
                You can now find this chart in the "Uploaded Charts" section of the sidebar.
              </p>
            </div>
          )}
        </div>

        <div className="flex justify-end gap-3">
          <Button
            variant="outline"
            onClick={handleClose}
            disabled={uploadMutation.isPending}
          >
            {uploadedChart ? 'Done' : 'Cancel'}
          </Button>
          {!uploadedChart && (
            <Button
              onClick={handleUpload}
              disabled={!selectedFile || uploadMutation.isPending}
            >
              {uploadMutation.isPending ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Uploading...
                </>
              ) : (
                <>
                  <Upload className="h-4 w-4 mr-2" />
                  Upload Chart
                </>
              )}
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
