/**
 * Run Module Action dialog (D-034 — manifest-declared test/scenario actions).
 *
 * Lets the operator pick an action declared by a post-apply container module's
 * artifact manifest, fill in its declared inputs (enum choices → Select,
 * boolean → Switch, string/number → Input), and submit it. Amber-rated actions
 * require an explicit confirmation naming why caution is needed (the action's
 * description carries the reason). Progress is tracked via the existing task
 * polling (useTask) — action runs never change module.status.
 */
import { useState } from 'react';
import { Link } from 'react-router-dom';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { AlertTriangle, CheckCircle2, FlaskConical, Loader2, XCircle } from 'lucide-react';
import { useModuleActions, useRunModuleAction } from '@/hooks/useModuleActions';
import { useTask } from '@/hooks/useTasks';
import type { ModuleActionInfo, ModuleActionInputDef } from '@/lib/api/projects';
import type { ProjectModule } from '@/types';

interface RunModuleActionDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  module: ProjectModule;
}

function defaultInputValues(action: ModuleActionInfo | undefined): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const input of action?.inputs ?? []) {
    if (input.default !== undefined && input.default !== null) {
      values[input.name] = input.default;
    }
  }
  return values;
}

/** Convert form state back to the input's declared type for the request body. */
function coerceInputValue(def: ModuleActionInputDef, value: unknown): unknown {
  if (def.choices && def.choices.length > 0) {
    // Select stores the stringified choice — return the original choice value.
    const original = def.choices.find((c) => String(c) === String(value));
    return original !== undefined ? original : value;
  }
  if (def.type === 'number' || def.type === 'integer' || def.type === 'int' || def.type === 'float') {
    const num = Number(value);
    return Number.isNaN(num) ? value : num;
  }
  return value;
}

export function RunModuleActionDialog({ open, onOpenChange, module }: RunModuleActionDialogProps) {
  const { data, isLoading } = useModuleActions(module.id);
  const runMutation = useRunModuleAction();

  const [selectedName, setSelectedName] = useState('');
  const [inputValues, setInputValues] = useState<Record<string, unknown>>({});
  const [amberConfirmed, setAmberConfirmed] = useState(false);
  const [taskId, setTaskId] = useState<number | null>(null);

  const { data: task } = useTask(taskId ?? 0);

  const actions = data?.actions ?? [];
  const selected = actions.find((a) => a.name === selectedName);
  // Normalize case: this client check is the entire amber safeguard (the backend
  // ack is UI-only), so a "Amber"/"AMBER" rating must not slip through ungated.
  const isAmber = selected?.rating?.toLowerCase() === 'amber';

  const taskStatus = taskId ? (task?.status ?? 'queued') : null;
  const taskActive = taskStatus === 'queued' || taskStatus === 'in_progress';

  const canSubmit = !!selected && (!isAmber || amberConfirmed) && !runMutation.isPending && !taskActive;

  const handleSelectAction = (name: string) => {
    setSelectedName(name);
    setAmberConfirmed(false);
    setTaskId(null);
    setInputValues(defaultInputValues(actions.find((a) => a.name === name)));
  };

  const setInputValue = (name: string, value: unknown) => {
    setInputValues((prev) => ({ ...prev, [name]: value }));
  };

  const handleSubmit = async () => {
    if (!selected) return;
    const inputs: Record<string, unknown> = {};
    for (const def of selected.inputs ?? []) {
      const value = inputValues[def.name];
      if (value === undefined || value === '') continue;
      inputs[def.name] = coerceInputValue(def, value);
    }
    // Errors surface via useAppMutation's notifyError — just skip task tracking.
    const result = await runMutation
      .mutateAsync({
        moduleId: module.id,
        actionName: selected.name,
        inputs: Object.keys(inputs).length > 0 ? inputs : undefined,
      })
      .catch(() => null);
    if (result) setTaskId(result.task_id);
  };

  const renderInput = (def: ModuleActionInputDef) => {
    const value = inputValues[def.name];

    if (def.choices && def.choices.length > 0) {
      return (
        <Select
          value={value !== undefined ? String(value) : undefined}
          onValueChange={(v) => setInputValue(def.name, v)}
        >
          <SelectTrigger aria-label={def.name}>
            <SelectValue placeholder={`Select ${def.name}`} />
          </SelectTrigger>
          <SelectContent>
            {def.choices.map((choice) => (
              <SelectItem key={String(choice)} value={String(choice)}>
                {String(choice)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      );
    }

    if (def.type === 'boolean' || def.type === 'bool') {
      return (
        <Switch
          aria-label={def.name}
          checked={value === true}
          onCheckedChange={(checked) => setInputValue(def.name, checked)}
        />
      );
    }

    return (
      <Input
        aria-label={def.name}
        type={def.type === 'number' || def.type === 'integer' || def.type === 'int' || def.type === 'float' ? 'number' : 'text'}
        value={value !== undefined ? String(value) : ''}
        onChange={(e) => setInputValue(def.name, e.target.value)}
      />
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FlaskConical className="h-5 w-5 text-info" />
            Run Module Action
          </DialogTitle>
          <DialogDescription>
            Run a test or scenario action declared by <strong>{module.module_name}</strong>&apos;s artifact
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {isLoading ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading actions…
            </div>
          ) : actions.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              This module&apos;s artifact declares no actions.
            </p>
          ) : (
            <>
              <div className="space-y-2">
                <Label htmlFor="action-picker">Action</Label>
                <Select value={selectedName || undefined} onValueChange={handleSelectAction}>
                  <SelectTrigger id="action-picker" aria-label="Action">
                    <SelectValue placeholder="Select an action" />
                  </SelectTrigger>
                  <SelectContent>
                    {actions.map((action) => (
                      <SelectItem key={action.name} value={action.name}>
                        <span className="flex items-center gap-2">
                          {action.title}
                          {action.rating?.toLowerCase() === 'amber' && (
                            <Badge variant="warning" className="text-[10px]">amber</Badge>
                          )}
                        </span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {selected && !isAmber && selected.description && (
                <p className="text-sm text-muted-foreground">{selected.description}</p>
              )}

              {selected && isAmber && (
                <div className="rounded-lg border border-warning/50 bg-warning/10 p-3 space-y-3">
                  <div className="flex items-start gap-2 text-sm">
                    <AlertTriangle className="h-4 w-4 mt-0.5 text-warning flex-shrink-0" />
                    <span>
                      This action is amber-rated — extra caution is needed
                      {selected.description ? <>: {selected.description}</> : '.'}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <Checkbox
                      id="amber-confirm"
                      checked={amberConfirmed}
                      onCheckedChange={(checked) => setAmberConfirmed(checked === true)}
                    />
                    <Label htmlFor="amber-confirm" className="text-sm font-normal">
                      I understand — run this amber-rated action
                    </Label>
                  </div>
                </div>
              )}

              {selected && (selected.inputs ?? []).length > 0 && (
                <div className="space-y-3">
                  {(selected.inputs ?? []).map((def) => (
                    <div key={def.name} className="space-y-1.5">
                      <Label htmlFor={`action-input-${def.name}`}>{def.name}</Label>
                      {renderInput(def)}
                      {def.description && (
                        <p className="text-xs text-muted-foreground">{def.description}</p>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {taskId && (
                <div className="rounded-lg bg-muted p-3 flex items-center gap-2 text-sm">
                  {taskActive ? (
                    <Loader2 className="h-4 w-4 animate-spin flex-shrink-0" />
                  ) : taskStatus === 'completed' ? (
                    <CheckCircle2 className="h-4 w-4 text-success flex-shrink-0" />
                  ) : (
                    <XCircle className="h-4 w-4 text-destructive flex-shrink-0" />
                  )}
                  <span>
                    Action task #{taskId}: {taskStatus?.replace('_', ' ')}
                  </span>
                  <Link to="/tasks" className="ml-auto text-primary hover:underline">
                    View logs
                  </Link>
                </div>
              )}

              {/* Post-run cleanup hint (e.g. scenarios leave cluster objects
                  until their Clean action is run). Shown once the run completes. */}
              {taskId && taskStatus === 'completed' && selected?.cleanup_note && (
                <div className="rounded-lg border border-warning/40 bg-warning/10 p-3 flex items-start gap-2 text-sm">
                  <AlertTriangle className="h-4 w-4 text-warning flex-shrink-0 mt-0.5" />
                  <span>{selected.cleanup_note}</span>
                </div>
              )}
            </>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
          <Button onClick={handleSubmit} disabled={!canSubmit}>
            {runMutation.isPending || taskActive ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Running…
              </>
            ) : (
              'Run'
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
