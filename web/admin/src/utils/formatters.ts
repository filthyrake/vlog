/**
 * Utility functions for formatting values in the admin UI
 */

/**
 * Format seconds into human-readable duration (H:MM:SS or M:SS)
 */
export function formatDuration(seconds: number | undefined | null, fallback = '0:00'): string {
  if (!seconds) return fallback;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) {
    return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
  }
  return `${m}:${s.toString().padStart(2, '0')}`;
}

/**
 * Format date string for display
 */
export function formatDate(
  dateStr: string | undefined | null,
  monthFormat: 'short' | 'long' = 'short'
): string {
  if (!dateStr) return '';
  return new Date(dateStr).toLocaleDateString('en-US', {
    year: 'numeric',
    month: monthFormat,
    day: 'numeric',
  });
}

/**
 * Format bytes to human-readable size
 */
export function formatBytes(bytes: number | undefined | null, decimals = 2): string {
  if (!bytes || bytes === 0) return '0 Bytes';

  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB', 'PB'];

  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
}

/**
 * Format a percentage
 */
export function formatPercent(value: number | undefined | null, decimals = 1): string {
  if (value === undefined || value === null) return '0%';
  return `${value.toFixed(decimals)}%`;
}

/**
 * Format hours (e.g., for watch time)
 */
export function formatHours(hours: number | undefined | null): string {
  if (!hours) return '0h';
  if (hours < 1) {
    return `${Math.round(hours * 60)}m`;
  }
  return `${hours.toFixed(1)}h`;
}

/**
 * Format watch time in seconds to human-readable
 */
export function formatWatchTime(seconds: number | undefined | null): string {
  if (!seconds) return '0m';
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }
  return `${minutes}m`;
}

/**
 * Format time since a given date
 */
export function formatTimeSince(value: string | number | undefined | null): string {
  if (value === undefined || value === null) return 'Unknown';

  // If it's a number, treat it as seconds since
  if (typeof value === 'number') {
    const diffSec = Math.floor(value);
    const diffMin = Math.floor(diffSec / 60);
    const diffHour = Math.floor(diffMin / 60);
    const diffDay = Math.floor(diffHour / 24);

    if (diffSec < 60) return `${diffSec}s ago`;
    if (diffMin < 60) return `${diffMin}m ago`;
    if (diffHour < 24) return `${diffHour}h ago`;
    return `${diffDay}d ago`;
  }

  // Otherwise treat as date string
  const date = new Date(value);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHour = Math.floor(diffMin / 60);
  const diffDay = Math.floor(diffHour / 24);

  if (diffSec < 60) return `${diffSec}s ago`;
  if (diffMin < 60) return `${diffMin}m ago`;
  if (diffHour < 24) return `${diffHour}h ago`;
  return `${diffDay}d ago`;
}

/**
 * Format a deployment timestamp
 */
export function formatDeploymentTime(timestamp: string | undefined | null): string {
  if (!timestamp) return '';

  const date = new Date(timestamp);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffHours = diffMs / (1000 * 60 * 60);

  if (diffHours < 24) {
    return date.toLocaleTimeString('en-US', {
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/**
 * Check if a version is outdated compared to current
 */
export function isVersionOutdated(workerVersion: string | undefined, currentVersion: string | undefined): boolean {
  if (!workerVersion || !currentVersion) return false;
  return workerVersion !== currentVersion;
}

/**
 * Get icon class for deployment event type
 */
export function getEventIcon(eventType: string): string {
  const icons: Record<string, string> = {
    deployed: 'rocket',
    restarted: 'refresh',
    updated: 'download',
    deleted: 'trash',
  };
  return icons[eventType] || 'info';
}

/**
 * Get color class for deployment event type
 */
export function getEventColor(eventType: string): string {
  const colors: Record<string, string> = {
    deployed: 'text-green-400',
    restarted: 'text-blue-400',
    updated: 'text-purple-400',
    deleted: 'text-red-400',
  };
  return colors[eventType] || 'text-gray-400';
}
