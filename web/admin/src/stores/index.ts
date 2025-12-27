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
import type { ProgressSSEEvent, WorkerSSEEvent } from '@/api/types';

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
     * Initialize the admin application
     * Called automatically by Alpine.js on component mount
     */
    async init(): Promise<void> {
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

// Polling setup (internal)
function setupPolling(this: AdminStore) {
  // Auto-refresh videos every 30 seconds
  setInterval(() => {
    this.loadVideos();
  }, 30000);

  // Fallback polling for progress if SSE not connected
  setInterval(() => {
    if (!this.progressSSE || this.progressSSE.eventSource.readyState !== EventSource.OPEN) {
      this.loadProgressForActiveVideos();
    }
  }, 5000);

  // Auto-refresh workers every 10 seconds when workers tab is active
  setInterval(() => {
    if (this.tab === 'workers' && (!this.workersSSE || this.workersSSE.eventSource.readyState !== EventSource.OPEN)) {
      this.loadWorkers();
    }
  }, 10000);
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
