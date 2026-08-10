/**
 * Centralized time formatting utilities
 *
 * Consolidates duplicate time calculations from 11 files into a single source of truth.
 * Provides consistent output formats across the application.
 */

/**
 * Format timestamp as compact age (e.g., "5d", "3h", "10m")
 * Use for tables, badges, and compact displays
 *
 * @param timestamp - ISO timestamp string, null, or undefined
 * @returns Compact age string or 'Unknown' if invalid
 */
export function formatAge(timestamp: string | null | undefined): string {
  if (!timestamp) return 'Unknown';

  const date = new Date(timestamp);
  if (isNaN(date.getTime())) return 'Unknown';

  const diff = Date.now() - date.getTime();
  if (diff < 0) return 'just now'; // Future timestamps

  const seconds = Math.floor(diff / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);

  if (days > 0) return `${days}d`;
  if (hours > 0) return `${hours}h`;
  if (minutes > 0) return `${minutes}m`;
  return 'just now';
}

/**
 * Format timestamp as relative time with "ago" suffix (e.g., "5d ago", "3h ago")
 * Use for activity feeds, history lists, and task displays
 *
 * @param timestamp - ISO timestamp string, null, or undefined
 * @returns Relative time string with "ago" suffix, or empty string if invalid
 */
export function formatTimeAgo(timestamp: string | null | undefined): string {
  if (!timestamp) return '';

  const age = formatAge(timestamp);
  if (age === 'Unknown') return '';
  if (age === 'just now') return age;
  return `${age} ago`;
}

/**
 * Format timestamp as a localized date string
 * Use for created/updated dates in detail views
 *
 * @param timestamp - ISO timestamp string, null, or undefined
 * @returns Localized date string or empty string if invalid
 */
export function formatDate(timestamp: string | null | undefined): string {
  if (!timestamp) return '';

  const date = new Date(timestamp);
  if (isNaN(date.getTime())) return '';

  return date.toLocaleDateString();
}

/**
 * Format timestamp as a localized date and time string
 * Use for precise timestamps in logs and audit trails
 *
 * @param timestamp - ISO timestamp string, null, or undefined
 * @returns Localized datetime string or empty string if invalid
 */
export function formatDateTime(timestamp: string | null | undefined): string {
  if (!timestamp) return '';

  const date = new Date(timestamp);
  if (isNaN(date.getTime())) return '';

  return date.toLocaleString();
}

/**
 * Format age from seconds as human-readable string (e.g., "5 minutes ago", "2 hours ago")
 * Use for plan ages and other durations measured in seconds
 *
 * @param seconds - Number of seconds
 * @returns Human-readable age string
 */
export function formatAgeFromSeconds(seconds: number): string {
  if (seconds < 60) {
    return 'just now';
  } else if (seconds < 3600) {
    const minutes = Math.floor(seconds / 60);
    return `${minutes} minute${minutes > 1 ? 's' : ''} ago`;
  } else if (seconds < 86400) {
    const hours = Math.floor(seconds / 3600);
    return `${hours} hour${hours > 1 ? 's' : ''} ago`;
  } else {
    const days = Math.floor(seconds / 86400);
    return `${days} day${days > 1 ? 's' : ''} ago`;
  }
}

/**
 * Format duration in seconds to human-readable format (e.g., "5m 30s", "2h 15m")
 * Use for task durations and elapsed time
 *
 * @param seconds - Duration in seconds
 * @returns Formatted duration string
 */
export function formatDuration(seconds: number): string {
  if (seconds < 60) {
    return `${seconds}s`;
  } else if (seconds < 3600) {
    const minutes = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return secs > 0 ? `${minutes}m ${secs}s` : `${minutes}m`;
  } else {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return minutes > 0 ? `${hours}h ${minutes}m` : `${hours}h`;
  }
}
