/**
 * VLog Admin API
 * Centralized API module with typed endpoints
 */

// Export the API client
export { apiClient, ApiClient } from './client';
export type { ApiClientConfig, RequestOptions } from './client';

// Export all types
export * from './types';

// Export endpoint modules
export { authApi } from './endpoints/auth';
export { videosApi } from './endpoints/videos';
export type { UploadCallbacks } from './endpoints/videos';
export { categoriesApi } from './endpoints/categories';
export { workersApi } from './endpoints/workers';
export { analyticsApi } from './endpoints/analytics';
export type { AnalyticsPeriod, VideoAnalyticsOptions } from './endpoints/analytics';
export { settingsApi } from './endpoints/settings';
export { customFieldsApi } from './endpoints/custom-fields';
export type { CreateCustomFieldRequest, UpdateCustomFieldRequest } from './endpoints/custom-fields';
export { sseApi } from './endpoints/sse';
export type { SSEConnectionOptions, SSEConnection } from './endpoints/sse';

// Convenience re-export of all APIs as a single object
export const api = {
  auth: async () => (await import('./endpoints/auth')).authApi,
  videos: async () => (await import('./endpoints/videos')).videosApi,
  categories: async () => (await import('./endpoints/categories')).categoriesApi,
  workers: async () => (await import('./endpoints/workers')).workersApi,
  analytics: async () => (await import('./endpoints/analytics')).analyticsApi,
  settings: async () => (await import('./endpoints/settings')).settingsApi,
  customFields: async () => (await import('./endpoints/custom-fields')).customFieldsApi,
  sse: async () => (await import('./endpoints/sse')).sseApi,
};
