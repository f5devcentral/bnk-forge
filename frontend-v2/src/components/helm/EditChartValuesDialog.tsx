import { useState, useEffect } from 'react';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { Loader2, Save, X } from 'lucide-react';
import { useChartValues, useUpdateChartValues } from '@/hooks/useHelm';
import { notifyError } from '@/lib/notify';

interface EditChartValuesDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  chartId: number | null;
  chartName: string;
  chartVersion: string;
}

export function EditChartValuesDialog({
  open,
  onOpenChange,
  chartId,
  chartName,
  chartVersion,
}: EditChartValuesDialogProps) {
  const [values, setValues] = useState('');
  const [hasChanges, setHasChanges] = useState(false);

  // Fetch chart values
  const { data: valuesData, isLoading: loadingValues } = useChartValues(chartId, {
    enabled: open && !!chartId,
  });

  // Update mutation
  const updateMutation = useUpdateChartValues();

  // Load values when data arrives
  useEffect(() => {
    if (valuesData?.values) {
      setValues(valuesData.values);
      setHasChanges(false);
    }
  }, [valuesData]);

  const handleSave = async () => {
    if (!chartId) return;

    try {
      await updateMutation.mutateAsync({ chartId, values });
      setHasChanges(false);
      onOpenChange(false);
    } catch (error) {
      notifyError(error);
    }
  };

  const handleCancel = () => {
    if (hasChanges) {
      if (!confirm('You have unsaved changes. Are you sure you want to close?')) {
        return;
      }
    }
    onOpenChange(false);
  };

  const handleValuesChange = (newValues: string) => {
    setValues(newValues);
    setHasChanges(newValues !== valuesData?.values);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>
            Edit Chart Values
          </DialogTitle>
          <DialogDescription>
            Editing values.yaml for {chartName}:{chartVersion}
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 min-h-0 flex flex-col gap-4">
          {loadingValues ? (
            <div className="flex items-center justify-center h-full">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
          ) : (
            <Textarea
              value={values}
              onChange={(e) => handleValuesChange(e.target.value)}
              className="flex-1 font-mono text-sm resize-none"
              placeholder="# values.yaml content"
            />
          )}
        </div>

        <DialogFooter className="flex-shrink-0">
          <Button
            variant="outline"
            onClick={handleCancel}
            disabled={updateMutation.isPending}
          >
            <X className="h-4 w-4 mr-2" />
            Cancel
          </Button>
          <Button
            onClick={handleSave}
            disabled={!hasChanges || updateMutation.isPending}
          >
            {updateMutation.isPending ? (
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
            ) : (
              <Save className="h-4 w-4 mr-2" />
            )}
            Save Changes
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
