/**
 * Studio Store
 * Manages streams, metrics, and SSE connections
 */

import { studioApi, connectStreamMetrics } from '@/api/endpoints/studio';
import type { SSEConnection } from '@/api/endpoints/studio';
import type { Stream, StreamMetrics, StreamCreateRequest, StreamUpdateRequest } from '@/api/types';
import { formatBitrate as sharedFormatBitrate, formatTimeSince as sharedFormatTimeSince } from '@/utils/formatters';

export interface StudioState {
  // Streams
  streams: Stream[];
  selectedStream: Stream | null;
  metrics: StreamMetrics | null;
  loading: boolean;
  error: string | null;

  // Pagination
  page: number;
  pageSize: number;
  total: number;
  hasMore: boolean;
  statusFilter: string;

  // SSE
  sseConnection: SSEConnection | null;

  // New stream key display (cleared after acknowledgment)
  newStreamKey: string | null;
  newStreamRtmpUrl: string | null;
  showKeyModal: boolean;
  keySaved: boolean;
  keyExpiryTimeout: ReturnType<typeof setTimeout> | null;

  // Create stream modal
  showCreateModal: boolean;
  createTitle: string;
  createDescription: string;
  createDvrEnabled: boolean;
  createDvrWindow: number;
  createAutoRecordVod: boolean;
  createLoading: boolean;
  createError: string | null;

  // Regenerate key modal
  showRegenerateModal: boolean;
  regenerateLoading: boolean;
  regenerateError: string | null;

  // End stream modal
  showEndModal: boolean;
  endLoading: boolean;
}

export interface StudioActions {
  // Data loading
  loadStreams(page?: number): Promise<void>;
  selectStream(slug: string): Promise<void>;
  refreshSelectedStream(): Promise<void>;

  // Stream management
  createStream(): Promise<void>;
  updateStream(slug: string, data: StreamUpdateRequest): Promise<void>;
  endStream(slug: string): Promise<void>;
  regenerateKey(slug: string): Promise<void>;

  // Key management
  clearNewStreamKey(): void;
  acknowledgeKeySaved(): void;

  // SSE
  connectSSE(slug: string): void;
  disconnectSSE(): void;

  // Modals
  openCreateModal(): void;
  closeCreateModal(): void;
  openRegenerateModal(): void;
  closeRegenerateModal(): void;
  openEndModal(): void;
  closeEndModal(): void;

  // Utilities
  formatBitrate(kbps: number | null): string;
  formatTimeSince(timestamp: string | null): string;
  getStatusColor(status: string): string;
}

export type StudioStore = StudioState & StudioActions;

export function createStudioStore(): StudioStore {
  return {
    // Initial state
    streams: [],
    selectedStream: null,
    metrics: null,
    loading: false,
    error: null,

    page: 1,
    pageSize: 20,
    total: 0,
    hasMore: false,
    statusFilter: '',

    sseConnection: null,

    newStreamKey: null,
    newStreamRtmpUrl: null,
    showKeyModal: false,
    keySaved: false,
    keyExpiryTimeout: null,

    showCreateModal: false,
    createTitle: '',
    createDescription: '',
    createDvrEnabled: true,
    createDvrWindow: 7200,
    createAutoRecordVod: true,
    createLoading: false,
    createError: null,

    showRegenerateModal: false,
    regenerateLoading: false,
    regenerateError: null,

    showEndModal: false,
    endLoading: false,

    /**
     * Load streams
     */
    async loadStreams(page = 1): Promise<void> {
      this.loading = true;
      this.error = null;

      try {
        const response = await studioApi.listStreams(
          page,
          this.pageSize,
          this.statusFilter || undefined
        );
        this.streams = response.streams;
        this.page = response.page;
        this.total = response.total;
        this.hasMore = response.has_more;
      } catch (e) {
        this.error = e instanceof Error ? e.message : 'Failed to load streams';
      } finally {
        this.loading = false;
      }
    },

    /**
     * Select a stream for detailed view
     */
    async selectStream(slug: string): Promise<void> {
      this.loading = true;
      this.error = null;

      try {
        const stream = await studioApi.getStream(slug);
        this.selectedStream = stream;

        // Connect SSE for live metrics
        this.connectSSE(slug);
      } catch (e) {
        this.error = e instanceof Error ? e.message : 'Failed to load stream';
      } finally {
        this.loading = false;
      }
    },

    /**
     * Refresh selected stream
     */
    async refreshSelectedStream(): Promise<void> {
      if (!this.selectedStream) return;

      try {
        const stream = await studioApi.getStream(this.selectedStream.slug);
        this.selectedStream = stream;
      } catch (e) {
        console.error('Failed to refresh stream:', e);
      }
    },

    /**
     * Create a new stream
     */
    async createStream(): Promise<void> {
      this.createLoading = true;
      this.createError = null;

      try {
        const data: StreamCreateRequest = {
          title: this.createTitle,
          description: this.createDescription,
          dvr_enabled: this.createDvrEnabled,
          dvr_window_seconds: this.createDvrWindow,
          auto_record_vod: this.createAutoRecordVod,
        };

        const response = await studioApi.createStream(data);

        // Store the stream key for one-time display
        this.newStreamKey = response.stream_key;
        this.newStreamRtmpUrl = response.rtmp_url;
        this.showKeyModal = true;
        this.keySaved = false;

        // Auto-clear key after 5 minutes
        if (this.keyExpiryTimeout) {
          clearTimeout(this.keyExpiryTimeout);
        }
        this.keyExpiryTimeout = setTimeout(() => {
          this.clearNewStreamKey();
        }, 5 * 60 * 1000);

        // Close create modal and reload streams
        this.closeCreateModal();
        await this.loadStreams();

        // Select the new stream
        await this.selectStream(response.slug);
      } catch (e) {
        this.createError = e instanceof Error ? e.message : 'Failed to create stream';
      } finally {
        this.createLoading = false;
      }
    },

    /**
     * Update a stream
     */
    async updateStream(slug: string, data: StreamUpdateRequest): Promise<void> {
      try {
        const updated = await studioApi.updateStream(slug, data);
        if (this.selectedStream?.slug === slug) {
          this.selectedStream = updated;
        }
        // Update in list
        const index = this.streams.findIndex(s => s.slug === slug);
        if (index >= 0) {
          this.streams[index] = updated;
        }
      } catch (e) {
        throw e;
      }
    },

    /**
     * End a stream
     */
    async endStream(slug: string): Promise<void> {
      this.endLoading = true;

      try {
        const updated = await studioApi.endStream(slug);
        if (this.selectedStream?.slug === slug) {
          this.selectedStream = updated;
        }
        // Update in list
        const index = this.streams.findIndex(s => s.slug === slug);
        if (index >= 0) {
          this.streams[index] = updated;
        }
        this.closeEndModal();
      } catch (e) {
        throw e;
      } finally {
        this.endLoading = false;
      }
    },

    /**
     * Regenerate stream key
     */
    async regenerateKey(slug: string): Promise<void> {
      this.regenerateLoading = true;
      this.regenerateError = null;

      try {
        const response = await studioApi.regenerateKey(slug);

        // Store the new key for one-time display
        this.newStreamKey = response.stream_key;
        this.newStreamRtmpUrl = response.rtmp_url;
        this.showKeyModal = true;
        this.keySaved = false;

        // Auto-clear key after 5 minutes
        if (this.keyExpiryTimeout) {
          clearTimeout(this.keyExpiryTimeout);
        }
        this.keyExpiryTimeout = setTimeout(() => {
          this.clearNewStreamKey();
        }, 5 * 60 * 1000);

        this.closeRegenerateModal();
      } catch (e) {
        this.regenerateError = e instanceof Error ? e.message : 'Failed to regenerate key';
      } finally {
        this.regenerateLoading = false;
      }
    },

    /**
     * Clear the new stream key from memory
     */
    clearNewStreamKey(): void {
      this.newStreamKey = null;
      this.newStreamRtmpUrl = null;
      this.showKeyModal = false;
      this.keySaved = false;
      if (this.keyExpiryTimeout) {
        clearTimeout(this.keyExpiryTimeout);
        this.keyExpiryTimeout = null;
      }
    },

    /**
     * Acknowledge that the key has been saved
     */
    acknowledgeKeySaved(): void {
      this.keySaved = true;
    },

    /**
     * Connect SSE for stream metrics
     */
    connectSSE(slug: string): void {
      // Disconnect existing connection
      this.disconnectSSE();

      this.sseConnection = connectStreamMetrics(slug, {
        onMessage: (event) => {
          const metrics = event as StreamMetrics;
          this.metrics = metrics;

          // Update selected stream status from metrics
          if (this.selectedStream && this.selectedStream.slug === slug) {
            this.selectedStream.status = metrics.status;
            this.selectedStream.segment_count = metrics.segment_count;
            this.selectedStream.qualities = metrics.qualities;
            this.selectedStream.last_segment_at = metrics.last_segment_at;
          }
        },
        onOpen: () => {
          console.log('SSE connected for stream:', slug);
        },
        onError: (error) => {
          console.error('SSE error:', error);
        },
      });
    },

    /**
     * Disconnect SSE
     */
    disconnectSSE(): void {
      if (this.sseConnection) {
        this.sseConnection.close();
        this.sseConnection = null;
      }
      this.metrics = null;
    },

    /**
     * Open create modal
     */
    openCreateModal(): void {
      this.createTitle = '';
      this.createDescription = '';
      this.createDvrEnabled = true;
      this.createDvrWindow = 7200;
      this.createAutoRecordVod = true;
      this.createError = null;
      this.showCreateModal = true;
    },

    /**
     * Close create modal
     */
    closeCreateModal(): void {
      this.showCreateModal = false;
      this.createError = null;
    },

    /**
     * Open regenerate modal
     */
    openRegenerateModal(): void {
      this.regenerateError = null;
      this.showRegenerateModal = true;
    },

    /**
     * Close regenerate modal
     */
    closeRegenerateModal(): void {
      this.showRegenerateModal = false;
      this.regenerateError = null;
    },

    /**
     * Open end stream modal
     */
    openEndModal(): void {
      this.showEndModal = true;
    },

    /**
     * Close end stream modal
     */
    closeEndModal(): void {
      this.showEndModal = false;
    },

    /**
     * Format bitrate - delegates to shared formatter
     */
    formatBitrate(kbps: number | null): string {
      return sharedFormatBitrate(kbps);
    },

    /**
     * Format time since - delegates to shared formatter
     */
    formatTimeSince(timestamp: string | null): string {
      return sharedFormatTimeSince(timestamp);
    },

    /**
     * Get status color class
     */
    getStatusColor(status: string): string {
      switch (status) {
        case 'live':
          return 'text-green-400';
        case 'idle':
          return 'text-yellow-400';
        case 'ending':
          return 'text-orange-400';
        case 'ended':
          return 'text-gray-400';
        default:
          return 'text-gray-400';
      }
    },
  };
}
