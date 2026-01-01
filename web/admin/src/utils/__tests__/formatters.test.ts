/**
 * Tests for utility formatters
 * Includes edge cases for unusual inputs
 */

import { describe, it, expect } from 'vitest';
import {
  formatDuration,
  formatDate,
  formatBytes,
  formatPercent,
  formatHours,
  formatWatchTime,
  formatTimeSince,
  formatDeploymentTime,
  isVersionOutdated,
  getEventIcon,
  getEventColor,
} from '../formatters';

describe('formatDuration', () => {
  it('should return fallback for null/undefined', () => {
    expect(formatDuration(null)).toBe('0:00');
    expect(formatDuration(undefined)).toBe('0:00');
    expect(formatDuration(0)).toBe('0:00');
  });

  it('should format seconds correctly', () => {
    expect(formatDuration(30)).toBe('0:30');
    expect(formatDuration(90)).toBe('1:30');
  });

  it('should format minutes and hours', () => {
    expect(formatDuration(3600)).toBe('1:00:00');
    expect(formatDuration(3661)).toBe('1:01:01');
    expect(formatDuration(7325)).toBe('2:02:05');
  });

  it('should handle large values', () => {
    expect(formatDuration(86400)).toBe('24:00:00'); // 24 hours
    expect(formatDuration(360000)).toBe('100:00:00'); // 100 hours
  });

  it('should handle custom fallback', () => {
    expect(formatDuration(null, 'N/A')).toBe('N/A');
    expect(formatDuration(undefined, '--:--')).toBe('--:--');
  });
});

describe('formatDate', () => {
  it('should return empty string for null/undefined', () => {
    expect(formatDate(null)).toBe('');
    expect(formatDate(undefined)).toBe('');
    expect(formatDate('')).toBe('');
  });

  it('should format valid date strings', () => {
    const result = formatDate('2024-01-15T12:00:00Z');
    expect(result).toContain('2024');
    expect(result).toContain('15');
  });

  it('should support different month formats', () => {
    const short = formatDate('2024-01-15', 'short');
    const long = formatDate('2024-01-15', 'long');
    expect(short).toContain('Jan');
    expect(long).toContain('January');
  });
});

describe('formatBytes', () => {
  it('should return 0 Bytes for null/undefined/zero', () => {
    expect(formatBytes(null)).toBe('0 Bytes');
    expect(formatBytes(undefined)).toBe('0 Bytes');
    expect(formatBytes(0)).toBe('0 Bytes');
  });

  it('should format bytes correctly', () => {
    expect(formatBytes(500)).toBe('500 Bytes');
    expect(formatBytes(1024)).toBe('1 KB');
    expect(formatBytes(1536)).toBe('1.5 KB');
  });

  it('should format larger sizes', () => {
    expect(formatBytes(1048576)).toBe('1 MB');
    expect(formatBytes(1073741824)).toBe('1 GB');
    expect(formatBytes(1099511627776)).toBe('1 TB');
  });

  it('should handle custom decimal places', () => {
    expect(formatBytes(1536, 0)).toBe('2 KB');
    expect(formatBytes(1536, 3)).toBe('1.5 KB');
  });

  it('should handle very large values', () => {
    expect(formatBytes(1125899906842624)).toBe('1 PB');
  });
});

describe('formatPercent', () => {
  it('should return 0% for null/undefined', () => {
    expect(formatPercent(null)).toBe('0%');
    expect(formatPercent(undefined)).toBe('0%');
  });

  it('should format percentages', () => {
    expect(formatPercent(50)).toBe('50.0%');
    expect(formatPercent(99.9)).toBe('99.9%');
    expect(formatPercent(0)).toBe('0.0%');
  });

  it('should handle custom decimals', () => {
    expect(formatPercent(33.333, 0)).toBe('33%');
    expect(formatPercent(33.333, 2)).toBe('33.33%');
  });

  it('should handle values over 100', () => {
    expect(formatPercent(150)).toBe('150.0%');
  });

  it('should handle negative values', () => {
    expect(formatPercent(-10)).toBe('-10.0%');
  });
});

describe('formatHours', () => {
  it('should return 0h for null/undefined/zero', () => {
    expect(formatHours(null)).toBe('0h');
    expect(formatHours(undefined)).toBe('0h');
    expect(formatHours(0)).toBe('0h');
  });

  it('should format minutes for less than 1 hour', () => {
    expect(formatHours(0.5)).toBe('30m');
    expect(formatHours(0.25)).toBe('15m');
  });

  it('should format hours', () => {
    expect(formatHours(1)).toBe('1.0h');
    expect(formatHours(2.5)).toBe('2.5h');
    expect(formatHours(100)).toBe('100.0h');
  });
});

describe('formatWatchTime', () => {
  it('should return 0m for null/undefined/zero', () => {
    expect(formatWatchTime(null)).toBe('0m');
    expect(formatWatchTime(undefined)).toBe('0m');
    expect(formatWatchTime(0)).toBe('0m');
  });

  it('should format minutes', () => {
    expect(formatWatchTime(60)).toBe('1m');
    expect(formatWatchTime(300)).toBe('5m');
  });

  it('should format hours and minutes', () => {
    expect(formatWatchTime(3600)).toBe('1h 0m');
    expect(formatWatchTime(3900)).toBe('1h 5m');
    expect(formatWatchTime(7200)).toBe('2h 0m');
  });
});

describe('formatTimeSince', () => {
  it('should return Unknown for null/undefined', () => {
    expect(formatTimeSince(null)).toBe('Unknown');
    expect(formatTimeSince(undefined)).toBe('Unknown');
  });

  it('should format numeric seconds', () => {
    expect(formatTimeSince(30)).toBe('30s ago');
    expect(formatTimeSince(90)).toBe('1m ago');
    expect(formatTimeSince(3600)).toBe('1h ago');
    expect(formatTimeSince(86400)).toBe('1d ago');
  });

  it('should handle edge cases for seconds', () => {
    expect(formatTimeSince(0)).toBe('0s ago');
    expect(formatTimeSince(59)).toBe('59s ago');
    expect(formatTimeSince(60)).toBe('1m ago');
  });

  it('should handle large values', () => {
    expect(formatTimeSince(604800)).toBe('7d ago'); // 7 days
    expect(formatTimeSince(2592000)).toBe('30d ago'); // 30 days
  });
});

describe('formatDeploymentTime', () => {
  it('should return empty string for null/undefined', () => {
    expect(formatDeploymentTime(null)).toBe('');
    expect(formatDeploymentTime(undefined)).toBe('');
    expect(formatDeploymentTime('')).toBe('');
  });

  // Note: Time-based tests are environment-dependent
  // These tests verify the function runs without error
  it('should format recent timestamps', () => {
    const recent = new Date(Date.now() - 3600000).toISOString(); // 1 hour ago
    const result = formatDeploymentTime(recent);
    expect(result).toBeTruthy();
    expect(typeof result).toBe('string');
  });

  it('should format older timestamps', () => {
    const old = new Date(Date.now() - 172800000).toISOString(); // 2 days ago
    const result = formatDeploymentTime(old);
    expect(result).toBeTruthy();
    expect(typeof result).toBe('string');
  });
});

describe('isVersionOutdated', () => {
  it('should return false for null/undefined', () => {
    expect(isVersionOutdated(undefined, undefined)).toBe(false);
    expect(isVersionOutdated('1.0.0', undefined)).toBe(false);
    expect(isVersionOutdated(undefined, '1.0.0')).toBe(false);
  });

  it('should return false for matching versions', () => {
    expect(isVersionOutdated('1.0.0', '1.0.0')).toBe(false);
    expect(isVersionOutdated('abc123', 'abc123')).toBe(false);
  });

  it('should return true for different versions', () => {
    expect(isVersionOutdated('1.0.0', '1.0.1')).toBe(true);
    expect(isVersionOutdated('abc123', 'def456')).toBe(true);
  });
});

describe('getEventIcon', () => {
  it('should return correct icons for known event types', () => {
    expect(getEventIcon('deployed')).toBe('rocket');
    expect(getEventIcon('restarted')).toBe('refresh');
    expect(getEventIcon('updated')).toBe('download');
    expect(getEventIcon('deleted')).toBe('trash');
  });

  it('should return info for unknown event types', () => {
    expect(getEventIcon('unknown')).toBe('info');
    expect(getEventIcon('')).toBe('info');
  });
});

describe('getEventColor', () => {
  it('should return correct colors for known event types', () => {
    expect(getEventColor('deployed')).toBe('text-green-400');
    expect(getEventColor('restarted')).toBe('text-blue-400');
    expect(getEventColor('updated')).toBe('text-purple-400');
    expect(getEventColor('deleted')).toBe('text-red-400');
  });

  it('should return gray for unknown event types', () => {
    expect(getEventColor('unknown')).toBe('text-gray-400');
    expect(getEventColor('')).toBe('text-gray-400');
  });
});
