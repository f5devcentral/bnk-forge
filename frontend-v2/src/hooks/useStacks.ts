/**
 * React Query hooks for Deployment Stacks (P2-4)
 *
 * Provides data fetching and mutations for stack templates and instances.
 */

import { useQuery, useQueryClient, UseQueryResult, UseMutationResult } from '@tanstack/react-query';
import { api } from '@/lib/api';
import { QUERY_STALE_TIME } from '@/lib/constants';
import { queryKeys } from '@/lib/queryKeys';
import type {
  BlueprintRelease,
  StackRequiredInputs,
  StackTemplateList,
  StackTemplate,
  StackPreview,
  StackInstance,
  StackInstanceCreate,
  StackStatus,
  StackDeployKind,
} from '@/types';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

// ============================================================================
// Query Keys
// ============================================================================

export const stackKeys = {
  all: ['stacks'] as const,
  templates: () => [...stackKeys.all, 'templates'] as const,
  template: (slug: string) => [...stackKeys.templates(), slug] as const,
  preview: (slug: string) => [...stackKeys.template(slug), 'preview'] as const,
  instances: (projectId: number) => [...stackKeys.all, 'instances', projectId] as const,
  instance: (projectId: number, stackId: number) => [...stackKeys.instances(projectId), stackId] as const,
  status: (projectId: number, stackId: number) => [...stackKeys.instance(projectId, stackId), 'status'] as const,
};

// ============================================================================
// Stack Templates Hooks
// ============================================================================

export interface UseStackTemplatesParams {
  category?: string;
  cloud_provider?: string;
  tags?: string;
  is_featured?: boolean;
}

export function useStackTemplates(
  params?: UseStackTemplatesParams
): UseQueryResult<StackTemplateList[], Error> {
  return useQuery({
    queryKey: [...stackKeys.templates(), params],
    queryFn: async () => {
      const [templates, allImported] = await Promise.all([
        api.fetchStackTemplates(params),
        // Fetch ALL imported releases (visible + hidden) so we can displace in-code
        // templates by every builtin release, not just visible ones.  Hidden releases
        // are filtered out of the display list below, preventing in-code resurrection.
        api.getBlueprintReleases({ release_state: 'imported', validation_state: 'valid' }),
      ]);

      // Only visible releases are shown on the page.
      const visibleReleases = allImported.filter((r: BlueprintRelease) => r.is_active);

      // Build a set of blueprint_ids covered by ANY builtin release (visible OR hidden).
      // This ensures hiding a builtin release doesn't resurrect its in-code twin.
      const builtinReleaseBlueprintIds = new Set(
        allImported
          .filter((r: BlueprintRelease) => r.source_type === 'builtin')
          .map((r: BlueprintRelease) => r.blueprint_id)
      );

      const releaseBlueprints: StackTemplateList[] = visibleReleases.map((release: BlueprintRelease) => {
        const hasTemplateTwin = templates.some((template) => template.slug === release.blueprint_id);
        const isTemplateBackedBuiltin = release.source_type === 'builtin' && hasTemplateTwin;
        // Builtin releases that still have an in-code stack template twin should
        // continue through StackDetailDialog so they keep the richer legacy
        // deploy flow. Builtin-only manifests (for example cli-bnkctl releases)
        // must use the imported-release dialog/endpoints; routing them by
        // blueprint_id makes the UI request /api/stacks/templates/{blueprint_id}
        // and yields a blank/error dialog because no template row exists.
        const deployKind: StackDeployKind = isTemplateBackedBuiltin ? 'builtin-release' : 'git-release';
        return {
          id: 1000000 + release.id,
          name: release.blueprint_name,
          // builtin releases use `release-{id}` slug for list key uniqueness; deploy opens template by blueprint_id
          slug: `release-${release.id}`,
          description: release.blueprint_description || 'Imported blueprint release',
          category: release.category || 'bnk',
          cloud_provider: release.cloud_provider || 'any',
          difficulty: 'intermediate',
          estimated_time: 'Varies',
          is_active: release.is_active,
          is_featured: release.is_featured ?? false,
          version: release.blueprint_version,
          bnk_version: release.bnk_version || null,
          tags: release.tags || [],
          source_kind: 'blueprint_release',
          blueprint_release_id: release.id,
          blueprint_source_id: release.blueprint_source_id,
          release_state: release.release_state,
          validation_state: release.validation_state,
          source_path: release.source_path || undefined,
          deploy_kind: deployKind,
          // For template-backed builtin releases: StackDetailDialog opens by blueprint_id (the template slug).
          deploy_slug: isTemplateBackedBuiltin ? release.blueprint_id : undefined,
        };
      });

      // Dedup: drop any in-code template whose slug matches a builtin release's blueprint_id.
      // Keep templates with no matching builtin release (safety for not-yet-migrated templates).
      const filteredTemplates = templates.filter(
        (template) => !builtinReleaseBlueprintIds.has(template.slug)
      );

      // Tag remaining plain templates with the 'template' deploy kind.
      const taggedTemplates: StackTemplateList[] = filteredTemplates.map((t) => ({
        ...t,
        deploy_kind: 'template' as StackDeployKind,
        deploy_slug: t.slug,
      }));

      // Also filter releases by category/provider params if specified.
      const filteredReleases = releaseBlueprints.filter((release) => {
        // Drop git releases whose slug collides with a template (old slug-based dedup, kept as safety)
        if (release.deploy_kind === 'git-release') {
          const existingSlugs = new Set(templates.map((t) => t.slug));
          if (existingSlugs.has(release.slug)) return false;
        }
        if (params?.category && release.category !== params.category) return false;
        if (params?.cloud_provider && release.cloud_provider !== params.cloud_provider) return false;
        return true;
      });

      return [...filteredReleases, ...taggedTemplates];
    },
    staleTime: QUERY_STALE_TIME.VERY_LONG,
  });
}

export function useStackTemplate(slug: string): UseQueryResult<StackTemplate, Error> {
  return useQuery({
    queryKey: stackKeys.template(slug),
    queryFn: () => api.fetchStackTemplate(slug),
    enabled: !!slug,
    staleTime: QUERY_STALE_TIME.VERY_LONG,
  });
}

export function useStackPreview(slug: string): UseQueryResult<StackPreview, Error> {
  return useQuery({
    queryKey: stackKeys.preview(slug),
    queryFn: () => api.previewStackTemplate(slug),
    enabled: !!slug,
    staleTime: QUERY_STALE_TIME.VERY_LONG,
  });
}

export function useStackRequiredInputs(slug: string): UseQueryResult<StackRequiredInputs, Error> {
  return useQuery({
    queryKey: [...stackKeys.template(slug), 'required-inputs'],
    queryFn: () => api.getStackRequiredInputs(slug),
    enabled: !!slug,
    staleTime: QUERY_STALE_TIME.VERY_LONG,
  });
}

// ============================================================================
// Stack Instances Hooks
// ============================================================================

export function useStackInstances(
  projectId: number,
  status?: string
): UseQueryResult<StackInstance[], Error> {
  return useQuery({
    queryKey: [...stackKeys.instances(projectId), status],
    queryFn: () => api.fetchStackInstances(projectId, status),
    enabled: !!projectId,
    staleTime: QUERY_STALE_TIME.MEDIUM,
    refetchInterval: (query) => {
      // Auto-refresh every 5s if any stack is deploying or destroying
      const hasActiveStacks = query.state.data?.some((stack: StackInstance) =>
        ['deploying', 'destroying'].includes(stack.status)
      );
      return hasActiveStacks ? 5000 : false;
    },
  });
}

export function useStackInstance(
  projectId: number,
  stackId: number
): UseQueryResult<StackInstance, Error> {
  return useQuery({
    queryKey: stackKeys.instance(projectId, stackId),
    queryFn: () => api.fetchStackInstance(projectId, stackId),
    enabled: !!projectId && !!stackId,
    staleTime: QUERY_STALE_TIME.MEDIUM,
    refetchInterval: (query) => {
      // Auto-refresh every 5s if stack is deploying or destroying
      const data = query.state.data as StackInstance | undefined;
      return data && ['deploying', 'destroying'].includes(data.status) ? 5000 : false;
    },
  });
}

export function useStackStatus(
  projectId: number,
  stackId: number,
  enabled: boolean = true
): UseQueryResult<StackStatus, Error> {
  return useQuery({
    queryKey: stackKeys.status(projectId, stackId),
    queryFn: () => api.fetchStackStatus(projectId, stackId),
    enabled: enabled && !!projectId && !!stackId,
    staleTime: QUERY_STALE_TIME.SHORT,
    refetchInterval: (query) => {
      // Auto-refresh every 3s if stack is deploying or destroying
      const data = query.state.data as StackStatus | undefined;
      return data && ['deploying', 'destroying'].includes(data.status) ? 3000 : false;
    },
  });
}

// ============================================================================
// Stack Mutations
// ============================================================================

export function useCreateStackInstance(
  projectId: number
): UseMutationResult<StackInstance, Error, StackInstanceCreate> {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (data: StackInstanceCreate) => api.createStackInstance(projectId, data),
    onSuccess: () => {
      // Invalidate stack instances list for this project
      queryClient.invalidateQueries({ queryKey: stackKeys.instances(projectId) });
    },
  });
}

export function useDeployStack(
  projectId: number,
  stackId: number
): UseMutationResult<
  { status: string; message: string; current_step: number; total_steps: number; deployed_modules: number[] },
  Error,
  void
> {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: () => api.deployStack(projectId, stackId),
    onSuccess: async () => await invalidateStackQueries(queryClient, projectId, stackId),
  });
}

// Shared invalidation helper for stack mutations
// Stack operations affect: stacks, modules, and project counts
async function invalidateStackQueries(
  queryClient: ReturnType<typeof useQueryClient>,
  projectId: number,
  stackId: number
) {
  // Stack-specific queries — use refetchQueries for keys that feed polling hooks
  // (useStackInstances, useStackInstance, useStackStatus all use refetchInterval)
  await Promise.all([
    queryClient.refetchQueries({ queryKey: stackKeys.instances(projectId) }),
    queryClient.refetchQueries({ queryKey: stackKeys.instance(projectId, stackId) }),
    queryClient.refetchQueries({ queryKey: stackKeys.status(projectId, stackId) }),
  ]);
  
  // Module queries - stack operations create/destroy modules
  queryClient.invalidateQueries({ queryKey: queryKeys.modules.project.byProject(projectId) });
  queryClient.invalidateQueries({ queryKey: queryKeys.modules.project.all });
  
  // Project queries - deployed_count and module_count change
  queryClient.invalidateQueries({ queryKey: queryKeys.projects.all });
  queryClient.invalidateQueries({ queryKey: queryKeys.projects.detail(projectId) });
}

export function useRunStackDeployment(
  projectId: number,
  stackId: number
): UseMutationResult<
  { status: string; message: string; queued_modules: number; total_modules: number },
  Error,
  void
> {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: () => api.runStackDeployment(projectId, stackId),
    onSuccess: async () => await invalidateStackQueries(queryClient, projectId, stackId),
  });
}

export function useDestroyStack(
  projectId: number,
  stackId: number
): UseMutationResult<{ status: string; message: string }, Error, { force?: boolean } | void> {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: (vars?: { force?: boolean } | void) => api.destroyStack(projectId, stackId, (vars as { force?: boolean } | undefined)?.force),
    onSuccess: async () => await invalidateStackQueries(queryClient, projectId, stackId),
  });
}
