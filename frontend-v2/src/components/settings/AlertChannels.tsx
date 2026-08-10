/**
 * Alert Channels management component.
 * 
 * CRUD interface for configuring webhook/Slack/Teams alert destinations.
 * Sprint 7 — Day 2 Operations.
 */
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api';
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { alertChannelSchema, type AlertChannelFormData } from '@/schemas';
import { cn } from '@/lib/utils';
import { notify } from '@/lib/notify';
import { parseApiError } from '@/lib/error-handler';

import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Bell,
  Plus,
  Trash2,
  Pencil,
  Zap,
  CheckCircle2,
  XCircle,
  Clock,
  Loader2,
  Webhook,
  Hash,
  MessageSquare,
  Mail,
  ToggleLeft,
  ToggleRight,
} from 'lucide-react';

import type { AlertChannel, AlertChannelCreate, AlertChannelType, AlertEventType } from '@/types';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

const CHANNEL_TYPES: { value: AlertChannelType; label: string; icon: typeof Webhook; description: string }[] = [
  { value: 'webhook', label: 'Webhook', icon: Webhook, description: 'POST JSON to any URL' },
  { value: 'slack', label: 'Slack', icon: Hash, description: 'Slack incoming webhook' },
  { value: 'msteams', label: 'MS Teams', icon: MessageSquare, description: 'Teams incoming webhook' },
  { value: 'email', label: 'Email', icon: Mail, description: 'SMTP email (future)' },
];

const EVENT_TYPES: { value: AlertEventType; label: string; color: string }[] = [
  { value: 'health_change', label: 'Health Change', color: 'text-destructive' },
  { value: 'drift_detected', label: 'Drift Detected', color: 'text-warning' },
  { value: 'deploy_success', label: 'Deploy Success', color: 'text-success' },
  { value: 'deploy_failed', label: 'Deploy Failed', color: 'text-destructive' },
  { value: 'deploy_started', label: 'Deploy Started', color: 'text-info' },
];

const EMPTY_FORM: AlertChannelCreate = {
  name: '',
  channel_type: 'webhook',
  url: '',
  description: '',
  event_types: [],
  min_interval_seconds: 60,
  enabled: true,
};

export function AlertChannels() {
  const queryClient = useQueryClient();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);

  const form = useForm<AlertChannelFormData>({
    resolver: zodResolver(alertChannelSchema),
    defaultValues: { ...EMPTY_FORM },
  });

  // Fetch channels
  const { data: channels, isLoading } = useQuery({
    queryKey: ['alert-channels'],
    queryFn: () => api.listAlertChannels(),
  });

  // Mutations
  const createMutation = useAppMutation({
    mutationFn: (data: AlertChannelCreate) => api.createAlertChannel(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['alert-channels'] });
      closeDialog();
    },
    onError: (error) => {
      const parsed = parseApiError(error);
      notify.error(parsed.title, parsed.message);
    },
  });

  const updateMutation = useAppMutation({
    mutationFn: ({ id, data }: { id: number; data: Partial<AlertChannelCreate> }) =>
      api.updateAlertChannel(id, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['alert-channels'] });
      closeDialog();
    },
    onError: (error) => {
      const parsed = parseApiError(error);
      notify.error(parsed.title, parsed.message);
    },
  });

  const deleteMutation = useAppMutation({
    mutationFn: (id: number) => api.deleteAlertChannel(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['alert-channels'] });
      notify.success('Alert channel deleted', undefined, { category: 'system' });
    },
    onError: (error) => {
      const parsed = parseApiError(error);
      notify.error(parsed.title, parsed.message);
    },
  });

  const testMutation = useAppMutation({
    mutationFn: (id: number) => api.testAlertChannel(id),
    onSuccess: (data) => {
      if (data.success) {
        notify.success('Test alert sent', `${data.duration_ms}ms`, { category: 'system' });
      } else {
        notify.error('Test alert failed', data.error || data.message);
      }
    },
    onError: (error) => {
      const parsed = parseApiError(error);
      notify.error(parsed.title, parsed.message);
    },
  });

  const toggleMutation = useAppMutation({
    mutationFn: (id: number) => api.toggleAlertChannel(id),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['alert-channels'] });
      notify.success(data.message, undefined, { category: 'system' });
    },
    onError: (error) => {
      const parsed = parseApiError(error);
      notify.error(parsed.title, parsed.message);
    },
  });

  function openCreate() {
    setEditingId(null);
    form.reset({ ...EMPTY_FORM });
    setDialogOpen(true);
  }

  function openEdit(channel: AlertChannel) {
    setEditingId(channel.id);
    form.reset({
      name: channel.name,
      channel_type: channel.channel_type,
      url: channel.url || '',
      description: channel.description || '',
      event_types: channel.event_types,
      min_interval_seconds: channel.min_interval_seconds,
      enabled: channel.enabled,
    });
    setDialogOpen(true);
  }

  function closeDialog() {
    setDialogOpen(false);
    setEditingId(null);
    form.reset({ ...EMPTY_FORM });
  }

  function onValid(values: AlertChannelFormData) {
    if (editingId) {
      updateMutation.mutate({ id: editingId, data: values });
    } else {
      createMutation.mutate(values as AlertChannelCreate);
    }
  }

  function toggleEventType(eventType: AlertEventType) {
    const current = form.getValues('event_types') || [];
    if (current.includes(eventType)) {
      form.setValue(
        'event_types',
        current.filter((t) => t !== eventType),
        { shouldDirty: true },
      );
    } else {
      form.setValue('event_types', [...current, eventType], { shouldDirty: true });
    }
  }

  const isPending = createMutation.isPending || updateMutation.isPending;
  const channelType = form.watch('channel_type');

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold text-foreground">
            Alert Channels
          </h3>
          <p className="text-sm text-muted-foreground">
            Configure where alerts are sent when health changes, drift is detected, or deployments complete.
          </p>
        </div>
        <Button onClick={openCreate} size="sm">
          <Plus className="h-4 w-4 mr-1" />
          Add Channel
        </Button>
      </div>

      {/* Channel List */}
      {isLoading ? (
        <div className="flex items-center gap-2 text-muted-foreground py-8 justify-center">
          <Loader2 className="h-5 w-5 animate-spin" />
          Loading channels...
        </div>
      ) : !channels || channels.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center">
            <Bell className="h-10 w-10 mx-auto mb-3 text-muted-foreground" />
            <p className="text-sm font-medium text-muted-foreground">
              No alert channels configured
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              Add a webhook, Slack, or Teams channel to receive alerts
            </p>
            <Button onClick={openCreate} variant="outline" size="sm" className="mt-4">
              <Plus className="h-4 w-4 mr-1" />
              Create your first channel
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-3">
          {channels.map((channel) => {
            const typeInfo = CHANNEL_TYPES.find(t => t.value === channel.channel_type);
            const TypeIcon = typeInfo?.icon || Webhook;

            return (
              <Card key={channel.id} className={cn(
                'transition-colors',
                !channel.enabled && 'opacity-60',
              )}>
                <CardContent className="py-4">
                  <div className="flex items-center justify-between gap-4">
                    {/* Left: Icon + Info */}
                    <div className="flex items-center gap-3 flex-1 min-w-0">
                      <div className={cn(
                        'h-9 w-9 rounded-lg flex items-center justify-center flex-shrink-0',
                        channel.enabled
                          ? 'bg-primary/10 text-primary'
                          : 'bg-muted text-muted-foreground',
                      )}>
                        <TypeIcon className="h-4.5 w-4.5" />
                      </div>
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium truncate text-foreground">
                            {channel.name}
                          </span>
                          <Badge variant="outline" className="text-[10px] px-1.5 py-0">
                            {typeInfo?.label || channel.channel_type}
                          </Badge>
                          {!channel.enabled && (
                            <Badge variant="muted" className="text-[10px] px-1.5 py-0">
                              Disabled
                            </Badge>
                          )}
                        </div>
                        <div className="flex items-center gap-3 mt-0.5">
                          {channel.event_types && channel.event_types.length > 0 ? (
                            <span className="text-xs text-muted-foreground">
                              {channel.event_types.map(e => e.replace('_', ' ')).join(', ')}
                            </span>
                          ) : (
                            <span className="text-xs text-muted-foreground">All events</span>
                          )}
                        </div>
                      </div>
                    </div>

                    {/* Center: Stats */}
                    <div className="hidden sm:flex items-center gap-4 text-xs text-muted-foreground">
                      <div className="flex items-center gap-1">
                        <CheckCircle2 className="h-3 w-3 text-success" />
                        <span>{channel.total_sent}</span>
                      </div>
                      {channel.total_failed > 0 && (
                        <div className="flex items-center gap-1">
                          <XCircle className="h-3 w-3 text-destructive" />
                          <span>{channel.total_failed}</span>
                        </div>
                      )}
                      {channel.last_fired_at && (
                        <div className="flex items-center gap-1">
                          <Clock className="h-3 w-3" />
                          <span>{new Date(channel.last_fired_at).toLocaleDateString()}</span>
                        </div>
                      )}
                    </div>

                    {/* Right: Actions */}
                    <div className="flex items-center gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => toggleMutation.mutate(channel.id)}
                        title={channel.enabled ? 'Disable' : 'Enable'}
                        className="h-8 w-8 p-0"
                      >
                        {channel.enabled ? (
                          <ToggleRight className="h-4 w-4 text-success" />
                        ) : (
                          <ToggleLeft className="h-4 w-4 text-muted-foreground" />
                        )}
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => testMutation.mutate(channel.id)}
                        disabled={testMutation.isPending || !channel.enabled}
                        title="Send test alert"
                        className="h-8 w-8 p-0"
                      >
                        {testMutation.isPending ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <Zap className="h-4 w-4" />
                        )}
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => openEdit(channel)}
                        title="Edit"
                        className="h-8 w-8 p-0"
                      >
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          if (confirm(`Delete channel "${channel.name}"?`)) {
                            deleteMutation.mutate(channel.id);
                          }
                        }}
                        title="Delete"
                        className="h-8 w-8 p-0 text-destructive hover:text-destructive"
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </div>

                  {/* Error banner */}
                  {channel.last_error && (
                    <div className="mt-2 px-3 py-1.5 rounded text-xs border-l-2 border-l-destructive bg-muted/50 text-destructive">
                      Last error: {channel.last_error}
                    </div>
                  )}
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      {/* Create / Edit Dialog */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-[500px] max-h-[90vh] overflow-y-auto">
          <Form {...form}>
            <form onSubmit={form.handleSubmit(onValid)} noValidate>
              <DialogHeader>
                <DialogTitle>{editingId ? 'Edit Alert Channel' : 'Create Alert Channel'}</DialogTitle>
                <DialogDescription>
                  {editingId
                    ? 'Update the alert channel configuration.'
                    : 'Configure a new destination for alerts.'}
                </DialogDescription>
              </DialogHeader>

              <div className="grid gap-4 py-4">
                <FormField
                  control={form.control}
                  name="name"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Name *</FormLabel>
                      <FormControl>
                        <Input placeholder="Production Alerts" {...field} />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name="channel_type"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Channel Type</FormLabel>
                      <Select value={field.value} onValueChange={field.onChange}>
                        <FormControl>
                          <SelectTrigger>
                            <SelectValue />
                          </SelectTrigger>
                        </FormControl>
                        <SelectContent>
                          {CHANNEL_TYPES.map((ct) => (
                            <SelectItem key={ct.value} value={ct.value}>
                              {ct.label} — {ct.description}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                {channelType !== 'email' && (
                  <FormField
                    control={form.control}
                    name="url"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>
                          {channelType === 'slack'
                            ? 'Slack Webhook URL'
                            : channelType === 'msteams'
                              ? 'Teams Webhook URL'
                              : 'Webhook URL'}{' '}
                          *
                        </FormLabel>
                        <FormControl>
                          <Input
                            type="url"
                            placeholder={
                              channelType === 'slack'
                                ? 'https://hooks.slack.com/services/T.../B.../xxx'
                                : channelType === 'msteams'
                                  ? 'https://outlook.office.com/webhook/...'
                                  : 'https://example.com/webhook'
                            }
                            {...field}
                            value={field.value ?? ''}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                )}

                <div className="grid gap-2">
                  <Label>Event Filters</Label>
                  <p className="text-xs text-muted-foreground -mt-1">
                    Select which events trigger this channel. Empty = all events.
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {EVENT_TYPES.map((et) => {
                      const selected = (form.watch('event_types') || []).includes(et.value);
                      return (
                        <Badge
                          key={et.value}
                          variant={selected ? 'default' : 'outline'}
                          className={cn(
                            'cursor-pointer transition-colors text-xs',
                            selected && 'bg-primary text-primary-foreground',
                          )}
                          onClick={() => toggleEventType(et.value)}
                        >
                          {et.label}
                        </Badge>
                      );
                    })}
                  </div>
                </div>

                <FormField
                  control={form.control}
                  name="min_interval_seconds"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Min Interval (seconds)</FormLabel>
                      <FormControl>
                        <Input
                          type="number"
                          min={0}
                          {...field}
                          value={field.value ?? 60}
                          onChange={(e) => field.onChange(parseInt(e.target.value) || 60)}
                        />
                      </FormControl>
                      <FormDescription>
                        Minimum seconds between alerts to prevent storms. 0 = no limit.
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name="description"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Description</FormLabel>
                      <FormControl>
                        <Textarea
                          placeholder="Optional description of this channel"
                          rows={2}
                          {...field}
                          value={field.value ?? ''}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>

              <DialogFooter>
                <Button type="button" variant="outline" onClick={closeDialog} disabled={isPending}>
                  Cancel
                </Button>
                <Button type="submit" disabled={isPending}>
                  {isPending ? (
                    <>
                      <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                      {editingId ? 'Updating...' : 'Creating...'}
                    </>
                  ) : (
                    editingId ? 'Update Channel' : 'Create Channel'
                  )}
                </Button>
              </DialogFooter>
            </form>
          </Form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
