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
import { Alert, AlertDescription } from '@/components/ui/alert';
import { ModuleVariableEditor } from './ModuleVariableEditor';
import { useUpdateModule, useModuleVariables } from '@/hooks/useModules';
import type { ProjectModule, JsonValue } from '@/types';
import { Loader2, AlertTriangle, Save, Info } from 'lucide-react';
import { notify } from '@/lib/notify';

interface EditModuleVariablesDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  module: ProjectModule;
}

export function EditModuleVariablesDialog({
  open,
  onOpenChange,
  module,
}: EditModuleVariablesDialogProps) {
  const [variables, setVariables] = useState<Record<string, JsonValue>>(module.variable_overrides || {});
  const [hasChanges, setHasChanges] = useState(false);

  const updateMutation = useUpdateModule();
  const { data: moduleVariables, isLoading: loadingVariables } = useModuleVariables(
    module.module_library_id
  );

  useEffect(() => {
    if (open) {
      // Reset to current values when dialog opens
      setVariables(module.variable_overrides || {});
      setHasChanges(false);
    }
  }, [open, module.variable_overrides]);

  const handleChange = (newVariables: Record<string, JsonValue>) => {
    setVariables(newVariables);
    setHasChanges(true);
  };

  const isApplied = module.status === 'applied';

  const handleSave = async () => {
    try {
      await updateMutation.mutateAsync({
        moduleId: module.id,
        data: {
          variable_overrides: variables,
        },
      });
      setHasChanges(false);
      onOpenChange(false);
      // S18-001: Notify user that redeploy is needed to apply variable changes
      if (isApplied) {
        notify.info(
          'Variables saved — redeploy to apply',
          'Use "Redeploy Blueprint" to apply the updated variables to your infrastructure.',
          { category: 'deployment' },
        );
      }
    } catch (_error) {
      // Error handling is done by the mutation
    }
  };

  const handleCancel = () => {
    if (hasChanges) {
      if (
        confirm(
          'You have unsaved changes. Are you sure you want to cancel?'
        )
      ) {
        onOpenChange(false);
      }
    } else {
      onOpenChange(false);
    }
  };

  // Check for required variables (only user-sourced variables need to be provided)
  const requiredVariables = moduleVariables?.filter(
    (v: { name: string; required?: boolean; source?: string }) => v.required && (!v.source || v.source === 'user')
  ) || [];
  const missingRequired = requiredVariables.filter(
    (v: { name: string; required?: boolean; source?: string }) => !variables[v.name] || variables[v.name] === ''
  );

  return (
    <Dialog open={open} onOpenChange={handleCancel}>
      <DialogContent className="max-w-3xl max-h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>Edit Module Variables</DialogTitle>
          <DialogDescription>
            Configure variables for <strong>{module.library_module?.name || 'module'}</strong>
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto py-4 space-y-4">
          {/* S18-001: Show notice when editing variables on an already-deployed module */}
          {isApplied && (
            <Alert>
              <Info className="h-4 w-4" />
              <AlertDescription>
                This module is already deployed. Saving changes here will not take effect
                until you redeploy the stack.
              </AlertDescription>
            </Alert>
          )}

          {loadingVariables ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
              <span className="ml-2 text-muted-foreground">Loading variables...</span>
            </div>
          ) : moduleVariables && moduleVariables.length > 0 ? (
            <>
              {missingRequired.length > 0 && (
                <Alert variant="destructive">
                  <AlertTriangle className="h-4 w-4" />
                  <AlertDescription>
                    {missingRequired.length} required variable{missingRequired.length !== 1 ? 's' : ''} not set:{' '}
                    {missingRequired.map((v: { name: string; required?: boolean; source?: string }) => v.name).join(', ')}
                  </AlertDescription>
                </Alert>
              )}

              <div className="space-y-1">
                <p className="text-sm font-medium">Variables</p>
                <p className="text-xs text-muted-foreground">
                  Configure the following variables for this module deployment
                </p>
              </div>

              <ModuleVariableEditor
                variables={moduleVariables}
                values={variables}
                onChange={handleChange}
              />
            </>
          ) : (
            <div className="text-center py-8">
              <p className="text-muted-foreground">No variables to configure</p>
              <p className="text-sm text-muted-foreground mt-2">
                This module doesn't require any variable configuration
              </p>
            </div>
          )}
        </div>

        <DialogFooter className="flex items-center justify-between">
          <div className="text-sm text-muted-foreground">
            {hasChanges && <span className="text-warning">● Unsaved changes</span>}
          </div>
          <div className="flex gap-2">
            <Button variant="outline" onClick={handleCancel}>
              Cancel
            </Button>
            <Button
              onClick={handleSave}
              disabled={updateMutation.isPending || missingRequired.length > 0}
            >
              {updateMutation.isPending ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Saving...
                </>
              ) : (
                <>
                  <Save className="h-4 w-4 mr-2" />
                  Save Changes
                </>
              )}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
