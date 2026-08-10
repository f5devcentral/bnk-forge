import { useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Loader2, RefreshCw, Search, AlertCircle, Info, AlertTriangle } from 'lucide-react';
import { useClusterEvents } from '@/hooks/useK8s';
import type { K8sEvent } from '@/types';

interface ResourceEventsViewerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  clusterId: number;
  namespace?: string;
  resourceType?: string;
  resourceName?: string;
}

export function ResourceEventsViewer({
  open,
  onOpenChange,
  clusterId,
  namespace,
  resourceType,
  resourceName,
}: ResourceEventsViewerProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [eventTypeFilter, setEventTypeFilter] = useState<string>('all');
  const pollingEnabled = open;

  const { data, isLoading, refetch } = useClusterEvents(
    clusterId,
    {
      namespace,
      resource_type: resourceType,
      resource_name: resourceName,
      event_type: eventTypeFilter === 'all' ? undefined : eventTypeFilter,
    },
    { pollingEnabled, enabled: open }
  );

  const events = data?.events || [];

  // Filter events by search query
  const filteredEvents = events.filter((event: K8sEvent) =>
    !searchQuery ||
    event.reason?.toLowerCase().includes(searchQuery.toLowerCase()) ||
    event.message?.toLowerCase().includes(searchQuery.toLowerCase()) ||
    event.involved_object?.name?.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const getEventIcon = (type: string) => {
    switch (type?.toLowerCase()) {
      case 'normal':
        return <Info className="h-5 w-5 text-info" />;
      case 'warning':
        return <AlertTriangle className="h-5 w-5 text-warning" />;
      case 'error':
        return <AlertCircle className="h-5 w-5 text-destructive" />;
      default:
        return <Info className="h-5 w-5 text-muted-foreground" />;
    }
  };

  const getEventTypeColor = (type: string): 'default' | 'secondary' | 'destructive' | 'outline' | 'success' | 'warning' | 'info' | 'muted' => {
    switch (type?.toLowerCase()) {
      case 'normal':
        return 'info';
      case 'warning':
        return 'warning';
      case 'error':
        return 'destructive';
      default:
        return 'outline';
    }
  };

  const formatTimestamp = (timestamp: string | null) => {
    if (!timestamp) return 'N/A';
    const date = new Date(timestamp);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return 'just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 30) return `${diffDays}d ago`;
    return date.toLocaleDateString();
  };

  const eventCounts = {
    normal: events.filter((e: K8sEvent) => e.type?.toLowerCase() === 'normal').length,
    warning: events.filter((e: K8sEvent) => e.type?.toLowerCase() === 'warning').length,
    error: events.filter((e: K8sEvent) => e.type?.toLowerCase() === 'error').length,
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-6xl max-h-[90vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            Cluster Events
            {resourceName && (
              <span className="text-muted-foreground text-base font-normal">
                for {resourceType}/{resourceName}
              </span>
            )}
          </DialogTitle>
        </DialogHeader>

        {/* Filters */}
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="Search events by reason or message..."
                aria-label="Search events"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-9"
              />
            </div>

            <Select value={eventTypeFilter} onValueChange={setEventTypeFilter}>
              <SelectTrigger className="w-[180px]">
                <SelectValue placeholder="Event type" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Types ({events.length})</SelectItem>
                <SelectItem value="Normal">Normal ({eventCounts.normal})</SelectItem>
                <SelectItem value="Warning">Warning ({eventCounts.warning})</SelectItem>
                <SelectItem value="Error">Error ({eventCounts.error})</SelectItem>
              </SelectContent>
            </Select>

            <Button
              variant="outline"
              size="sm"
              onClick={() => refetch()}
              disabled={isLoading}
            >
              <RefreshCw className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
            </Button>
          </div>

          {/* Event Type Summary */}
          <div className="flex gap-2">
            <Badge variant="info" className="gap-1">
              <Info className="h-3 w-3" />
              {eventCounts.normal} Normal
            </Badge>
            <Badge variant="warning" className="gap-1">
              <AlertTriangle className="h-3 w-3" />
              {eventCounts.warning} Warning
            </Badge>
            <Badge variant="destructive" className="gap-1">
              <AlertCircle className="h-3 w-3" />
              {eventCounts.error} Error
            </Badge>
          </div>
        </div>

        {/* Events List */}
        <div className="flex-1 overflow-y-auto space-y-2">
          {isLoading && events.length === 0 ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
          ) : filteredEvents.length > 0 ? (
            filteredEvents.map((event: K8sEvent, idx: number) => (
              <Card key={idx} className="p-4">
                <div className="flex items-start gap-3">
                  {getEventIcon(event.type)}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-start justify-between gap-2 mb-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <Badge variant={getEventTypeColor(event.type)}>
                          {event.type}
                        </Badge>
                        <span className="font-semibold">{event.reason}</span>
                        {(event.count ?? 0) > 1 && (
                          <Badge variant="outline">{event.count}x</Badge>
                        )}
                      </div>
                      <span className="text-xs text-muted-foreground whitespace-nowrap">
                        {formatTimestamp(event.last_timestamp ?? null)}
                      </span>
                    </div>

                    <div className="text-sm text-muted-foreground mb-2 break-words">
                      {event.message}
                    </div>

                    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                      {event.involved_object?.name && (
                        <span>
                          <span className="font-medium">Object:</span>{' '}
                          {event.involved_object.kind}/{event.involved_object.name}
                        </span>
                      )}
                      {event.source && (
                        <span>
                          <span className="font-medium">Source:</span> {event.source}
                        </span>
                      )}
                      {event.involved_object?.namespace && (
                        <span>
                          <span className="font-medium">Namespace:</span> {event.involved_object.namespace}
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              </Card>
            ))
          ) : (
            <Card className="p-8 text-center text-muted-foreground">
              {searchQuery || eventTypeFilter !== 'all'
                ? 'No events match your filters'
                : 'No events found'}
            </Card>
          )}
        </div>

        <div className="text-xs text-muted-foreground">
          Showing {filteredEvents.length} of {events.length} events • Auto-refreshing every 10 seconds
        </div>
      </DialogContent>
    </Dialog>
  );
}
