import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Shield, Network, AlertCircle, ChevronDown, ChevronRight, Lock, Unlock, ArrowRightLeft, CheckCircle2, XCircle } from 'lucide-react';
import { useF5PolicyGatewayAssociations } from '@/hooks/useK8s';
import { cn } from '@/lib/utils';
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
import type { F5PolicyGatewayAssociation, F5FirewallRule } from '@/types';
import { ErrorState } from '@/components/ui/error-state';
import { parseApiError } from '@/lib/error-handler';

interface F5BNKPolicyViewerProps {
  clusterId: number;
  namespace?: string;
  onSelectResource?: (sel: { kind: string; name: string; namespace: string }) => void;
}

type ResourceSelector = (sel: { kind: string; name: string; namespace: string }) => void;

function PolicyStatusBadge({ status }: { status?: { resolved: boolean; programmed: boolean } }) {
  if (!status) return null;
  const ready = status.resolved && status.programmed;
  const pending = !status.resolved && !status.programmed;
  return (
    <Badge
      variant={ready ? 'success' : pending ? 'warning' : 'secondary'}
      className="text-[10px] gap-1"
    >
      {ready ? <CheckCircle2 className="h-3 w-3" /> : <XCircle className="h-3 w-3" />}
      {ready ? 'Ready' : status.programmed ? 'Programmed' : status.resolved ? 'Resolved' : 'Pending'}
    </Badge>
  );
}

function ClickableName({
  name,
  kind,
  namespace,
  onSelect,
  className,
}: {
  name: string;
  kind: string;
  namespace: string;
  onSelect?: ResourceSelector;
  className?: string;
}) {
  if (!onSelect) {
    return <span className={cn('font-medium text-xs', className)}>{name}</span>;
  }
  return (
    <button
      type="button"
      onClick={() => onSelect({ kind, name, namespace })}
      className={cn(
        'font-medium text-xs hover:underline text-left text-primary hover:text-primary/80',
        className,
      )}
    >
      {name}
    </button>
  );
}

/** Resolved addresses/ports for a rule endpoint, plus clickable provenance links */
function RuleEndpointCell({
  addresses,
  ports,
  addressLists,
  portLists,
  namespace,
  onSelectResource,
}: {
  addresses?: string[];
  ports?: number[];
  addressLists?: string[];
  portLists?: string[];
  namespace: string;
  onSelectResource?: ResourceSelector;
}) {
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap gap-1">
        {addresses && addresses.length > 0 ? (
          addresses.map((addr) => (
            <Badge key={addr} variant="outline" className="font-mono text-[10px] py-0">
              {addr}
            </Badge>
          ))
        ) : (
          <span className="text-xs text-muted-foreground">any</span>
        )}
      </div>
      {addressLists && addressLists.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 mt-1 text-xs text-muted-foreground">
          <span>from:</span>
          {addressLists.map((name) => (
            <ClickableName
              key={name}
              name={name}
              kind="F5BigCneAddresslist"
              namespace={namespace}
              onSelect={onSelectResource}
            />
          ))}
        </div>
      )}
      {ports && ports.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-1">
          {ports.map((port) => (
            <Badge key={port} variant="secondary" className="text-[10px] py-0">
              {port}
            </Badge>
          ))}
        </div>
      )}
      {portLists && portLists.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 mt-1 text-xs text-muted-foreground">
          <span>from:</span>
          {portLists.map((name) => (
            <ClickableName
              key={name}
              name={name}
              kind="F5BigCnePortlist"
              namespace={namespace}
              onSelect={onSelectResource}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export function F5BNKPolicyViewer({ clusterId, namespace, onSelectResource }: F5BNKPolicyViewerProps) {
  const navigate = useNavigate();
  const [expandedRules, setExpandedRules] = useState<Set<string>>(new Set());

  const { data, isLoading, error } = useF5PolicyGatewayAssociations(
    clusterId,
    namespace ? { namespace } : undefined,
    { pollingEnabled: true, enabled: !!clusterId }
  );

  const ruleHitMap = useMemo(() => {
    const map = new Map<string, number>();
    const stats = data?.trafficStats;
    if (!stats?.available) return map;
    for (const rule of stats.firewallRules || []) {
      const key = `${rule.namespace}/${rule.policyName}/${rule.ruleName}`;
      map.set(key, rule.hitCount || 0);
    }
    return map;
  }, [data?.trafficStats]);

  const getRuleHits = (policyName: string, ruleName: string, associationNs: string) => {
    const key = `${associationNs}/${policyName}/${ruleName}`;
    return ruleHitMap.get(key);
  };

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
            F5 BNK Policy Associations
          </CardTitle>
          <CardDescription>
            No F5 BNK policy associations found in this cluster
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
                F5 BNK policies associate firewall rules with Gateway listeners or egress traffic.
              </p>
            )}
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Shield className="h-5 w-5" />
          F5 BNK Policy Associations
        </CardTitle>
        <CardDescription>
          Viewing {associations.length} policy association{associations.length !== 1 ? 's' : ''} (gateway & egress)
          {namespace && ` in namespace: ${namespace}`}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="space-y-4">
          {associations.map((association: F5PolicyGatewayAssociation, index: number) => {
            const isEgress = association.kind === 'egress';
            const associationKey = isEgress
              ? `${association.namespace}-egress-${association.egress_name}-${index}`
              : `${association.namespace}-${association.gateway_name}-${association.listener_name}-${index}`;
            const isExpanded = expandedRules.has(associationKey);

            return (
              <Card key={associationKey} className="border-2">
                <CardHeader className="pb-3">
                  <div className="flex items-start justify-between">
                    <div className="space-y-1 flex-1">
                      <div className="flex items-center gap-2">
                        {isEgress ? (
                          <ArrowRightLeft className="h-5 w-5 text-info" />
                        ) : (
                          <Network className="h-5 w-5 text-info" />
                        )}
                        <CardTitle className="text-lg flex items-center gap-2 flex-wrap">
                          {isEgress
                            ? `Egress: ${association.egress_name}`
                            : `${association.gateway_name} / ${association.listener_name}`}
                          {isEgress ? (
                            <PolicyStatusBadge status={association.egress_status} />
                          ) : (
                            <PolicyStatusBadge status={association.bnk_policy_status} />
                          )}
                        </CardTitle>
                      </div>
                      <div className="text-muted-foreground text-sm flex flex-wrap gap-2 mt-2">
                          <Badge variant="outline">
                            Namespace: {association.namespace}
                          </Badge>
                          {isEgress ? (
                            <>
                              {association.snat_type && (
                                <Badge variant="outline">
                                  SNAT: {association.snat_type}
                                </Badge>
                              )}
                              {(association.captured_namespaces || []).map((ns) => (
                                <Badge key={ns} variant="secondary">
                                  ns: {ns}
                                </Badge>
                              ))}
                            </>
                          ) : (
                            <>
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
                            </>
                          )}
                        </div>
                      </div>
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={!onSelectResource}
                      onClick={() => onSelectResource?.({
                        kind: 'F5BigFwPolicy',
                        name: association.firewall_policy_name,
                        namespace: association.namespace,
                      })}
                    >
                      Open Policy
                    </Button>
                  </div>
                </CardHeader>
                <CardContent>
                  <div className="space-y-2">
                    {!isEgress && (
                      <div className="flex items-center gap-2">
                        <Shield className="h-4 w-4 text-muted-foreground" />
                        <span className="text-sm font-medium">BNK Policy:</span>
                        <code className="text-sm bg-muted px-2 py-1 rounded">
                          {association.bnk_policy_name}
                        </code>
                      </div>
                    )}
                    <div className="flex items-center gap-2">
                      <Lock className="h-4 w-4 text-muted-foreground" />
                      <span className="text-sm font-medium">Firewall Policy:</span>
                      <ClickableName
                        name={association.firewall_policy_name}
                        kind="F5BigFwPolicy"
                        namespace={association.namespace}
                        onSelect={onSelectResource}
                        className="text-sm"
                      />
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
                                  <TableHead>Hits</TableHead>
                                </TableRow>
                              </TableHeader>
                              <TableBody>
                                {association.rules.map((rule: F5FirewallRule, ruleIndex: number) => {
                                  const hits = getRuleHits(association.firewall_policy_name, rule.name, association.namespace);
                                  return (
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
                                        <RuleEndpointCell
                                          addresses={rule.source.addresses}
                                          ports={rule.source.ports}
                                          addressLists={rule.source.addressLists}
                                          portLists={rule.source.portLists}
                                          namespace={association.namespace}
                                          onSelectResource={onSelectResource}
                                        />
                                      </TableCell>
                                      <TableCell className="text-sm">
                                        <RuleEndpointCell
                                          addresses={rule.destination.addresses}
                                          ports={rule.destination.ports}
                                          addressLists={rule.destination.addressLists}
                                          portLists={rule.destination.portLists}
                                          namespace={association.namespace}
                                          onSelectResource={onSelectResource}
                                        />
                                      </TableCell>
                                      <TableCell>
                                        <Badge variant={rule.logging ? 'default' : 'outline'}>
                                          {rule.logging ? 'Enabled' : 'Disabled'}
                                        </Badge>
                                      </TableCell>
                                      <TableCell>
                                        {hits !== undefined ? (
                                          <Badge variant={hits > 0 ? 'info' : 'secondary'} className="text-xs">
                                            {hits}
                                          </Badge>
                                        ) : (
                                          <span className="text-xs text-muted-foreground">-</span>
                                        )}
                                      </TableCell>
                                    </TableRow>
                                  );
                                })}
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
  );
}
