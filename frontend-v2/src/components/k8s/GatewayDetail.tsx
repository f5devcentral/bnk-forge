/**
 * Gateway Detail Component
 *
 * Displays Gateway API Gateway resource details with tabs:
 * - Summary: Gateway class, addresses, status
 * - Listeners: List of listeners with protocols and ports
 * - Status: Conditions and events
 */

import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Globe, Lock, Network, AlertCircle } from 'lucide-react';
import { useMemo } from 'react';
import { ConditionsList } from '@/components/k8s/ConditionsList';
import type { K8sResource, K8sCondition, K8sGatewayListener, K8sGatewayAddress } from '@/types';

interface GatewayDetailProps {
  resource: K8sResource;
}

type BadgeVariant = 'success' | 'info' | 'warning' | 'muted' | 'destructive';

export function GatewayDetail({ resource }: GatewayDetailProps) {
  const spec = resource.spec || {};
  const status = resource.status || {};
  const listeners: K8sGatewayListener[] = spec.listeners || [];
  const addresses: K8sGatewayAddress[] = status.addresses || [];
  const conditions: K8sCondition[] = status.conditions || [];

  const getListenerIcon = (protocol: string) => {
    switch (protocol?.toUpperCase()) {
      case 'HTTPS':
      case 'TLS':
        return Lock;
      case 'HTTP':
        return Globe;
      case 'TCP':
      case 'UDP':
        return Network;
      default:
        return Globe;
    }
  };

  const getListenerVariant = (protocol: string): BadgeVariant => {
    switch (protocol?.toUpperCase()) {
      case 'HTTPS':
      case 'TLS':
        return 'success';
      case 'HTTP':
        return 'info';
      case 'TCP':
      case 'UDP':
        return 'warning';
      default:
        return 'muted';
    }
  };

  const listenerStatusMap = useMemo(() => {
    const map = new Map<string, { attachedRoutes?: number; conditions: K8sCondition[] }>();
    const listenersStatus = (status.listeners || []) as Array<{
      name: string;
      attachedRoutes?: number;
      conditions?: K8sCondition[];
    }>;
    for (const ls of listenersStatus) {
      map.set(ls.name, {
        attachedRoutes: ls.attachedRoutes,
        conditions: ls.conditions || [],
      });
    }
    return map;
  }, [status.listeners]);

  return (
    <div className="space-y-4">
      <Tabs defaultValue="summary" className="w-full">
        <TabsList className="grid w-full grid-cols-3">
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="listeners">Listeners ({listeners.length})</TabsTrigger>
          <TabsTrigger value="status">Status</TabsTrigger>
        </TabsList>

        {/* Summary Tab */}
        <TabsContent value="summary" className="space-y-3">
          {/* Gateway Class */}
          <div className="p-3 rounded-lg bg-muted/50">
            <h4 className="text-xs font-semibold mb-2">Gateway Class</h4>
            <code className="text-sm font-mono text-foreground/80">
              {spec.gatewayClassName || 'Not specified'}
            </code>
          </div>

          {/* Addresses */}
          {addresses.length > 0 && (
            <div className="p-3 rounded-lg bg-muted/50">
              <h4 className="text-xs font-semibold mb-2">Addresses</h4>
              <div className="space-y-1.5">
                {addresses.map((addr: K8sGatewayAddress, idx: number) => (
                  <div key={idx} className="flex items-center gap-2">
                    <Badge variant="outline" className="text-[10px]">
                      {addr.type || 'IP'}
                    </Badge>
                    <code className="text-xs font-mono text-foreground/80">
                      {addr.value}
                    </code>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Summary Stats */}
          <div className="p-3 rounded-lg bg-muted/50">
            <h4 className="text-xs font-semibold mb-2">Summary</h4>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div>
                <span className="text-muted-foreground">Listeners:</span>
                <span className="ml-2 font-medium text-foreground/80">
                  {listeners.length}
                </span>
              </div>
              <div>
                <span className="text-muted-foreground">Namespace:</span>
                <span className="ml-2 font-medium text-foreground/80">
                  {resource.metadata?.namespace}
                </span>
              </div>
            </div>
          </div>
        </TabsContent>

        {/* Listeners Tab */}
        <TabsContent value="listeners" className="space-y-3">
          {listeners.length === 0 ? (
            <div className="p-6 text-center rounded-lg bg-muted/50">
              <Globe className="h-8 w-8 mx-auto mb-2 opacity-50" />
              <p className="text-xs text-muted-foreground">No listeners configured</p>
            </div>
          ) : (
            listeners.map((listener: K8sGatewayListener, idx: number) => {
              const Icon = getListenerIcon(listener.protocol);
              const lsStatus = listenerStatusMap.get(listener.name);
              return (
                <div
                  key={idx}
                  className="p-3 rounded-lg border bg-muted/50 border-border"
                >
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <Icon className="h-4 w-4" />
                      <span className="font-medium text-sm">{listener.name}</span>
                    </div>
                    <Badge variant={getListenerVariant(listener.protocol)} className="text-[10px]">
                      {listener.protocol}
                    </Badge>
                  </div>
                  <div className="space-y-1 text-xs">
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Port:</span>
                      <code className="font-mono text-foreground/80">
                        {listener.port}
                      </code>
                    </div>
                    {listener.hostname && (
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">Hostname:</span>
                        <code className="font-mono text-foreground/80">
                          {listener.hostname}
                        </code>
                      </div>
                    )}
                    {listener.tls && (
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">TLS:</span>
                        <Badge variant="success" className="text-[10px]">
                          Enabled
                        </Badge>
                      </div>
                    )}
                    {lsStatus?.attachedRoutes !== undefined && (
                      <div className="flex justify-between">
                        <span className="text-muted-foreground">Attached Routes:</span>
                        <span className="font-medium text-foreground/80">
                          {lsStatus.attachedRoutes}
                        </span>
                      </div>
                    )}
                  </div>
                  {lsStatus && lsStatus.conditions.length > 0 && (
                    <div className="mt-3 pt-3 border-t border-border">
                      <h5 className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground mb-2">
                        Conditions
                      </h5>
                      <ConditionsList conditions={lsStatus.conditions} />
                    </div>
                  )}
                </div>
              );
            })
          )}
        </TabsContent>

        {/* Status Tab */}
        <TabsContent value="status" className="space-y-3">
          {conditions.length === 0 ? (
            <div className="p-6 text-center rounded-lg bg-muted/50">
              <AlertCircle className="h-8 w-8 mx-auto mb-2 opacity-50" />
              <p className="text-xs text-muted-foreground">No status conditions available</p>
            </div>
          ) : (
            <ConditionsList conditions={conditions} />
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
