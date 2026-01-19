/**
 * Settings API Endpoints
 */

import { apiClient } from '../client';
import type {
  SettingDefinition,
  WatermarkSettings,
  SettingsExportResponse,
} from '../types';

// Response types matching backend schemas
interface SettingsByCategoryResponse {
  categories: Record<string, SettingDefinition[]>;
}

interface SettingsCategoryResponse {
  category: string;
  settings: SettingDefinition[];
}

export const settingsApi = {
  // ===========================================================================
  // General Settings
  // ===========================================================================

  /**
   * Get all settings
   */
  async getAll(): Promise<Record<string, SettingDefinition[]>> {
    const response = await apiClient.fetch<SettingsByCategoryResponse>('/api/settings');
    return response.categories ?? {};
  },

  /**
   * Get settings categories
   */
  async getCategories(): Promise<string[]> {
    return apiClient.fetch<string[]>('/api/settings/categories');
  },

  /**
   * Get settings for a specific category
   */
  async getCategory(category: string): Promise<SettingDefinition[]> {
    const response = await apiClient.fetch<SettingsCategoryResponse>(`/api/settings/category/${category}`);
    return response.settings ?? [];
  },

  /**
   * Update a setting value
   */
  async setValue(key: string, value: string | number | boolean | null): Promise<void> {
    const response = await apiClient.fetchResponse(`/api/settings/key/${key}`, {
      method: 'PUT',
      body: JSON.stringify({ value }),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Update setting failed: ${response.status}`);
    }
  },

  /**
   * Reset a setting to its default value
   */
  async resetValue(key: string): Promise<void> {
    const response = await apiClient.fetchResponse(`/api/settings/key/${key}`, {
      method: 'DELETE',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Reset setting failed: ${response.status}`);
    }
  },

  // ===========================================================================
  // Import/Export
  // ===========================================================================

  /**
   * Export all settings
   */
  async export(): Promise<SettingsExportResponse> {
    const response = await apiClient.fetchResponse('/api/settings/export', {
      method: 'POST',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Export settings failed: ${response.status}`);
    }

    return response.json();
  },

  /**
   * Import settings from a file
   */
  async import(data: SettingsExportResponse): Promise<{ imported: number; skipped: number }> {
    const response = await apiClient.fetchResponse('/api/settings/import', {
      method: 'POST',
      body: JSON.stringify(data),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Import settings failed: ${response.status}`);
    }

    return response.json();
  },

  // ===========================================================================
  // Watermark Settings
  // ===========================================================================

  watermark: {
    /**
     * Get watermark settings
     */
    async get(): Promise<WatermarkSettings> {
      return apiClient.fetch<WatermarkSettings>('/api/settings/watermark');
    },

    /**
     * Save watermark settings (excluding image upload)
     */
    async save(settings: Partial<WatermarkSettings>): Promise<void> {
      const response = await apiClient.fetchResponse('/api/settings/watermark', {
        method: 'POST',
        body: JSON.stringify(settings),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Save watermark settings failed: ${response.status}`);
      }
    },

    /**
     * Upload watermark image with progress tracking
     * Returns the XHR object for abort capability
     */
    upload(
      file: File,
      onProgress?: (percent: number) => void,
      onComplete?: (imageUrl: string) => void,
      onError?: (error: Error) => void
    ): XMLHttpRequest {
      const formData = new FormData();
      formData.append('image', file);

      return apiClient.uploadWithProgress(
        '/api/settings/watermark/upload',
        formData,
        onProgress,
        async (response) => {
          if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            onError?.(new Error(data.detail || `Upload failed: ${response.status}`));
            return;
          }
          const result = await response.json();
          onComplete?.(result.image_url);
        },
        onError
      );
    },

    /**
     * Delete watermark image
     */
    async deleteImage(): Promise<void> {
      const response = await apiClient.fetchResponse('/api/settings/watermark', {
        method: 'DELETE',
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Delete watermark image failed: ${response.status}`);
      }
    },
  },

  // ===========================================================================
  // Branding Settings (Issue #214)
  // ===========================================================================

  branding: {
    /**
     * Get branding settings
     */
    async get(): Promise<BrandingSettings> {
      return apiClient.fetch<BrandingSettings>('/api/settings/branding');
    },

    /**
     * Upload logo with progress tracking
     */
    upload(
      file: File,
      onProgress?: (percent: number) => void,
      onComplete?: (path: string) => void,
      onError?: (error: Error) => void
    ): XMLHttpRequest {
      const formData = new FormData();
      formData.append('file', file);

      return apiClient.uploadWithProgress(
        '/api/settings/branding/logo/upload',
        formData,
        onProgress,
        async (response) => {
          if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            onError?.(new Error(data.detail || `Upload failed: ${response.status}`));
            return;
          }
          const result = await response.json();
          onComplete?.(result.path);
        },
        onError
      );
    },

    /**
     * Delete logo
     */
    async deleteLogo(): Promise<void> {
      const response = await apiClient.fetchResponse('/api/settings/branding/logo', {
        method: 'DELETE',
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Delete logo failed: ${response.status}`);
      }
    },

    /**
     * Upload favicon with progress tracking
     */
    uploadFavicon(
      file: File,
      onProgress?: (percent: number) => void,
      onComplete?: (path: string) => void,
      onError?: (error: Error) => void
    ): XMLHttpRequest {
      const formData = new FormData();
      formData.append('file', file);

      return apiClient.uploadWithProgress(
        '/api/settings/branding/favicon/upload',
        formData,
        onProgress,
        async (response) => {
          if (!response.ok) {
            const data = await response.json().catch(() => ({}));
            onError?.(new Error(data.detail || `Upload failed: ${response.status}`));
            return;
          }
          const result = await response.json();
          onComplete?.(result.path);
        },
        onError
      );
    },

    /**
     * Delete favicon
     */
    async deleteFavicon(): Promise<void> {
      const response = await apiClient.fetchResponse('/api/settings/branding/favicon', {
        method: 'DELETE',
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Delete favicon failed: ${response.status}`);
      }
    },
  },
};

// Branding settings response type
export interface BrandingSettings {
  site_name: string;
  logo_path: string | null;
  logo_exists: boolean;
  logo_url: string | null;
  favicon_path: string | null;
  favicon_exists: boolean;
  favicon_url: string | null;
  footer_text: string | null;
  footer_links: Array<{ label: string; url: string }>;
}
