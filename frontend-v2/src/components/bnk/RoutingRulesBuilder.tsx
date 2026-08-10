/**
 * Routing Rules Builder Component for HTTPRoute Configuration
 *
 * Visual UI for configuring Gateway API routing rules - replaces JSON editing.
 * Features:
 * - Add/remove routing rules with cards
 * - Path matching (PathPrefix, Exact, RegularExpression)
 * - Backend service references (name + port)
 * - Optional filters (headers, URL rewrite)
 * - Traffic weight for load balancing
 */

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Card } from '@/components/ui/card';
import { Badge, type BadgeProps } from '@/components/ui/badge';
import { Plus, Trash2, Route, Server, Percent } from 'lucide-react';
import { cn } from '@/lib/utils';
import { validatePort, validateK8sResourceName } from '@/lib/validators';

export interface PathMatch {
  type: 'PathPrefix' | 'Exact' | 'RegularExpression';
  value: string;
}

export interface BackendRef {
  name: string;
  port: number;
  weight?: number;
}

export interface RoutingRule {
  matches: Array<{ path: PathMatch }>;
  backend_refs: BackendRef[];
}

interface RoutingRulesBuilderProps {
  value: RoutingRule[];
  onChange: (rules: RoutingRule[]) => void;
}

const PATH_MATCH_TYPES = [
  { value: 'PathPrefix', label: 'Path Prefix', example: '/api' },
  { value: 'Exact', label: 'Exact Path', example: '/api/users' },
  { value: 'RegularExpression', label: 'Regex', example: '^/api/.*' },
];

function pathTypeBadgeVariant(type: string): BadgeProps['variant'] {
  switch (type) {
    case 'PathPrefix':
      return 'info';
    case 'Exact':
      return 'success';
    case 'RegularExpression':
      return 'secondary';
    default:
      return 'outline';
  }
}

export function RoutingRulesBuilder({
  value,
  onChange,
}: RoutingRulesBuilderProps) {
  const [errors, setErrors] = useState<Record<string, string>>({});

  const addRule = () => {
    const newRule: RoutingRule = {
      matches: [
        {
          path: {
            type: 'PathPrefix',
            value: '/',
          },
        },
      ],
      backend_refs: [
        {
          name: 'service-name',
          port: 80,
          weight: 100,
        },
      ],
    };
    onChange([...value, newRule]);
  };

  const removeRule = (ruleIndex: number) => {
    const newRules = value.filter((_, i) => i !== ruleIndex);
    onChange(newRules);
  };

  const updatePathMatch = (ruleIndex: number, matchIndex: number, updates: Partial<PathMatch>) => {
    const newRules = [...value];
    const currentMatch = newRules[ruleIndex].matches[matchIndex];
    newRules[ruleIndex].matches[matchIndex] = {
      path: { ...currentMatch.path, ...updates },
    };
    onChange(newRules);
  };

  const addBackendRef = (ruleIndex: number) => {
    const newRules = [...value];
    newRules[ruleIndex].backend_refs.push({
      name: 'service-name',
      port: 80,
      weight: 100,
    });
    onChange(newRules);
  };

  const removeBackendRef = (ruleIndex: number, backendIndex: number) => {
    const newRules = [...value];
    newRules[ruleIndex].backend_refs = newRules[ruleIndex].backend_refs.filter(
      (_, i) => i !== backendIndex
    );
    onChange(newRules);
  };

  const updateBackendRef = (
    ruleIndex: number,
    backendIndex: number,
    updates: Partial<BackendRef>
  ) => {
    const newRules = [...value];
    newRules[ruleIndex].backend_refs[backendIndex] = {
      ...newRules[ruleIndex].backend_refs[backendIndex],
      ...updates,
    };
    onChange(newRules);
  };

  const validateBackend = (ruleIndex: number, backendIndex: number, backend: BackendRef) => {
    const newErrors = { ...errors };
    const key = `${ruleIndex}-backend-${backendIndex}`;

    // Validate service name
    const nameValidation = validateK8sResourceName(backend.name);
    if (!nameValidation.isValid) {
      newErrors[`${key}-name`] = nameValidation.error || 'Invalid service name';
    } else {
      delete newErrors[`${key}-name`];
    }

    // Validate port
    const portValidation = validatePort(backend.port);
    if (!portValidation.isValid) {
      newErrors[`${key}-port`] = portValidation.error || 'Invalid port';
    } else {
      delete newErrors[`${key}-port`];
    }

    // Validate weight (1-100)
    if (backend.weight !== undefined && (backend.weight < 1 || backend.weight > 100)) {
      newErrors[`${key}-weight`] = 'Weight must be between 1 and 100';
    } else {
      delete newErrors[`${key}-weight`];
    }

    setErrors(newErrors);
  };

  return (
    <div className="space-y-4">
      {/* Rules */}
      <div className="space-y-4">
        {value.length === 0 ? (
          <div className="text-center py-8 rounded-lg border-2 border-dashed border-border text-muted-foreground">
            <Route className="h-12 w-12 mx-auto mb-2 opacity-50" />
            <p className="text-sm font-medium">No routing rules configured</p>
            <p className="text-xs mt-1">Add a rule to route traffic to backend services</p>
          </div>
        ) : (
          value.map((rule, ruleIndex) => (
            <Card key={ruleIndex} className="p-4">
              <div className="space-y-4">
                {/* Rule Header */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Badge variant="info" className="px-2 py-1 font-mono text-xs">
                      <Route className="h-3 w-3 mr-1" />
                      Rule {ruleIndex + 1}
                    </Badge>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => removeRule(ruleIndex)}
                    className="h-7 w-7 p-0 text-destructive hover:bg-destructive/10"
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>

                {/* Path Match */}
                <div className="space-y-2">
                  <Label className="text-xs font-semibold">Path Matching</Label>
                  {rule.matches.map((match, matchIndex) => (
                    <div key={matchIndex} className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      {/* Match Type */}
                      <div className="space-y-1">
                        <Label className="text-xs">Match Type</Label>
                        <Select
                          value={match.path.type}
                          onValueChange={(value) =>
                            updatePathMatch(ruleIndex, matchIndex, {
                              type: value as PathMatch['type'],
                            })
                          }
                        >
                          <SelectTrigger className="text-sm">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {PATH_MATCH_TYPES.map((type) => (
                              <SelectItem key={type.value} value={type.value}>
                                <div className="flex flex-col items-start">
                                  <span className="font-medium">{type.label}</span>
                                  <span className="text-xs text-muted-foreground">{type.example}</span>
                                </div>
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </div>

                      {/* Path Value */}
                      <div className="space-y-1">
                        <Label className="text-xs">Path Value</Label>
                        <Input
                          value={match.path.value}
                          onChange={(e) =>
                            updatePathMatch(ruleIndex, matchIndex, { value: e.target.value })
                          }
                          placeholder={
                            PATH_MATCH_TYPES.find((t) => t.value === match.path.type)?.example ||
                            '/path'
                          }
                          className="text-sm"
                        />
                      </div>
                    </div>
                  ))}
                  <Badge variant={pathTypeBadgeVariant(rule.matches[0]?.path.type || 'PathPrefix')} className="text-[10px]">
                    {rule.matches[0]?.path.type}: {rule.matches[0]?.path.value}
                  </Badge>
                </div>

                {/* Backend References */}
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <Label className="text-xs font-semibold">Backend Services</Label>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => addBackendRef(ruleIndex)}
                      className="h-6 text-xs"
                    >
                      <Plus className="h-3 w-3 mr-1" />
                      Add Backend
                    </Button>
                  </div>

                  {rule.backend_refs.map((backend, backendIndex) => (
                    <Card key={backendIndex} className="p-3 bg-muted/40">
                      <div className="space-y-3">
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-2">
                            <Server className="h-4 w-4 text-info" />
                            <span className="text-xs font-medium">Backend {backendIndex + 1}</span>
                          </div>
                          {rule.backend_refs.length > 1 && (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => removeBackendRef(ruleIndex, backendIndex)}
                              className="h-5 w-5 p-0 text-destructive"
                            >
                              <Trash2 className="h-3 w-3" />
                            </Button>
                          )}
                        </div>

                        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                          {/* Service Name */}
                          <div className="space-y-1">
                            <Label className="text-xs">Service Name</Label>
                            <Input
                              value={backend.name}
                              onChange={(e) => {
                                updateBackendRef(ruleIndex, backendIndex, { name: e.target.value });
                                validateBackend(ruleIndex, backendIndex, {
                                  ...backend,
                                  name: e.target.value,
                                });
                              }}
                              placeholder="my-service"
                              className={cn(
                                'text-sm',
                                errors[`${ruleIndex}-backend-${backendIndex}-name`] && 'border-destructive',
                              )}
                            />
                            {errors[`${ruleIndex}-backend-${backendIndex}-name`] && (
                              <p className="text-xs text-destructive">
                                {errors[`${ruleIndex}-backend-${backendIndex}-name`]}
                              </p>
                            )}
                          </div>

                          {/* Port */}
                          <div className="space-y-1">
                            <Label className="text-xs">Port</Label>
                            <Input
                              type="number"
                              value={backend.port}
                              onChange={(e) => {
                                const port = parseInt(e.target.value);
                                updateBackendRef(ruleIndex, backendIndex, { port });
                                validateBackend(ruleIndex, backendIndex, { ...backend, port });
                              }}
                              min={1}
                              max={65535}
                              placeholder="80"
                              className={cn(
                                'text-sm',
                                errors[`${ruleIndex}-backend-${backendIndex}-port`] && 'border-destructive',
                              )}
                            />
                            {errors[`${ruleIndex}-backend-${backendIndex}-port`] && (
                              <p className="text-xs text-destructive">
                                {errors[`${ruleIndex}-backend-${backendIndex}-port`]}
                              </p>
                            )}
                          </div>

                          {/* Weight */}
                          <div className="space-y-1">
                            <Label className="text-xs flex items-center gap-1">
                              <Percent className="h-3 w-3" />
                              Weight
                            </Label>
                            <Input
                              type="number"
                              value={backend.weight || 100}
                              onChange={(e) => {
                                const weight = parseInt(e.target.value);
                                updateBackendRef(ruleIndex, backendIndex, { weight });
                                validateBackend(ruleIndex, backendIndex, { ...backend, weight });
                              }}
                              min={1}
                              max={100}
                              placeholder="100"
                              className={cn(
                                'text-sm',
                                errors[`${ruleIndex}-backend-${backendIndex}-weight`] && 'border-destructive',
                              )}
                            />
                            {errors[`${ruleIndex}-backend-${backendIndex}-weight`] && (
                              <p className="text-xs text-destructive">
                                {errors[`${ruleIndex}-backend-${backendIndex}-weight`]}
                              </p>
                            )}
                          </div>
                        </div>
                      </div>
                    </Card>
                  ))}
                </div>

                {/* Rule Summary */}
                <div className="text-xs p-2 rounded bg-muted text-muted-foreground">
                  <span className="font-medium">Traffic:</span> {rule.matches[0]?.path.value} →{' '}
                  {rule.backend_refs.map((b) => `${b.name}:${b.port}`).join(', ')}
                </div>
              </div>
            </Card>
          ))
        )}
      </div>

      {/* Add Rule Button */}
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={addRule}
        className="w-full"
      >
        <Plus className="h-4 w-4 mr-2" />
        Add Routing Rule
      </Button>

      {/* Summary */}
      {value.length > 0 && (
        <div className="text-xs rounded p-3 bg-muted text-muted-foreground">
          <span className="font-medium">Summary:</span> {value.length} routing rule
          {value.length !== 1 ? 's' : ''} configured •{' '}
          {value.reduce((sum, rule) => sum + rule.backend_refs.length, 0)} backend
          {value.reduce((sum, rule) => sum + rule.backend_refs.length, 0) !== 1 ? 's' : ''}
          {Object.keys(errors).length > 0 && (
            <span className="text-destructive ml-2">
              • {Object.keys(errors).length} validation error{Object.keys(errors).length !== 1 ? 's' : ''}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
