/**
 * Admin Store
 * Combined store that composes all feature stores for Alpine.js
 */

import { createAuthStore, type AuthStore } from './auth.store';
import { createUIStore, type UIStore } from './ui.store';
import { createVideosStore, type VideosStore } from './videos.store';
import { createCategoriesStore, type CategoriesStore } from './categories.store';
import { createUploadStore, type UploadStore } from './upload.store';
import { createWorkersStore, type WorkersStore } from './workers.store';
import { createAnalyticsStore, type AnalyticsStore } from './analytics.store';
import { createSettingsStore, type SettingsStore } from './settings.store';
import { createBulkStore, type BulkStore } from './bulk.store';
import { createSSEStore, type SSEStore, getActiveVideoIds } from './sse.store';
import { getKeyboardManager, destroyKeyboardManager } from '@/utils/keyboard';
import type { ProgressSSEEvent, WorkerSSEEvent, CustomField } from '@/api/types';

// Polling interval IDs for cleanup
let pollingIntervals: ReturnType<typeof setInterval>[] = [];

// Combined store type
export type AdminStore = AuthStore &
  UIStore &
  VideosStore &
  CategoriesStore &
  UploadStore &
  WorkersStore &
  AnalyticsStore &
  SettingsStore &
  BulkStore &
  SSEStore & {
    init(): Promise<void>;
    destroy(): void;
    getApplicableCustomFields(): CustomField[];
  };

/**
 * Create the combined admin store
 * This factory function returns an object compatible with Alpine.js x-data
 */
export function createAdminStore(): AdminStore {
  // Create all individual stores
  const authStore = createAuthStore();
  const uiStore = createUIStore();
  const videosStore = createVideosStore();
  const categoriesStore = createCategoriesStore();
  const uploadStore = createUploadStore();
  const workersStore = createWorkersStore();
  const analyticsStore = createAnalyticsStore();
  const settingsStore = createSettingsStore();
  const bulkStore = createBulkStore();
  const sseStore = createSSEStore();

  // Create the combined store
  const store: AdminStore = {
    // Spread all stores
    ...authStore,
    ...uiStore,
    ...videosStore,
    ...categoriesStore,
    ...uploadStore,
    ...workersStore,
    ...analyticsStore,
    ...settingsStore,
    ...bulkStore,
    ...sseStore,

    /**
     * Get custom fields applicable to the currently editing video's category
     * Used in the edit modal to show only relevant custom fields
     */
    getApplicableCustomFields(): CustomField[] {
      const categoryId = this.editCategory;
      return this.customFields.filter((field) => {
        // If no category restrictions, field applies to all
        if (!field.applies_to_categories || field.applies_to_categories.length === 0) {
          return true;
        }
        // If editing video has no category, show fields with no restrictions
        if (!categoryId) {
          return field.applies_to_categories.length === 0;
        }
        // Check if field applies to the selected category
        return field.applies_to_categories.includes(categoryId);
      });
    },

    /**
     * Clean up polling intervals and SSE connections
     * Called when the admin component is destroyed (if ever)
     */
    destroy(): void {
      // Clear all polling intervals
      for (const intervalId of pollingIntervals) {
        clearInterval(intervalId);
      }
      pollingIntervals = [];

      // Close SSE connections
      this.disconnectProgressSSE();
      this.disconnectWorkersSSE();

      // Clean up keyboard manager
      destroyKeyboardManager();
    },

    /**
     * Initialize the admin application
     * Called automatically by Alpine.js on component mount
     */
    async init(): Promise<void> {
      // Initialize toast container
      this.initToastContainer();

      // Initialize keyboard shortcuts
      getKeyboardManager();

      // Check authentication first
      const authOk = await this.checkAuth();
      if (!authOk) {
        // Focus the auth input after modal is shown
        // Note: $nextTick and $refs are provided by Alpine context
        return;
      }

      // Fetch CSRF token for state-changing requests
      await this.fetchCsrfToken();

      // Load initial data
      await Promise.all([
        this.loadVideos(),
        this.loadCategories(),
      ]);

      // Set up SSE event handlers
      this.onProgressEvent = (event: ProgressSSEEvent) => {
        this.updateProgress(event.video_id, {
          id: event.video_id,
          status: event.status,
          current_step: event.current_step,
          current_progress: event.current_progress,
          qualities: event.qualities,
        });
      };

      this.onWorkerEvent = (event: WorkerSSEEvent) => {
        // Update worker status in list
        if (event.type === 'status' && event.worker_id) {
          const worker = this.workersList.find((w) => w.worker_id === event.worker_id);
          if (worker && event.status) {
            worker.status = event.status;
            this.computeWorkerStats();
          }
        }
      };

      // Connect to SSE for real-time updates
      this.connectProgressSSE(getActiveVideoIds(this.videos));

      // Set up polling intervals as fallback
      setupPolling.call(this);
    },
  };

  return store;
}

/**
 * Set up polling intervals as fallback for SSE
 * Interval IDs are stored for cleanup in destroy()
 */
function setupPolling(this: AdminStore) {
  // Clear any existing intervals first
  for (const intervalId of pollingIntervals) {
    clearInterval(intervalId);
  }
  pollingIntervals = [];

  // Auto-refresh videos every 30 seconds
  pollingIntervals.push(
    setInterval(() => {
      this.loadVideos();
    }, 30000)
  );

  // Fallback polling for progress if SSE not connected
  pollingIntervals.push(
    setInterval(() => {
      if (!this.progressSSE || this.progressSSE.eventSource.readyState !== EventSource.OPEN) {
        this.loadProgressForActiveVideos();
      }
    }, 5000)
  );

  // Auto-refresh workers every 10 seconds when workers tab is active
  pollingIntervals.push(
    setInterval(() => {
      if (this.tab === 'workers' && (!this.workersSSE || this.workersSSE.eventSource.readyState !== EventSource.OPEN)) {
        this.loadWorkers();
      }
    }, 10000)
  );
}

// Export for Alpine.js global access
declare global {
  interface Window {
    createAdminStore: typeof createAdminStore;
  }
}

if (typeof window !== 'undefined') {
  window.createAdminStore = createAdminStore;
}

// Re-export types and individual store creators for testing/extension
export { createAuthStore, type AuthStore } from './auth.store';
export { createUIStore, type UIStore } from './ui.store';
export { createVideosStore, type VideosStore } from './videos.store';
export { createCategoriesStore, type CategoriesStore } from './categories.store';
export { createUploadStore, type UploadStore } from './upload.store';
export { createWorkersStore, type WorkersStore } from './workers.store';
export { createAnalyticsStore, type AnalyticsStore } from './analytics.store';
export { createSettingsStore, type SettingsStore } from './settings.store';
export { createBulkStore, type BulkStore } from './bulk.store';
export { createSSEStore, type SSEStore } from './sse.store';
export type { AlpineContext, AdminTab, SettingsTab } from './types';
