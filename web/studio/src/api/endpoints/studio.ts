/**
 * Studio API Endpoints
 */

import { apiClient } from '../client';
import type {
  Stream,
  StreamListResponse,
  StreamCreateRequest,
  StreamUpdateRequest,
  StreamCreatedResponse,
  StreamKeyResponse,
} from '../types';

export const studioApi = {
  /**
   * List user's streams
   */
  async listStreams(page = 1, pageSize = 20, status?: string): Promise<StreamListResponse> {
    let url = `/api/v1/studio/streams?page=${page}&page_size=${pageSize}`;
    if (status) {
      url += `&status=${status}`;
    }
    return apiClient.fetch<StreamListResponse>(url);
  },

  /**
   * Get a specific stream
   */
  async getStream(slug: string): Promise<Stream> {
    return apiClient.fetch<Stream>(`/api/v1/studio/streams/${slug}`);
  },

  /**
   * Create a new stream
   */
  async createStream(data: StreamCreateRequest): Promise<StreamCreatedResponse> {
    return apiClient.fetch<StreamCreatedResponse>('/api/v1/studio/streams', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  /**
   * Update a stream
   */
  async updateStream(slug: string, data: StreamUpdateRequest): Promise<Stream> {
    return apiClient.fetch<Stream>(`/api/v1/studio/streams/${slug}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    });
  },

  /**
   * End a stream
   */
  async endStream(slug: string): Promise<Stream> {
    return apiClient.fetch<Stream>(`/api/v1/studio/streams/${slug}/end`, {
      method: 'POST',
    });
  },

  /**
   * Regenerate stream key
   */
  async regenerateKey(slug: string): Promise<StreamKeyResponse> {
    return apiClient.fetch<StreamKeyResponse>(`/api/v1/studio/streams/${slug}/key/regenerate`, {
      method: 'POST',
    });
  },
};

/**
 * SSE connection for stream metrics
 */
export interface SSEConnection {
  close: () => void;
}

export interface SSEOptions {
  onMessage: (event: unknown) => void;
  onOpen?: () => void;
  onError?: (error: Event) => void;
  reconnectDelay?: number;
  maxReconnectAttempts?: number;
}

export function connectStreamMetrics(slug: string, options: SSEOptions): SSEConnection {
  const { onMessage, onOpen, onError, reconnectDelay = 5000, maxReconnectAttempts = 10 } = options;

  let eventSource: EventSource | null = null;
  let reconnectAttempts = 0;
  let isClosed = false;

  function connect() {
    if (isClosed) return;

    eventSource = new EventSource(`/api/v1/studio/streams/${slug}/events`);

    eventSource.onopen = () => {
      reconnectAttempts = 0;
      onOpen?.();
    };

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        onMessage(data);
      } catch (e) {
        console.error('Failed to parse SSE message:', e);
      }
    };

    eventSource.addEventListener('metrics', (event) => {
      try {
        const data = JSON.parse((event as MessageEvent).data);
        onMessage(data);
      } catch (e) {
        console.error('Failed to parse metrics event:', e);
      }
    });

    eventSource.addEventListener('session_expired', () => {
      console.warn('Session expired');
      close();
      window.location.reload();
    });

    eventSource.addEventListener('heartbeat', () => {
      // Heartbeat received, connection is alive
    });

    eventSource.onerror = (error) => {
      onError?.(error);

      if (eventSource?.readyState === EventSource.CLOSED) {
        reconnectAttempts++;
        if (reconnectAttempts <= maxReconnectAttempts) {
          console.log(`SSE reconnecting... (attempt ${reconnectAttempts})`);
          setTimeout(connect, reconnectDelay);
        } else {
          console.error('Max SSE reconnect attempts reached');
        }
      }
    };
  }

  function close() {
    isClosed = true;
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
  }

  connect();

  return { close };
}
