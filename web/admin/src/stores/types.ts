/**
 * Shared types for Admin stores
 *
 * ## Error Property Naming Convention
 *
 * Stores use contextual error property names to track different error states:
 * - `error` - General store-level errors (e.g., loading list failed)
 * - `{operation}Error` - Operation-specific errors (e.g., `editError`, `uploadError`)
 *
 * This allows the UI to display appropriate error messages for different operations
 * without conflicts. For example, the videos store has:
 * - `error` for video list loading errors
 * - `editError` for video edit modal errors
 * - `reuploadError` for re-upload modal errors
 * - `retranscodeError` for re-transcode modal errors
 * - `thumbnailError` for thumbnail modal errors
 *
 * ## Method Aliases
 *
 * Some stores have method aliases for backward compatibility with existing HTML templates.
 * These are documented with `// Alias for <original method>` comments.
 * When updating HTML templates in the future, prefer the canonical method names.
 */

/**
 * Alpine.js component context type
 * This provides access to Alpine.js features like $watch, $nextTick, $refs
 * Note: Currently not used by stores but kept for future extensibility
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
