/**
 * Chapters API Endpoints (Issue #413 Phase 7)
 */

import { apiClient } from '../client';
import type {
  Chapter,
  ChapterListResponse,
  ChapterCreateRequest,
  ChapterUpdateRequest,
  ReorderChaptersRequest,
} from '../types';

export const chaptersApi = {
  /**
   * List all chapters for a video
   */
  async list(videoId: number): Promise<ChapterListResponse> {
    return apiClient.fetch<ChapterListResponse>(`/api/videos/${videoId}/chapters`);
  },

  /**
   * Get a single chapter
   */
  async get(videoId: number, chapterId: number): Promise<Chapter> {
    return apiClient.fetch<Chapter>(`/api/videos/${videoId}/chapters/${chapterId}`);
  },

  /**
   * Create a new chapter
   */
  async create(videoId: number, data: ChapterCreateRequest): Promise<Chapter> {
    const response = await apiClient.fetchResponse(`/api/videos/${videoId}/chapters`, {
      method: 'POST',
      body: JSON.stringify(data),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Create chapter failed: ${response.status}`);
    }

    return response.json();
  },

  /**
   * Update a chapter
   */
  async update(videoId: number, chapterId: number, data: ChapterUpdateRequest): Promise<Chapter> {
    const response = await apiClient.fetchResponse(`/api/videos/${videoId}/chapters/${chapterId}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Update chapter failed: ${response.status}`);
    }

    return response.json();
  },

  /**
   * Delete a chapter
   */
  async delete(videoId: number, chapterId: number): Promise<void> {
    const response = await apiClient.fetchResponse(`/api/videos/${videoId}/chapters/${chapterId}`, {
      method: 'DELETE',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Delete chapter failed: ${response.status}`);
    }
  },

  /**
   * Reorder chapters
   */
  async reorder(videoId: number, data: ReorderChaptersRequest): Promise<void> {
    const response = await apiClient.fetchResponse(`/api/videos/${videoId}/chapters/reorder`, {
      method: 'POST',
      body: JSON.stringify(data),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Reorder chapters failed: ${response.status}`);
    }
  },
};
