/**
 * Videos API Endpoints
 */

import { apiClient } from '../client';
import type {
  Video,
  VideoProgress,
  QualityInfo,
  ThumbnailFrame,
  VideoCustomFields,
  BulkDeleteRequest,
  BulkUpdateRequest,
  BulkRetranscodeRequest,
  BulkRestoreRequest,
  BulkCustomFieldsRequest,
  BulkOperationResult,
} from '../types';

export interface UploadCallbacks {
  onProgress?: (percent: number) => void;
  onComplete?: (video: Video) => void;
  onError?: (error: Error) => void;
}

export const videosApi = {
  // ===========================================================================
  // Core CRUD
  // ===========================================================================

  /**
   * List all videos
   */
  async list(): Promise<Video[]> {
    const response = await apiClient.fetch<{ videos: Video[] }>('/api/videos');
    return response.videos || [];
  },

  /**
   * Get a single video by ID
   */
  async get(id: number): Promise<Video> {
    return apiClient.fetch<Video>(`/api/videos/${id}`);
  },

  /**
   * Upload a new video with progress tracking
   * Returns the XHR object for abort capability
   */
  upload(formData: FormData, callbacks: UploadCallbacks = {}): XMLHttpRequest {
    return apiClient.uploadWithProgress(
      '/api/videos',
      formData,
      callbacks.onProgress,
      async (response) => {
        if (!response.ok) {
          const data = await response.json().catch(() => ({}));
          callbacks.onError?.(new Error(data.detail || `Upload failed: ${response.status}`));
          return;
        }
        const video = await response.json();
        callbacks.onComplete?.(video);
      },
      callbacks.onError
    );
  },

  /**
   * Update video metadata
   */
  async update(id: number, data: FormData): Promise<Video> {
    const response = await apiClient.fetchResponse(`/api/videos/${id}`, {
      method: 'PUT',
      body: data,
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Update failed: ${response.status}`);
    }

    return response.json();
  },

  /**
   * Delete a video (soft delete by default)
   */
  async delete(id: number): Promise<void> {
    const response = await apiClient.fetchResponse(`/api/videos/${id}`, {
      method: 'DELETE',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Delete failed: ${response.status}`);
    }
  },

  /**
   * Retry a failed video
   */
  async retry(id: number): Promise<void> {
    const response = await apiClient.fetchResponse(`/api/videos/${id}/retry`, {
      method: 'POST',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Retry failed: ${response.status}`);
    }
  },

  // ===========================================================================
  // Publish/Unpublish
  // ===========================================================================

  /**
   * Publish a video
   */
  async publish(id: number): Promise<void> {
    const response = await apiClient.fetchResponse(`/api/videos/${id}/publish`, {
      method: 'POST',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Publish failed: ${response.status}`);
    }
  },

  /**
   * Unpublish a video
   */
  async unpublish(id: number): Promise<void> {
    const response = await apiClient.fetchResponse(`/api/videos/${id}/unpublish`, {
      method: 'POST',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Unpublish failed: ${response.status}`);
    }
  },

  // ===========================================================================
  // Re-upload
  // ===========================================================================

  /**
   * Re-upload a video file with progress tracking
   */
  reupload(videoId: number, formData: FormData, callbacks: UploadCallbacks = {}): XMLHttpRequest {
    return apiClient.uploadWithProgress(
      `/api/videos/${videoId}/re-upload`,
      formData,
      callbacks.onProgress,
      async (response) => {
        if (!response.ok) {
          const data = await response.json().catch(() => ({}));
          callbacks.onError?.(new Error(data.detail || `Re-upload failed: ${response.status}`));
          return;
        }
        const video = await response.json();
        callbacks.onComplete?.(video);
      },
      callbacks.onError
    );
  },

  // ===========================================================================
  // Progress & Status
  // ===========================================================================

  /**
   * Get progress for a video
   */
  async getProgress(id: number): Promise<VideoProgress> {
    return apiClient.fetch<VideoProgress>(`/api/videos/${id}/progress`);
  },

  // ===========================================================================
  // Qualities & Transcoding
  // ===========================================================================

  /**
   * Get available qualities for a video
   */
  async getQualities(id: number): Promise<QualityInfo[]> {
    const response = await apiClient.fetch<{ available: QualityInfo[]; existing: QualityInfo[] }>(
      `/api/videos/${id}/qualities`
    );
    return response.available;
  },

  /**
   * Get both available and existing qualities for a video
   */
  async getQualitiesDetailed(id: number): Promise<{ available: QualityInfo[]; existing: QualityInfo[] }> {
    return apiClient.fetch(`/api/videos/${id}/qualities`);
  },

  /**
   * Retranscode a video to selected qualities
   */
  async retranscode(id: number, qualities: string[]): Promise<void> {
    const response = await apiClient.fetchResponse(`/api/videos/${id}/retranscode`, {
      method: 'POST',
      body: JSON.stringify({ qualities }),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Retranscode failed: ${response.status}`);
    }
  },

  // ===========================================================================
  // Thumbnails
  // ===========================================================================

  /**
   * Generate thumbnail frames for a video
   */
  async getThumbnailFrames(id: number, count: number = 10): Promise<ThumbnailFrame[]> {
    const response = await apiClient.fetch<{ frames: ThumbnailFrame[] }>(
      `/api/videos/${id}/thumbnail/frames`,
      {
        method: 'POST',
        body: JSON.stringify({ count }),
      }
    );
    return response.frames;
  },

  /**
   * Select a thumbnail from generated frames
   */
  async selectThumbnail(id: number, timestamp: number): Promise<void> {
    const response = await apiClient.fetchResponse(`/api/videos/${id}/thumbnail/select`, {
      method: 'POST',
      body: JSON.stringify({ timestamp }),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Select thumbnail failed: ${response.status}`);
    }
  },

  /**
   * Upload a custom thumbnail
   */
  async uploadThumbnail(id: number, formData: FormData): Promise<void> {
    const response = await apiClient.fetchResponse(`/api/videos/${id}/thumbnail/upload`, {
      method: 'POST',
      body: formData,
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Upload thumbnail failed: ${response.status}`);
    }
  },

  /**
   * Revert to auto-generated thumbnail
   */
  async revertThumbnail(id: number): Promise<void> {
    const response = await apiClient.fetchResponse(`/api/videos/${id}/thumbnail/revert`, {
      method: 'POST',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Revert thumbnail failed: ${response.status}`);
    }
  },

  // ===========================================================================
  // Custom Fields
  // ===========================================================================

  /**
   * Get custom field values for a video
   */
  async getCustomFields(id: number): Promise<VideoCustomFields> {
    return apiClient.fetch<VideoCustomFields>(`/api/videos/${id}/custom-fields`);
  },

  /**
   * Save custom field values for a video
   */
  async saveCustomFields(id: number, values: VideoCustomFields): Promise<void> {
    const response = await apiClient.fetchResponse(`/api/videos/${id}/custom-fields`, {
      method: 'POST',
      body: JSON.stringify({ values }),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Save custom fields failed: ${response.status}`);
    }
  },

  // ===========================================================================
  // Export
  // ===========================================================================

  /**
   * Export videos as JSON or CSV
   */
  async export(format: 'json' | 'csv' = 'json'): Promise<Blob> {
    const response = await apiClient.fetchResponse(`/api/videos/export?format=${format}`);

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Export failed: ${response.status}`);
    }

    return response.blob();
  },

  // ===========================================================================
  // Sprite Sheet Generation
  // ===========================================================================

  /**
   * Generate sprite sheets for a video (timeline thumbnails)
   */
  async generateSprites(id: number, priority: 'high' | 'normal' | 'low' = 'normal'): Promise<{ message: string; status: string }> {
    const response = await apiClient.fetchResponse(`/api/videos/${id}/sprites/generate`, {
      method: 'POST',
      body: JSON.stringify({ priority }),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Sprite generation failed: ${response.status}`);
    }

    return response.json();
  },

  // ===========================================================================
  // Bulk Operations
  // ===========================================================================

  bulk: {
    /**
     * Delete multiple videos
     */
    async delete(request: BulkDeleteRequest): Promise<BulkOperationResult> {
      const response = await apiClient.fetchResponse('/api/videos/bulk/delete', {
        method: 'POST',
        body: JSON.stringify(request),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Bulk delete failed: ${response.status}`);
      }

      return response.json();
    },

    /**
     * Update multiple videos
     */
    async update(request: BulkUpdateRequest): Promise<BulkOperationResult> {
      const response = await apiClient.fetchResponse('/api/videos/bulk/update', {
        method: 'POST',
        body: JSON.stringify(request),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Bulk update failed: ${response.status}`);
      }

      return response.json();
    },

    /**
     * Retranscode multiple videos
     */
    async retranscode(request: BulkRetranscodeRequest): Promise<BulkOperationResult> {
      const response = await apiClient.fetchResponse('/api/videos/bulk/retranscode', {
        method: 'POST',
        body: JSON.stringify(request),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Bulk retranscode failed: ${response.status}`);
      }

      return response.json();
    },

    /**
     * Restore multiple soft-deleted videos
     */
    async restore(request: BulkRestoreRequest): Promise<BulkOperationResult> {
      const response = await apiClient.fetchResponse('/api/videos/bulk/restore', {
        method: 'POST',
        body: JSON.stringify(request),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Bulk restore failed: ${response.status}`);
      }

      return response.json();
    },

    /**
     * Update custom fields for multiple videos
     */
    async customFields(request: BulkCustomFieldsRequest): Promise<BulkOperationResult> {
      const response = await apiClient.fetchResponse('/api/videos/bulk/custom-fields', {
        method: 'POST',
        body: JSON.stringify(request),
      });

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || `Bulk custom fields failed: ${response.status}`);
      }

      return response.json();
    },
  },
};
