/**
 * Chat Store (Issue #530)
 * Manages real-time chat via WebSocket with fallback to REST
 */

import { chatApi } from '@/api/endpoints/chat';
import { ChatWebSocket, createChatWebSocket } from '@/api/websocket';
import type {
  ChatMessage,
  ChatSettings,
  StreamModerator,
} from '@/api/types';

export interface ChatState {
  // Messages
  messages: ChatMessage[];
  messagesLoading: boolean;
  messagesError: string | null;
  hasMoreMessages: boolean;
  oldestMessageId: number | null;

  // Connection
  wsConnected: boolean;
  wsReconnecting: boolean;
  wsError: string | null;

  // User info (from WebSocket)
  chatUserId: string | null;
  chatUsername: string | null;
  isModerator: boolean;
  isOwner: boolean;

  // Settings
  chatSettings: ChatSettings | null;
  settingsLoading: boolean;

  // Message input
  messageInput: string;
  sendingMessage: boolean;

  // Moderators
  moderators: StreamModerator[];
  moderatorsLoading: boolean;
}

export interface ChatActions {
  // WebSocket
  connectChat(streamSlug: string): void;
  disconnectChat(): void;

  // Messages
  loadMessages(streamSlug: string): Promise<void>;
  loadMoreMessages(streamSlug: string): Promise<void>;
  sendMessage(streamSlug: string): Promise<void>;
  deleteMessage(streamSlug: string, messageId: number): Promise<void>;

  // Settings
  loadSettings(streamSlug: string): Promise<void>;
  updateSettings(streamSlug: string, settings: Partial<ChatSettings>): Promise<void>;

  // Moderators
  loadModerators(streamSlug: string): Promise<void>;
  addModerator(streamSlug: string, userId: string, permissions?: string[]): Promise<void>;
  removeModerator(streamSlug: string, userId: string): Promise<void>;

  // Utilities
  formatTimestamp(timestamp: string): string;
  canSendMessages(): boolean;
}

export type ChatStore = ChatState & ChatActions;

// Keep WebSocket instance outside store for persistence
let chatWs: ChatWebSocket | null = null;
let currentStreamSlug: string | null = null;

export function createChatStore(): ChatStore {
  return {
    // Initial state
    messages: [],
    messagesLoading: false,
    messagesError: null,
    hasMoreMessages: false,
    oldestMessageId: null,

    wsConnected: false,
    wsReconnecting: false,
    wsError: null,

    chatUserId: null,
    chatUsername: null,
    isModerator: false,
    isOwner: false,

    chatSettings: null,
    settingsLoading: false,

    messageInput: '',
    sendingMessage: false,

    moderators: [],
    moderatorsLoading: false,

    // ==========================================================================
    // WebSocket
    // ==========================================================================

    /**
     * Connect to chat WebSocket
     */
    connectChat(streamSlug: string): void {
      // Disconnect existing if different stream
      if (chatWs && currentStreamSlug !== streamSlug) {
        chatWs.disconnect();
        chatWs = null;
      }

      // Already connected to this stream
      if (chatWs?.isConnected && currentStreamSlug === streamSlug) {
        return;
      }

      currentStreamSlug = streamSlug;
      this.wsError = null;
      this.wsReconnecting = true;

      // Create new WebSocket
      chatWs = createChatWebSocket(streamSlug);

      // Set up event handlers
      chatWs.on('connected', (data: {
        user_id: string;
        username: string;
        is_moderator: boolean;
        is_owner: boolean;
        settings: ChatSettings;
      }) => {
        this.wsConnected = true;
        this.wsReconnecting = false;
        this.wsError = null;
        this.chatUserId = data.user_id;
        this.chatUsername = data.username;
        this.isModerator = data.is_moderator;
        this.isOwner = data.is_owner;
        this.chatSettings = data.settings;
      });

      chatWs.on('close', () => {
        this.wsConnected = false;
        if (chatWs?.isReconnecting) {
          this.wsReconnecting = true;
        }
      });

      chatWs.on('error', (data: { error?: string; code?: string }) => {
        this.wsError = data.error || 'Connection error';
        if (data.code === 'session_expired') {
          // Session expired, trigger re-auth
          this.wsConnected = false;
          this.wsReconnecting = false;
        }
      });

      chatWs.on('message', (data: {
        id: number;
        user_id: string;
        username: string;
        content: string;
        timestamp: string;
      }) => {
        // Add message to list
        const message: ChatMessage = {
          id: data.id,
          stream_id: 0, // Will be set by server
          user_id: data.user_id,
          username: data.username,
          content: data.content,
          stream_offset_ms: null,
          created_at: data.timestamp,
          deleted_at: null,
          deleted_by_username: null,
        };

        // Add to beginning (newest first)
        this.messages = [message, ...this.messages];
      });

      chatWs.on('message_deleted', (data: {
        message_id: number;
        deleted_by: string;
      }) => {
        // Mark message as deleted
        this.messages = this.messages.map(m =>
          m.id === data.message_id
            ? { ...m, deleted_at: new Date().toISOString(), deleted_by_username: data.deleted_by }
            : m
        );
      });

      chatWs.on('settings_updated', (data: { settings: ChatSettings }) => {
        this.chatSettings = data.settings;
      });

      chatWs.on('shutdown', () => {
        this.wsConnected = false;
        this.wsReconnecting = false;
        this.wsError = 'Server is shutting down';
      });

      // Connect
      chatWs.connect();
    },

    /**
     * Disconnect from chat
     */
    disconnectChat(): void {
      if (chatWs) {
        chatWs.disconnect();
        chatWs = null;
      }
      currentStreamSlug = null;
      this.wsConnected = false;
      this.wsReconnecting = false;
      this.messages = [];
    },

    // ==========================================================================
    // Messages
    // ==========================================================================

    /**
     * Load initial messages via REST
     */
    async loadMessages(streamSlug: string): Promise<void> {
      this.messagesLoading = true;
      this.messagesError = null;

      try {
        const response = await chatApi.listMessages(streamSlug);
        this.messages = response.messages;
        this.hasMoreMessages = response.has_more;
        this.oldestMessageId = response.before_id;
      } catch (e) {
        this.messagesError = e instanceof Error ? e.message : 'Failed to load messages';
      } finally {
        this.messagesLoading = false;
      }
    },

    /**
     * Load more (older) messages
     */
    async loadMoreMessages(streamSlug: string): Promise<void> {
      if (!this.hasMoreMessages || !this.oldestMessageId) return;

      this.messagesLoading = true;

      try {
        const response = await chatApi.listMessages(
          streamSlug,
          this.oldestMessageId
        );
        // Append older messages
        this.messages = [...this.messages, ...response.messages];
        this.hasMoreMessages = response.has_more;
        this.oldestMessageId = response.before_id;
      } catch (e) {
        this.messagesError = e instanceof Error ? e.message : 'Failed to load messages';
      } finally {
        this.messagesLoading = false;
      }
    },

    /**
     * Send a chat message
     */
    async sendMessage(streamSlug: string): Promise<void> {
      const content = this.messageInput.trim();
      if (!content) return;

      this.sendingMessage = true;

      try {
        // Try WebSocket first
        if (chatWs?.isConnected) {
          const sent = chatWs.sendMessage(content);
          if (sent) {
            this.messageInput = '';
            this.sendingMessage = false;
            return;
          }
        }

        // Fall back to REST
        await chatApi.sendMessage(streamSlug, content);
        this.messageInput = '';

        // Reload messages since REST doesn't broadcast to us
        await this.loadMessages(streamSlug);
      } catch (e) {
        this.wsError = e instanceof Error ? e.message : 'Failed to send message';
      } finally {
        this.sendingMessage = false;
      }
    },

    /**
     * Delete a chat message
     */
    async deleteMessage(streamSlug: string, messageId: number): Promise<void> {
      try {
        // Try WebSocket first
        if (chatWs?.isConnected) {
          const sent = chatWs.deleteMessage(messageId);
          if (sent) return;
        }

        // Fall back to REST
        await chatApi.deleteMessage(streamSlug, messageId);

        // Update local state
        this.messages = this.messages.map(m =>
          m.id === messageId
            ? { ...m, deleted_at: new Date().toISOString(), deleted_by_username: this.chatUsername || 'moderator' }
            : m
        );
      } catch (e) {
        this.wsError = e instanceof Error ? e.message : 'Failed to delete message';
      }
    },

    // ==========================================================================
    // Settings
    // ==========================================================================

    /**
     * Load chat settings via REST
     */
    async loadSettings(streamSlug: string): Promise<void> {
      this.settingsLoading = true;

      try {
        this.chatSettings = await chatApi.getSettings(streamSlug);
      } catch (e) {
        console.error('Failed to load chat settings:', e);
      } finally {
        this.settingsLoading = false;
      }
    },

    /**
     * Update chat settings
     */
    async updateSettings(streamSlug: string, settings: Partial<ChatSettings>): Promise<void> {
      this.settingsLoading = true;

      try {
        this.chatSettings = await chatApi.updateSettings(streamSlug, settings);
      } catch (e) {
        this.wsError = e instanceof Error ? e.message : 'Failed to update settings';
        throw e;
      } finally {
        this.settingsLoading = false;
      }
    },

    // ==========================================================================
    // Moderators
    // ==========================================================================

    /**
     * Load stream moderators
     */
    async loadModerators(streamSlug: string): Promise<void> {
      this.moderatorsLoading = true;

      try {
        const response = await chatApi.listModerators(streamSlug);
        this.moderators = response.moderators;
      } catch (e) {
        console.error('Failed to load moderators:', e);
      } finally {
        this.moderatorsLoading = false;
      }
    },

    /**
     * Add a moderator
     */
    async addModerator(
      streamSlug: string,
      userId: string,
      permissions?: string[]
    ): Promise<void> {
      try {
        const mod = await chatApi.addModerator(streamSlug, {
          user_id: userId,
          permissions,
        });
        this.moderators = [...this.moderators, mod];
      } catch (e) {
        throw e;
      }
    },

    /**
     * Remove a moderator
     */
    async removeModerator(streamSlug: string, userId: string): Promise<void> {
      try {
        await chatApi.removeModerator(streamSlug, userId);
        this.moderators = this.moderators.filter(m => m.user_id !== userId);
      } catch (e) {
        throw e;
      }
    },

    // ==========================================================================
    // Utilities
    // ==========================================================================

    /**
     * Format timestamp for display
     */
    formatTimestamp(timestamp: string): string {
      const date = new Date(timestamp);
      const now = new Date();
      const diff = now.getTime() - date.getTime();

      // Less than 1 minute
      if (diff < 60000) {
        return 'just now';
      }

      // Less than 1 hour
      if (diff < 3600000) {
        const minutes = Math.floor(diff / 60000);
        return `${minutes}m ago`;
      }

      // Same day
      if (date.toDateString() === now.toDateString()) {
        return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      }

      // Different day
      return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
    },

    /**
     * Check if user can send messages
     */
    canSendMessages(): boolean {
      if (!this.chatSettings?.chat_enabled) return false;
      if (!this.wsConnected) return false;
      return true;
    },
  };
}
