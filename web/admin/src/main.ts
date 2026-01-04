/**
 * VLog Admin - Main Entry Point
 *
 * This module initializes the admin UI by:
 * 1. Importing design tokens (CSS custom properties)
 * 2. Registering all web components
 * 3. Setting up the admin store factory for Alpine.js
 * 4. Starting Alpine.js (to ensure correct execution order)
 */

// Import Alpine.js CSP build - avoids unsafe-eval for stricter CSP
import Alpine from '@alpinejs/csp';

// Import design tokens
import '@/styles/tokens.css';

// Import and register all web components
import '@/components/index';

// Import the admin store factory
import { createAdminStore } from '@/stores/index';
import type { AdminStore } from '@/stores/index';

// Import API modules for external access
import { apiClient } from '@/api/client';
import { authApi } from '@/api/endpoints/auth';
import { videosApi } from '@/api/endpoints/videos';
import { categoriesApi } from '@/api/endpoints/categories';
import { workersApi } from '@/api/endpoints/workers';
import { analyticsApi } from '@/api/endpoints/analytics';
import { settingsApi } from '@/api/endpoints/settings';
import { customFieldsApi } from '@/api/endpoints/custom-fields';
import { sseApi } from '@/api/endpoints/sse';

// Import formatters for template use
import * as formatters from '@/utils/formatters';

// Declare global types
declare global {
  interface Window {
    // Alpine.js
    Alpine: typeof Alpine;

    // Admin store factory
    createAdminStore: typeof createAdminStore;
    admin: () => AdminStore;

    // API access
    VLogApi: {
      client: typeof apiClient;
      auth: typeof authApi;
      videos: typeof videosApi;
      categories: typeof categoriesApi;
      workers: typeof workersApi;
      analytics: typeof analyticsApi;
      settings: typeof settingsApi;
      customFields: typeof customFieldsApi;
      sse: typeof sseApi;
    };

    // Formatters
    VLogFormatters: typeof formatters;
  }
}

// Export Alpine to window for debugging
window.Alpine = Alpine;

// Export store factory to window for Alpine.js
window.createAdminStore = createAdminStore;

// Create the admin() function that Alpine.js will call
// This is a wrapper that creates and returns the store
window.admin = () => createAdminStore();

// Export API modules to window for direct access
window.VLogApi = {
  client: apiClient,
  auth: authApi,
  videos: videosApi,
  categories: categoriesApi,
  workers: workersApi,
  analytics: analyticsApi,
  settings: settingsApi,
  customFields: customFieldsApi,
  sse: sseApi,
};

// Export formatters
window.VLogFormatters = formatters;

// Start Alpine.js AFTER everything is set up
// Note: Keyboard shortcuts are initialized in stores/index.ts when the admin store starts
// This ensures window.admin() is defined before Alpine processes x-data
Alpine.start();

// Export for module consumers
export { createAdminStore };
export type { AdminStore };
