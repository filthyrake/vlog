/**
 * Videos Store
 * Manages video list and individual video operations
 */

import { videosApi } from '@/api/endpoints/videos';
import type { Video, QualityInfo, ThumbnailFrame, VideoProgress } from '@/api/types';
import { formatDuration, formatDate } from '@/utils/formatters';

export interface VideosState {
  // Video list
  videos: Video[];
  loading: boolean;
  error: string | null;

  // Edit modal state
  editModal: boolean;
  editVideoId: number | null;
  editTitle: string;
  editDescription: string;
  editCategory: number;
  editPublishedAt: string;
  editCustomFieldValues: Record<string, unknown>; // Custom field values for edit modal
  editSaving: boolean;
  editMessage: string;
  editError: string;

  // Re-upload modal state
  reuploadModal: boolean;
  reuploadVideoId: number | null;
  reuploadTitle: string;
  reuploadFile: File | null;
  reuploading: boolean;
  reuploadProgress: number;
  reuploadMessage: string;
  reuploadError: string;

  // Re-transcode modal state
  retranscodeModal: boolean;
  retranscodeVideoId: number | null;
  retranscodeTitle: string;
  retranscodeQualities: string[];
  retranscodeAvailable: QualityInfo[];
  retranscodeExisting: QualityInfo[];
  retranscodeSelected: string[];
  retranscoding: boolean;
  retranscodeMessage: string;
  retranscodeError: string;

  // Thumbnail modal state
  thumbnailModal: boolean;
  thumbnailVideoId: number | null;
  thumbnailVideoSlug: string;
  thumbnailVideoTitle: string;
  thumbnailDuration: number;
  thumbnailSource: 'generated' | 'custom';
  thumbnailFrames: ThumbnailFrame[];
  thumbnailLoading: boolean;
  thumbnailUploading: boolean;
  thumbnailUploadFile: File | null;
  thumbnailCacheBust: number;
  thumbnailMessage: string;
  thumbnailError: string;

  // Progress data (keyed by video ID)
  progressData: Record<number, VideoProgress>;
}

export interface VideosActions {
  // Core operations
  loadVideos(): Promise<void>;
  deleteVideo(id: number): Promise<void>;
  retryVideo(id: number): Promise<void>;
  toggleVideoPublish(video: Video): Promise<void>;
  togglePublish(video: Video): Promise<void>; // Alias for toggleVideoPublish
  exportVideos(): Promise<void>;

  // Edit modal
  openEditModal(video: Video): void;
  closeEditModal(): void;
  saveVideo(): Promise<void>;

  // Re-upload modal
  openReuploadModal(video: Video): void;
  closeReuploadModal(): void;
  reuploadVideo(): XMLHttpRequest | null;

  // Re-transcode modal
  openRetranscodeModal(video: Video): Promise<void>;
  closeRetranscodeModal(): void;
  startRetranscode(): Promise<void>;
  submitRetranscode(): Promise<void>; // Alias for startRetranscode
  retranscodeAll(video: Video): Promise<void>;
  toggleRetranscodeQuality(quality: string): void;

  // Custom field editing
  toggleMultiSelectOption(fieldId: string, option: string): void;

  // Thumbnail modal
  openThumbnailModal(video: Video): Promise<void>;
  closeThumbnailModal(): void;
  generateThumbnailFrames(): Promise<void>;
  selectThumbnailFrame(timestamp: number): Promise<void>;
  uploadCustomThumbnail(): Promise<void>;
  revertToGeneratedThumbnail(): Promise<void>;
  revertThumbnail(): Promise<void>; // Alias for revertToGeneratedThumbnail

  // Progress tracking
  loadProgressForActiveVideos(): Promise<void>;
  updateProgress(videoId: number, progress: VideoProgress): void;

  // Formatters (bound to this store for use in templates)
  formatDuration: typeof formatDuration;
  formatDate: typeof formatDate;
}

export type VideosStore = VideosState & VideosActions;

export function createVideosStore(): VideosStore {
  return {
    // Initial state
    videos: [],
    loading: false,
    error: null,

    // Edit modal
    editModal: false,
    editVideoId: null,
    editTitle: '',
    editDescription: '',
    editCategory: 0,
    editPublishedAt: '',
    editCustomFieldValues: {},
    editSaving: false,
    editMessage: '',
    editError: '',

    // Re-upload modal
    reuploadModal: false,
    reuploadVideoId: null,
    reuploadTitle: '',
    reuploadFile: null,
    reuploading: false,
    reuploadProgress: 0,
    reuploadMessage: '',
    reuploadError: '',

    // Re-transcode modal
    retranscodeModal: false,
    retranscodeVideoId: null,
    retranscodeTitle: '',
    retranscodeQualities: [],
    retranscodeAvailable: [],
    retranscodeExisting: [],
    retranscodeSelected: [],
    retranscoding: false,
    retranscodeMessage: '',
    retranscodeError: '',

    // Thumbnail modal
    thumbnailModal: false,
    thumbnailVideoId: null,
    thumbnailVideoSlug: '',
    thumbnailVideoTitle: '',
    thumbnailDuration: 0,
    thumbnailSource: 'generated',
    thumbnailFrames: [],
    thumbnailLoading: false,
    thumbnailUploading: false,
    thumbnailUploadFile: null,
    thumbnailCacheBust: Date.now(),
    thumbnailMessage: '',
    thumbnailError: '',

    // Progress data
    progressData: {},

    // Formatters
    formatDuration,
    formatDate,

    // ===========================================================================
    // Core Operations
    // ===========================================================================

    async loadVideos(): Promise<void> {
      this.loading = true;
      this.error = null;

      try {
        this.videos = await videosApi.list();
      } catch (e) {
        this.error = e instanceof Error ? e.message : 'Failed to load videos';
        this.videos = [];
      } finally {
        this.loading = false;
      }
    },

    async deleteVideo(id: number): Promise<void> {
      try {
        await videosApi.delete(id);
        this.videos = this.videos.filter((v) => v.id !== id);
      } catch (e) {
        this.error = e instanceof Error ? e.message : 'Failed to delete video';
      }
    },

    async retryVideo(id: number): Promise<void> {
      try {
        await videosApi.retry(id);
        // Update local state
        const video = this.videos.find((v) => v.id === id);
        if (video) {
          video.status = 'pending';
        }
      } catch (e) {
        this.error = e instanceof Error ? e.message : 'Failed to retry video';
      }
    },

    async toggleVideoPublish(video: Video): Promise<void> {
      try {
        if (video.published_at) {
          await videosApi.unpublish(video.id);
          video.published_at = undefined;
        } else {
          await videosApi.publish(video.id);
          video.published_at = new Date().toISOString();
        }
      } catch (e) {
        this.error = e instanceof Error ? e.message : 'Failed to toggle publish status';
      }
    },

    // Alias for toggleVideoPublish
    async togglePublish(video: Video): Promise<void> {
      return this.toggleVideoPublish(video);
    },

    async exportVideos(): Promise<void> {
      try {
        const blob = await videosApi.export('json');
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `videos-export-${new Date().toISOString().split('T')[0]}.json`;
        a.click();
        URL.revokeObjectURL(url);
      } catch (e) {
        this.error = e instanceof Error ? e.message : 'Failed to export videos';
      }
    },

    // ===========================================================================
    // Edit Modal
    // ===========================================================================

    openEditModal(video: Video): void {
      this.editVideoId = video.id;
      this.editTitle = video.title;
      this.editDescription = video.description || '';
      this.editCategory = video.category_id || 0;
      this.editPublishedAt = video.published_at?.slice(0, 16) || '';
      this.editMessage = '';
      this.editError = '';
      this.editModal = true;
    },

    closeEditModal(): void {
      this.editModal = false;
      this.editVideoId = null;
    },

    async saveVideo(): Promise<void> {
      if (!this.editVideoId) return;

      this.editSaving = true;
      this.editMessage = '';
      this.editError = '';

      try {
        const formData = new FormData();
        formData.append('title', this.editTitle);
        formData.append('description', this.editDescription);
        if (this.editCategory) {
          formData.append('category_id', this.editCategory.toString());
        }
        if (this.editPublishedAt) {
          formData.append('published_at', new Date(this.editPublishedAt).toISOString());
        }

        const updated = await videosApi.update(this.editVideoId, formData);

        // Update local state
        const index = this.videos.findIndex((v) => v.id === this.editVideoId);
        if (index !== -1) {
          this.videos[index] = { ...this.videos[index], ...updated };
        }

        this.editMessage = 'Video updated successfully';
        setTimeout(() => this.closeEditModal(), 1500);
      } catch (e) {
        this.editError = e instanceof Error ? e.message : 'Failed to save video';
      } finally {
        this.editSaving = false;
      }
    },

    // ===========================================================================
    // Re-upload Modal
    // ===========================================================================

    openReuploadModal(video: Video): void {
      this.reuploadVideoId = video.id;
      this.reuploadTitle = video.title;
      this.reuploadFile = null;
      this.reuploadProgress = 0;
      this.reuploadMessage = '';
      this.reuploadError = '';
      this.reuploading = false;
      this.reuploadModal = true;
    },

    closeReuploadModal(): void {
      this.reuploadModal = false;
      this.reuploadVideoId = null;
    },

    reuploadVideo(): XMLHttpRequest | null {
      if (!this.reuploadVideoId || !this.reuploadFile) return null;

      this.reuploading = true;
      this.reuploadProgress = 0;
      this.reuploadMessage = '';
      this.reuploadError = '';

      const formData = new FormData();
      formData.append('file', this.reuploadFile);

      const videoId = this.reuploadVideoId;

      return videosApi.reupload(videoId, formData, {
        onProgress: (percent) => {
          this.reuploadProgress = percent;
        },
        onComplete: () => {
          this.reuploadMessage = 'Video re-uploaded successfully. Processing will begin shortly.';
          this.reuploading = false;

          // Update local state
          const video = this.videos.find((v) => v.id === videoId);
          if (video) {
            video.status = 'pending';
          }

          setTimeout(() => this.closeReuploadModal(), 2000);
        },
        onError: (error) => {
          this.reuploadError = error.message;
          this.reuploading = false;
        },
      });
    },

    // ===========================================================================
    // Re-transcode Modal
    // ===========================================================================

    async openRetranscodeModal(video: Video): Promise<void> {
      this.retranscodeVideoId = video.id;
      this.retranscodeTitle = video.title;
      this.retranscodeSelected = [];
      this.retranscodeMessage = '';
      this.retranscodeError = '';
      this.retranscodeModal = true;

      try {
        const { available, existing } = await videosApi.getQualitiesDetailed(video.id);
        this.retranscodeAvailable = available;
        this.retranscodeExisting = existing;
        this.retranscodeQualities = available.map((q) => q.quality);
      } catch (e) {
        this.retranscodeError = e instanceof Error ? e.message : 'Failed to load qualities';
      }
    },

    closeRetranscodeModal(): void {
      this.retranscodeModal = false;
      this.retranscodeVideoId = null;
    },

    async startRetranscode(): Promise<void> {
      if (!this.retranscodeVideoId || this.retranscodeSelected.length === 0) return;

      this.retranscoding = true;
      this.retranscodeMessage = '';
      this.retranscodeError = '';

      try {
        await videosApi.retranscode(this.retranscodeVideoId, this.retranscodeSelected);
        this.retranscodeMessage = 'Retranscode started. Check progress in the video list.';

        // Update local state
        const video = this.videos.find((v) => v.id === this.retranscodeVideoId);
        if (video) {
          video.status = 'processing';
        }

        setTimeout(() => this.closeRetranscodeModal(), 2000);
      } catch (e) {
        this.retranscodeError = e instanceof Error ? e.message : 'Failed to start retranscode';
      } finally {
        this.retranscoding = false;
      }
    },

    // Alias for startRetranscode
    async submitRetranscode(): Promise<void> {
      return this.startRetranscode();
    },

    async retranscodeAll(video: Video): Promise<void> {
      if (!confirm(`Re-transcode all qualities for "${video.title}"? This will delete all existing transcoded files and re-process the video.`)) {
        return;
      }

      try {
        await videosApi.retranscode(video.id, ['all']);
        await this.loadVideos();
      } catch (e) {
        this.error = e instanceof Error ? e.message : 'Re-transcode failed';
      }
    },

    toggleRetranscodeQuality(quality: string): void {
      const idx = this.retranscodeSelected.indexOf(quality);
      if (idx === -1) {
        this.retranscodeSelected.push(quality);
      } else {
        this.retranscodeSelected.splice(idx, 1);
      }
    },

    // ===========================================================================
    // Custom Field Editing
    // ===========================================================================

    toggleMultiSelectOption(fieldId: string, option: string): void {
      let current = this.editCustomFieldValues[fieldId];
      if (!Array.isArray(current)) {
        current = [];
      }

      const idx = (current as string[]).indexOf(option);
      if (idx >= 0) {
        (current as string[]).splice(idx, 1);
      } else {
        (current as string[]).push(option);
      }
      this.editCustomFieldValues[fieldId] = [...(current as string[])];
    },

    // ===========================================================================
    // Thumbnail Modal
    // ===========================================================================

    async openThumbnailModal(video: Video): Promise<void> {
      this.thumbnailVideoId = video.id;
      this.thumbnailVideoSlug = video.slug;
      this.thumbnailVideoTitle = video.title;
      this.thumbnailDuration = video.duration || 0;
      this.thumbnailSource = video.has_custom_thumbnail ? 'custom' : 'generated';
      this.thumbnailFrames = [];
      this.thumbnailUploadFile = null;
      this.thumbnailMessage = '';
      this.thumbnailError = '';
      this.thumbnailCacheBust = Date.now();
      this.thumbnailModal = true;

      // Generate initial frames
      await this.generateThumbnailFrames();
    },

    closeThumbnailModal(): void {
      this.thumbnailModal = false;
      this.thumbnailVideoId = null;
    },

    async generateThumbnailFrames(): Promise<void> {
      if (!this.thumbnailVideoId) return;

      this.thumbnailLoading = true;
      this.thumbnailError = '';

      try {
        this.thumbnailFrames = await videosApi.getThumbnailFrames(this.thumbnailVideoId, 10);
      } catch (e) {
        this.thumbnailError = e instanceof Error ? e.message : 'Failed to generate frames';
      } finally {
        this.thumbnailLoading = false;
      }
    },

    async selectThumbnailFrame(timestamp: number): Promise<void> {
      if (!this.thumbnailVideoId) return;

      this.thumbnailLoading = true;
      this.thumbnailMessage = '';
      this.thumbnailError = '';

      try {
        await videosApi.selectThumbnail(this.thumbnailVideoId, timestamp);
        this.thumbnailSource = 'generated';
        this.thumbnailCacheBust = Date.now();
        this.thumbnailMessage = 'Thumbnail updated successfully';

        // Update local state
        const video = this.videos.find((v) => v.id === this.thumbnailVideoId);
        if (video) {
          video.has_custom_thumbnail = false;
        }
      } catch (e) {
        this.thumbnailError = e instanceof Error ? e.message : 'Failed to select thumbnail';
      } finally {
        this.thumbnailLoading = false;
      }
    },

    async uploadCustomThumbnail(): Promise<void> {
      if (!this.thumbnailVideoId || !this.thumbnailUploadFile) return;

      this.thumbnailUploading = true;
      this.thumbnailMessage = '';
      this.thumbnailError = '';

      try {
        const formData = new FormData();
        formData.append('file', this.thumbnailUploadFile);

        await videosApi.uploadThumbnail(this.thumbnailVideoId, formData);
        this.thumbnailSource = 'custom';
        this.thumbnailCacheBust = Date.now();
        this.thumbnailUploadFile = null;
        this.thumbnailMessage = 'Custom thumbnail uploaded successfully';

        // Update local state
        const video = this.videos.find((v) => v.id === this.thumbnailVideoId);
        if (video) {
          video.has_custom_thumbnail = true;
        }
      } catch (e) {
        this.thumbnailError = e instanceof Error ? e.message : 'Failed to upload thumbnail';
      } finally {
        this.thumbnailUploading = false;
      }
    },

    async revertToGeneratedThumbnail(): Promise<void> {
      if (!this.thumbnailVideoId) return;

      this.thumbnailLoading = true;
      this.thumbnailMessage = '';
      this.thumbnailError = '';

      try {
        await videosApi.revertThumbnail(this.thumbnailVideoId);
        this.thumbnailSource = 'generated';
        this.thumbnailCacheBust = Date.now();
        this.thumbnailMessage = 'Reverted to auto-generated thumbnail';

        // Update local state
        const video = this.videos.find((v) => v.id === this.thumbnailVideoId);
        if (video) {
          video.has_custom_thumbnail = false;
        }
      } catch (e) {
        this.thumbnailError = e instanceof Error ? e.message : 'Failed to revert thumbnail';
      } finally {
        this.thumbnailLoading = false;
      }
    },

    // Alias for revertToGeneratedThumbnail
    async revertThumbnail(): Promise<void> {
      return this.revertToGeneratedThumbnail();
    },

    // ===========================================================================
    // Progress Tracking
    // ===========================================================================

    async loadProgressForActiveVideos(): Promise<void> {
      const activeVideos = this.videos.filter(
        (v) => v.status === 'pending' || v.status === 'processing'
      );

      for (const video of activeVideos) {
        try {
          const progress = await videosApi.getProgress(video.id);
          this.progressData[video.id] = progress;

          // Update video in list
          const idx = this.videos.findIndex((v) => v.id === video.id);
          if (idx !== -1) {
            const existing = this.videos[idx];
            if (existing) {
              existing.status = progress.status;
              existing.current_step = progress.current_step;
              existing.current_progress = progress.current_progress;
            }
          }
        } catch (e) {
          console.error(`Failed to load progress for video ${video.id}:`, e);
        }
      }
    },

    updateProgress(videoId: number, progress: VideoProgress): void {
      this.progressData[videoId] = progress;

      // Update video in list if found
      const video = this.videos.find((v) => v.id === videoId);
      if (video) {
        video.status = progress.status;
        video.current_step = progress.current_step;
        video.current_progress = progress.current_progress;
      }
    },
  };
}
