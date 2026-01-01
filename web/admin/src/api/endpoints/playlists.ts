/**
 * Playlists API Endpoints
 */

import { apiClient } from '../client';
import type {
  Playlist,
  PlaylistDetail,
  PlaylistListResponse,
  PlaylistCreateRequest,
  PlaylistUpdateRequest,
  AddVideoToPlaylistRequest,
  ReorderPlaylistRequest,
  PlaylistVideoInfo,
} from '../types';

export const playlistsApi = {
  /**
   * List all playlists
   */
  async list(): Promise<PlaylistListResponse> {
    return apiClient.fetch<PlaylistListResponse>('/api/playlists');
  },

  /**
   * Get a single playlist with videos
   */
  async get(id: number): Promise<PlaylistDetail> {
    return apiClient.fetch<PlaylistDetail>(`/api/playlists/${id}`);
  },

  /**
   * Create a new playlist
   */
  async create(data: PlaylistCreateRequest): Promise<Playlist> {
    const response = await apiClient.fetchResponse('/api/playlists', {
      method: 'POST',
      body: JSON.stringify(data),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Create playlist failed: ${response.status}`);
    }

    return response.json();
  },

  /**
   * Update a playlist
   */
  async update(id: number, data: PlaylistUpdateRequest): Promise<Playlist> {
    const response = await apiClient.fetchResponse(`/api/playlists/${id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Update playlist failed: ${response.status}`);
    }

    return response.json();
  },

  /**
   * Delete a playlist (soft delete)
   */
  async delete(id: number): Promise<void> {
    const response = await apiClient.fetchResponse(`/api/playlists/${id}`, {
      method: 'DELETE',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Delete playlist failed: ${response.status}`);
    }
  },

  /**
   * Get videos in a playlist
   */
  async getVideos(id: number): Promise<PlaylistVideoInfo[]> {
    return apiClient.fetch<PlaylistVideoInfo[]>(`/api/playlists/${id}/videos`);
  },

  /**
   * Add a video to a playlist
   */
  async addVideo(id: number, data: AddVideoToPlaylistRequest): Promise<void> {
    const response = await apiClient.fetchResponse(`/api/playlists/${id}/videos`, {
      method: 'POST',
      body: JSON.stringify(data),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Add video to playlist failed: ${response.status}`);
    }
  },

  /**
   * Remove a video from a playlist
   */
  async removeVideo(playlistId: number, videoId: number): Promise<void> {
    const response = await apiClient.fetchResponse(`/api/playlists/${playlistId}/videos/${videoId}`, {
      method: 'DELETE',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Remove video from playlist failed: ${response.status}`);
    }
  },

  /**
   * Reorder videos in a playlist
   */
  async reorder(id: number, data: ReorderPlaylistRequest): Promise<void> {
    const response = await apiClient.fetchResponse(`/api/playlists/${id}/reorder`, {
      method: 'POST',
      body: JSON.stringify(data),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Reorder playlist failed: ${response.status}`);
    }
  },
};
