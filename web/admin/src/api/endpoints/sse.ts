/**
 * Server-Sent Events (SSE) API
 * Manages real-time event connections for progress and worker updates
 */

import type { ProgressSSEEvent, WorkerSSEEvent } from '../types';

export interface SSEConnectionOptions {
  onMessage?: (event: ProgressSSEEvent | WorkerSSEEvent) => void;
  onError?: (error: Event) => void;
  onOpen?: () => void;
  reconnectDelay?: number;
  maxReconnectAttempts?: number;
}

export interface SSEConnection {
  eventSource: EventSource;
  close: () => void;
  reconnect: () => void;
}

/**
 * Create an SSE connection with automatic reconnection
 */
function createSSEConnection(
  url: string,
  options: SSEConnectionOptions = {}
): SSEConnection {
  const {
    onMessage,
    onError,
    onOpen,
    reconnectDelay = 5000,
    maxReconnectAttempts = 10,
  } = options;

  let eventSource: EventSource;
  let reconnectAttempts = 0;
  let reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  let closed = false;

  const connect = () => {
    if (closed) return;

    eventSource = new EventSource(url);

    eventSource.onopen = () => {
      reconnectAttempts = 0;
      onOpen?.();
    };

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        onMessage?.(data);
      } catch (e) {
        console.error('Failed to parse SSE message:', e);
      }
    };

    eventSource.onerror = (error) => {
      onError?.(error);

      // Attempt reconnection with exponential backoff
      if (!closed && reconnectAttempts < maxReconnectAttempts) {
        eventSource.close();
        const delay = Math.min(reconnectDelay * Math.pow(2, reconnectAttempts), 60000);
        reconnectAttempts++;
        reconnectTimeout = setTimeout(connect, delay);
      }
    };
  };

  connect();

  return {
    get eventSource() {
      return eventSource;
    },

    close() {
      closed = true;
      if (reconnectTimeout) {
        clearTimeout(reconnectTimeout);
        reconnectTimeout = null;
      }
      eventSource?.close();
    },

    reconnect() {
      if (eventSource) {
        eventSource.close();
      }
      reconnectAttempts = 0;
      closed = false;
      connect();
    },
  };
}

export const sseApi = {
  /**
   * Connect to the progress SSE stream
   * @param videoIds Optional array of video IDs to filter events
   */
  connectProgress(
    options: SSEConnectionOptions & { videoIds?: number[] } = {}
  ): SSEConnection {
    const { videoIds, ...sseOptions } = options;
    const url = videoIds?.length
      ? `/api/events/progress?video_ids=${videoIds.join(',')}`
      : '/api/events/progress';

    return createSSEConnection(url, sseOptions);
  },

  /**
   * Connect to the workers SSE stream
   */
  connectWorkers(options: SSEConnectionOptions = {}): SSEConnection {
    return createSSEConnection('/api/events/workers', options);
  },
};
