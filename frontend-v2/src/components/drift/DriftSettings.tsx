import { useState, useEffect } from 'react';
import { SectionCard } from '@/components/ui/section-card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import { Badge } from '@/components/ui/badge';
import { Loader2, AlertCircle, Play, Square } from 'lucide-react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  useDriftSettings,
  useUpdateDriftSettings,
  useEnableDriftDetection,
  useDisableDriftDetection,
} from '@/hooks/useDrift';
import { DRIFT_SCHEDULE_INTERVALS, DRIFT_SCHEDULE_LABELS } from '@/lib/constants';

interface DriftSettingsProps {
  projectId: number;
}

export function DriftSettings({ projectId }: DriftSettingsProps) {
  const { data: settings, isLoading, error } = useDriftSettings(projectId);
  const updateSettings = useUpdateDriftSettings();
  const enableDrift = useEnableDriftDetection();
  const disableDrift = useDisableDriftDetection();

  const [scheduleType, setScheduleType] = useState<'cron' | 'interval'>(settings?.schedule_type || 'interval');
  const [scheduleValue, setScheduleValue] = useState(settings?.schedule_value || '3600');
  const [checkAllModules, setCheckAllModules] = useState(settings?.check_all_modules ?? true);
  const [notifyOnDrift, setNotifyOnDrift] = useState(settings?.notify_on_drift ?? true);
  const [ignoreInsignificant, setIgnoreInsignificant] = useState(settings?.ignore_insignificant_changes ?? true);

  // Update local state when settings load
  useEffect(() => {
    if (settings) {
      setScheduleType(settings.schedule_type);
      setScheduleValue(settings.schedule_value);
      setCheckAllModules(settings.check_all_modules);
      setNotifyOnDrift(settings.notify_on_drift);
      setIgnoreInsignificant(settings.ignore_insignificant_changes);
    }
  }, [settings]);

  const handleSave = () => {
    updateSettings.mutate({
      projectId,
      data: {
        schedule_type: scheduleType,
        schedule_value: scheduleValue,
        check_all_modules: checkAllModules,
        notify_on_drift: notifyOnDrift,
        ignore_insignificant_changes: ignoreInsignificant,
      },
    });
  };

  const handleToggleEnabled = () => {
    if (settings?.enabled) {
      disableDrift.mutate(projectId);
    } else {
      enableDrift.mutate(projectId);
    }
  };

  if (isLoading) {
    return (
      <SectionCard>
        <div className="flex items-center justify-center py-8">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      </SectionCard>
    );
  }

  if (error) {
    return (
      <Alert variant="destructive">
        <AlertCircle className="h-4 w-4" />
        <AlertDescription>
          Failed to load drift settings. {error instanceof Error ? error.message : 'Unknown error'}
        </AlertDescription>
      </Alert>
    );
  }

  return (
    <SectionCard title="Drift Detection Settings">
      <div className="flex items-start justify-between -mt-3 mb-5">
        <p className="text-sm text-muted-foreground">
          Configure automated drift detection to monitor infrastructure changes.
        </p>
        <div className="flex items-center gap-2 flex-shrink-0">
          {settings?.enabled ? (
            <Badge variant="success">Enabled</Badge>
          ) : (
            <Badge variant="muted">Disabled</Badge>
          )}
          <Button
            variant="outline"
            size="sm"
            onClick={handleToggleEnabled}
            disabled={enableDrift.isPending || disableDrift.isPending}
          >
            {enableDrift.isPending || disableDrift.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : settings?.enabled ? (
              <>
                <Square className="h-4 w-4 mr-1" />
                Disable
              </>
            ) : (
              <>
                <Play className="h-4 w-4 mr-1" />
                Enable
              </>
            )}
          </Button>
        </div>
      </div>

      <div className="space-y-6">
        {/* Schedule Configuration */}
        <div className="space-y-2">
          <Label>Schedule Type</Label>
          <Select value={scheduleType} onValueChange={(v) => setScheduleType(v as 'cron' | 'interval')}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="interval">Interval (seconds)</SelectItem>
              <SelectItem value="cron">Cron Expression</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-2">
          <Label>Schedule Value</Label>
          {scheduleType === 'interval' ? (
            <div className="space-y-3">
              <div className="grid grid-cols-3 gap-2">
                <Button
                  type="button"
                  variant={scheduleValue === DRIFT_SCHEDULE_INTERVALS.FIVE_MIN ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => setScheduleValue(DRIFT_SCHEDULE_INTERVALS.FIVE_MIN)}
                >
                  5 min
                </Button>
                <Button
                  type="button"
                  variant={scheduleValue === DRIFT_SCHEDULE_INTERVALS.THIRTY_MIN ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => setScheduleValue(DRIFT_SCHEDULE_INTERVALS.THIRTY_MIN)}
                >
                  30 min
                </Button>
                <Button
                  type="button"
                  variant={scheduleValue === DRIFT_SCHEDULE_INTERVALS.TWO_HOURS ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => setScheduleValue(DRIFT_SCHEDULE_INTERVALS.TWO_HOURS)}
                >
                  2 hours
                </Button>
                <Button
                  type="button"
                  variant={scheduleValue === DRIFT_SCHEDULE_INTERVALS.TWELVE_HOURS ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => setScheduleValue(DRIFT_SCHEDULE_INTERVALS.TWELVE_HOURS)}
                >
                  12 hours
                </Button>
                <Button
                  type="button"
                  variant={scheduleValue === DRIFT_SCHEDULE_INTERVALS.TWENTYFOUR_HOURS ? 'default' : 'outline'}
                  size="sm"
                  onClick={() => setScheduleValue(DRIFT_SCHEDULE_INTERVALS.TWENTYFOUR_HOURS)}
                >
                  24 hours
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    const custom = prompt('Enter custom interval in seconds:', scheduleValue);
                    if (custom) setScheduleValue(custom);
                  }}
                >
                  Custom
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                Selected: {DRIFT_SCHEDULE_LABELS[scheduleValue] || `${scheduleValue} seconds`}
              </p>
            </div>
          ) : (
            <>
              <Input
                value={scheduleValue}
                onChange={(e) => setScheduleValue(e.target.value)}
                placeholder="0 */6 * * *"
              />
              <p className="text-xs text-muted-foreground">
                Cron expression (e.g., "0 */6 * * *" = every 6 hours)
              </p>
            </>
          )}
        </div>

        {/* Module Selection */}
        <div className="flex items-center justify-between">
          <div className="space-y-0.5">
            <Label>Check All Modules</Label>
            <p className="text-xs text-muted-foreground">
              Check all modules in this project for drift
            </p>
          </div>
          <Switch checked={checkAllModules} onCheckedChange={setCheckAllModules} />
        </div>

        {/* Notification Settings */}
        <div className="flex items-center justify-between">
          <div className="space-y-0.5">
            <Label>Notify on Drift</Label>
            <p className="text-xs text-muted-foreground">
              Send notifications when drift is detected
            </p>
          </div>
          <Switch checked={notifyOnDrift} onCheckedChange={setNotifyOnDrift} />
        </div>

        {/* Ignore Insignificant Changes */}
        <div className="flex items-center justify-between">
          <div className="space-y-0.5">
            <Label>Ignore Insignificant Changes</Label>
            <p className="text-xs text-muted-foreground">
              Ignore minor changes like timestamps and metadata
            </p>
          </div>
          <Switch checked={ignoreInsignificant} onCheckedChange={setIgnoreInsignificant} />
        </div>

        {/* Save Button */}
        <div className="flex justify-end pt-4">
          <Button
            variant="outline"
            size="sm"
            onClick={handleSave}
            disabled={updateSettings.isPending || !settings?.enabled}
          >
            {updateSettings.isPending ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Saving...
              </>
            ) : (
              'Save Settings'
            )}
          </Button>
        </div>

        {!settings?.enabled && (
          <Alert>
            <AlertCircle className="h-4 w-4" />
            <AlertDescription>
              Enable drift detection to activate scheduled checks and modify settings.
            </AlertDescription>
          </Alert>
        )}
      </div>
    </SectionCard>
  );
}
