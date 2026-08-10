/**
 * React Query hooks for backup and restore operations
 */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { backupApi } from '@/lib/api/backup';
import { queryKeys } from '@/lib/queryKeys';
import { notify } from '@/lib/notify';
import { useAppMutation } from '@/hooks/lib/useAppMutation';

/**
 * Hook to check backup/restore operation status
 */
export function useBackupStatus() {
  return useQuery({
    queryKey: queryKeys.system.backupStatus(),
    queryFn: () => backupApi.getBackupStatus(),
    refetchInterval: 5000, // Poll every 5s when operation in progress
  });
}

/**
 * Hook to check maintenance mode status
 * Polls more frequently since it affects the whole app
 */
export function useMaintenanceStatus() {
  return useQuery({
    queryKey: queryKeys.system.maintenanceStatus(),
    queryFn: () => backupApi.getMaintenanceStatus(),
    refetchInterval: 3000, // Poll every 3s during maintenance
    retry: 1, // Don't retry too much - server may be restarting
  });
}

/**
 * Hook to create a backup
 */
export function useCreateBackup() {
  const queryClient = useQueryClient();

  return useAppMutation({
    mutationFn: async (passphrase: string) => {
      const { blob, filename } = await backupApi.createBackup(passphrase);

      // Trigger download
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      window.URL.revokeObjectURL(url);

      return { blob, filename };
    },
    onSuccess: () => {
      notify.success('Backup created successfully', undefined, { category: 'system' });
      queryClient.invalidateQueries({ queryKey: queryKeys.system.backupStatus() });
    },
    onError: (error: Error) => {
      notify.error('Backup failed', error.message, { category: 'system' });
    },
  });
}

/**
 * Hook to restore from a backup
 */
export function useRestoreBackup() {
  return useAppMutation({
    mutationFn: ({ file, passphrase }: { file: File; passphrase: string }) =>
      backupApi.restoreBackup(file, passphrase),
    onSuccess: (data) => {
      if (data.status === 'success') {
        const counts = data.table_counts;
        const nonZero = Object.entries(counts)
          .filter(([, v]) => v > 0)
          .map(([k, v]) => `${v} ${k.replace(/_/g, ' ')}`)
          .join(', ');
        const summary = nonZero || 'empty database';
        notify.success(
          'Restore completed',
          `Restored: ${summary}. System restarting...`,
          { category: 'system' },
        );
      }
      // Don't invalidate queries — backend is restarting
    },
    // Error handling is done at the call site (BackupPanel) to support
    // inline passphrase error display vs generic toast for other errors.
  });
}
