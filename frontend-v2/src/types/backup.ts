/**
 * Backup and restore types
 */

export interface BackupStatusResponse {
  in_progress: boolean;
  operation: 'backup' | 'restore' | null;
  started_at: string | null;
  message: string | null;
}

export interface RestoreResponse {
  status: 'success' | 'error';
  table_counts: Record<string, number>;
  migrations_applied: string[];
  warnings: string[];
  restart_triggered: boolean;
}

export interface MaintenanceStatusResponse {
  maintenance_mode: boolean;
  message: string | null;
  started_at: string | null;
}

export interface BackupDownload {
  blob: Blob;
  filename: string;
}
