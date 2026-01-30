/**
 * Studio Store Factory
 * Creates the combined store for the Studio dashboard
 */

import { createAuthStore, type AuthStore } from './auth.store';
import { createStudioStore, type StudioStore } from './studio.store';

export interface StudioAppStore extends AuthStore, StudioStore {
  // App-level state
  initialized: boolean;
  view: 'list' | 'detail';

  // Lifecycle
  init(): Promise<void>;
  destroy(): void;

  // Navigation
  goToList(): void;
  goToDetail(slug: string): void;
}

export function createStudioAppStore(): StudioAppStore {
  const authStore = createAuthStore();
  const studioStore = createStudioStore();

  return {
    // Merge auth store
    ...authStore,

    // Merge studio store
    ...studioStore,

    // App-level state
    initialized: false,
    view: 'list',

    /**
     * Initialize the app
     */
    async init(): Promise<void> {
      // Check authentication
      const isAuthenticated = await this.checkAuth();
      if (!isAuthenticated) {
        return;
      }

      // Load initial data
      await this.loadStreams();
      this.initialized = true;
    },

    /**
     * Cleanup
     */
    destroy(): void {
      this.disconnectSSE();
      this.clearNewStreamKey();
    },

    /**
     * Navigate to list view
     */
    goToList(): void {
      this.view = 'list';
      this.selectedStream = null;
      this.disconnectSSE();
    },

    /**
     * Navigate to detail view
     */
    goToDetail(slug: string): void {
      this.view = 'detail';
      this.selectStream(slug);
    },
  };
}

export type { AuthStore } from './auth.store';
export type { StudioStore } from './studio.store';
