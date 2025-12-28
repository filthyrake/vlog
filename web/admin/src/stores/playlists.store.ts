/**
 * Playlists Store
 * Manages video playlists
 */

import { playlistsApi } from '@/api/endpoints/playlists';
import type {
  Playlist,
  PlaylistDetail,
  PlaylistVisibility,
  PlaylistType,
  Video,
} from '@/api/types';

export interface PlaylistsState {
  // Data
  playlists: Playlist[];

  // Form state - Create playlist
  newPlaylistTitle: string;
  newPlaylistDescription: string;
  newPlaylistVisibility: PlaylistVisibility;
  newPlaylistType: PlaylistType;
  newPlaylistIsFeatured: boolean;

  // Edit state
  editingPlaylist: PlaylistDetail | null;
  editPlaylistTitle: string;
  editPlaylistDescription: string;
  editPlaylistVisibility: PlaylistVisibility;
  editPlaylistType: PlaylistType;
  editPlaylistIsFeatured: boolean;

  // Video management
  showAddVideoModal: boolean;
  addVideoSearch: string;
  draggingVideoId: number | null;

  // Loading/error
  playlistsLoading: boolean;
  playlistsError: string | null;
}

export interface PlaylistsActions {
  loadPlaylists(): Promise<void>;
  createPlaylist(): Promise<void>;
  deletePlaylist(id: number): Promise<void>;
  editPlaylist(id: number): Promise<void>;
  savePlaylistEdits(): Promise<void>;
  cancelPlaylistEdit(): void;
  addVideoToPlaylist(videoId: number): Promise<void>;
  removeVideoFromPlaylist(videoId: number): Promise<void>;
  reorderPlaylistVideos(videoIds: number[]): Promise<void>;
  startDragVideo(videoId: number): void;
  dropVideo(targetVideoId: number): void;
  getFilteredVideosForAdd(): Video[];
  formatPlaylistDuration(seconds: number): string;
}

export type PlaylistsStore = PlaylistsState & PlaylistsActions;

export function createPlaylistsStore(): PlaylistsStore {
  return {
    // Initial state
    playlists: [],
    newPlaylistTitle: '',
    newPlaylistDescription: '',
    newPlaylistVisibility: 'public',
    newPlaylistType: 'playlist',
    newPlaylistIsFeatured: false,

    editingPlaylist: null,
    editPlaylistTitle: '',
    editPlaylistDescription: '',
    editPlaylistVisibility: 'public',
    editPlaylistType: 'playlist',
    editPlaylistIsFeatured: false,

    showAddVideoModal: false,
    addVideoSearch: '',
    draggingVideoId: null,

    playlistsLoading: false,
    playlistsError: null,

    /**
     * Load all playlists
     */
    async loadPlaylists(): Promise<void> {
      this.playlistsLoading = true;
      this.playlistsError = null;

      try {
        const response = await playlistsApi.list();
        this.playlists = response.playlists;
      } catch (e) {
        this.playlistsError = e instanceof Error ? e.message : 'Failed to load playlists';
        this.playlists = [];
      } finally {
        this.playlistsLoading = false;
      }
    },

    /**
     * Create a new playlist
     */
    async createPlaylist(): Promise<void> {
      if (!this.newPlaylistTitle.trim()) {
        return;
      }

      this.playlistsLoading = true;
      this.playlistsError = null;

      try {
        const playlist = await playlistsApi.create({
          title: this.newPlaylistTitle.trim(),
          description: this.newPlaylistDescription.trim() || undefined,
          visibility: this.newPlaylistVisibility,
          playlist_type: this.newPlaylistType,
          is_featured: this.newPlaylistIsFeatured,
        });

        this.playlists.push(playlist);

        // Reset form
        this.newPlaylistTitle = '';
        this.newPlaylistDescription = '';
        this.newPlaylistVisibility = 'public';
        this.newPlaylistType = 'playlist';
        this.newPlaylistIsFeatured = false;
      } catch (e) {
        this.playlistsError = e instanceof Error ? e.message : 'Failed to create playlist';
      } finally {
        this.playlistsLoading = false;
      }
    },

    /**
     * Delete a playlist
     */
    async deletePlaylist(id: number): Promise<void> {
      if (!confirm('Are you sure you want to delete this playlist?')) {
        return;
      }

      this.playlistsLoading = true;
      this.playlistsError = null;

      try {
        await playlistsApi.delete(id);
        this.playlists = this.playlists.filter((p) => p.id !== id);

        // Close edit mode if deleting currently editing playlist
        if (this.editingPlaylist?.id === id) {
          this.cancelPlaylistEdit();
        }
      } catch (e) {
        this.playlistsError = e instanceof Error ? e.message : 'Failed to delete playlist';
      } finally {
        this.playlistsLoading = false;
      }
    },

    /**
     * Enter edit mode for a playlist
     */
    async editPlaylist(id: number): Promise<void> {
      this.playlistsLoading = true;
      this.playlistsError = null;

      try {
        const playlist = await playlistsApi.get(id);
        this.editingPlaylist = playlist;
        this.editPlaylistTitle = playlist.title;
        this.editPlaylistDescription = playlist.description || '';
        this.editPlaylistVisibility = playlist.visibility;
        this.editPlaylistType = playlist.playlist_type;
        this.editPlaylistIsFeatured = playlist.is_featured;
      } catch (e) {
        this.playlistsError = e instanceof Error ? e.message : 'Failed to load playlist';
      } finally {
        this.playlistsLoading = false;
      }
    },

    /**
     * Save playlist edits
     */
    async savePlaylistEdits(): Promise<void> {
      if (!this.editingPlaylist || !this.editPlaylistTitle.trim()) {
        return;
      }

      this.playlistsLoading = true;
      this.playlistsError = null;

      try {
        const updated = await playlistsApi.update(this.editingPlaylist.id, {
          title: this.editPlaylistTitle.trim(),
          description: this.editPlaylistDescription.trim() || undefined,
          visibility: this.editPlaylistVisibility,
          playlist_type: this.editPlaylistType,
          is_featured: this.editPlaylistIsFeatured,
        });

        // Update in list
        const index = this.playlists.findIndex((p) => p.id === updated.id);
        if (index !== -1) {
          this.playlists[index] = updated;
        }

        // Update editing state
        this.editingPlaylist = { ...this.editingPlaylist, ...updated };
      } catch (e) {
        this.playlistsError = e instanceof Error ? e.message : 'Failed to update playlist';
      } finally {
        this.playlistsLoading = false;
      }
    },

    /**
     * Cancel playlist edit mode
     */
    cancelPlaylistEdit(): void {
      this.editingPlaylist = null;
      this.editPlaylistTitle = '';
      this.editPlaylistDescription = '';
      this.editPlaylistVisibility = 'public';
      this.editPlaylistType = 'playlist';
      this.editPlaylistIsFeatured = false;
      this.showAddVideoModal = false;
      this.addVideoSearch = '';
    },

    /**
     * Add a video to the current playlist
     */
    async addVideoToPlaylist(videoId: number): Promise<void> {
      if (!this.editingPlaylist) {
        return;
      }

      this.playlistsLoading = true;
      this.playlistsError = null;

      try {
        await playlistsApi.addVideo(this.editingPlaylist.id, { video_id: videoId });

        // Reload the playlist to get updated video list
        const updated = await playlistsApi.get(this.editingPlaylist.id);
        this.editingPlaylist = updated;

        // Update video count in list
        const playlistIndex = this.playlists.findIndex((p) => p.id === updated.id);
        if (playlistIndex !== -1 && this.playlists[playlistIndex]) {
          this.playlists[playlistIndex]!.video_count = updated.video_count;
          this.playlists[playlistIndex]!.total_duration = updated.total_duration;
        }

        this.showAddVideoModal = false;
        this.addVideoSearch = '';
      } catch (e) {
        this.playlistsError = e instanceof Error ? e.message : 'Failed to add video to playlist';
      } finally {
        this.playlistsLoading = false;
      }
    },

    /**
     * Remove a video from the current playlist
     */
    async removeVideoFromPlaylist(videoId: number): Promise<void> {
      if (!this.editingPlaylist) {
        return;
      }

      this.playlistsLoading = true;
      this.playlistsError = null;

      try {
        await playlistsApi.removeVideo(this.editingPlaylist.id, videoId);

        // Reload the playlist to get updated video list
        const updated = await playlistsApi.get(this.editingPlaylist.id);
        this.editingPlaylist = updated;

        // Update video count in list
        const playlistIndex = this.playlists.findIndex((p) => p.id === updated.id);
        if (playlistIndex !== -1 && this.playlists[playlistIndex]) {
          this.playlists[playlistIndex]!.video_count = updated.video_count;
          this.playlists[playlistIndex]!.total_duration = updated.total_duration;
        }
      } catch (e) {
        this.playlistsError = e instanceof Error ? e.message : 'Failed to remove video from playlist';
      } finally {
        this.playlistsLoading = false;
      }
    },

    /**
     * Reorder videos in the playlist
     */
    async reorderPlaylistVideos(videoIds: number[]): Promise<void> {
      if (!this.editingPlaylist) {
        return;
      }

      this.playlistsLoading = true;
      this.playlistsError = null;

      try {
        await playlistsApi.reorder(this.editingPlaylist.id, { video_ids: videoIds });

        // Reload the playlist to get updated video list with new positions
        const updated = await playlistsApi.get(this.editingPlaylist.id);
        this.editingPlaylist = updated;
      } catch (e) {
        this.playlistsError = e instanceof Error ? e.message : 'Failed to reorder playlist';
      } finally {
        this.playlistsLoading = false;
      }
    },

    /**
     * Start dragging a video (for reordering)
     */
    startDragVideo(videoId: number): void {
      this.draggingVideoId = videoId;
    },

    /**
     * Drop a video at a new position
     */
    dropVideo(targetVideoId: number): void {
      if (!this.editingPlaylist || !this.draggingVideoId || this.draggingVideoId === targetVideoId) {
        this.draggingVideoId = null;
        return;
      }

      const videos = [...this.editingPlaylist.videos];
      const dragIndex = videos.findIndex((v) => v.id === this.draggingVideoId);
      const dropIndex = videos.findIndex((v) => v.id === targetVideoId);

      if (dragIndex === -1 || dropIndex === -1) {
        this.draggingVideoId = null;
        return;
      }

      // Reorder locally first for immediate feedback
      const draggedVideo = videos[dragIndex];
      if (!draggedVideo) {
        this.draggingVideoId = null;
        return;
      }

      videos.splice(dragIndex, 1);
      videos.splice(dropIndex, 0, draggedVideo);

      // Update local state
      this.editingPlaylist.videos = videos;

      // Send to server
      const videoIds = videos.map((v) => v.id);
      this.reorderPlaylistVideos(videoIds);

      this.draggingVideoId = null;
    },

    /**
     * Get filtered videos that can be added to the playlist
     * Uses the videos array from VideosStore (accessed via 'this' in Alpine context)
     */
    getFilteredVideosForAdd(): Video[] {
      // Note: In Alpine.js context, 'this.videos' will be available from VideosStore
      const videos = (this as any).videos || [];
      const currentVideoIds = new Set(this.editingPlaylist?.videos.map((v) => v.id) || []);

      const availableVideos = videos.filter(
        (v: Video) => v.status === 'ready' && !currentVideoIds.has(v.id)
      );

      if (!this.addVideoSearch.trim()) {
        return availableVideos;
      }

      const search = this.addVideoSearch.toLowerCase();
      return availableVideos.filter((v: Video) => v.title.toLowerCase().includes(search));
    },

    /**
     * Format playlist duration for display
     */
    formatPlaylistDuration(seconds: number): string {
      if (!seconds || seconds === 0) {
        return '0m';
      }

      const hours = Math.floor(seconds / 3600);
      const minutes = Math.floor((seconds % 3600) / 60);

      if (hours > 0) {
        return `${hours}h ${minutes}m`;
      }
      return `${minutes}m`;
    },
  };
}
