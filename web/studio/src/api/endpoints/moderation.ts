/**
 * Moderation API Endpoints (Issue #530 - Phase 2C)
 */

import { apiClient } from '../client';
import type {
  StreamBan,
  StreamBanListResponse,
  StreamBanCreateRequest,
  WordFilter,
  WordFilterListResponse,
  WordFilterCreateRequest,
  ModerationLogListResponse,
} from '../types';

export const moderationApi = {
  // ==========================================================================
  // Bans
  // ==========================================================================

  /**
   * List bans for a stream
   */
  async listBans(
    streamSlug: string,
    activeOnly = true,
    limit = 50,
    offset = 0
  ): Promise<StreamBanListResponse> {
    const params = new URLSearchParams({
      active_only: String(activeOnly),
      limit: String(limit),
      offset: String(offset),
    });
    return apiClient.fetch<StreamBanListResponse>(
      `/api/v1/studio/streams/${streamSlug}/bans?${params}`
    );
  },

  /**
   * Ban or timeout a user
   */
  async createBan(streamSlug: string, data: StreamBanCreateRequest): Promise<StreamBan> {
    return apiClient.fetch<StreamBan>(
      `/api/v1/studio/streams/${streamSlug}/bans`,
      {
        method: 'POST',
        body: JSON.stringify(data),
      }
    );
  },

  /**
   * Unban a user
   */
  async unban(streamSlug: string, banId: number): Promise<{ unbanned: boolean; ban_id: number }> {
    return apiClient.fetch<{ unbanned: boolean; ban_id: number }>(
      `/api/v1/studio/streams/${streamSlug}/bans/${banId}`,
      { method: 'DELETE' }
    );
  },

  /**
   * Check if a user is banned
   */
  async checkBan(
    streamSlug: string,
    userId: string
  ): Promise<{ banned: boolean; ban: { id: number; ban_type: string; reason: string | null; expires_at: string | null } | null }> {
    return apiClient.fetch(
      `/api/v1/studio/streams/${streamSlug}/bans/check/${userId}`
    );
  },

  // ==========================================================================
  // Word Filters
  // ==========================================================================

  /**
   * List word filters for a stream
   */
  async listFilters(streamSlug: string): Promise<WordFilterListResponse> {
    return apiClient.fetch<WordFilterListResponse>(
      `/api/v1/studio/streams/${streamSlug}/filters`
    );
  },

  /**
   * Create a word filter
   */
  async createFilter(streamSlug: string, data: WordFilterCreateRequest): Promise<WordFilter> {
    return apiClient.fetch<WordFilter>(
      `/api/v1/studio/streams/${streamSlug}/filters`,
      {
        method: 'POST',
        body: JSON.stringify(data),
      }
    );
  },

  /**
   * Delete a word filter
   */
  async deleteFilter(
    streamSlug: string,
    filterId: number
  ): Promise<{ deleted: boolean; filter_id: number }> {
    return apiClient.fetch<{ deleted: boolean; filter_id: number }>(
      `/api/v1/studio/streams/${streamSlug}/filters/${filterId}`,
      { method: 'DELETE' }
    );
  },

  // ==========================================================================
  // Moderation Logs
  // ==========================================================================

  /**
   * List moderation logs for a stream
   */
  async listLogs(
    streamSlug: string,
    limit = 50,
    offset = 0
  ): Promise<ModerationLogListResponse> {
    const params = new URLSearchParams({
      limit: String(limit),
      offset: String(offset),
    });
    return apiClient.fetch<ModerationLogListResponse>(
      `/api/v1/studio/streams/${streamSlug}/moderation-logs?${params}`
    );
  },
};
