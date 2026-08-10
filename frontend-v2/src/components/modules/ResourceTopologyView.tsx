import { useState, useEffect, useCallback } from 'react';
import { SectionCard } from '@/components/ui/section-card';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Alert, AlertDescription } from '@/components/ui/alert';
import {
  RefreshCw,
  AlertTriangle,
  Layers,
  ChevronRight,
  ChevronDown,
  Server,
  Cloud,
  Database,
  Network,
  Shield,
  Box,
  type LucideIcon,
} from 'lucide-react';
import { api } from '@/lib/api';
import type { ProjectModule } from '@/types';
import { notifyError } from '@/lib/notify';
import { parseApiError } from '@/lib/error-handler';

interface ResourceTopologyViewProps {
  module: ProjectModule;
}

interface StateResource {
  full_address: string;
  type: string;
  name: string;
  provider: string;
  mode: string;
}

interface ResourceGroup {
  category: string;
  icon: LucideIcon;
  color: string;
  resources: StateResource[];
}

function getResourceCategory(type: string): { category: string; icon: LucideIcon; color: string } {
  const typeUpper = type.toUpperCase();

  if (typeUpper.includes('COMPUTE') || typeUpper.includes('INSTANCE') || typeUpper.includes('VM')) {
    return { category: 'Compute', icon: Server, color: 'bg-info' };
  }
  if (typeUpper.includes('NETWORK') || typeUpper.includes('VPC') || typeUpper.includes('SUBNET') || typeUpper.includes('ROUTE')) {
    return { category: 'Networking', icon: Network, color: 'bg-primary' };
  }
  if (typeUpper.includes('DATABASE') || typeUpper.includes('DB') || typeUpper.includes('RDS') || typeUpper.includes('SQL')) {
    return { category: 'Database', icon: Database, color: 'bg-success' };
  }
  if (typeUpper.includes('STORAGE') || typeUpper.includes('BUCKET') || typeUpper.includes('S3') || typeUpper.includes('BLOB')) {
    return { category: 'Storage', icon: Box, color: 'bg-warning' };
  }
  if (typeUpper.includes('SECURITY') || typeUpper.includes('IAM') || typeUpper.includes('ROLE') || typeUpper.includes('POLICY') || typeUpper.includes('FIREWALL')) {
    return { category: 'Security & IAM', icon: Shield, color: 'bg-destructive' };
  }
  return { category: 'Other', icon: Cloud, color: 'bg-muted-foreground' };
}

function categorizeResources(resources: StateResource[]): ResourceGroup[] {
  const groups = new Map<string, ResourceGroup>();

  resources.forEach((resource) => {
    const { category, icon, color } = getResourceCategory(resource.type);

    if (!groups.has(category)) {
      groups.set(category, { category, icon, color, resources: [] });
    }

    groups.get(category)!.resources.push(resource);
  });

  return Array.from(groups.values()).sort((a, b) =>
    b.resources.length - a.resources.length
  );
}

export function ResourceTopologyView({ module }: ResourceTopologyViewProps) {
  const [resources, setResources] = useState<StateResource[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());

  const loadResources = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await api.getModuleResources(module.id);
      setResources(response.resources || []);

      // Expand all groups by default
      const categories = categorizeResources(response.resources || []);
      setExpandedGroups(new Set(categories.map(c => c.category)));
    } catch (err) {
      const parsed = parseApiError(err);
      setError(parsed.message);
      notifyError(err);
    } finally {
      setIsLoading(false);
    }
  }, [module.id]);

  useEffect(() => {
    loadResources();
  }, [loadResources]);

  const toggleGroup = (category: string) => {
    const newExpanded = new Set(expandedGroups);
    if (newExpanded.has(category)) {
      newExpanded.delete(category);
    } else {
      newExpanded.add(category);
    }
    setExpandedGroups(newExpanded);
  };

  const resourceGroups = categorizeResources(resources);

  return (
    <SectionCard title="Resource topology" className="h-full">
      <div className="flex items-center justify-between -mt-1 mb-4">
        <p className="text-sm text-muted-foreground">
          Hierarchical view of infrastructure resources in this module by category.
        </p>
        <Button size="sm" variant="outline" onClick={loadResources} disabled={isLoading}>
          <RefreshCw className={`h-4 w-4 mr-2 ${isLoading ? 'animate-spin' : ''}`} />
          Refresh
        </Button>
      </div>
      <div>
        {isLoading && resources.length === 0 ? (
          <div className="flex items-center justify-center py-12">
            <div className="text-center">
              <RefreshCw className="h-12 w-12 animate-spin mx-auto mb-4 text-muted-foreground" />
              <p className="text-sm text-muted-foreground">Loading topology...</p>
            </div>
          </div>
        ) : error ? (
          <Alert variant="destructive">
            <AlertTriangle className="h-4 w-4" />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : resources.length === 0 ? (
          <Alert>
            <Layers className="h-4 w-4" />
            <AlertDescription>
              No resources found. This module may not have been applied yet.
            </AlertDescription>
          </Alert>
        ) : (
          <>
            {/* Summary Stats */}
            <div className="grid grid-cols-3 gap-4 mb-6">
              <div className="border rounded-lg p-3">
                <p className="text-sm text-muted-foreground mb-1">Total Resources</p>
                <p className="text-2xl font-bold">{resources.length}</p>
              </div>
              <div className="border rounded-lg p-3">
                <p className="text-sm text-muted-foreground mb-1">Categories</p>
                <p className="text-2xl font-bold">{resourceGroups.length}</p>
              </div>
              <div className="border rounded-lg p-3">
                <p className="text-sm text-muted-foreground mb-1">Provider</p>
                <p className="text-sm font-mono mt-1">{resources[0]?.provider || 'N/A'}</p>
              </div>
            </div>

            {/* Topology View */}
            <ScrollArea className="h-[500px]">
              <div className="space-y-3">
                {resourceGroups.map((group) => {
                  const Icon = group.icon;
                  const isExpanded = expandedGroups.has(group.category);

                  return (
                    <Card key={group.category} className="border-2">
                      <CardHeader
                        className="cursor-pointer hover:bg-muted/50 transition-colors"
                        onClick={() => toggleGroup(group.category)}
                      >
                        <div className="flex items-center justify-between">
                          <div className="flex items-center gap-3">
                            <div className={`w-10 h-10 rounded-lg ${group.color} flex items-center justify-center`}>
                              <Icon className="h-5 w-5 text-white" />
                            </div>
                            <div>
                              <CardTitle className="text-base">{group.category}</CardTitle>
                              <CardDescription className="text-xs">
                                {group.resources.length} resource{group.resources.length !== 1 ? 's' : ''}
                              </CardDescription>
                            </div>
                          </div>
                          <div className="flex items-center gap-2">
                            <Badge variant="secondary">{group.resources.length}</Badge>
                            {isExpanded ? (
                              <ChevronDown className="h-5 w-5 text-muted-foreground" />
                            ) : (
                              <ChevronRight className="h-5 w-5 text-muted-foreground" />
                            )}
                          </div>
                        </div>
                      </CardHeader>

                      {isExpanded && (
                        <CardContent className="pt-0">
                          <div className="space-y-2">
                            {group.resources.map((resource) => (
                              <div
                                key={resource.full_address}
                                className="border rounded-lg p-3 bg-muted/30 hover:bg-muted/50 transition-colors"
                              >
                                <div className="flex items-start justify-between gap-2">
                                  <div className="flex-1 min-w-0">
                                    <p className="font-mono text-sm font-medium truncate">
                                      {resource.name}
                                    </p>
                                    <p className="font-mono text-xs text-muted-foreground mt-1">
                                      {resource.type}
                                    </p>
                                  </div>
                                  <Badge variant="outline" className="text-xs shrink-0">
                                    {resource.mode}
                                  </Badge>
                                </div>
                                <p className="font-mono text-xs text-muted-foreground mt-2 break-all">
                                  {resource.full_address}
                                </p>
                              </div>
                            ))}
                          </div>
                        </CardContent>
                      )}
                    </Card>
                  );
                })}
              </div>
            </ScrollArea>
          </>
        )}
      </div>
    </SectionCard>
  );
}
