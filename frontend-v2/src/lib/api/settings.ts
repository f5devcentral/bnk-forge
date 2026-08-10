/**
 * Settings & System Defaults API methods
 */
import { apiClient } from './client';

export const settingsApi = {
  /** Get all application settings */
  getSettings: () =>
    apiClient.get('/api/settings').then((res) => res.data),

  /** Update a single setting */
  updateSetting: (key: string, value: string) =>
    apiClient.put(`/api/settings/${key}`, { key, value }).then((res) => res.data),

  /** Batch update multiple settings */
  batchUpdateSettings: (settings: Record<string, string>) =>
    apiClient.put('/api/settings', { settings }).then((res) => res.data),

  /** Get system defaults (GUI-based configuration) */
  getSystemDefaults: () =>
    apiClient.get('/api/system/defaults').then((res) => res.data),

  /** Update system defaults */
  updateSystemDefaults: (updates: Record<string, string>) =>
    apiClient.put('/api/system/defaults', { updates }).then((res) => res.data),

  /** Get defaults configuration status */
  getDefaultsStatus: () =>
    apiClient.get('/api/system/defaults/status').then((res) => res.data),
};
