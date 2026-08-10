/**
 * Backup and Restore API methods
 */
import { apiClient } from './client';
import type {
  BackupDownload,
  BackupStatusResponse,
  MaintenanceStatusResponse,
  RestoreResponse,
} from '@/types/backup';

function getFilenameFromDisposition(contentDisposition: string | undefined): string {
  if (!contentDisposition) {
    return 'bnkforge-backup.tar.gz';
  }

  const utf8Match = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match) {
    return decodeURIComponent(utf8Match[1]);
  }

  const filenameMatch = contentDisposition.match(/filename="?([^";]+)"?/i);
  return filenameMatch?.[1] ?? 'bnkforge-backup.tar.gz';
}

export const backupApi = {
  /**
   * Create a backup archive.
   * Returns the archive as a blob for download.
   */
  createBackup: async (passphrase: string): Promise<BackupDownload> => {
    const response = await apiClient.post('/api/system/backup', { passphrase }, {
      responseType: 'blob',
    });

    return {
      blob: response.data as Blob,
      filename: getFilenameFromDisposition(response.headers['content-disposition']),
    };
  },

  /**
   * Get backup/restore operation status.
   */
  getBackupStatus: () =>
    apiClient.get<BackupStatusResponse>('/api/system/backup/status').then((res) => res.data),

  /**
   * Restore from a backup archive.
   * Uses multipart/form-data for file upload.
   */
  restoreBackup: async (file: File, passphrase: string): Promise<RestoreResponse> => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('passphrase', passphrase);

    const response = await apiClient.post<RestoreResponse>('/api/system/restore', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
      timeout: 600000, // 10 minute timeout for large restores
    });
    return response.data;
  },

  /**
   * Get maintenance mode status (no auth required).
   */
  getMaintenanceStatus: () =>
    apiClient.get<MaintenanceStatusResponse>('/api/system/maintenance').then((res) => res.data),
};
