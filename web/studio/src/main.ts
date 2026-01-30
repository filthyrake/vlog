/**
 * VLog Studio - Main Entry Point
 */

import Alpine from '@alpinejs/csp';

// Import design tokens
import '@/styles/tokens.css';

// Import store factory
import { createStudioAppStore } from '@/stores/index';
import type { StudioAppStore } from '@/stores/index';

// Import API client
import { apiClient } from '@/api/client';

// Import formatters
import * as formatters from '@/utils/formatters';

// Declare global types
declare global {
  interface Window {
    Alpine: typeof Alpine;
    createStudioAppStore: typeof createStudioAppStore;
    studio: () => StudioAppStore;
    VLogApi: {
      client: typeof apiClient;
    };
    VLogFormatters: typeof formatters;
  }
}

// Export Alpine to window
window.Alpine = Alpine;

// Export store factory
window.createStudioAppStore = createStudioAppStore;

// Create the studio() function for Alpine.js
window.studio = () => createStudioAppStore();

// Export API and formatters
window.VLogApi = {
  client: apiClient,
};

window.VLogFormatters = formatters;

// Register store with Alpine.data()
Alpine.data('studio', createStudioAppStore);

// Start Alpine.js
Alpine.start();

// Export for module consumers
export { createStudioAppStore };
export type { StudioAppStore };
