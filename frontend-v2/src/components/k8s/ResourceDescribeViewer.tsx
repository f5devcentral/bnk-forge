import { useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Loader2, Copy, CheckCircle2, XCircle, Clock, AlertTriangle } from 'lucide-react';
import type { K8sResource, K8sCondition, K8sEvent, K8sOwnerReference, K8sResourceRelationship } from '@/types';
import { useDescribeResource } from '@/hooks/useK8s';

interface ResourceDescribeViewerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  resource: K8sResource | null;
  clusterId: number;
  namespace?: string;
}

export function ResourceDescribeViewer({
  open,
  onOpenChange,
  resource,
  clusterId,
  namespace,
}: ResourceDescribeViewerProps) {
  const [activeTab, setActiveTab] = useState('overview');

  const { data: description, isLoading } = useDescribeResource(
    clusterId,
    resource?.kind?.toLowerCase() || '',
    resource?.metadata?.name || '',
    namespace || resource?.metadata?.namespace,
    { enabled: open && !!resource }
  );

  if (!resource) return null;

  const handleCopy = (text: string) => {
    navigator.clipboard.writeText(text);
  };

  const getConditionIcon = (status: string) => {
    switch (status?.toLowerCase()) {
      case 'true':
        return <CheckCircle2 className="h-4 w-4 text-success" />;
      case 'false':
        return <XCircle className="h-4 w-4 text-destructive" />;
      case 'unknown':
        return <AlertTriangle className="h-4 w-4 text-warning" />;
      default:
        return <Clock className="h-4 w-4 text-muted-foreground" />;
    }
  };

  const getEventTypeColor = (type: string): 'default' | 'secondary' | 'destructive' | 'outline' | 'success' | 'warning' => {
    switch (type?.toLowerCase()) {
      case 'normal':
        return 'default';
      case 'warning':
        return 'secondary';
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

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-6xl max-h-[90vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            Describe: {resource.kind} / {resource.metadata.name}
          </DialogTitle>
        </DialogHeader>

        {isLoading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </div>
        ) : description ? (
          <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col overflow-hidden">
            <TabsList className="grid w-full grid-cols-5">
              <TabsTrigger value="overview">Overview</TabsTrigger>
              <TabsTrigger value="conditions">
                Conditions
                {(description.conditions?.length ?? 0) > 0 && (
                  <Badge variant="secondary" className="ml-2">
                    {description.conditions?.length}
                  </Badge>
                )}
              </TabsTrigger>
              <TabsTrigger value="events">
                Events
                {(description.events?.length ?? 0) > 0 && (
                  <Badge variant="secondary" className="ml-2">
                    {description.events?.length}
                  </Badge>
                )}
              </TabsTrigger>
              <TabsTrigger value="relationships">Related</TabsTrigger>
              <TabsTrigger value="raw">Raw Data</TabsTrigger>
            </TabsList>

            <div className="flex-1 overflow-y-auto mt-4">
              <TabsContent value="overview" className="space-y-4 mt-0">
                {/* Metadata */}
                <Card className="p-4">
                  <div className="flex items-center justify-between mb-3">
                    <h4 className="font-semibold">Metadata</h4>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleCopy(JSON.stringify(description.metadata, null, 2))}
                    >
                      <Copy className="h-4 w-4" />
                    </Button>
                  </div>
                  <div className="grid grid-cols-2 gap-3 text-sm">
                    <div>
                      <span className="text-muted-foreground">Name:</span>
                      <span className="ml-2 font-mono">{description.metadata.name}</span>
                    </div>
                    {description.metadata.namespace && (
                      <div>
                        <span className="text-muted-foreground">Namespace:</span>
                        <span className="ml-2 font-mono">{description.metadata.namespace}</span>
                      </div>
                    )}
                    <div>
                      <span className="text-muted-foreground">UID:</span>
                      <span className="ml-2 font-mono text-xs">{description.metadata.uid}</span>
                    </div>
                    <div>
                      <span className="text-muted-foreground">Created:</span>
                      <span className="ml-2">{formatTimestamp(description.metadata.creation_timestamp ?? null)}</span>
                    </div>
                  </div>

                  {/* Labels */}
                  {description.metadata.labels && Object.keys(description.metadata.labels).length > 0 && (
                    <div className="mt-4">
                      <h5 className="text-sm font-medium mb-2">Labels</h5>
                      <div className="flex flex-wrap gap-2">
                        {Object.entries(description.metadata.labels).map(([key, value]) => (
                          <Badge key={key} variant="secondary">
                            {key}: {value as string}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Owner References */}
                  {description.metadata.owner_references && description.metadata.owner_references.length > 0 && (
                    <div className="mt-4">
                      <h5 className="text-sm font-medium mb-2">Owner</h5>
                      {description.metadata.owner_references.map((owner: K8sOwnerReference, idx: number) => (
                        <div key={idx} className="text-sm">
                          <span className="font-mono">
                            {owner.kind}/{owner.name}
                          </span>
                          {owner.controller && (
                            <Badge variant="outline" className="ml-2">
                              Controller
                            </Badge>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </Card>

                {/* Status */}
                {description.status && Object.keys(description.status).length > 0 && (
                  <Card className="p-4">
                    <div className="flex items-center justify-between mb-3">
                      <h4 className="font-semibold">Status</h4>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleCopy(JSON.stringify(description.status, null, 2))}
                      >
                        <Copy className="h-4 w-4" />
                      </Button>
                    </div>
                    <pre className="text-xs font-mono bg-muted p-3 rounded overflow-auto max-h-60">
                      {JSON.stringify(description.status, null, 2)}
                    </pre>
                  </Card>
                )}
              </TabsContent>

              <TabsContent value="conditions" className="mt-0">
                {description.conditions && description.conditions.length > 0 ? (
                  <div className="space-y-2">
                    {description.conditions.map((condition: K8sCondition, idx: number) => (
                      <Card key={idx} className="p-4">
                        <div className="flex items-start gap-3">
                          {getConditionIcon(condition.status)}
                          <div className="flex-1">
                            <div className="flex items-center gap-2 mb-1">
                              <span className="font-semibold">{condition.type}</span>
                              <Badge variant={condition.status === 'True' ? 'default' : 'secondary'}>
                                {condition.status}
                              </Badge>
                            </div>
                            {condition.reason && (
                              <div className="text-sm text-muted-foreground">
                                <span className="font-medium">Reason:</span> {condition.reason}
                              </div>
                            )}
                            {condition.message && (
                              <div className="text-sm text-muted-foreground mt-1">
                                {condition.message}
                              </div>
                            )}
                            {condition.last_transition_time && (
                              <div className="text-xs text-muted-foreground mt-2">
                                Last transition: {formatTimestamp(condition.last_transition_time)}
                              </div>
                            )}
                          </div>
                        </div>
                      </Card>
                    ))}
                  </div>
                ) : (
                  <Card className="p-8 text-center text-muted-foreground">
                    No conditions available for this resource
                  </Card>
                )}
              </TabsContent>

              <TabsContent value="events" className="mt-0">
                {description.events && description.events.length > 0 ? (
                  <div className="space-y-2">
                    {description.events.map((event: K8sEvent, idx: number) => (
                      <Card key={idx} className="p-4">
                        <div className="flex items-start gap-3">
                          <Badge variant={getEventTypeColor(event.type)}>
                            {event.type}
                          </Badge>
                          <div className="flex-1">
                            <div className="flex items-center gap-2 mb-1">
                              <span className="font-semibold">{event.reason}</span>
                              {(event.count ?? 0) > 1 && (
                                <Badge variant="outline">{event.count}x</Badge>
                              )}
                            </div>
                            <div className="text-sm text-muted-foreground">{event.message}</div>
                            <div className="text-xs text-muted-foreground mt-2 flex gap-4">
                              <span>Source: {event.source ?? 'unknown'}</span>
                              <span>Last: {formatTimestamp(event.last_timestamp ?? null)}</span>
                            </div>
                          </div>
                        </div>
                      </Card>
                    ))}
                  </div>
                ) : (
                  <Card className="p-8 text-center text-muted-foreground">
                    No events found for this resource
                  </Card>
                )}
              </TabsContent>

              <TabsContent value="relationships" className="mt-0 space-y-4">
                {/* Owned Resources */}
                {description.relationships?.owned && description.relationships.owned.length > 0 && (
                  <Card className="p-4">
                    <h4 className="font-semibold mb-3">Owned Resources</h4>
                    <div className="space-y-2">
                      {description.relationships.owned.map((rel: K8sResourceRelationship, idx: number) => (
                        <div key={idx} className="text-sm font-mono">
                          {rel.kind}/{rel.name}
                        </div>
                      ))}
                    </div>
                  </Card>
                )}

                {/* Related Resources */}
                {description.relationships?.related && description.relationships.related.length > 0 && (
                  <Card className="p-4">
                    <h4 className="font-semibold mb-3">Related Resources</h4>
                    <div className="space-y-2">
                      {description.relationships.related.map((rel: K8sResourceRelationship, idx: number) => (
                        <div key={idx} className="text-sm font-mono">
                          {rel.kind}/{rel.name}
                        </div>
                      ))}
                    </div>
                  </Card>
                )}

                {!description.relationships?.owned?.length &&
                  !description.relationships?.related?.length && (
                    <Card className="p-8 text-center text-muted-foreground">
                      No relationships found for this resource
                    </Card>
                  )}
              </TabsContent>

              <TabsContent value="raw" className="mt-0">
                <Card className="p-4">
                  <div className="flex items-center justify-between mb-3">
                    <h4 className="font-semibold">Raw Description Data</h4>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleCopy(JSON.stringify(description, null, 2))}
                    >
                      <Copy className="h-4 w-4 mr-2" />
                      Copy All
                    </Button>
                  </div>
                  <pre className="text-xs font-mono bg-muted p-3 rounded overflow-auto max-h-[500px]">
                    {JSON.stringify(description, null, 2)}
                  </pre>
                </Card>
              </TabsContent>
            </div>
          </Tabs>
        ) : (
          <div className="flex items-center justify-center py-12 text-muted-foreground">
            Failed to load resource description
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
