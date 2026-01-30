/**
 * Upload Store
 * Manages video upload form and progress
 */

import { videosApi } from '@/api/endpoints/videos';
import type { Video } from '@/api/types';

export interface UploadState {
  // Form fields
  uploadFile: File | null;
  uploadTitle: string;
  uploadDescription: string;
  uploadCategory: number;

  // Progress
  uploading: boolean;
  uploadProgress: number;
  uploadMessage: string;
  uploadError: string;

  // Current XHR for abort capability
  uploadXhr: XMLHttpRequest | null;
}

export interface UploadActions {
  uploadVideo(): XMLHttpRequest | null;
  cancelUpload(): void;
  resetForm(): void;
  setFile(file: File): void;
}

export type UploadStore = UploadState & UploadActions;

export function createUploadStore(): UploadStore {
  return {
    // Initial state
    uploadFile: null,
    uploadTitle: '',
    uploadDescription: '',
    uploadCategory: 0,
    uploading: false,
    uploadProgress: 0,
    uploadMessage: '',
    uploadError: '',
    uploadXhr: null,

    /**
     * Upload a new video
     */
    uploadVideo(): XMLHttpRequest | null {
      if (!this.uploadFile) {
        this.uploadError = 'Please select a file to upload';
        return null;
      }

      if (!this.uploadTitle.trim()) {
        // Use filename as title if not provided
        this.uploadTitle = this.uploadFile.name.replace(/\.[^/.]+$/, '');
      }

      this.uploading = true;
      this.uploadProgress = 0;
      this.uploadMessage = '';
      this.uploadError = '';

      const formData = new FormData();
      formData.append('file', this.uploadFile);
      formData.append('title', this.uploadTitle.trim());
      formData.append('description', this.uploadDescription.trim());
      if (this.uploadCategory) {
        formData.append('category_id', this.uploadCategory.toString());
      }

      this.uploadXhr = videosApi.upload(formData, {
        onProgress: (percent) => {
          this.uploadProgress = percent;
        },
        onComplete: (video: Video) => {
          this.uploadMessage = `Video "${video.title}" uploaded successfully! Processing will begin shortly.`;
          this.uploading = false;
          this.uploadXhr = null;

          // Reset form after success
          setTimeout(() => {
            this.resetForm();
          }, 3000);
        },
        onError: (error) => {
          this.uploadError = error.message;
          this.uploading = false;
          this.uploadXhr = null;
        },
      });

      return this.uploadXhr;
    },

    /**
     * Cancel the current upload
     */
    cancelUpload(): void {
      if (this.uploadXhr) {
        this.uploadXhr.abort();
        this.uploadXhr = null;
        this.uploading = false;
        this.uploadProgress = 0;
        this.uploadMessage = 'Upload cancelled';
      }
    },

    /**
     * Reset the upload form
     */
    resetForm(): void {
      this.uploadFile = null;
      this.uploadTitle = '';
      this.uploadDescription = '';
      this.uploadCategory = 0;
      this.uploadProgress = 0;
      this.uploadMessage = '';
      this.uploadError = '';

      // Clear the dropzone component if it exists
      const dropzone = document.querySelector('vlog-dropzone[x-ref="uploadDropzone"]');
      if (dropzone && 'clear' in dropzone) {
        (dropzone as { clear(): void }).clear();
      }
    },

    /**
     * Set the file from input or drag-drop
     */
    setFile(file: File): void {
      this.uploadFile = file;
      // Auto-fill title from filename if empty
      if (!this.uploadTitle) {
        this.uploadTitle = file.name.replace(/\.[^/.]+$/, '');
      }
    },
  };
}
