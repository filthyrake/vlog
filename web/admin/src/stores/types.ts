/**
 * Shared types for Admin stores
 */

/**
 * Alpine.js component context type
 * This provides access to Alpine.js features like $watch, $nextTick, $refs
 */
export interface AlpineContext {
  $watch: <T>(expression: string, callback: (value: T) => void) => void;
  $nextTick: (callback: () => void) => void;
  $refs: Record<string, HTMLElement>;
}

/**
 * Base store interface that all stores extend
 */
export interface BaseStore {
  loading?: boolean;
  error?: string | null;
}

/**
 * Tab identifiers for the admin UI
 */
export type AdminTab = 'videos' | 'categories' | 'upload' | 'workers' | 'analytics' | 'settings';

/**
 * Settings sub-tabs
 */
export type SettingsTab = 'watermark' | 'custom-fields' | 'database';
