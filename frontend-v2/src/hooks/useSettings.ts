import { useQuery, useQueryClient } from '@tanstack/react-query';
import { settingsApi } from '@/lib/api/settings';
import { queryKeys } from '@/lib/queryKeys';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

interface Setting {
  key: string;
  value: string;
  value_type: string;
  description: string;
  is_encrypted: boolean;
}

interface SettingsResponse {
  settings: Record<string, Setting[]>;
}

export function useSettings() {
  const queryClient = useQueryClient();

  const { data, isLoading, error } = useQuery<SettingsResponse>({
    queryKey: queryKeys.system.settings(),
    queryFn: () => settingsApi.getSettings(),
  });

  const updateSetting = useAppMutation({
    mutationFn: ({ key, value }: { key: string; value: string }) =>
      settingsApi.updateSetting(key, value),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.system.settings() });
    },
  });

  const batchUpdateSettings = useAppMutation({
    mutationFn: (settings: Record<string, string>) =>
      settingsApi.batchUpdateSettings(settings),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.system.settings() });
    },
  });

  return {
    settings: data?.settings || {},
    isLoading,
    error,
    updateSetting: updateSetting.mutate,
    batchUpdateSettings: batchUpdateSettings.mutate,
    isUpdating: updateSetting.isPending || batchUpdateSettings.isPending,
  };
}

// ============================================================================
// System Defaults Hook (GUI-based configuration)
// ============================================================================

interface SystemDefaultSetting {
  key: string;
  value: string | number | boolean | null;
  raw_value: string;
  value_type: string;
  description: string;
  required: boolean;
  is_configured: boolean;
}

interface SystemDefaultsResponse {
  module_library: Record<string, SystemDefaultSetting>;
  system: Record<string, SystemDefaultSetting>;
  cloud: Record<string, SystemDefaultSetting>;
  opentofu: Record<string, SystemDefaultSetting>;
  execution: Record<string, SystemDefaultSetting>;
  project: Record<string, SystemDefaultSetting>;
}

interface DefaultsStatusResponse {
  all_configured: boolean;
  missing: Array<{ key: string; description: string; category: string; suggested_value?: string }>;
  total_required: number;
  configured_count: number;
}

export function useSystemDefaults() {
  const queryClient = useQueryClient();

  const { data, isLoading, error, refetch } = useQuery<SystemDefaultsResponse>({
    queryKey: queryKeys.system.systemDefaults(),
    queryFn: () => settingsApi.getSystemDefaults(),
  });

  const updateDefaults = useAppMutation({
    mutationFn: (updates: Record<string, string>) =>
      settingsApi.updateSystemDefaults(updates),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.system.systemDefaults() });
      queryClient.invalidateQueries({ queryKey: queryKeys.system.defaultsStatus() });
    },
  });

  return {
    defaults: data,
    isLoading,
    error,
    refetch,
    updateDefaults: updateDefaults.mutate,
    isUpdating: updateDefaults.isPending,
  };
}

export function useDefaultsStatus() {
  const { data, isLoading, error } = useQuery<DefaultsStatusResponse>({
    queryKey: queryKeys.system.defaultsStatus(),
    queryFn: () => settingsApi.getDefaultsStatus(),
    // Check frequently for unconfigured required settings
    staleTime: 30000,
  });

  return {
    allConfigured: data?.all_configured ?? true,
    missing: data?.missing ?? [],
    totalRequired: data?.total_required ?? 0,
    configuredCount: data?.configured_count ?? 0,
    isLoading,
    error,
  };
}
