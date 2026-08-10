import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Shield, Network, AlertCircle, ChevronDown, ChevronRight, Lock, Unlock } from 'lucide-react';
import { useF5PolicyGatewayAssociations } from '@/hooks/useK8s';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type { F5PolicyGatewayAssociation, F5FirewallRule } from '@/types';
import { ErrorState } from '@/components/ui/error-state';
import { parseApiError } from '@/lib/error-handler';

interface F5BNKPolicyViewerProps {
  clusterId: number;
  namespace?: string;
}

export function F5BNKPolicyViewer({ clusterId, namespace }: F5BNKPolicyViewerProps) {
  const navigate = useNavigate();
  const [selectedAssociation, setSelectedAssociation] = useState<F5PolicyGatewayAssociation | null>(null);
  const [expandedRules, setExpandedRules] = useState<Set<string>>(new Set());

  const { data, isLoading, error } = useF5PolicyGatewayAssociations(
    clusterId,
    namespace ? { namespace } : undefined,
    { pollingEnabled: true, enabled: !!clusterId }
  );

  const toggleRuleExpansion = (associationKey: string) => {
    const newExpanded = new Set(expandedRules);
    if (newExpanded.has(associationKey)) {
      newExpanded.delete(associationKey);
    } else {
      newExpanded.add(associationKey);
    }
    setExpandedRules(newExpanded);
  };

  const getActionIcon = (action?: string | null) => {
    const actionLower = action?.toLowerCase();
    if (actionLower === 'allow' || actionLower === 'accept') {
      return <Unlock className="h-4 w-4 text-success" />;
    }
    return <Lock className="h-4 w-4 text-destructive" />;
  };

  const getActionBadge = (action?: string | null) => {
    const actionLower = action?.toLowerCase();
    const variant = actionLower === 'allow' || actionLower === 'accept' ? 'default' : 'destructive';
    return (
      <Badge variant={variant} className="gap-1">
        {getActionIcon(action)}
        {action || 'unknown'}
      </Badge>
    );
  };

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (error) {
    const parsedPolicyError = parseApiError(error);
    const policyErrorRoute = parsedPolicyError.action?.route;
    return (
      <ErrorState
        error={error}
        size="sm"
        {...(policyErrorRoute ? {
          secondaryAction: {
            label: parsedPolicyError.action!.label,
            onClick: () => navigate(policyErrorRoute),
          },
        } : {})}
      />
    );
  }

  const associations = data?.associations || [];
  const apiError = data?.error;
  const apiInfo = data?.info;

  // Show RBAC/permissions error if present
  if (apiError && associations.length === 0) {
    return (
      <Card className="border-warning/20 bg-warning/10">
        <CardHeader>
          <CardTitle className="text-warning flex items-center gap-2">
            <AlertCircle className="h-5 w-5" />
            Permissions Required
          </CardTitle>
          <CardDescription className="text-warning/80">
            {apiError}
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  if (associations.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Shield className="h-5 w-5" />
            F5 BNK Policy-Gateway Associations
          </CardTitle>
          <CardDescription>
            No F5 BNK policy-gateway associations found in this cluster
            {namespace && ` (namespace: ${namespace})`}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="text-center py-8 text-muted-foreground">
            <Shield className="h-12 w-12 mx-auto mb-4 opacity-20" />
            <p>This cluster doesn't have any F5 BNK security policies configured.</p>
            {apiInfo ? (
              <p className="text-sm mt-2">{apiInfo}</p>
            ) : (
              <p className="text-sm mt-2">
                F5 BNK policies associate firewall rules with Gateway listeners.
              </p>
            )}
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Shield className="h-5 w-5" />
            F5 BNK Policy-Gateway Associations
          </CardTitle>
          <CardDescription>
            Viewing {associations.length} policy-gateway association{associations.length !== 1 ? 's' : ''}
            {namespace && ` in namespace: ${namespace}`}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {associations.map((association: F5PolicyGatewayAssociation, index: number) => {
              const associationKey = `${association.namespace}-${association.gateway_name}-${association.listener_name}-${index}`;
              const isExpanded = expandedRules.has(associationKey);

              return (
                <Card key={associationKey} className="border-2">
                  <CardHeader className="pb-3">
                    <div className="flex items-start justify-between">
                      <div className="space-y-1 flex-1">
                        <div className="flex items-center gap-2">
                          <Network className="h-5 w-5 text-info" />
                          <CardTitle className="text-lg">
                            {association.gateway_name} / {association.listener_name}
                          </CardTitle>
                        </div>
                        <CardDescription>
                          <div className="flex flex-wrap gap-2 mt-2">
                            <Badge variant="outline">
                              Namespace: {association.namespace}
                            </Badge>
                            {association.gateway_ip && (
                              <Badge variant="outline">
                                IP: {association.gateway_ip}
                              </Badge>
                            )}
                            {association.port && (
                              <Badge variant="outline">
                                Port: {association.port}
                              </Badge>
                            )}
                            {association.protocol && (
                              <Badge variant="outline">
                                Protocol: {association.protocol}
                              </Badge>
                            )}
                          </div>
                        </CardDescription>
                      </div>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setSelectedAssociation(association)}
                      >
                        View Details
                      </Button>
                    </div>
                  </CardHeader>
                  <CardContent>
                    <div className="space-y-2">
                      <div className="flex items-center gap-2">
                        <Shield className="h-4 w-4 text-muted-foreground" />
                        <span className="text-sm font-medium">BNK Policy:</span>
                        <code className="text-sm bg-muted px-2 py-1 rounded">
                          {association.bnk_policy_name}
                        </code>
                      </div>
                      <div className="flex items-center gap-2">
                        <Lock className="h-4 w-4 text-muted-foreground" />
                        <span className="text-sm font-medium">Firewall Policy:</span>
                        <code className="text-sm bg-muted px-2 py-1 rounded">
                          {association.firewall_policy_name}
                        </code>
                        {association.rules_count !== undefined && (
                          <Badge variant="secondary">
                            {association.rules_count} rule{association.rules_count !== 1 ? 's' : ''}
                          </Badge>
                        )}
                      </div>

                      {association.rules && association.rules.length > 0 && (
                        <div className="mt-4">
                          <Button
                            variant="ghost"
                            size="sm"
                            className="w-full justify-start"
                            onClick={() => toggleRuleExpansion(associationKey)}
                          >
                            {isExpanded ? (
                              <ChevronDown className="h-4 w-4 mr-2" />
                            ) : (
                              <ChevronRight className="h-4 w-4 mr-2" />
                            )}
                            {isExpanded ? 'Hide' : 'Show'} Firewall Rules
                          </Button>

                          {isExpanded && (
                            <div className="mt-2 border rounded-lg overflow-hidden">
                              <Table>
                                <TableHeader>
                                  <TableRow>
                                    <TableHead>Rule</TableHead>
                                    <TableHead>Action</TableHead>
                                    <TableHead>Protocol</TableHead>
                                    <TableHead>Source</TableHead>
                                    <TableHead>Destination</TableHead>
                                    <TableHead>Logging</TableHead>
                                  </TableRow>
                                </TableHeader>
                                <TableBody>
                                  {association.rules.map((rule: F5FirewallRule, ruleIndex: number) => (
                                    <TableRow key={ruleIndex}>
                                      <TableCell className="font-mono text-sm">
                                        {rule.name}
                                      </TableCell>
                                      <TableCell>
                                        {getActionBadge(rule.action)}
                                      </TableCell>
                                      <TableCell>
                                        <Badge variant="outline">{rule.ipProtocol}</Badge>
                                      </TableCell>
                                      <TableCell className="text-sm">
                                        <div>
                                          {rule.source.addresses?.join(', ') || 'any'}
                                        </div>
                                        {rule.source.ports && rule.source.ports.length > 0 && (
                                          <div className="text-xs text-muted-foreground">
                                            Ports: {rule.source.ports.join(', ')}
                                          </div>
                                        )}
                                      </TableCell>
                                      <TableCell className="text-sm">
                                        <div>
                                          {rule.destination.addresses?.join(', ') || 'any'}
                                        </div>
                                        {rule.destination.ports && rule.destination.ports.length > 0 && (
                                          <div className="text-xs text-muted-foreground">
                                            Ports: {rule.destination.ports.join(', ')}
                                          </div>
                                        )}
                                      </TableCell>
                                      <TableCell>
                                        <Badge variant={rule.logging ? 'default' : 'outline'}>
                                          {rule.logging ? 'Enabled' : 'Disabled'}
                                        </Badge>
                                      </TableCell>
                                    </TableRow>
                                  ))}
                                </TableBody>
                              </Table>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {/* Details Dialog */}
      <Dialog open={!!selectedAssociation} onOpenChange={() => setSelectedAssociation(null)}>
        <DialogContent className="max-w-3xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Shield className="h-5 w-5" />
              Policy-Gateway Association Details
            </DialogTitle>
            <DialogDescription>
              {selectedAssociation?.gateway_name} / {selectedAssociation?.listener_name}
            </DialogDescription>
          </DialogHeader>

          {selectedAssociation && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <h4 className="text-sm font-medium mb-2">Gateway Information</h4>
                  <dl className="space-y-1 text-sm">
                    <div>
                      <dt className="text-muted-foreground inline">Name: </dt>
                      <dd className="inline font-mono">{selectedAssociation.gateway_name}</dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground inline">Listener: </dt>
                      <dd className="inline font-mono">{selectedAssociation.listener_name}</dd>
                    </div>
                    {selectedAssociation.gateway_ip && (
                      <div>
                        <dt className="text-muted-foreground inline">IP: </dt>
                        <dd className="inline font-mono">{selectedAssociation.gateway_ip}</dd>
                      </div>
                    )}
                    {selectedAssociation.port && (
                      <div>
                        <dt className="text-muted-foreground inline">Port: </dt>
                        <dd className="inline">{selectedAssociation.port}</dd>
                      </div>
                    )}
                    {selectedAssociation.protocol && (
                      <div>
                        <dt className="text-muted-foreground inline">Protocol: </dt>
                        <dd className="inline">{selectedAssociation.protocol}</dd>
                      </div>
                    )}
                  </dl>
                </div>

                <div>
                  <h4 className="text-sm font-medium mb-2">Policy Information</h4>
                  <dl className="space-y-1 text-sm">
                    <div>
                      <dt className="text-muted-foreground inline">Namespace: </dt>
                      <dd className="inline font-mono">{selectedAssociation.namespace}</dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground inline">BNK Policy: </dt>
                      <dd className="inline font-mono">{selectedAssociation.bnk_policy_name}</dd>
                    </div>
                    <div>
                      <dt className="text-muted-foreground inline">Firewall Policy: </dt>
                      <dd className="inline font-mono">{selectedAssociation.firewall_policy_name}</dd>
                    </div>
                    {selectedAssociation.rules_count !== undefined && (
                      <div>
                        <dt className="text-muted-foreground inline">Rules: </dt>
                        <dd className="inline">{selectedAssociation.rules_count}</dd>
                      </div>
                    )}
                  </dl>
                </div>
              </div>

              {selectedAssociation.rules && selectedAssociation.rules.length > 0 && (
                <div>
                  <h4 className="text-sm font-medium mb-2">Firewall Rules</h4>
                  <div className="border rounded-lg overflow-hidden">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Rule</TableHead>
                          <TableHead>Action</TableHead>
                          <TableHead>Protocol</TableHead>
                          <TableHead>Source</TableHead>
                          <TableHead>Destination</TableHead>
                          <TableHead>Logging</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {selectedAssociation.rules.map((rule, ruleIndex) => (
                          <TableRow key={ruleIndex}>
                            <TableCell className="font-mono text-sm">
                              {rule.name}
                            </TableCell>
                            <TableCell>
                              {getActionBadge(rule.action)}
                            </TableCell>
                            <TableCell>
                              <Badge variant="outline">{rule.ipProtocol}</Badge>
                            </TableCell>
                            <TableCell className="text-sm">
                              <div>{rule.source.addresses?.join(', ') || 'any'}</div>
                              {rule.source.ports && rule.source.ports.length > 0 && (
                                <div className="text-xs text-muted-foreground">
                                  Ports: {rule.source.ports.join(', ')}
                                </div>
                              )}
                            </TableCell>
                            <TableCell className="text-sm">
                              <div>{rule.destination.addresses?.join(', ') || 'any'}</div>
                              {rule.destination.ports && rule.destination.ports.length > 0 && (
                                <div className="text-xs text-muted-foreground">
                                  Ports: {rule.destination.ports.join(', ')}
                                </div>
                              )}
                            </TableCell>
                            <TableCell>
                              <Badge variant={rule.logging ? 'default' : 'outline'}>
                                {rule.logging ? 'Enabled' : 'Disabled'}
                              </Badge>
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                </div>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
