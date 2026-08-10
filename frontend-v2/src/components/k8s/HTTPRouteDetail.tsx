/**
 * HTTPRoute Detail Component
 *
 * Displays HTTPRoute resource details with tabs:
 * - Summary: Parent gateway, hostnames, namespace
 * - Rules: Routing rules with path matching and backends
 * - Status: Accepted conditions and route status
 */

import { Badge } from '@/components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Route, Server, CheckCircle2, XCircle, AlertCircle, Clock, Globe } from 'lucide-react';
import type { K8sResource, K8sCondition, K8sGatewayRef, K8sHTTPRouteRule, K8sHTTPRouteMatch, K8sBackendRef } from '@/types';

interface HTTPRouteDetailProps {
  resource: K8sResource;
}

type BadgeVariant = 'success' | 'info' | 'warning' | 'muted';

export function HTTPRouteDetail({ resource }: HTTPRouteDetailProps) {
  const spec = resource.spec || {};
  const status = resource.status || {};
  const rules: K8sHTTPRouteRule[] = spec.rules || [];
  const hostnames: string[] = spec.hostnames || [];
  const parentRefs: K8sGatewayRef[] = spec.parentRefs || [];
  const conditions: K8sCondition[] = status.conditions || [];

  const getPathTypeVariant = (type: string): BadgeVariant => {
    switch (type) {
      case 'PathPrefix':
        return 'info';
      case 'Exact':
        return 'success';
      case 'RegularExpression':
        return 'warning';
      default:
        return 'muted';
    }
  };

  const getConditionIcon = (status: string) => {
    switch (status?.toLowerCase()) {
      case 'true':
        return <CheckCircle2 className="h-4 w-4 text-success" />;
      case 'false':
        return <XCircle className="h-4 w-4 text-destructive" />;
      case 'unknown':
        return <AlertCircle className="h-4 w-4 text-warning" />;
      default:
        return <Clock className="h-4 w-4 text-muted-foreground" />;
    }
  };

  const getConditionColor = (status: string) => {
    switch (status?.toLowerCase()) {
      case 'true':
        return 'text-success';
      case 'false':
        return 'text-destructive';
      case 'unknown':
        return 'text-warning';
      default:
        return 'text-muted-foreground';
    }
  };

  return (
    <div className="space-y-4">
      <Tabs defaultValue="summary" className="w-full">
        <TabsList className="grid w-full grid-cols-3">
          <TabsTrigger value="summary">Summary</TabsTrigger>
          <TabsTrigger value="rules">Rules ({rules.length})</TabsTrigger>
          <TabsTrigger value="status">Status</TabsTrigger>
        </TabsList>

        {/* Summary Tab */}
        <TabsContent value="summary" className="space-y-3">
          {/* Parent Gateway */}
          {parentRefs.length > 0 && (
            <div className="p-3 rounded-lg bg-muted/50">
              <h4 className="text-xs font-semibold mb-2">Parent Gateway</h4>
              <div className="space-y-1.5">
                {parentRefs.map((ref: K8sGatewayRef, idx: number) => (
                  <div key={idx} className="flex items-center gap-2">
                    <Globe className="h-3.5 w-3.5 text-primary" />
                    <code className="text-sm font-mono text-foreground/80">
                      {ref.name}
                    </code>
                    {ref.namespace && ref.namespace !== resource.metadata?.namespace && (
                      <Badge variant="outline" className="text-[10px]">
                        {ref.namespace}
                      </Badge>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Hostnames */}
          {hostnames.length > 0 && (
            <div className="p-3 rounded-lg bg-muted/50">
              <h4 className="text-xs font-semibold mb-2">Hostnames</h4>
              <div className="flex flex-wrap gap-1.5">
                {hostnames.map((hostname: string, idx: number) => (
                  <code
                    key={idx}
                    className="text-xs px-2 py-1 rounded font-mono bg-muted text-foreground/80"
                  >
                    {hostname}
                  </code>
                ))}
              </div>
            </div>
          )}

          {/* Summary Stats */}
          <div className="p-3 rounded-lg bg-muted/50">
            <h4 className="text-xs font-semibold mb-2">Summary</h4>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div>
                <span className="text-muted-foreground">Rules:</span>
                <span className="ml-2 font-medium text-foreground/80">
                  {rules.length}
                </span>
              </div>
              <div>
                <span className="text-muted-foreground">Backends:</span>
                <span className="ml-2 font-medium text-foreground/80">
                  {rules.reduce((sum: number, rule: K8sHTTPRouteRule) => sum + (rule.backendRefs?.length || 0), 0)}
                </span>
              </div>
            </div>
          </div>
        </TabsContent>

        {/* Rules Tab */}
        <TabsContent value="rules" className="space-y-3">
          {rules.length === 0 ? (
            <div className="p-6 text-center rounded-lg bg-muted/50">
              <Route className="h-8 w-8 mx-auto mb-2 opacity-50" />
              <p className="text-xs text-muted-foreground">No routing rules configured</p>
            </div>
          ) : (
            rules.map((rule: K8sHTTPRouteRule, ruleIdx: number) => (
              <div
                key={ruleIdx}
                className="p-3 rounded-lg border bg-muted/50 border-border"
              >
                <div className="flex items-center gap-2 mb-3">
                  <Route className="h-4 w-4 text-primary" />
                  <span className="font-medium text-sm">Rule {ruleIdx + 1}</span>
                </div>

                {/* Path Matches */}
                {rule.matches && rule.matches.length > 0 && (
                  <div className="mb-3">
                    <h5 className="text-xs font-semibold mb-2 text-muted-foreground">Path Matching</h5>
                    <div className="space-y-1.5">
                      {rule.matches.map((match: K8sHTTPRouteMatch, matchIdx: number) => (
                        <div key={matchIdx}>
                          {match.path && (
                            <div className="flex items-center gap-2">
                              <Badge variant={getPathTypeVariant(match.path.type)} className="text-[10px]">
                                {match.path.type}
                              </Badge>
                              <code className="text-xs font-mono text-foreground/80">
                                {match.path.value}
                              </code>
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Backend References */}
                {rule.backendRefs && rule.backendRefs.length > 0 && (
                  <div>
                    <h5 className="text-xs font-semibold mb-2 text-muted-foreground">Backends</h5>
                    <div className="space-y-2">
                      {rule.backendRefs.map((backend: K8sBackendRef, backendIdx: number) => {
                        const isCrossNs = backend.namespace && backend.namespace !== resource.metadata?.namespace;
                        const isCustomKind = backend.kind && backend.kind !== 'Service';
                        return (
                          <div
                            key={backendIdx}
                            className="p-2 rounded border bg-card border-border"
                          >
                            <div className="flex items-center justify-between">
                              <div className="flex items-center gap-2">
                                <Server className="h-3.5 w-3.5 text-primary" />
                                <code className="text-xs font-mono text-foreground/80">
                                  {backend.name}
                                </code>
                                {isCrossNs && (
                                  <Badge variant="warning" className="text-[10px]">
                                    ns:{backend.namespace}
                                  </Badge>
                                )}
                                {isCustomKind && (
                                  <Badge variant="outline" className="text-[10px]">
                                    {backend.kind}
                                  </Badge>
                                )}
                              </div>
                              <div className="flex items-center gap-2 text-xs">
                                <span className="text-muted-foreground">Port:</span>
                                <code className="font-mono text-foreground/80">
                                  {backend.port}
                                </code>
                                {backend.weight && (
                                  <>
                                    <span className="text-muted-foreground ml-2">Weight:</span>
                                    <code className="font-mono text-foreground/80">
                                      {backend.weight}
                                    </code>
                                  </>
                                )}
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}

                {/* Traffic Summary */}
                {rule.matches && rule.backendRefs && (
                  <div className="mt-3 p-2 rounded text-xs bg-card text-muted-foreground">
                    <span className="font-medium">Traffic:</span> {rule.matches[0]?.path?.value || '/'} →{' '}
                    {rule.backendRefs.map((b: K8sBackendRef) => b.name).join(', ')}
                  </div>
                )}
              </div>
            ))
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
            conditions.map((condition: K8sCondition, idx: number) => (
              <div
                key={idx}
                className="p-3 rounded-lg border bg-muted/50 border-border"
              >
                <div className="flex items-center gap-2 mb-2">
                  {getConditionIcon(condition.status)}
                  <span className={`font-medium text-sm ${getConditionColor(condition.status)}`}>
                    {condition.type}
                  </span>
                </div>
                <div className="space-y-1 text-xs">
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Status:</span>
                    <span className={`font-medium ${getConditionColor(condition.status)}`}>
                      {condition.status}
                    </span>
                  </div>
                  {condition.reason && (
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Reason:</span>
                      <span className="text-foreground/80">{condition.reason}</span>
                    </div>
                  )}
                  {condition.message && (
                    <div className="mt-2">
                      <p className="text-xs text-muted-foreground">
                        {condition.message}
                      </p>
                    </div>
                  )}
                </div>
              </div>
            ))
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
