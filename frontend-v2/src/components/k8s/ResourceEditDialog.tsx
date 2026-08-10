import { useState, useEffect } from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Label } from '@/components/ui/label';
import { YamlEditor } from './YamlEditor';
import { Loader2, Save } from 'lucide-react';
import yaml from 'js-yaml';
import type { K8sResource } from '@/types';

interface ResourceEditDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  resource: K8sResource | null;
  onSubmit: (resourceYaml: string, dryRun: boolean) => void;
  isLoading?: boolean;
}

export function ResourceEditDialog({
  open,
  onOpenChange,
  resource,
  onSubmit,
  isLoading = false,
}: ResourceEditDialogProps) {
  const [resourceYaml, setResourceYaml] = useState('');
  const [dryRun, setDryRun] = useState(false);

  useEffect(() => {
    if (resource) {
      // Convert resource object to YAML
      const yamlStr = yaml.dump(resource, { indent: 2, lineWidth: -1 });
      setResourceYaml(yamlStr);
    }
  }, [resource]);

  const handleSubmit = () => {
    onSubmit(resourceYaml, dryRun);
  };

  if (!resource) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl max-h-[90vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle>
            Edit {resource.kind}: {resource.metadata.name}
          </DialogTitle>
          <DialogDescription>
            Modify the YAML definition below. Changes will be applied to the cluster.
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 overflow-auto py-4">
          <YamlEditor
            value={resourceYaml}
            onChange={setResourceYaml}
            height="500px"
            showValidation
          />
        </div>

        <DialogFooter className="flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <Checkbox
              id="dryRunEdit"
              checked={dryRun}
              onCheckedChange={(checked) => setDryRun(checked as boolean)}
            />
            <Label htmlFor="dryRunEdit" className="text-sm text-muted-foreground cursor-pointer">
              Dry run (preview changes without applying)
            </Label>
          </div>

          <div className="flex gap-2">
            <Button
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={isLoading}
            >
              Cancel
            </Button>
            <Button onClick={handleSubmit} disabled={isLoading}>
              {isLoading ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  {dryRun ? 'Validating...' : 'Updating...'}
                </>
              ) : (
                <>
                  <Save className="mr-2 h-4 w-4" />
                  {dryRun ? 'Dry Run' : 'Save Changes'}
                </>
              )}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
