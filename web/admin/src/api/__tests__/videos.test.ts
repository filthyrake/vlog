/**
 * Tests for Videos API
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { videosApi } from '../endpoints/videos';
import { apiClient } from '../client';
import type { Video } from '../types';

// Mock the apiClient
vi.mock('../client', () => ({
  apiClient: {
    fetch: vi.fn(),
    fetchResponse: vi.fn(),
    uploadWithProgress: vi.fn(),
  },
}));

const mockApiClient = vi.mocked(apiClient);

describe('videosApi', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('list', () => {
    it('should fetch all videos', async () => {
      const mockVideos: Video[] = [
        {
          id: 1,
          title: 'Test Video',
          slug: 'test-video',
          status: 'ready',
          created_at: '2024-01-01T00:00:00Z',
        },
      ];
      mockApiClient.fetch.mockResolvedValueOnce(mockVideos);

      const result = await videosApi.list();

      expect(mockApiClient.fetch).toHaveBeenCalledWith('/api/videos');
      expect(result).toEqual(mockVideos);
    });
  });

  describe('get', () => {
    it('should fetch a single video by ID', async () => {
      const mockVideo: Video = {
        id: 1,
        title: 'Test Video',
        slug: 'test-video',
        status: 'ready',
        created_at: '2024-01-01T00:00:00Z',
      };
      mockApiClient.fetch.mockResolvedValueOnce(mockVideo);

      const result = await videosApi.get(1);

      expect(mockApiClient.fetch).toHaveBeenCalledWith('/api/videos/1');
      expect(result).toEqual(mockVideo);
    });
  });

  describe('delete', () => {
    it('should delete a video', async () => {
      mockApiClient.fetchResponse.mockResolvedValueOnce(new Response('', { status: 200 }));

      await videosApi.delete(1);

      expect(mockApiClient.fetchResponse).toHaveBeenCalledWith('/api/videos/1', {
        method: 'DELETE',
      });
    });

    it('should throw on error', async () => {
      mockApiClient.fetchResponse.mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: 'Not found' }), { status: 404 })
      );

      await expect(videosApi.delete(1)).rejects.toThrow('Not found');
    });
  });

  describe('retry', () => {
    it('should retry a failed video', async () => {
      mockApiClient.fetchResponse.mockResolvedValueOnce(new Response('', { status: 200 }));

      await videosApi.retry(1);

      expect(mockApiClient.fetchResponse).toHaveBeenCalledWith('/api/videos/1/retry', {
        method: 'POST',
      });
    });
  });

  describe('publish/unpublish', () => {
    it('should publish a video', async () => {
      mockApiClient.fetchResponse.mockResolvedValueOnce(new Response('', { status: 200 }));

      await videosApi.publish(1);

      expect(mockApiClient.fetchResponse).toHaveBeenCalledWith('/api/videos/1/publish', {
        method: 'POST',
      });
    });

    it('should unpublish a video', async () => {
      mockApiClient.fetchResponse.mockResolvedValueOnce(new Response('', { status: 200 }));

      await videosApi.unpublish(1);

      expect(mockApiClient.fetchResponse).toHaveBeenCalledWith('/api/videos/1/unpublish', {
        method: 'POST',
      });
    });
  });

  describe('getProgress', () => {
    it('should fetch video progress', async () => {
      const mockProgress = {
        id: 1,
        status: 'processing' as const,
        current_step: 'transcoding',
        current_progress: 50,
      };
      mockApiClient.fetch.mockResolvedValueOnce(mockProgress);

      const result = await videosApi.getProgress(1);

      expect(mockApiClient.fetch).toHaveBeenCalledWith('/api/videos/1/progress');
      expect(result).toEqual(mockProgress);
    });
  });

  describe('retranscode', () => {
    it('should start retranscoding with selected qualities', async () => {
      mockApiClient.fetchResponse.mockResolvedValueOnce(new Response('', { status: 200 }));

      await videosApi.retranscode(1, ['1080p', '720p']);

      expect(mockApiClient.fetchResponse).toHaveBeenCalledWith('/api/videos/1/retranscode', {
        method: 'POST',
        body: JSON.stringify({ qualities: ['1080p', '720p'] }),
      });
    });
  });

  describe('thumbnails', () => {
    it('should get thumbnail frames', async () => {
      const mockFrames = {
        frames: [
          { timestamp: 10, url: '/frames/1.jpg' },
          { timestamp: 20, url: '/frames/2.jpg' },
        ],
      };
      mockApiClient.fetch.mockResolvedValueOnce(mockFrames);

      const result = await videosApi.getThumbnailFrames(1, 10);

      expect(mockApiClient.fetch).toHaveBeenCalledWith('/api/videos/1/thumbnail/frames', {
        method: 'POST',
        body: JSON.stringify({ count: 10 }),
      });
      expect(result).toEqual(mockFrames.frames);
    });

    it('should select a thumbnail', async () => {
      mockApiClient.fetchResponse.mockResolvedValueOnce(new Response('', { status: 200 }));

      await videosApi.selectThumbnail(1, 15.5);

      expect(mockApiClient.fetchResponse).toHaveBeenCalledWith('/api/videos/1/thumbnail/select', {
        method: 'POST',
        body: JSON.stringify({ timestamp: 15.5 }),
      });
    });

    it('should revert to generated thumbnail', async () => {
      mockApiClient.fetchResponse.mockResolvedValueOnce(new Response('', { status: 200 }));

      await videosApi.revertThumbnail(1);

      expect(mockApiClient.fetchResponse).toHaveBeenCalledWith('/api/videos/1/thumbnail/revert', {
        method: 'POST',
      });
    });
  });

  describe('custom fields', () => {
    it('should get custom fields for a video', async () => {
      const mockFields = { field_1: 'value1', field_2: 42 };
      mockApiClient.fetch.mockResolvedValueOnce(mockFields);

      const result = await videosApi.getCustomFields(1);

      expect(mockApiClient.fetch).toHaveBeenCalledWith('/api/videos/1/custom-fields');
      expect(result).toEqual(mockFields);
    });

    it('should save custom fields for a video', async () => {
      mockApiClient.fetchResponse.mockResolvedValueOnce(new Response('', { status: 200 }));

      await videosApi.saveCustomFields(1, { field_1: 'value1' });

      expect(mockApiClient.fetchResponse).toHaveBeenCalledWith('/api/videos/1/custom-fields', {
        method: 'POST',
        body: JSON.stringify({ values: { field_1: 'value1' } }),
      });
    });
  });

  describe('bulk operations', () => {
    describe('bulk.delete', () => {
      it('should delete multiple videos', async () => {
        const mockResult = { success: true, processed: 3, failed: 0 };
        mockApiClient.fetchResponse.mockResolvedValueOnce(
          new Response(JSON.stringify(mockResult), { status: 200 })
        );

        const result = await videosApi.bulk.delete({ video_ids: [1, 2, 3], permanent: false });

        expect(mockApiClient.fetchResponse).toHaveBeenCalledWith('/api/videos/bulk/delete', {
          method: 'POST',
          body: JSON.stringify({ video_ids: [1, 2, 3], permanent: false }),
        });
        expect(result).toEqual(mockResult);
      });
    });

    describe('bulk.update', () => {
      it('should update multiple videos', async () => {
        const mockResult = { success: true, processed: 2, failed: 0 };
        mockApiClient.fetchResponse.mockResolvedValueOnce(
          new Response(JSON.stringify(mockResult), { status: 200 })
        );

        const result = await videosApi.bulk.update({
          video_ids: [1, 2],
          category_id: 5,
        });

        expect(mockApiClient.fetchResponse).toHaveBeenCalledWith('/api/videos/bulk/update', {
          method: 'POST',
          body: JSON.stringify({ video_ids: [1, 2], category_id: 5 }),
        });
        expect(result).toEqual(mockResult);
      });
    });

    describe('bulk.retranscode', () => {
      it('should retranscode multiple videos', async () => {
        const mockResult = { success: true, processed: 2, failed: 0 };
        mockApiClient.fetchResponse.mockResolvedValueOnce(
          new Response(JSON.stringify(mockResult), { status: 200 })
        );

        const result = await videosApi.bulk.retranscode({
          video_ids: [1, 2],
          quality: '1080p',
        });

        expect(mockApiClient.fetchResponse).toHaveBeenCalledWith('/api/videos/bulk/retranscode', {
          method: 'POST',
          body: JSON.stringify({ video_ids: [1, 2], quality: '1080p' }),
        });
        expect(result).toEqual(mockResult);
      });
    });

    describe('bulk.restore', () => {
      it('should restore multiple deleted videos', async () => {
        const mockResult = { success: true, processed: 2, failed: 0 };
        mockApiClient.fetchResponse.mockResolvedValueOnce(
          new Response(JSON.stringify(mockResult), { status: 200 })
        );

        const result = await videosApi.bulk.restore({ video_ids: [1, 2] });

        expect(mockApiClient.fetchResponse).toHaveBeenCalledWith('/api/videos/bulk/restore', {
          method: 'POST',
          body: JSON.stringify({ video_ids: [1, 2] }),
        });
        expect(result).toEqual(mockResult);
      });
    });

    describe('bulk.customFields', () => {
      it('should update custom fields for multiple videos', async () => {
        const mockResult = { success: true, processed: 2, failed: 0 };
        mockApiClient.fetchResponse.mockResolvedValueOnce(
          new Response(JSON.stringify(mockResult), { status: 200 })
        );

        const result = await videosApi.bulk.customFields({
          video_ids: [1, 2],
          values: { field_1: 'shared_value' },
        });

        expect(mockApiClient.fetchResponse).toHaveBeenCalledWith('/api/videos/bulk/custom-fields', {
          method: 'POST',
          body: JSON.stringify({ video_ids: [1, 2], values: { field_1: 'shared_value' } }),
        });
        expect(result).toEqual(mockResult);
      });
    });
  });

  describe('upload', () => {
    it('should call uploadWithProgress with correct parameters', () => {
      const mockXhr = {} as XMLHttpRequest;
      mockApiClient.uploadWithProgress.mockReturnValueOnce(mockXhr);

      const formData = new FormData();
      const onProgress = vi.fn();
      const onComplete = vi.fn();
      const onError = vi.fn();

      const result = videosApi.upload(formData, { onProgress, onComplete, onError });

      expect(mockApiClient.uploadWithProgress).toHaveBeenCalledWith(
        '/api/videos',
        formData,
        onProgress,
        expect.any(Function),
        onError
      );
      expect(result).toBe(mockXhr);
    });
  });

  describe('reupload', () => {
    it('should call uploadWithProgress with correct video ID', () => {
      const mockXhr = {} as XMLHttpRequest;
      mockApiClient.uploadWithProgress.mockReturnValueOnce(mockXhr);

      const formData = new FormData();
      const result = videosApi.reupload(123, formData, {});

      expect(mockApiClient.uploadWithProgress).toHaveBeenCalledWith(
        '/api/videos/123/re-upload',
        formData,
        undefined,
        expect.any(Function),
        undefined
      );
      expect(result).toBe(mockXhr);
    });
  });
});
