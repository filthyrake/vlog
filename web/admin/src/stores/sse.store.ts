/**
 * SSE Store
 * Manages Server-Sent Events connections for real-time updates
 */

import { sseApi } from '@/api/endpoints/sse';
import type { SSEConnection } from '@/api/endpoints/sse';
import type { ProgressSSEEvent, WorkerSSEEvent, Video } from '@/api/types';
import type { AlpineContext } from './types';

export interface SSEState {
  // Connection state
  progressSSE: SSEConnection | null;
  workersSSE: SSEConnection | null;
  sseReconnectAttempts: number;
  maxSseReconnectDelay: number;
}

export interface SSEActions {
  // Progress SSE
  connectProgressSSE(videoIds?: number[]): void;
  disconnectProgressSSE(): void;

  // Workers SSE
  connectWorkersSSE(): void;
  disconnectWorkersSSE(): void;

  // Event handlers (to be set by parent store)
  onProgressEvent?: (event: ProgressSSEEvent) => void;
  onWorkerEvent?: (event: WorkerSSEEvent) => void;
}

export type SSEStore = SSEState & SSEActions;

export function createSSEStore(_context?: AlpineContext): SSEStore {
  return {
    // Connection state
    progressSSE: null,
    workersSSE: null,
    sseReconnectAttempts: 0,
    maxSseReconnectDelay: 60000,

    // Event handlers (can be overridden by parent store)
    onProgressEvent: undefined,
    onWorkerEvent: undefined,

    /**
     * Connect to the progress SSE stream
     */
    connectProgressSSE(videoIds?: number[]): void {
      // Close existing connection if any
      if (this.progressSSE) {
        this.progressSSE.close();
      }

      this.progressSSE = sseApi.connectProgress({
        videoIds,
        onMessage: (event) => {
          if (this.onProgressEvent) {
            this.onProgressEvent(event as ProgressSSEEvent);
          }
        },
        onOpen: () => {
          this.sseReconnectAttempts = 0;
          console.log('Progress SSE connected');
        },
        onError: (error) => {
          console.error('Progress SSE error:', error);
        },
        reconnectDelay: 5000,
        maxReconnectAttempts: 10,
      });
    },

    /**
     * Disconnect from the progress SSE stream
     */
    disconnectProgressSSE(): void {
      if (this.progressSSE) {
        this.progressSSE.close();
        this.progressSSE = null;
      }
    },

    /**
     * Connect to the workers SSE stream
     */
    connectWorkersSSE(): void {
      // Close existing connection if any
      if (this.workersSSE) {
        this.workersSSE.close();
      }

      this.workersSSE = sseApi.connectWorkers({
        onMessage: (event) => {
          if (this.onWorkerEvent) {
            this.onWorkerEvent(event as WorkerSSEEvent);
          }
        },
        onOpen: () => {
          console.log('Workers SSE connected');
        },
        onError: (error) => {
          console.error('Workers SSE error:', error);
        },
        reconnectDelay: 5000,
        maxReconnectAttempts: 10,
      });
    },

    /**
     * Disconnect from the workers SSE stream
     */
    disconnectWorkersSSE(): void {
      if (this.workersSSE) {
        this.workersSSE.close();
        this.workersSSE = null;
      }
    },
  };
}

/**
 * Helper to get active video IDs for SSE filtering
 */
export function getActiveVideoIds(videos: Video[]): number[] {
  return videos
    .filter((v) => v.status === 'pending' || v.status === 'processing')
    .map((v) => v.id);
}
