/**
 * Studio Dashboard Main Entry Point
 *
 * Initializes Alpine.js and the studio stores.
 */

// Import Alpine (using CSP-safe build)
import Alpine from '@alpinejs/csp';
import { createStores } from './stores';

// Make Alpine available globally
declare global {
  interface Window {
    Alpine: typeof Alpine;
  }
}

// Initialize Alpine
window.Alpine = Alpine;

// Register stores
Alpine.data('studio', () => {
  const stores = createStores();

  return {
    // Spread both stores
    ...stores.auth,
    ...stores.studio,

    // Store references for nested access
    auth: stores.auth,
    studioStore: stores.studio,

    // Initialization
    async init() {
      // Check authentication
      await stores.auth.checkAuth();

      if (!stores.auth.isAuthenticated) {
        // Redirect to login
        window.location.href = '/admin/?redirect=/studio/';
        return;
      }

      if (!stores.auth.canAccessStudio) {
        // Not authorized for studio
        window.location.href = '/admin/';
        return;
      }

      // Check if we have a stream slug in the URL
      const urlParams = new URLSearchParams(window.location.search);
      const streamSlug = urlParams.get('stream');

      if (streamSlug) {
        // Load specific stream
        await stores.studio.loadStream(streamSlug);
      } else {
        // Load stream list
        await stores.studio.loadStreams();
      }
    },

    // Navigation
    selectStream(slug: string) {
      window.history.pushState({}, '', `?stream=${slug}`);
      stores.studio.loadStream(slug);
    },

    backToList() {
      window.history.pushState({}, '', window.location.pathname);
      stores.studio.currentStream = null;
      stores.studio.disconnectSSE();
      stores.studio.loadStreams();
    },

    // Cleanup on destroy
    destroy() {
      stores.studio.destroy();
    },
  };
});

// Start Alpine
Alpine.start();
