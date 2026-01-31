/**
 * Chat WebSocket Client (Issue #530)
 *
 * Provides real-time chat functionality via WebSocket with:
 * - Automatic reconnection with exponential backoff
 * - Heartbeat handling
 * - Message queuing during reconnection
 * - Event-based message handling
 */

import type {
  WSClientMessage,
  WSServerMessage,
  WSConnectedMessage,
  ChatSettings,
} from './types';

export type WSEventType =
  | 'open'
  | 'close'
  | 'error'
  | 'connected'
  | 'message'
  | 'message_deleted'
  | 'user_timeout'
  | 'user_ban'
  | 'user_unban'
  | 'settings_updated'
  | 'shutdown';

export type WSEventHandler<T = unknown> = (data: T) => void;

export interface ChatWSConfig {
  maxReconnectAttempts?: number;
  reconnectBaseDelay?: number;
  reconnectMaxDelay?: number;
  heartbeatTimeout?: number;
}

const DEFAULT_CONFIG: Required<ChatWSConfig> = {
  maxReconnectAttempts: 5,
  reconnectBaseDelay: 1000,
  reconnectMaxDelay: 30000,
  heartbeatTimeout: 45000, // Should be > server heartbeat interval (30s)
};

export class ChatWebSocket {
  private ws: WebSocket | null = null;
  private streamSlug: string;
  private config: Required<ChatWSConfig>;

  // Connection state
  private reconnectAttempts = 0;
  private reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimeout: ReturnType<typeof setTimeout> | null = null;
  private intentionalClose = false;
  private isConnecting = false;

  // Event handlers
  private eventHandlers: Map<WSEventType, Set<WSEventHandler>> = new Map();

  // Message queue for messages sent during reconnection
  private messageQueue: WSClientMessage[] = [];

  // Connection info (set after 'connected' message)
  private _userId: string | null = null;
  private _username: string | null = null;
  private _isModerator = false;
  private _isOwner = false;
  private _settings: ChatSettings | null = null;

  constructor(streamSlug: string, config: ChatWSConfig = {}) {
    this.streamSlug = streamSlug;
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  // ==========================================================================
  // Public API
  // ==========================================================================

  get userId(): string | null {
    return this._userId;
  }

  get username(): string | null {
    return this._username;
  }

  get isModerator(): boolean {
    return this._isModerator;
  }

  get isOwner(): boolean {
    return this._isOwner;
  }

  get settings(): ChatSettings | null {
    return this._settings;
  }

  get isConnected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  get isReconnecting(): boolean {
    return this.reconnectTimeout !== null || this.isConnecting;
  }

  /**
   * Connect to the chat WebSocket
   */
  connect(): void {
    if (this.ws?.readyState === WebSocket.OPEN || this.isConnecting) {
      return;
    }

    this.intentionalClose = false;
    this.isConnecting = true;

    // Build WebSocket URL
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${window.location.host}/api/v1/studio/streams/${this.streamSlug}/chat`;

    try {
      this.ws = new WebSocket(url);
      this.setupEventListeners();
    } catch (error) {
      this.isConnecting = false;
      this.emit('error', { error: 'Failed to create WebSocket' });
      this.scheduleReconnect();
    }
  }

  /**
   * Disconnect from the chat WebSocket
   */
  disconnect(): void {
    this.intentionalClose = true;
    this.clearReconnectTimeout();
    this.clearHeartbeatTimeout();

    if (this.ws) {
      this.ws.close(1000, 'Client disconnect');
      this.ws = null;
    }

    this.resetConnectionState();
  }

  /**
   * Send a chat message
   */
  sendMessage(content: string): boolean {
    return this.send({ type: 'message', content });
  }

  /**
   * Delete a chat message
   */
  deleteMessage(messageId: number): boolean {
    return this.send({ type: 'delete', message_id: messageId });
  }

  /**
   * Register an event handler
   */
  on<T = unknown>(event: WSEventType, handler: WSEventHandler<T>): () => void {
    if (!this.eventHandlers.has(event)) {
      this.eventHandlers.set(event, new Set());
    }
    this.eventHandlers.get(event)!.add(handler as WSEventHandler);

    // Return unsubscribe function
    return () => {
      this.eventHandlers.get(event)?.delete(handler as WSEventHandler);
    };
  }

  /**
   * Remove an event handler
   */
  off(event: WSEventType, handler: WSEventHandler): void {
    this.eventHandlers.get(event)?.delete(handler);
  }

  // ==========================================================================
  // Private Methods
  // ==========================================================================

  private setupEventListeners(): void {
    if (!this.ws) return;

    this.ws.onopen = () => {
      this.isConnecting = false;
      this.reconnectAttempts = 0;
      this.emit('open', {});
      this.resetHeartbeatTimeout();
      this.flushMessageQueue();
    };

    this.ws.onclose = (event) => {
      this.isConnecting = false;
      this.clearHeartbeatTimeout();

      this.emit('close', {
        code: event.code,
        reason: event.reason,
        wasClean: event.wasClean,
      });

      if (!this.intentionalClose) {
        this.scheduleReconnect();
      }
    };

    this.ws.onerror = (_event) => {
      this.emit('error', { error: 'WebSocket error' });
    };

    this.ws.onmessage = (event) => {
      this.resetHeartbeatTimeout();
      this.handleMessage(event.data);
    };
  }

  private handleMessage(data: string): void {
    try {
      const message = JSON.parse(data) as WSServerMessage;

      switch (message.type) {
        case 'ping':
          // Respond to ping with pong
          this.send({ type: 'pong' });
          break;

        case 'connected':
          this.handleConnected(message as WSConnectedMessage);
          break;

        case 'chat_message':
          this.emit('message', {
            id: message.id,
            user_id: message.user_id,
            username: message.username,
            content: message.content,
            timestamp: message.timestamp,
          });
          break;

        case 'message_deleted':
          this.emit('message_deleted', {
            message_id: message.message_id,
            deleted_by: message.deleted_by,
          });
          break;

        case 'user_timeout':
          this.emit('user_timeout', {
            target_user_id: message.target_user_id,
            target_username: message.target_username,
            duration_seconds: message.duration_seconds,
            reason: message.reason,
          });
          break;

        case 'user_ban':
          this.emit('user_ban', {
            target_user_id: message.target_user_id,
            target_username: message.target_username,
            reason: message.reason,
          });
          break;

        case 'user_unban':
          this.emit('user_unban', {
            target_user_id: message.target_user_id,
            target_username: message.target_username,
          });
          break;

        case 'settings_updated':
          this._settings = message.settings || this._settings;
          this.emit('settings_updated', { settings: this._settings });
          break;

        case 'error':
          this.emit('error', {
            code: message.code,
            error: message.error || message.message,
            retry_after: message.retry_after,
          });
          break;

        case 'shutdown':
          this.emit('shutdown', { message: message.message });
          // Don't reconnect on server shutdown
          this.intentionalClose = true;
          break;

        default:
          console.warn('Unknown WebSocket message type:', message.type);
      }
    } catch (error) {
      console.error('Failed to parse WebSocket message:', error);
    }
  }

  private handleConnected(message: WSConnectedMessage): void {
    this._userId = message.user_id;
    this._username = message.username;
    this._isModerator = message.is_moderator;
    this._isOwner = message.is_owner;
    this._settings = message.settings;

    this.emit('connected', {
      user_id: this._userId,
      username: this._username,
      is_moderator: this._isModerator,
      is_owner: this._isOwner,
      settings: this._settings,
    });
  }

  private send(message: WSClientMessage): boolean {
    if (this.ws?.readyState === WebSocket.OPEN) {
      try {
        this.ws.send(JSON.stringify(message));
        return true;
      } catch (error) {
        console.error('Failed to send WebSocket message:', error);
        return false;
      }
    } else {
      // Queue message for later
      if (message.type !== 'pong') {
        this.messageQueue.push(message);
      }
      return false;
    }
  }

  private flushMessageQueue(): void {
    while (this.messageQueue.length > 0 && this.isConnected) {
      const message = this.messageQueue.shift()!;
      this.send(message);
    }
  }

  private emit<T>(event: WSEventType, data: T): void {
    const handlers = this.eventHandlers.get(event);
    if (handlers) {
      handlers.forEach((handler) => {
        try {
          handler(data);
        } catch (error) {
          console.error(`Error in WebSocket event handler for '${event}':`, error);
        }
      });
    }
  }

  private scheduleReconnect(): void {
    if (this.intentionalClose || this.reconnectAttempts >= this.config.maxReconnectAttempts) {
      return;
    }

    this.clearReconnectTimeout();

    // Exponential backoff with jitter
    const delay = Math.min(
      this.config.reconnectBaseDelay * Math.pow(2, this.reconnectAttempts) +
        Math.random() * 1000,
      this.config.reconnectMaxDelay
    );

    this.reconnectTimeout = setTimeout(() => {
      this.reconnectTimeout = null;
      this.reconnectAttempts++;
      this.connect();
    }, delay);
  }

  private clearReconnectTimeout(): void {
    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout);
      this.reconnectTimeout = null;
    }
  }

  private resetHeartbeatTimeout(): void {
    this.clearHeartbeatTimeout();
    this.heartbeatTimeout = setTimeout(() => {
      // Connection seems dead, try to reconnect
      console.warn('WebSocket heartbeat timeout, reconnecting...');
      this.ws?.close();
    }, this.config.heartbeatTimeout);
  }

  private clearHeartbeatTimeout(): void {
    if (this.heartbeatTimeout) {
      clearTimeout(this.heartbeatTimeout);
      this.heartbeatTimeout = null;
    }
  }

  private resetConnectionState(): void {
    this._userId = null;
    this._username = null;
    this._isModerator = false;
    this._isOwner = false;
    this._settings = null;
    this.messageQueue = [];
    this.reconnectAttempts = 0;
  }
}

/**
 * Create a chat WebSocket client for a stream
 */
export function createChatWebSocket(
  streamSlug: string,
  config?: ChatWSConfig
): ChatWebSocket {
  return new ChatWebSocket(streamSlug, config);
}
