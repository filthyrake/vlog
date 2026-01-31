/**
 * Chat API Endpoints (Issue #530)
 */

import { apiClient } from '../client';
import type {
  ChatMessage,
  ChatMessageListResponse,
  ChatMessageSendRequest,
  ChatSettings,
  ChatSettingsUpdateRequest,
  StreamModerator,
  StreamModeratorListResponse,
  StreamModeratorAddRequest,
  StreamModeratorUpdateRequest,
} from '../types';

export const chatApi = {
  // ==========================================================================
  // Chat Messages
  // ==========================================================================

  /**
   * List chat messages for a stream
   */
  async listMessages(
    streamSlug: string,
    beforeId?: number,
    limit = 50
  ): Promise<ChatMessageListResponse> {
    let url = `/api/v1/studio/streams/${streamSlug}/chat/messages?limit=${limit}`;
    if (beforeId) {
      url += `&before_id=${beforeId}`;
    }
    return apiClient.fetch<ChatMessageListResponse>(url);
  },

  /**
   * Send a chat message (REST fallback for when WebSocket unavailable)
   */
  async sendMessage(streamSlug: string, content: string): Promise<ChatMessage> {
    return apiClient.fetch<ChatMessage>(
      `/api/v1/studio/streams/${streamSlug}/chat/messages`,
      {
        method: 'POST',
        body: JSON.stringify({ content } as ChatMessageSendRequest),
      }
    );
  },

  /**
   * Delete a chat message
   */
  async deleteMessage(
    streamSlug: string,
    messageId: number
  ): Promise<{ deleted: boolean; message_id: number }> {
    return apiClient.fetch<{ deleted: boolean; message_id: number }>(
      `/api/v1/studio/streams/${streamSlug}/chat/messages/${messageId}`,
      { method: 'DELETE' }
    );
  },

  // ==========================================================================
  // Chat Settings
  // ==========================================================================

  /**
   * Get chat settings for a stream
   */
  async getSettings(streamSlug: string): Promise<ChatSettings> {
    return apiClient.fetch<ChatSettings>(
      `/api/v1/studio/streams/${streamSlug}/chat/settings`
    );
  },

  /**
   * Update chat settings for a stream
   */
  async updateSettings(
    streamSlug: string,
    settings: ChatSettingsUpdateRequest
  ): Promise<ChatSettings> {
    return apiClient.fetch<ChatSettings>(
      `/api/v1/studio/streams/${streamSlug}/chat/settings`,
      {
        method: 'PATCH',
        body: JSON.stringify(settings),
      }
    );
  },

  // ==========================================================================
  // Stream Moderators
  // ==========================================================================

  /**
   * List moderators for a stream
   */
  async listModerators(streamSlug: string): Promise<StreamModeratorListResponse> {
    return apiClient.fetch<StreamModeratorListResponse>(
      `/api/v1/studio/streams/${streamSlug}/moderators`
    );
  },

  /**
   * Add a moderator to a stream
   */
  async addModerator(
    streamSlug: string,
    data: StreamModeratorAddRequest
  ): Promise<StreamModerator> {
    return apiClient.fetch<StreamModerator>(
      `/api/v1/studio/streams/${streamSlug}/moderators`,
      {
        method: 'POST',
        body: JSON.stringify(data),
      }
    );
  },

  /**
   * Update a moderator's permissions
   */
  async updateModerator(
    streamSlug: string,
    userId: string,
    data: StreamModeratorUpdateRequest
  ): Promise<StreamModerator> {
    return apiClient.fetch<StreamModerator>(
      `/api/v1/studio/streams/${streamSlug}/moderators/${userId}`,
      {
        method: 'PATCH',
        body: JSON.stringify(data),
      }
    );
  },

  /**
   * Remove a moderator from a stream
   */
  async removeModerator(
    streamSlug: string,
    userId: string
  ): Promise<{ removed: boolean; user_id: string }> {
    return apiClient.fetch<{ removed: boolean; user_id: string }>(
      `/api/v1/studio/streams/${streamSlug}/moderators/${userId}`,
      { method: 'DELETE' }
    );
  },
};
