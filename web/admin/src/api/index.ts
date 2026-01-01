/**
 * VLog Admin API
 * Centralized API module with typed endpoints
 */

// Export the API client
export { apiClient, ApiClient } from './client';
export type { ApiClientConfig, RequestOptions } from './client';

// Export all types
export * from './types';

// Import endpoint modules for the convenience api object
import { authApi } from './endpoints/auth';
import { videosApi } from './endpoints/videos';
import { categoriesApi } from './endpoints/categories';
import { workersApi } from './endpoints/workers';
import { analyticsApi } from './endpoints/analytics';
import { settingsApi } from './endpoints/settings';
import { customFieldsApi } from './endpoints/custom-fields';
import { sseApi } from './endpoints/sse';

// Re-export endpoint modules
export { authApi, videosApi, categoriesApi, workersApi, analyticsApi, settingsApi, customFieldsApi, sseApi };
export type { UploadCallbacks } from './endpoints/videos';
export type { AnalyticsPeriod, VideoAnalyticsOptions } from './endpoints/analytics';
export type { CreateCustomFieldRequest, UpdateCustomFieldRequest } from './endpoints/custom-fields';
export type { SSEConnectionOptions, SSEConnection } from './endpoints/sse';

// Convenience re-export of all APIs as a single object
export const api = {
  auth: authApi,
  videos: videosApi,
  categories: categoriesApi,
  workers: workersApi,
  analytics: analyticsApi,
  settings: settingsApi,
  customFields: customFieldsApi,
  sse: sseApi,
};
