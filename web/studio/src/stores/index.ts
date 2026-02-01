/**
 * Studio Store Factory
 * Creates the combined store for the Studio dashboard
 */

import { createAuthStore, type AuthStore } from './auth.store';
import { createStudioStore, type StudioStore } from './studio.store';
import { createVODStore, type VODStore } from './vod.store';
import { createChatStore, type ChatStore } from './chat.store';

export interface StudioAppStore extends AuthStore, StudioStore, VODStore, ChatStore {
  // App-level state
  initialized: boolean;
  view: 'list' | 'detail' | 'vods' | 'vod-detail';

  // Lifecycle
  init(): Promise<void>;
  destroy(): void;

  // Navigation
  goToList(): void;
  goToDetail(slug: string): void;
  goToVODs(): void;
  goToVODDetail(slug: string): void;
}

export function createStudioAppStore(): StudioAppStore {
  const authStore = createAuthStore();
  const studioStore = createStudioStore();
  const vodStore = createVODStore();
  const chatStore = createChatStore();

  return {
    // Merge auth store
    ...authStore,

    // Merge studio store
    ...studioStore,

    // Merge VOD store
    ...vodStore,

    // Merge chat store
    ...chatStore,

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
      this.disconnectChat();
      this.clearNewStreamKey();
    },

    /**
     * Navigate to streams list view
     */
    goToList(): void {
      this.view = 'list';
      this.selectedStream = null;
      this.disconnectSSE();
    },

    /**
     * Navigate to stream detail view
     */
    goToDetail(slug: string): void {
      this.view = 'detail';
      this.selectStream(slug);
    },

    /**
     * Navigate to VODs list view
     */
    goToVODs(): void {
      this.view = 'vods';
      this.selectedVOD = null;
      this.vodAnalytics = null;
      this.loadVODs();
    },

    /**
     * Navigate to VOD detail view
     */
    goToVODDetail(slug: string): void {
      this.view = 'vod-detail';
      this.selectVOD(slug);
    },
  };
}

export type { AuthStore } from './auth.store';
export type { StudioStore } from './studio.store';
export type { VODStore } from './vod.store';
export type { ChatStore } from './chat.store';
