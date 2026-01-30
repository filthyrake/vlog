/**
 * Studio Store - Main state for broadcaster dashboard
 */

import type {
  StudioStream,
  StreamMetricsResponse,
  MetricDataPoint,
  ViewerStats,
  ConnectionHealth,
  LiveStreamStatus,
  SSEEvent,
} from '../api/types';
import { studioApi } from '../api';

export interface StudioState {
  // Stream state
  currentStream: StudioStream | null;
  streamKey: string | null;
  showStreamKey: boolean;
  streams: StudioStream[];

  // Real-time metrics
  viewerCount: number;
  peakViewers: number;
  totalViewers: number;
  streamHealth: ConnectionHealth;
  currentBitrate: number;
  bitrateHistory: MetricDataPoint[];

  // SSE connection
  sseConnection: EventSource | null;
  sseConnected: boolean;

  // UI state
  loading: boolean;
  error: string | null;
  passwordDialogOpen: boolean;
  passwordAction: 'show' | 'regenerate' | null;
}

export function createStudioStore() {
  return {
    // Stream state
    currentStream: null as StudioStream | null,
    streamKey: null as string | null,
    showStreamKey: false,
    streams: [] as StudioStream[],

    // Real-time metrics
    viewerCount: 0,
    peakViewers: 0,
    totalViewers: 0,
    streamHealth: 'unknown' as ConnectionHealth,
    currentBitrate: 0,
    bitrateHistory: [] as MetricDataPoint[],

    // SSE connection
    sseConnection: null as EventSource | null,
    sseConnected: false,

    // UI state
    loading: false,
    error: null as string | null,
    passwordDialogOpen: false,
    passwordAction: null as 'show' | 'regenerate' | null,

    // Actions
    async loadStreams(): Promise<void> {
      this.loading = true;
      this.error = null;

      try {
        const response = await studioApi.listStreams();
        this.streams = response.streams;
      } catch (err) {
        this.error = err instanceof Error ? err.message : 'Failed to load streams';
      } finally {
        this.loading = false;
      }
    },

    async loadStream(slug: string): Promise<void> {
      this.loading = true;
      this.error = null;

      try {
        this.currentStream = await studioApi.getStream(slug);
        this.viewerCount = this.currentStream.viewer_count_current;
        this.peakViewers = this.currentStream.viewer_count_peak;
        this.totalViewers = this.currentStream.viewer_count_total;
        this.streamHealth = this.currentStream.connection_health;
        this.currentBitrate = this.currentStream.current_bitrate || 0;

        // Load initial metrics
        await this.loadMetrics();

        // Connect to SSE for real-time updates
        this.connectSSE(slug);
      } catch (err) {
        this.error = err instanceof Error ? err.message : 'Failed to load stream';
      } finally {
        this.loading = false;
      }
    },

    async loadMetrics(): Promise<void> {
      if (!this.currentStream) return;

      try {
        const response = await studioApi.getStreamMetrics(this.currentStream.slug, 5);
        this.bitrateHistory = response.metrics;
        this.streamHealth = response.connection_health;
        this.currentBitrate = response.current_bitrate || 0;
      } catch (err) {
        console.error('Failed to load metrics:', err);
      }
    },

    async loadViewerStats(): Promise<void> {
      if (!this.currentStream) return;

      try {
        const stats = await studioApi.getViewerStats(this.currentStream.slug);
        this.viewerCount = stats.current;
        this.peakViewers = stats.peak;
        this.totalViewers = stats.total;
      } catch (err) {
        console.error('Failed to load viewer stats:', err);
      }
    },

    async updateStream(data: { title?: string; description?: string }): Promise<boolean> {
      if (!this.currentStream) return false;

      try {
        this.currentStream = await studioApi.updateStream(this.currentStream.slug, data);
        return true;
      } catch (err) {
        this.error = err instanceof Error ? err.message : 'Failed to update stream';
        return false;
      }
    },

    async endStream(): Promise<boolean> {
      if (!this.currentStream) return false;

      try {
        this.currentStream = await studioApi.endStream(this.currentStream.slug);
        return true;
      } catch (err) {
        this.error = err instanceof Error ? err.message : 'Failed to end stream';
        return false;
      }
    },

    openPasswordDialog(action: 'show' | 'regenerate'): void {
      // Stream keys can only be regenerated, not retrieved
      this.passwordAction = 'regenerate';
      this.passwordDialogOpen = true;
    },

    closePasswordDialog(): void {
      this.passwordDialogOpen = false;
      this.passwordAction = null;
    },

    async submitPassword(password: string): Promise<boolean> {
      if (!this.currentStream) return false;

      try {
        // Only regeneration is supported (keys are hashed and cannot be retrieved)
        const response = await studioApi.regenerateStreamKey(this.currentStream.slug, password);
        this.streamKey = response.stream_key;
        this.showStreamKey = true;
        this.closePasswordDialog();
        return true;
      } catch (err) {
        this.error = err instanceof Error ? err.message : 'Invalid password';
        return false;
      }
    },

    hideStreamKey(): void {
      this.streamKey = null;
      this.showStreamKey = false;
    },

    connectSSE(slug: string): void {
      // Close existing connection
      this.disconnectSSE();

      const url = `/api/events/studio/${slug}`;
      const eventSource = new EventSource(url, { withCredentials: true });

      eventSource.onopen = () => {
        this.sseConnected = true;
        console.log('SSE connected');
      };

      eventSource.onerror = (event) => {
        console.error('SSE error:', event);
        this.sseConnected = false;
      };

      // Handle different event types
      eventSource.addEventListener('init', (event) => {
        this.handleSSEEvent(JSON.parse((event as MessageEvent).data));
      });

      eventSource.addEventListener('metrics', (event) => {
        this.handleSSEEvent(JSON.parse((event as MessageEvent).data));
      });

      eventSource.addEventListener('viewers', (event) => {
        this.handleSSEEvent(JSON.parse((event as MessageEvent).data));
      });

      eventSource.addEventListener('state', (event) => {
        this.handleSSEEvent(JSON.parse((event as MessageEvent).data));
      });

      eventSource.addEventListener('heartbeat', (event) => {
        // Heartbeat received, connection is alive
        console.debug('SSE heartbeat');
      });

      eventSource.addEventListener('error', (event) => {
        const data = JSON.parse((event as MessageEvent).data);
        console.error('SSE error event:', data.error);
      });

      eventSource.addEventListener('close', (event) => {
        const data = JSON.parse((event as MessageEvent).data);
        console.log('SSE close:', data.reason);
        this.disconnectSSE();
      });

      this.sseConnection = eventSource;
    },

    handleSSEEvent(event: SSEEvent): void {
      switch (event.type) {
        case 'init':
          this.viewerCount = event.viewer_count_current;
          this.peakViewers = event.viewer_count_peak;
          this.streamHealth = event.connection_health;
          this.currentBitrate = event.current_bitrate || 0;
          break;

        case 'metrics':
          this.streamHealth = event.connection_health;
          this.currentBitrate = event.bitrate_total || 0;
          // Add to history (keep last 30 data points)
          this.bitrateHistory.push({
            timestamp: event.timestamp,
            bitrate_total: event.bitrate_total,
            segment_push_latency_ms: event.segment_latency_ms,
            segments_received: event.segments_received,
            segments_dropped: event.segments_dropped,
            interval_seconds: 10,
          });
          if (this.bitrateHistory.length > 30) {
            this.bitrateHistory.shift();
          }
          break;

        case 'viewers':
          this.viewerCount = event.current;
          this.peakViewers = event.peak;
          this.totalViewers = event.total;
          break;

        case 'state':
          if (this.currentStream) {
            this.currentStream.status = event.status;
            this.currentStream.title = event.title;
          }
          break;
      }
    },

    disconnectSSE(): void {
      if (this.sseConnection) {
        this.sseConnection.close();
        this.sseConnection = null;
      }
      this.sseConnected = false;
    },

    // Getters
    get isLive(): boolean {
      return this.currentStream?.status === 'live';
    },

    get isEnding(): boolean {
      return this.currentStream?.status === 'ending';
    },

    get isEnded(): boolean {
      return this.currentStream?.status === 'ended';
    },

    get streamDuration(): string {
      if (!this.currentStream?.started_at) return '0:00';
      const start = new Date(this.currentStream.started_at).getTime();
      const now = Date.now();
      const seconds = Math.floor((now - start) / 1000);
      const hours = Math.floor(seconds / 3600);
      const minutes = Math.floor((seconds % 3600) / 60);
      const secs = seconds % 60;
      if (hours > 0) {
        return `${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
      }
      return `${minutes}:${secs.toString().padStart(2, '0')}`;
    },

    get formattedBitrate(): string {
      const mbps = this.currentBitrate / 125000; // bytes/s to Mbps
      return mbps.toFixed(1) + ' Mbps';
    },

    get healthColor(): string {
      switch (this.streamHealth) {
        case 'good':
          return 'text-green-500';
        case 'degraded':
          return 'text-yellow-500';
        case 'poor':
          return 'text-red-500';
        default:
          return 'text-gray-500';
      }
    },

    // Cleanup
    destroy(): void {
      this.disconnectSSE();
    },
  };
}
