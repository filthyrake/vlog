/**
 * VOD Store (Issue #530)
 * Manages VOD listings, details, and actions
 */

import { vodApi } from '@/api/endpoints/vod';
import type { VOD, VODUpdateRequest, VODAnalytics } from '@/api/types';

export interface VODState {
  // VODs
  vods: VOD[];
  selectedVOD: VOD | null;
  vodAnalytics: VODAnalytics | null;
  vodLoading: boolean;
  vodError: string | null;

  // Pagination
  vodPage: number;
  vodPageSize: number;
  vodTotal: number;
  vodHasMore: boolean;
  vodStatusFilter: string;

  // Edit modal
  showVODEditModal: boolean;
  vodEditTitle: string;
  vodEditDescription: string;
  vodEditCategoryId: number | null;
  vodEditLoading: boolean;
  vodEditError: string | null;

  // Delete modal
  showVODDeleteModal: boolean;
  vodDeleteLoading: boolean;

  // Thumbnail upload
  vodThumbnailUploading: boolean;
  vodThumbnailError: string | null;

  // Download
  vodDownloading: boolean;
}

export interface VODActions {
  // Data loading
  loadVODs(page?: number): Promise<void>;
  selectVOD(slug: string): Promise<void>;
  loadVODAnalytics(slug: string): Promise<void>;

  // VOD management
  updateVOD(slug: string, data: VODUpdateRequest): Promise<void>;
  deleteVOD(slug: string): Promise<void>;
  uploadVODThumbnail(slug: string, file: File): Promise<void>;
  downloadVOD(slug: string): Promise<void>;

  // Modals
  openVODEditModal(vod: VOD): void;
  closeVODEditModal(): void;
  openVODDeleteModal(): void;
  closeVODDeleteModal(): void;

  // Utilities
  formatDuration(seconds: number): string;
  formatFileSize(bytes: number): string;
  getVODStatusColor(status: string): string;
}

export type VODStore = VODState & VODActions;

export function createVODStore(): VODStore {
  return {
    // Initial state
    vods: [],
    selectedVOD: null,
    vodAnalytics: null,
    vodLoading: false,
    vodError: null,

    vodPage: 1,
    vodPageSize: 20,
    vodTotal: 0,
    vodHasMore: false,
    vodStatusFilter: '',

    showVODEditModal: false,
    vodEditTitle: '',
    vodEditDescription: '',
    vodEditCategoryId: null,
    vodEditLoading: false,
    vodEditError: null,

    showVODDeleteModal: false,
    vodDeleteLoading: false,

    vodThumbnailUploading: false,
    vodThumbnailError: null,

    vodDownloading: false,

    /**
     * Load VODs
     */
    async loadVODs(page = 1): Promise<void> {
      this.vodLoading = true;
      this.vodError = null;

      try {
        const response = await vodApi.listVODs(
          page,
          this.vodPageSize,
          this.vodStatusFilter || undefined
        );
        this.vods = response.vods;
        this.vodPage = response.page;
        this.vodTotal = response.total;
        this.vodHasMore = response.has_more;
      } catch (e) {
        this.vodError = e instanceof Error ? e.message : 'Failed to load VODs';
      } finally {
        this.vodLoading = false;
      }
    },

    /**
     * Select a VOD for detailed view
     */
    async selectVOD(slug: string): Promise<void> {
      this.vodLoading = true;
      this.vodError = null;

      try {
        const vod = await vodApi.getVOD(slug);
        this.selectedVOD = vod;

        // Also load analytics
        await this.loadVODAnalytics(slug);
      } catch (e) {
        this.vodError = e instanceof Error ? e.message : 'Failed to load VOD';
      } finally {
        this.vodLoading = false;
      }
    },

    /**
     * Load analytics for a VOD
     */
    async loadVODAnalytics(slug: string): Promise<void> {
      try {
        const analytics = await vodApi.getVODAnalytics(slug);
        this.vodAnalytics = analytics;
      } catch (e) {
        console.error('Failed to load VOD analytics:', e);
        this.vodAnalytics = null;
      }
    },

    /**
     * Update a VOD
     */
    async updateVOD(slug: string, data: VODUpdateRequest): Promise<void> {
      this.vodEditLoading = true;
      this.vodEditError = null;

      try {
        const updated = await vodApi.updateVOD(slug, data);
        if (this.selectedVOD?.slug === slug) {
          this.selectedVOD = updated;
        }
        // Update in list
        const index = this.vods.findIndex(v => v.slug === slug);
        if (index >= 0) {
          this.vods[index] = updated;
        }
        this.closeVODEditModal();
      } catch (e) {
        this.vodEditError = e instanceof Error ? e.message : 'Failed to update VOD';
        throw e;
      } finally {
        this.vodEditLoading = false;
      }
    },

    /**
     * Delete a VOD (soft delete)
     */
    async deleteVOD(slug: string): Promise<void> {
      this.vodDeleteLoading = true;

      try {
        await vodApi.deleteVOD(slug);
        // Remove from list
        this.vods = this.vods.filter(v => v.slug !== slug);
        this.vodTotal = Math.max(0, this.vodTotal - 1);
        // Clear selection if deleted
        if (this.selectedVOD?.slug === slug) {
          this.selectedVOD = null;
          this.vodAnalytics = null;
        }
        this.closeVODDeleteModal();
      } catch (e) {
        throw e;
      } finally {
        this.vodDeleteLoading = false;
      }
    },

    /**
     * Upload a custom thumbnail for a VOD
     */
    async uploadVODThumbnail(slug: string, file: File): Promise<void> {
      this.vodThumbnailUploading = true;
      this.vodThumbnailError = null;

      try {
        const updated = await vodApi.uploadThumbnail(slug, file);
        if (this.selectedVOD?.slug === slug) {
          this.selectedVOD = updated;
        }
        // Update in list
        const index = this.vods.findIndex(v => v.slug === slug);
        if (index >= 0) {
          this.vods[index] = updated;
        }
      } catch (e) {
        this.vodThumbnailError = e instanceof Error ? e.message : 'Failed to upload thumbnail';
        throw e;
      } finally {
        this.vodThumbnailUploading = false;
      }
    },

    /**
     * Download a VOD
     */
    async downloadVOD(slug: string): Promise<void> {
      this.vodDownloading = true;

      try {
        const response = await vodApi.getDownloadURL(slug);
        // Open download in new tab/window
        window.open(response.download_url, '_blank');
      } catch (e) {
        console.error('Failed to get download URL:', e);
      } finally {
        this.vodDownloading = false;
      }
    },

    /**
     * Open edit modal
     */
    openVODEditModal(vod: VOD): void {
      this.vodEditTitle = vod.title;
      this.vodEditDescription = vod.description;
      this.vodEditCategoryId = vod.category_id;
      this.vodEditError = null;
      this.showVODEditModal = true;
    },

    /**
     * Close edit modal
     */
    closeVODEditModal(): void {
      this.showVODEditModal = false;
      this.vodEditError = null;
    },

    /**
     * Open delete modal
     */
    openVODDeleteModal(): void {
      this.showVODDeleteModal = true;
    },

    /**
     * Close delete modal
     */
    closeVODDeleteModal(): void {
      this.showVODDeleteModal = false;
    },

    /**
     * Format duration in HH:MM:SS format
     */
    formatDuration(seconds: number): string {
      if (!seconds || seconds <= 0) return '0:00';

      const hours = Math.floor(seconds / 3600);
      const minutes = Math.floor((seconds % 3600) / 60);
      const secs = Math.floor(seconds % 60);

      if (hours > 0) {
        return `${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
      }
      return `${minutes}:${secs.toString().padStart(2, '0')}`;
    },

    /**
     * Format file size in human-readable format
     */
    formatFileSize(bytes: number): string {
      if (bytes === 0) return '0 B';
      const k = 1024;
      const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
      const i = Math.floor(Math.log(bytes) / Math.log(k));
      return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    },

    /**
     * Get status color class
     */
    getVODStatusColor(status: string): string {
      switch (status) {
        case 'ready':
          return 'text-green-400';
        case 'processing':
          return 'text-blue-400';
        case 'pending':
          return 'text-yellow-400';
        case 'failed':
          return 'text-red-400';
        default:
          return 'text-gray-400';
      }
    },
  };
}
