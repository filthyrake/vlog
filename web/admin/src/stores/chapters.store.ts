/**
 * Chapters Store (Issue #413 Phase 7)
 * Manages video chapters for the video edit page
 */

import { chaptersApi } from '@/api/endpoints/chapters';
import type { Chapter, ChapterCreateRequest, ChapterUpdateRequest } from '@/api/types';

export interface ChaptersState {
  // Data
  chapters: Chapter[];
  videoId: number | null;

  // Form state - Create chapter
  newChapterTitle: string;
  newChapterDescription: string;
  newChapterStartTime: string; // MM:SS format for input
  newChapterEndTime: string;

  // Edit state
  editingChapter: Chapter | null;
  editChapterTitle: string;
  editChapterDescription: string;
  editChapterStartTime: string;
  editChapterEndTime: string;

  // Loading/error
  chaptersLoading: boolean;
  chaptersError: string | null;
  chaptersSaving: boolean;
}

export interface ChaptersActions {
  loadChapters(videoId: number): Promise<void>;
  createChapter(): Promise<void>;
  deleteChapter(id: number): Promise<void>;
  startEditChapter(chapter: Chapter): void;
  saveChapterEdits(): Promise<void>;
  cancelChapterEdit(): void;
  reorderChapters(chapterIds: number[]): Promise<void>;
  resetChapterForm(): void;
  parseTimeToSeconds(timeStr: string): number;
  formatSecondsToTime(seconds: number): string;
  moveChapterUp(chapterId: number): void;
  moveChapterDown(chapterId: number): void;
}

export type ChaptersStore = ChaptersState & ChaptersActions;

export function createChaptersStore(): ChaptersStore {
  return {
    // Initial state
    chapters: [],
    videoId: null,

    newChapterTitle: '',
    newChapterDescription: '',
    newChapterStartTime: '',
    newChapterEndTime: '',

    editingChapter: null,
    editChapterTitle: '',
    editChapterDescription: '',
    editChapterStartTime: '',
    editChapterEndTime: '',

    chaptersLoading: false,
    chaptersError: null,
    chaptersSaving: false,

    /**
     * Parse time string (MM:SS or HH:MM:SS) to seconds
     */
    parseTimeToSeconds(timeStr: string): number {
      if (!timeStr) return 0;
      const parts = timeStr.split(':').map((p) => parseInt(p, 10) || 0);
      if (parts.length === 2) {
        // MM:SS
        return (parts[0] ?? 0) * 60 + (parts[1] ?? 0);
      } else if (parts.length === 3) {
        // HH:MM:SS
        return (parts[0] ?? 0) * 3600 + (parts[1] ?? 0) * 60 + (parts[2] ?? 0);
      }
      return parseInt(timeStr, 10) || 0;
    },

    /**
     * Format seconds to time string (MM:SS or HH:MM:SS)
     */
    formatSecondsToTime(seconds: number): string {
      if (!seconds || seconds <= 0) return '';
      const h = Math.floor(seconds / 3600);
      const m = Math.floor((seconds % 3600) / 60);
      const s = Math.floor(seconds % 60);
      if (h > 0) {
        return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`;
      }
      return `${m}:${s.toString().padStart(2, '0')}`;
    },

    /**
     * Load chapters for a video
     */
    async loadChapters(videoId: number): Promise<void> {
      this.videoId = videoId;
      this.chaptersLoading = true;
      this.chaptersError = null;

      try {
        const response = await chaptersApi.list(videoId);
        this.chapters = response.chapters;
      } catch (e) {
        this.chaptersError = e instanceof Error ? e.message : 'Failed to load chapters';
        this.chapters = [];
      } finally {
        this.chaptersLoading = false;
      }
    },

    /**
     * Create a new chapter
     */
    async createChapter(): Promise<void> {
      if (!this.videoId || !this.newChapterTitle.trim()) {
        return;
      }

      const startTime = this.parseTimeToSeconds(this.newChapterStartTime);
      if (startTime < 0) {
        this.chaptersError = 'Start time is required';
        return;
      }

      this.chaptersSaving = true;
      this.chaptersError = null;

      try {
        const data: ChapterCreateRequest = {
          title: this.newChapterTitle.trim(),
          description: this.newChapterDescription.trim() || undefined,
          start_time: startTime,
          end_time: this.newChapterEndTime ? this.parseTimeToSeconds(this.newChapterEndTime) : undefined,
        };

        const chapter = await chaptersApi.create(this.videoId, data);
        this.chapters.push(chapter);
        this.resetChapterForm();
      } catch (e) {
        this.chaptersError = e instanceof Error ? e.message : 'Failed to create chapter';
      } finally {
        this.chaptersSaving = false;
      }
    },

    /**
     * Delete a chapter
     */
    async deleteChapter(id: number): Promise<void> {
      if (!this.videoId) return;

      if (!confirm('Are you sure you want to delete this chapter?')) {
        return;
      }

      this.chaptersSaving = true;
      this.chaptersError = null;

      try {
        await chaptersApi.delete(this.videoId, id);
        this.chapters = this.chapters.filter((c) => c.id !== id);
      } catch (e) {
        this.chaptersError = e instanceof Error ? e.message : 'Failed to delete chapter';
      } finally {
        this.chaptersSaving = false;
      }
    },

    /**
     * Start editing a chapter
     */
    startEditChapter(chapter: Chapter): void {
      this.editingChapter = chapter;
      this.editChapterTitle = chapter.title;
      this.editChapterDescription = chapter.description || '';
      this.editChapterStartTime = this.formatSecondsToTime(chapter.start_time);
      this.editChapterEndTime = chapter.end_time ? this.formatSecondsToTime(chapter.end_time) : '';
    },

    /**
     * Save chapter edits
     */
    async saveChapterEdits(): Promise<void> {
      if (!this.videoId || !this.editingChapter) {
        return;
      }

      this.chaptersSaving = true;
      this.chaptersError = null;

      try {
        const data: ChapterUpdateRequest = {};

        if (this.editChapterTitle.trim() !== this.editingChapter.title) {
          data.title = this.editChapterTitle.trim();
        }
        if (this.editChapterDescription.trim() !== (this.editingChapter.description || '')) {
          data.description = this.editChapterDescription.trim();
        }

        const newStartTime = this.parseTimeToSeconds(this.editChapterStartTime);
        if (newStartTime !== this.editingChapter.start_time) {
          data.start_time = newStartTime;
        }

        const newEndTime = this.editChapterEndTime ? this.parseTimeToSeconds(this.editChapterEndTime) : undefined;
        if (newEndTime !== this.editingChapter.end_time) {
          data.end_time = newEndTime;
        }

        // Only update if there are changes
        if (Object.keys(data).length > 0) {
          const updated = await chaptersApi.update(this.videoId, this.editingChapter.id, data);
          const index = this.chapters.findIndex((c) => c.id === updated.id);
          if (index !== -1) {
            this.chapters[index] = updated;
          }
        }

        this.cancelChapterEdit();
      } catch (e) {
        this.chaptersError = e instanceof Error ? e.message : 'Failed to update chapter';
      } finally {
        this.chaptersSaving = false;
      }
    },

    /**
     * Cancel chapter editing
     */
    cancelChapterEdit(): void {
      this.editingChapter = null;
      this.editChapterTitle = '';
      this.editChapterDescription = '';
      this.editChapterStartTime = '';
      this.editChapterEndTime = '';
    },

    /**
     * Reorder chapters
     */
    async reorderChapters(chapterIds: number[]): Promise<void> {
      if (!this.videoId) return;

      this.chaptersSaving = true;
      this.chaptersError = null;

      try {
        await chaptersApi.reorder(this.videoId, { chapter_ids: chapterIds });

        // Reorder local array to match
        const reordered: Chapter[] = [];
        for (const id of chapterIds) {
          const chapter = this.chapters.find((c) => c.id === id);
          if (chapter) {
            reordered.push({ ...chapter, position: reordered.length });
          }
        }
        this.chapters = reordered;
      } catch (e) {
        this.chaptersError = e instanceof Error ? e.message : 'Failed to reorder chapters';
      } finally {
        this.chaptersSaving = false;
      }
    },

    /**
     * Move chapter up (decrease position)
     */
    moveChapterUp(chapterId: number): void {
      const index = this.chapters.findIndex((c) => c.id === chapterId);
      if (index <= 0) return;

      const ids = this.chapters.map((c) => c.id);
      // We know these indices are valid because we checked index > 0
      const temp = ids[index - 1]!;
      ids[index - 1] = ids[index]!;
      ids[index] = temp;
      this.reorderChapters(ids);
    },

    /**
     * Move chapter down (increase position)
     */
    moveChapterDown(chapterId: number): void {
      const index = this.chapters.findIndex((c) => c.id === chapterId);
      if (index === -1 || index >= this.chapters.length - 1) return;

      const ids = this.chapters.map((c) => c.id);
      // We know these indices are valid because we checked bounds above
      const temp = ids[index]!;
      ids[index] = ids[index + 1]!;
      ids[index + 1] = temp;
      this.reorderChapters(ids);
    },

    /**
     * Reset the create chapter form
     */
    resetChapterForm(): void {
      this.newChapterTitle = '';
      this.newChapterDescription = '';
      this.newChapterStartTime = '';
      this.newChapterEndTime = '';
    },
  };
}
