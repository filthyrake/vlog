/**
 * VOD API Endpoints (Issue #530)
 */

import { apiClient } from '../client';
import type {
  VOD,
  VODListResponse,
  VODUpdateRequest,
  VODAnalytics,
  VODDownloadResponse,
} from '../types';

export const vodApi = {
  /**
   * List user's VODs
   */
  async listVODs(page = 1, pageSize = 20, status?: string): Promise<VODListResponse> {
    let url = `/api/v1/studio/vods?page=${page}&page_size=${pageSize}`;
    if (status) {
      url += `&status=${status}`;
    }
    return apiClient.fetch<VODListResponse>(url);
  },

  /**
   * Get a specific VOD
   */
  async getVOD(slug: string): Promise<VOD> {
    return apiClient.fetch<VOD>(`/api/v1/studio/vods/${slug}`);
  },

  /**
   * Update a VOD
   */
  async updateVOD(slug: string, data: VODUpdateRequest): Promise<VOD> {
    return apiClient.fetch<VOD>(`/api/v1/studio/vods/${slug}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    });
  },

  /**
   * Delete a VOD (soft delete)
   */
  async deleteVOD(slug: string): Promise<{ deleted: boolean; slug: string }> {
    return apiClient.fetch<{ deleted: boolean; slug: string }>(`/api/v1/studio/vods/${slug}`, {
      method: 'DELETE',
    });
  },

  /**
   * Get VOD analytics
   */
  async getVODAnalytics(slug: string): Promise<VODAnalytics> {
    return apiClient.fetch<VODAnalytics>(`/api/v1/studio/vods/${slug}/analytics`);
  },

  /**
   * Get download URL for a VOD
   */
  async getDownloadURL(slug: string): Promise<VODDownloadResponse> {
    return apiClient.fetch<VODDownloadResponse>(`/api/v1/studio/vods/${slug}/download`);
  },

  /**
   * Upload a custom thumbnail for a VOD
   */
  async uploadThumbnail(slug: string, file: File): Promise<VOD> {
    const formData = new FormData();
    formData.append('file', file);

    // Use fetchRaw to handle multipart form data
    const response = await fetch(`/api/v1/studio/vods/${slug}/thumbnail`, {
      method: 'POST',
      body: formData,
      headers: {
        'X-CSRF-Token': apiClient.getCsrfToken(),
      },
      credentials: 'include',
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: 'Upload failed' }));
      throw new Error(error.detail || 'Failed to upload thumbnail');
    }

    return response.json();
  },
};
