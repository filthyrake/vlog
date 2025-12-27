/**
 * Tests for Bulk Store
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createBulkStore } from '../bulk.store';
import { videosApi } from '@/api/endpoints/videos';

// Mock the videos API
vi.mock('@/api/endpoints/videos', () => ({
  videosApi: {
    bulk: {
      delete: vi.fn(),
      update: vi.fn(),
      retranscode: vi.fn(),
      restore: vi.fn(),
      customFields: vi.fn(),
    },
  },
}));

// Get typed mocks for nested bulk methods
const mockBulkDelete = vi.mocked(videosApi.bulk.delete);
const mockBulkUpdate = vi.mocked(videosApi.bulk.update);
const mockBulkRetranscode = vi.mocked(videosApi.bulk.retranscode);
const mockBulkRestore = vi.mocked(videosApi.bulk.restore);
const mockBulkCustomFields = vi.mocked(videosApi.bulk.customFields);

describe('BulkStore', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('initial state', () => {
    it('should have correct initial values', () => {
      const store = createBulkStore();

      expect(store.selectedVideos).toEqual([]);
      expect(store.bulkDeleteModal).toBe(false);
      expect(store.bulkUpdateModal).toBe(false);
      expect(store.bulkRetranscodeModal).toBe(false);
      expect(store.bulkCustomFieldsModal).toBe(false);
      expect(store.bulkOpLoading).toBe(false);
    });
  });

  describe('selection', () => {
    it('should toggle video selection', () => {
      const store = createBulkStore();

      store.toggleVideoSelection(1);
      expect(store.selectedVideos).toEqual([1]);

      store.toggleVideoSelection(2);
      expect(store.selectedVideos).toEqual([1, 2]);

      store.toggleVideoSelection(1);
      expect(store.selectedVideos).toEqual([2]);
    });

    it('should check if video is selected', () => {
      const store = createBulkStore();
      store.selectedVideos = [1, 3, 5];

      expect(store.isSelected(1)).toBe(true);
      expect(store.isSelected(2)).toBe(false);
      expect(store.isSelected(3)).toBe(true);
    });

    it('should clear selection', () => {
      const store = createBulkStore();
      store.selectedVideos = [1, 2, 3];

      store.clearSelection();

      expect(store.selectedVideos).toEqual([]);
    });

    it('should toggle select all', () => {
      const store = createBulkStore();
      const videos = [{ id: 1 }, { id: 2 }, { id: 3 }] as any[];

      // Select all
      store.toggleSelectAll(videos);
      expect(store.selectedVideos).toEqual([1, 2, 3]);

      // Deselect all
      store.toggleSelectAll(videos);
      expect(store.selectedVideos).toEqual([]);
    });
  });

  describe('bulk delete modal', () => {
    it('should not open modal when no videos selected', () => {
      const store = createBulkStore();

      store.openBulkDeleteModal();

      expect(store.bulkDeleteModal).toBe(false);
    });

    it('should open modal when videos are selected', () => {
      const store = createBulkStore();
      store.selectedVideos = [1, 2];

      store.openBulkDeleteModal();

      expect(store.bulkDeleteModal).toBe(true);
      expect(store.bulkDeletePermanent).toBe(false);
    });

    it('should close modal', () => {
      const store = createBulkStore();
      store.bulkDeleteModal = true;

      store.closeBulkDeleteModal();

      expect(store.bulkDeleteModal).toBe(false);
    });
  });

  describe('bulkDeleteVideos', () => {
    it('should delete selected videos', async () => {
      mockBulkDelete.mockResolvedValueOnce({
        success: true,
        processed: 2,
        failed: 0,
      });

      const store = createBulkStore();
      store.selectedVideos = [1, 2];
      store.bulkDeletePermanent = false;

      await store.bulkDeleteVideos();

      expect(mockBulkDelete).toHaveBeenCalledWith({
        video_ids: [1, 2],
        permanent: false,
      });
      expect(store.bulkOpMessage).toBe('Deleted 2 videos');
      expect(store.selectedVideos).toEqual([]);
    });

    it('should handle partial failures', async () => {
      mockBulkDelete.mockResolvedValueOnce({
        success: true,
        processed: 1,
        failed: 1,
      });

      const store = createBulkStore();
      store.selectedVideos = [1, 2];

      await store.bulkDeleteVideos();

      expect(store.bulkOpMessage).toBe('Deleted 1 videos (1 failed)');
    });

    it('should handle errors', async () => {
      mockBulkDelete.mockRejectedValueOnce(new Error('Server error'));

      const store = createBulkStore();
      store.selectedVideos = [1, 2];

      await store.bulkDeleteVideos();

      expect(store.bulkOpError).toBe('Server error');
      expect(store.bulkOpLoading).toBe(false);
    });

    it('should not delete when no videos selected', async () => {
      const store = createBulkStore();
      store.selectedVideos = [];

      await store.bulkDeleteVideos();

      expect(mockBulkDelete).not.toHaveBeenCalled();
    });
  });

  describe('bulkUpdateVideos', () => {
    it('should update selected videos with category', async () => {
      mockBulkUpdate.mockResolvedValueOnce({
        success: true,
        processed: 2,
        failed: 0,
      });

      const store = createBulkStore();
      store.selectedVideos = [1, 2];
      store.bulkUpdateCategory = 5;

      await store.bulkUpdateVideos();

      expect(mockBulkUpdate).toHaveBeenCalledWith({
        video_ids: [1, 2],
        category_id: 5,
      });
    });

    it('should update with unpublish flag', async () => {
      mockBulkUpdate.mockResolvedValueOnce({
        success: true,
        processed: 2,
        failed: 0,
      });

      const store = createBulkStore();
      store.selectedVideos = [1, 2];
      store.bulkUpdateUnpublish = true;

      await store.bulkUpdateVideos();

      expect(mockBulkUpdate).toHaveBeenCalledWith({
        video_ids: [1, 2],
        unpublish: true,
      });
    });
  });

  describe('bulkRetranscodeVideos', () => {
    it('should queue selected videos for retranscode', async () => {
      mockBulkRetranscode.mockResolvedValueOnce({
        success: true,
        processed: 2,
        failed: 0,
      });

      const store = createBulkStore();
      store.selectedVideos = [1, 2];
      store.bulkRetranscodeQuality = '1080p';

      await store.bulkRetranscodeVideos();

      expect(mockBulkRetranscode).toHaveBeenCalledWith({
        video_ids: [1, 2],
        quality: '1080p',
      });
      expect(store.bulkOpMessage).toBe('Queued 2 videos for retranscoding');
    });
  });

  describe('bulkRestoreVideos', () => {
    it('should restore selected videos', async () => {
      mockBulkRestore.mockResolvedValueOnce({
        success: true,
        processed: 2,
        failed: 0,
      });

      const store = createBulkStore();
      store.selectedVideos = [1, 2];

      await store.bulkRestoreVideos();

      expect(mockBulkRestore).toHaveBeenCalledWith({
        video_ids: [1, 2],
      });
      expect(store.bulkOpMessage).toBe('Restored 2 videos');
    });
  });

  describe('bulkUpdateCustomFields', () => {
    it('should update custom fields for selected videos', async () => {
      mockBulkCustomFields.mockResolvedValueOnce({
        success: true,
        processed: 2,
        failed: 0,
      });

      const store = createBulkStore();
      store.selectedVideos = [1, 2];
      store.bulkCustomFieldValues = { field_1: 'value1' };

      await store.bulkUpdateCustomFields();

      expect(mockBulkCustomFields).toHaveBeenCalledWith({
        video_ids: [1, 2],
        values: { field_1: 'value1' },
      });
    });
  });
});
