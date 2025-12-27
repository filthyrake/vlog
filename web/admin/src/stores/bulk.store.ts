/**
 * Bulk Operations Store
 * Manages bulk video operations and selection
 */

import { videosApi } from '@/api/endpoints/videos';
import type { Video, VideoCustomFields } from '@/api/types';
import type { AlpineContext } from './types';

export interface BulkState {
  // Selection
  selectedVideos: number[];

  // Bulk delete modal
  bulkDeleteModal: boolean;
  bulkDeletePermanent: boolean;

  // Bulk update modal
  bulkUpdateModal: boolean;
  bulkUpdateCategory: number;
  bulkUpdatePublishedAt: string;
  bulkUpdateUnpublish: boolean;

  // Bulk retranscode modal
  bulkRetranscodeModal: boolean;
  bulkRetranscodeQuality: string;

  // Bulk custom fields modal
  bulkCustomFieldsModal: boolean;
  bulkCustomFieldValues: VideoCustomFields;

  // Operation state
  bulkOpLoading: boolean;
  bulkOpMessage: string;
  bulkOpError: string;
}

export interface BulkActions {
  // Selection
  toggleSelectAll(videos: Video[]): void;
  clearSelection(): void;
  toggleVideoSelection(videoId: number): void;
  isSelected(videoId: number): boolean;

  // Bulk delete
  openBulkDeleteModal(): void;
  closeBulkDeleteModal(): void;
  bulkDeleteVideos(): Promise<void>;

  // Bulk update
  openBulkUpdateModal(): void;
  closeBulkUpdateModal(): void;
  bulkUpdateVideos(): Promise<void>;

  // Bulk retranscode
  openBulkRetranscodeModal(): void;
  closeBulkRetranscodeModal(): void;
  bulkRetranscodeVideos(): Promise<void>;

  // Bulk restore
  bulkRestoreVideos(): Promise<void>;

  // Bulk custom fields
  openBulkCustomFieldsModal(): void;
  closeBulkCustomFieldsModal(): void;
  bulkUpdateCustomFields(): Promise<void>;
  toggleBulkMultiSelectOption(fieldId: string, option: string): void;
}

export type BulkStore = BulkState & BulkActions;

export function createBulkStore(_context?: AlpineContext): BulkStore {
  return {
    // Selection
    selectedVideos: [],

    // Bulk delete
    bulkDeleteModal: false,
    bulkDeletePermanent: false,

    // Bulk update
    bulkUpdateModal: false,
    bulkUpdateCategory: 0,
    bulkUpdatePublishedAt: '',
    bulkUpdateUnpublish: false,

    // Bulk retranscode
    bulkRetranscodeModal: false,
    bulkRetranscodeQuality: '1080p',

    // Bulk custom fields
    bulkCustomFieldsModal: false,
    bulkCustomFieldValues: {},

    // Operation state
    bulkOpLoading: false,
    bulkOpMessage: '',
    bulkOpError: '',

    // ===========================================================================
    // Selection
    // ===========================================================================

    toggleSelectAll(videos: Video[]): void {
      if (this.selectedVideos.length === videos.length) {
        this.selectedVideos = [];
      } else {
        this.selectedVideos = videos.map((v) => v.id);
      }
    },

    clearSelection(): void {
      this.selectedVideos = [];
    },

    toggleVideoSelection(videoId: number): void {
      const idx = this.selectedVideos.indexOf(videoId);
      if (idx === -1) {
        this.selectedVideos.push(videoId);
      } else {
        this.selectedVideos.splice(idx, 1);
      }
    },

    isSelected(videoId: number): boolean {
      return this.selectedVideos.includes(videoId);
    },

    // ===========================================================================
    // Bulk Delete
    // ===========================================================================

    openBulkDeleteModal(): void {
      if (this.selectedVideos.length === 0) return;
      this.bulkDeletePermanent = false;
      this.bulkOpMessage = '';
      this.bulkOpError = '';
      this.bulkDeleteModal = true;
    },

    closeBulkDeleteModal(): void {
      this.bulkDeleteModal = false;
    },

    async bulkDeleteVideos(): Promise<void> {
      if (this.selectedVideos.length === 0) return;

      this.bulkOpLoading = true;
      this.bulkOpMessage = '';
      this.bulkOpError = '';

      try {
        const result = await videosApi.bulk.delete({
          video_ids: this.selectedVideos,
          permanent: this.bulkDeletePermanent,
        });

        this.bulkOpMessage = `Deleted ${result.processed} videos`;
        if (result.failed > 0) {
          this.bulkOpMessage += ` (${result.failed} failed)`;
        }

        this.selectedVideos = [];
        setTimeout(() => this.closeBulkDeleteModal(), 1500);
      } catch (e) {
        this.bulkOpError = e instanceof Error ? e.message : 'Bulk delete failed';
      } finally {
        this.bulkOpLoading = false;
      }
    },

    // ===========================================================================
    // Bulk Update
    // ===========================================================================

    openBulkUpdateModal(): void {
      if (this.selectedVideos.length === 0) return;
      this.bulkUpdateCategory = 0;
      this.bulkUpdatePublishedAt = '';
      this.bulkUpdateUnpublish = false;
      this.bulkOpMessage = '';
      this.bulkOpError = '';
      this.bulkUpdateModal = true;
    },

    closeBulkUpdateModal(): void {
      this.bulkUpdateModal = false;
    },

    async bulkUpdateVideos(): Promise<void> {
      if (this.selectedVideos.length === 0) return;

      this.bulkOpLoading = true;
      this.bulkOpMessage = '';
      this.bulkOpError = '';

      try {
        const updateData: {
          video_ids: number[];
          category_id?: number;
          published_at?: string | null;
          unpublish?: boolean;
        } = {
          video_ids: this.selectedVideos,
        };

        if (this.bulkUpdateCategory) {
          updateData.category_id = this.bulkUpdateCategory;
        }

        if (this.bulkUpdateUnpublish) {
          updateData.unpublish = true;
        } else if (this.bulkUpdatePublishedAt) {
          updateData.published_at = new Date(this.bulkUpdatePublishedAt).toISOString();
        }

        const result = await videosApi.bulk.update(updateData);

        this.bulkOpMessage = `Updated ${result.processed} videos`;
        if (result.failed > 0) {
          this.bulkOpMessage += ` (${result.failed} failed)`;
        }

        this.selectedVideos = [];
        setTimeout(() => this.closeBulkUpdateModal(), 1500);
      } catch (e) {
        this.bulkOpError = e instanceof Error ? e.message : 'Bulk update failed';
      } finally {
        this.bulkOpLoading = false;
      }
    },

    // ===========================================================================
    // Bulk Retranscode
    // ===========================================================================

    openBulkRetranscodeModal(): void {
      if (this.selectedVideos.length === 0) return;
      this.bulkRetranscodeQuality = '1080p';
      this.bulkOpMessage = '';
      this.bulkOpError = '';
      this.bulkRetranscodeModal = true;
    },

    closeBulkRetranscodeModal(): void {
      this.bulkRetranscodeModal = false;
    },

    async bulkRetranscodeVideos(): Promise<void> {
      if (this.selectedVideos.length === 0) return;

      this.bulkOpLoading = true;
      this.bulkOpMessage = '';
      this.bulkOpError = '';

      try {
        const result = await videosApi.bulk.retranscode({
          video_ids: this.selectedVideos,
          quality: this.bulkRetranscodeQuality,
        });

        this.bulkOpMessage = `Queued ${result.processed} videos for retranscoding`;
        if (result.failed > 0) {
          this.bulkOpMessage += ` (${result.failed} failed)`;
        }

        this.selectedVideos = [];
        setTimeout(() => this.closeBulkRetranscodeModal(), 1500);
      } catch (e) {
        this.bulkOpError = e instanceof Error ? e.message : 'Bulk retranscode failed';
      } finally {
        this.bulkOpLoading = false;
      }
    },

    // ===========================================================================
    // Bulk Restore
    // ===========================================================================

    async bulkRestoreVideos(): Promise<void> {
      if (this.selectedVideos.length === 0) return;

      this.bulkOpLoading = true;
      this.bulkOpMessage = '';
      this.bulkOpError = '';

      try {
        const result = await videosApi.bulk.restore({
          video_ids: this.selectedVideos,
        });

        this.bulkOpMessage = `Restored ${result.processed} videos`;
        if (result.failed > 0) {
          this.bulkOpMessage += ` (${result.failed} failed)`;
        }

        this.selectedVideos = [];
      } catch (e) {
        this.bulkOpError = e instanceof Error ? e.message : 'Bulk restore failed';
      } finally {
        this.bulkOpLoading = false;
      }
    },

    // ===========================================================================
    // Bulk Custom Fields
    // ===========================================================================

    openBulkCustomFieldsModal(): void {
      if (this.selectedVideos.length === 0) return;
      this.bulkCustomFieldValues = {};
      this.bulkOpMessage = '';
      this.bulkOpError = '';
      this.bulkCustomFieldsModal = true;
    },

    closeBulkCustomFieldsModal(): void {
      this.bulkCustomFieldsModal = false;
    },

    async bulkUpdateCustomFields(): Promise<void> {
      if (this.selectedVideos.length === 0) return;

      this.bulkOpLoading = true;
      this.bulkOpMessage = '';
      this.bulkOpError = '';

      try {
        const result = await videosApi.bulk.customFields({
          video_ids: this.selectedVideos,
          values: this.bulkCustomFieldValues,
        });

        this.bulkOpMessage = `Updated custom fields for ${result.processed} videos`;
        if (result.failed > 0) {
          this.bulkOpMessage += ` (${result.failed} failed)`;
        }

        this.selectedVideos = [];
        setTimeout(() => this.closeBulkCustomFieldsModal(), 1500);
      } catch (e) {
        this.bulkOpError = e instanceof Error ? e.message : 'Bulk custom fields update failed';
      } finally {
        this.bulkOpLoading = false;
      }
    },

    toggleBulkMultiSelectOption(fieldId: string, option: string): void {
      let current = this.bulkCustomFieldValues[fieldId];
      if (!Array.isArray(current)) {
        current = [];
      }

      const idx = (current as string[]).indexOf(option);
      if (idx >= 0) {
        (current as string[]).splice(idx, 1);
      } else {
        (current as string[]).push(option);
      }
      this.bulkCustomFieldValues[fieldId] = [...(current as string[])];
    },
  };
}
