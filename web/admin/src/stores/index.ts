/**
 * Admin Store
 * Combined store that composes all feature stores for Alpine.js
 */

import { createAuthStore, type AuthStore } from './auth.store';
import { createUIStore, type UIStore } from './ui.store';
import { createVideosStore, type VideosStore } from './videos.store';
import { createCategoriesStore, type CategoriesStore } from './categories.store';
import { createPlaylistsStore, type PlaylistsStore } from './playlists.store';
import { createUploadStore, type UploadStore } from './upload.store';
import { createWorkersStore, type WorkersStore } from './workers.store';
import { createAnalyticsStore, type AnalyticsStore } from './analytics.store';
import { createSettingsStore, type SettingsStore } from './settings.store';
import { createBulkStore, type BulkStore } from './bulk.store';
import { createSSEStore, type SSEStore, getActiveVideoIds } from './sse.store';
import { createChaptersStore, type ChaptersStore } from './chapters.store';
import { getKeyboardManager, destroyKeyboardManager } from '@/utils/keyboard';
import type { ProgressSSEEvent, WorkerSSEEvent, CustomField } from '@/api/types';

// Polling interval IDs for cleanup
let pollingIntervals: ReturnType<typeof setInterval>[] = [];

// Combined store type
export type AdminStore = AuthStore &
  UIStore &
  VideosStore &
  CategoriesStore &
  PlaylistsStore &
  UploadStore &
  WorkersStore &
  AnalyticsStore &
  SettingsStore &
  BulkStore &
  SSEStore &
  ChaptersStore & {
    init(): Promise<void>;
    destroy(): void;
    getApplicableCustomFields(): CustomField[];
    // CSP-compatible navigation methods (Alpine CSP doesn't support semicolons in expressions)
    switchToWorkers(): void;
    switchToAnalytics(): void;
    switchToSettings(): void;
    switchToSettingsBranding(): void;
    switchToSettingsCustomFields(): void;
    editModalTab: 'details' | 'chapters';
    openEditModalWithChapters(video: import('@/api/types').Video): void;
    // CSP-compatible helper methods (Alpine CSP doesn't support >, <, ternary, arrow functions)
    hasSelectedVideos(): boolean;
    hasCustomFields(): boolean;
    hasFilteredVideos(): boolean;
    hasVideos(): boolean;
    showVideosTable(): boolean;
    showVideoCards(): boolean;
    hasDeletedSelected(): boolean;
    isAllSelected(): boolean;
    showClearFiltersEmpty(): boolean;
    showNoVideosEmpty(): boolean;
    hasActiveWorkers(): boolean;
    hasDeploymentEvents(): boolean;
    hasGlobalCustomFields(): boolean;
    getGlobalCustomFields(): CustomField[];
    hasCategoryCustomFields(catId: number): boolean;
    getCategoryCustomFields(catId: number): CustomField[];
    hasChapters(): boolean;
    hasThumbnailFrames(): boolean;
    hasApplicableCustomFields(): boolean;
    tabClass(tabName: string): string;
    greaterThan(a: number, b: number): boolean;
    greaterOrEqual(a: number, b: number): boolean;
    lessThan(a: number, b: number): boolean;
    isUploadInProgress(): boolean;
    isReuploadInProgress(): boolean;
    workerHeartbeatClass(seconds: number): string;
    hasJobsFailed(worker: { jobs_failed?: number }): boolean;
    hasJobQualities(job: { qualities?: unknown[] }): boolean;
    // CSP-compatible optional chaining alternatives (playlist, constraint, analytics, settings helpers)
    // Note: progress helpers are in videos.store, metrics helpers are in workers.store
    getPlaylistTitle(): string;
    getPlaylistVideoCount(): number;
    getPlaylistVideos(): unknown[];
    hasPlaylistVideos(): boolean;
    getConstraintMin(obj: { constraints?: { min?: number } }): number | undefined;
    getConstraintMax(obj: { constraints?: { max?: number } }): number | undefined;
    getConstraintStep(obj: { constraints?: { step?: number } }): number | string;
    getConstraintMinLength(obj: { constraints?: { min_length?: number } }): number | undefined;
    getConstraintMaxLength(obj: { constraints?: { max_length?: number } }): number | undefined;
    getConstraintPattern(obj: { constraints?: { pattern?: string } }): string | undefined;
    getConstraintMinDate(obj: { constraints?: { min_date?: string } }): string | undefined;
    getConstraintMaxDate(obj: { constraints?: { max_date?: string } }): string | undefined;
    getConstraintEnumValues(obj: { constraints?: { enum_values?: string[] } }): string[];
    hasConstraintMin(obj: { constraints?: { min?: number } }): boolean;
    hasConstraintMax(obj: { constraints?: { max?: number } }): boolean;
    isSettingModified(category: string, key: string): boolean;
    getAnalyticsTotalViews(): string;
    // CSP-compatible ternary alternatives
    errorOrSuccess(hasError: boolean): 'error' | 'success';
    videoStatusVariant(status: string): string;
    publishedVariant(publishedAt: string | null | undefined): 'success' | 'neutral';
    publishedText(publishedAt: string | null | undefined): string;
    publishToggleText(publishedAt: string | null | undefined): string;
    spriteButtonText(status: string | null | undefined): string;
    workerHwaccelLabel(worker: { hwaccel_type?: string | null; gpu_name?: string | null }): string;
    workerDeploymentLabel(deploymentType: string | null | undefined): string;
    settingsTabClass(tabName: string): string;
    errorMessageClass(hasError: boolean): string;
    enabledDisabledText(enabled: boolean): string;
    editModalTabClass(tabName: string): string;
    customFieldModalTitle(): string;
    customFieldSubmitText(): string;
    numberFieldValue(values: Record<number, unknown>, fieldId: number): string;
    conditionalText(condition: boolean, trueText: string, falseText: string): string;
    conditionalClass(condition: boolean, trueClass: string, falseClass: string): string;
    watermarkStatusClass(): string;
    thumbnailSrc(): string;
    bulkMessageClass(hasError: boolean): string;
    versionTitleText(isOutdated: boolean): string;
    commandButtonTitle(isPending: boolean): string;
    parseNumberInput(value: string): number | null;
    setEditCustomFieldNumber(fieldId: number, value: string): void;
    setBulkCustomFieldNumber(fieldId: number, value: string): void;
    // Safe string/number formatting helpers
    capitalizeStatus(status: string | null | undefined): string;
    truncateId(id: string | null | undefined, length: number): string;
    formatPercent(value: number | null | undefined, decimals?: number): string;
    formatMB(value: number | null | undefined): string;
    formatOpacity(value: number | null | undefined): string;
    formatDiskUsage(): string;
    formatGpuMemory(): string;
    formatViews(views: number | null | undefined): string;
    formatEventType(eventType: string | null | undefined): string;
    formatSettingKey(key: string | null | undefined): string;
    formatCategoryName(category: string | null | undefined): string;
    formatCategoryTitle(category: string | null | undefined): string;
    hasActiveJobsList(): boolean;
    getActiveJobsList(): unknown[];
    hasBrandingLogo(): boolean;
    getBrandingLogoUrl(): string;
    getBrandingLogoPath(): string;
    hasBrandingFavicon(): boolean;
    getBrandingFaviconUrl(): string;
    getBrandingFaviconPath(): string;
  };

/**
 * Create the combined admin store
 * This factory function returns an object compatible with Alpine.js x-data
 */
export function createAdminStore(): AdminStore {
  // Create all individual stores
  const authStore = createAuthStore();
  const uiStore = createUIStore();
  const videosStore = createVideosStore();
  const categoriesStore = createCategoriesStore();
  const playlistsStore = createPlaylistsStore();
  const uploadStore = createUploadStore();
  const workersStore = createWorkersStore();
  const analyticsStore = createAnalyticsStore();
  const settingsStore = createSettingsStore();
  const bulkStore = createBulkStore();
  const sseStore = createSSEStore();
  const chaptersStore = createChaptersStore();

  // Create the combined store
  const store: AdminStore = {
    // Spread all stores
    ...authStore,
    ...uiStore,
    ...videosStore,
    ...categoriesStore,
    ...playlistsStore,
    ...uploadStore,
    ...workersStore,
    ...analyticsStore,
    ...settingsStore,
    ...bulkStore,
    ...sseStore,
    ...chaptersStore,

    // Edit modal tab state
    editModalTab: 'details' as 'details' | 'chapters',

    /**
     * Open edit modal and load chapters for the video
     * This wraps openEditModal with chapter loading
     */
    openEditModalWithChapters(video: import('@/api/types').Video): void {
      this.openEditModal(video);
      this.editModalTab = 'details';
      // Load chapters asynchronously
      this.loadChapters(video.id);
    },

    /**
     * CSP-compatible navigation: Switch to Workers tab and load data
     * Replaces: tab = 'workers'; loadWorkers(); loadDeploymentHistory()
     */
    switchToWorkers(): void {
      this.tab = 'workers';
      this.loadWorkers();
      this.loadDeploymentHistory();
    },

    /**
     * CSP-compatible navigation: Switch to Analytics tab and load data
     * Replaces: tab = 'analytics'; loadAnalytics()
     */
    switchToAnalytics(): void {
      this.tab = 'analytics';
      this.loadAnalytics();
    },

    /**
     * CSP-compatible navigation: Switch to Settings tab and load data
     * Replaces: tab = 'settings'; loadWatermarkSettings(); loadAllSettings()
     */
    switchToSettings(): void {
      this.tab = 'settings';
      this.loadWatermarkSettings();
      this.loadAllSettings();
    },

    /**
     * CSP-compatible navigation: Switch to Branding settings sub-tab
     * Replaces: settingsTab = 'branding'; loadBrandingSettings()
     */
    switchToSettingsBranding(): void {
      this.settingsTab = 'branding';
      this.loadBrandingSettings();
    },

    /**
     * CSP-compatible navigation: Switch to Custom Fields settings sub-tab
     * Replaces: settingsTab = 'custom_fields'; loadCustomFields()
     */
    switchToSettingsCustomFields(): void {
      this.settingsTab = 'custom_fields';
      this.loadCustomFields();
    },

    /**
     * Get custom fields applicable to the currently editing video's category
     * Used in the edit modal to show only relevant custom fields
     */
    getApplicableCustomFields(): CustomField[] {
      const categoryId = this.editCategory;
      return this.customFields.filter((field) => {
        // If no category restrictions, field applies to all
        if (!field.applies_to_categories || field.applies_to_categories.length === 0) {
          return true;
        }
        // If editing video has no category, show fields with no restrictions
        if (!categoryId) {
          return field.applies_to_categories.length === 0;
        }
        // Check if field applies to the selected category
        return field.applies_to_categories.includes(categoryId);
      });
    },

    // CSP-compatible helper methods
    // Alpine CSP mode doesn't support >, <, >=, <=, ternary operators, or arrow functions
    hasSelectedVideos(): boolean {
      return this.selectedVideos.length > 0;
    },

    hasCustomFields(): boolean {
      return this.customFields.length > 0;
    },

    hasFilteredVideos(): boolean {
      return this.filteredVideos.length > 0;
    },

    hasVideos(): boolean {
      return this.videos.length > 0;
    },

    showVideosTable(): boolean {
      return !this.loading && this.filteredVideos.length > 0;
    },

    showVideoCards(): boolean {
      return !this.loading && this.filteredVideos.length > 0;
    },

    hasDeletedSelected(): boolean {
      return this.selectedVideos.some(id => {
        const video = this.videos.find(v => v.id === id);
        return video?.deleted_at != null;
      });
    },

    isAllSelected(): boolean {
      return this.selectedVideos.length === this.videos.length && this.videos.length > 0;
    },

    showClearFiltersEmpty(): boolean {
      return !this.loading && this.filteredVideos.length === 0 &&
        (Boolean(this.videoSearch) || Boolean(this.videoStatusFilter) || Boolean(this.videoCategoryFilter));
    },

    showNoVideosEmpty(): boolean {
      return !this.loading && this.videos.length === 0 &&
        !this.videoSearch && !this.videoStatusFilter && !this.videoCategoryFilter;
    },

    hasActiveWorkers(): boolean {
      return this.workersList.filter(w => w.status !== 'offline' && w.status !== 'disabled').length > 0;
    },

    hasDeploymentEvents(): boolean {
      return !this.deploymentEventsLoading && this.deploymentEvents.length > 0;
    },

    hasGlobalCustomFields(): boolean {
      return this.getGlobalCustomFields().length > 0;
    },

    getGlobalCustomFields(): CustomField[] {
      const result: CustomField[] = [];
      for (const f of this.customFields) {
        if (!f.applies_to_categories || f.applies_to_categories.length === 0) {
          result.push(f);
        }
      }
      return result;
    },

    hasCategoryCustomFields(catId: number): boolean {
      return this.getCategoryCustomFields(catId).length > 0;
    },

    getCategoryCustomFields(catId: number): CustomField[] {
      const result: CustomField[] = [];
      for (const f of this.customFields) {
        if (f.applies_to_categories && f.applies_to_categories.includes(catId)) {
          result.push(f);
        }
      }
      return result;
    },

    hasChapters(): boolean {
      return this.chapters.length > 0;
    },

    hasThumbnailFrames(): boolean {
      return this.thumbnailFrames.length > 0;
    },

    hasApplicableCustomFields(): boolean {
      return this.getApplicableCustomFields().length > 0;
    },

    tabClass(tabName: string): string {
      return this.tab === tabName ? 'text-blue-400' : 'text-dark-400 hover:text-white';
    },

    greaterThan(a: number, b: number): boolean {
      return a > b;
    },

    greaterOrEqual(a: number, b: number): boolean {
      return a >= b;
    },

    lessThan(a: number, b: number): boolean {
      return a < b;
    },

    isUploadInProgress(): boolean {
      return this.uploadProgress >= 0;
    },

    isReuploadInProgress(): boolean {
      return this.reuploadProgress >= 0;
    },

    workerHeartbeatClass(seconds: number): string {
      if (seconds < 30) return 'text-green-400';
      if (seconds < 120) return 'text-yellow-400';
      return 'text-red-400';
    },

    hasJobsFailed(worker: { jobs_failed?: number }): boolean {
      return (worker.jobs_failed ?? 0) > 0;
    },

    hasJobQualities(job: { qualities?: unknown[] }): boolean {
      return Boolean(job.qualities && job.qualities.length > 0);
    },

    // CSP-compatible optional chaining alternatives for playlist, constraints, analytics
    // Note: progress helpers (getProgressPercent, etc.) are in videos.store.ts
    // Note: metrics helpers (getMetricsProcess, etc.) are in workers.store.ts

    getPlaylistTitle(): string {
      return this.editingPlaylist ? (this.editingPlaylist.title || '') : '';
    },

    getPlaylistVideoCount(): number {
      return (this.editingPlaylist && this.editingPlaylist.videos) ? this.editingPlaylist.videos.length : 0;
    },

    getPlaylistVideos(): unknown[] {
      return (this.editingPlaylist && this.editingPlaylist.videos) ? this.editingPlaylist.videos : [];
    },

    hasPlaylistVideos(): boolean {
      return Boolean(this.editingPlaylist && this.editingPlaylist.videos && this.editingPlaylist.videos.length > 0);
    },

    getConstraintMin(obj: { constraints?: { min?: number } }): number | undefined {
      return (obj && obj.constraints) ? obj.constraints.min : undefined;
    },

    getConstraintMax(obj: { constraints?: { max?: number } }): number | undefined {
      return (obj && obj.constraints) ? obj.constraints.max : undefined;
    },

    getConstraintStep(obj: { constraints?: { step?: number } }): number | string {
      return (obj && obj.constraints && obj.constraints.step !== undefined) ? obj.constraints.step : 'any';
    },

    getConstraintMinLength(obj: { constraints?: { min_length?: number } }): number | undefined {
      return (obj && obj.constraints) ? obj.constraints.min_length : undefined;
    },

    getConstraintMaxLength(obj: { constraints?: { max_length?: number } }): number | undefined {
      return (obj && obj.constraints) ? obj.constraints.max_length : undefined;
    },

    getConstraintPattern(obj: { constraints?: { pattern?: string } }): string | undefined {
      return (obj && obj.constraints) ? obj.constraints.pattern : undefined;
    },

    getConstraintMinDate(obj: { constraints?: { min_date?: string } }): string | undefined {
      return (obj && obj.constraints) ? obj.constraints.min_date : undefined;
    },

    getConstraintMaxDate(obj: { constraints?: { max_date?: string } }): string | undefined {
      return (obj && obj.constraints) ? obj.constraints.max_date : undefined;
    },

    getConstraintEnumValues(obj: { constraints?: { enum_values?: string[] } }): string[] {
      return (obj && obj.constraints && obj.constraints.enum_values) ? obj.constraints.enum_values : [];
    },

    hasConstraintMin(obj: { constraints?: { min?: number } }): boolean {
      return Boolean(obj && obj.constraints && obj.constraints.min !== undefined);
    },

    hasConstraintMax(obj: { constraints?: { max?: number } }): boolean {
      return Boolean(obj && obj.constraints && obj.constraints.max !== undefined);
    },

    isSettingModified(category: string, key: string): boolean {
      const catMods = this.settingsModified[category];
      return Boolean(catMods && catMods[key] !== undefined);
    },

    getAnalyticsTotalViews(): string {
      return (this.analyticsOverview && this.analyticsOverview.total_views)
        ? this.analyticsOverview.total_views.toLocaleString()
        : '0';
    },

    // CSP-compatible ternary alternatives
    errorOrSuccess(hasError: boolean): 'error' | 'success' {
      return hasError ? 'error' : 'success';
    },

    videoStatusVariant(status: string): string {
      if (status === 'ready') return 'success';
      if (status === 'processing') return 'processing';
      if (status === 'pending') return 'pending';
      return 'error';
    },

    publishedVariant(publishedAt: string | null | undefined): 'success' | 'neutral' {
      return publishedAt ? 'success' : 'neutral';
    },

    publishedText(publishedAt: string | null | undefined): string {
      return publishedAt ? 'Published' : 'Draft';
    },

    publishToggleText(publishedAt: string | null | undefined): string {
      return publishedAt ? 'Unpublish' : 'Publish';
    },

    spriteButtonText(status: string | null | undefined): string {
      if (status === 'generating') return 'Generating...';
      if (status === 'pending') return 'Queued';
      if (status === 'ready') return 'Regenerate Sprites';
      return 'Generate Sprites';
    },

    workerHwaccelLabel(worker: { hwaccel_type?: string | null; gpu_name?: string | null }): string {
      if (!worker.hwaccel_type) return 'CPU';
      return worker.gpu_name || worker.hwaccel_type.toUpperCase();
    },

    workerDeploymentLabel(deploymentType: string | null | undefined): string {
      if (deploymentType === 'kubernetes') return 'k8s';
      return deploymentType || '';
    },

    settingsTabClass(tabName: string): string {
      const currentTab = this.settingsTab || '';
      return currentTab === tabName
        ? 'bg-blue-600 text-white'
        : 'bg-dark-800 text-dark-300 hover:bg-dark-700';
    },

    errorMessageClass(hasError: boolean): string {
      return hasError
        ? 'bg-red-900/20 border-red-800 text-red-400'
        : 'bg-green-900/20 border-green-800 text-green-400';
    },

    enabledDisabledText(enabled: boolean): string {
      return enabled ? 'Enabled' : 'Disabled';
    },

    editModalTabClass(tabName: string): string {
      const currentTab = this.editModalTab || '';
      return currentTab === tabName
        ? 'text-blue-400 border-blue-400'
        : 'text-dark-400 border-transparent hover:text-white';
    },

    customFieldModalTitle(): string {
      return this.customFieldEditing ? 'Edit Custom Field' : 'Create Custom Field';
    },

    customFieldSubmitText(): string {
      return this.customFieldEditing ? 'Save Changes' : 'Create Field';
    },

    numberFieldValue(values: Record<number, unknown>, fieldId: number): string {
      const val = values[fieldId];
      if (val === null || val === undefined) return '';
      return String(val);
    },

    conditionalText(condition: boolean, trueText: string, falseText: string): string {
      return condition ? trueText : falseText;
    },

    conditionalClass(condition: boolean, trueClass: string, falseClass: string): string {
      return condition ? trueClass : falseClass;
    },

    watermarkStatusClass(): string {
      const enabled = this.watermarkSettings && this.watermarkSettings.enabled;
      return enabled
        ? 'bg-green-900/20 border border-green-800'
        : 'bg-dark-800';
    },

    thumbnailSrc(): string {
      if (!this.thumbnailVideoSlug) return '';
      return '/videos/' + this.thumbnailVideoSlug + '/thumbnail.jpg?t=' + this.thumbnailCacheBust;
    },

    bulkMessageClass(hasError: boolean): string {
      return hasError ? 'text-red-400' : 'text-green-400';
    },

    versionTitleText(isOutdated: boolean): string {
      return isOutdated ? 'Outdated version' : 'Current version';
    },

    commandButtonTitle(isPending: boolean): string {
      return isPending ? 'Command pending...' : 'Restart worker (after current job)';
    },

    parseNumberInput(value: string): number | null {
      return value ? parseFloat(value) : null;
    },

    setEditCustomFieldNumber(fieldId: number, value: string): void {
      this.editCustomFieldValues[fieldId] = value ? parseFloat(value) : null;
    },

    setBulkCustomFieldNumber(fieldId: number, value: string): void {
      this.bulkCustomFieldValues[fieldId] = value ? parseFloat(value) : null;
    },

    // Safe string/number formatting helpers
    capitalizeStatus(status: string | null | undefined): string {
      if (!status) return '';
      return status.charAt(0).toUpperCase() + status.slice(1);
    },

    truncateId(id: string | null | undefined, length: number): string {
      if (!id) return '';
      return id.slice(0, length);
    },

    formatPercent(value: number | null | undefined, decimals?: number): string {
      if (value === null || value === undefined) return '0%';
      return value.toFixed(decimals ?? 0) + '%';
    },

    formatMB(value: number | null | undefined): string {
      if (value === null || value === undefined) return '0 MB';
      return value.toFixed(0) + ' MB';
    },

    formatOpacity(value: number | null | undefined): string {
      if (value === null || value === undefined) return '0%';
      return (value * 100).toFixed(0) + '%';
    },

    formatDiskUsage(): string {
      const used = this.getMetricsDisk('used_gb');
      const total = this.getMetricsDisk('total_gb');
      return used.toFixed(1) + ' / ' + total.toFixed(1) + ' GB';
    },

    formatGpuMemory(): string {
      const used = this.getMetricsGpu('memory_used_mb');
      const total = this.getMetricsGpu('memory_total_mb');
      return used.toFixed(0) + ' / ' + total.toFixed(0) + ' MB';
    },

    // Additional safe formatting helpers
    formatViews(views: number | null | undefined): string {
      if (views === null || views === undefined) return '0';
      return views.toLocaleString();
    },

    formatEventType(eventType: string | null | undefined): string {
      if (!eventType) return '';
      return eventType.replace(/_/g, ' ');
    },

    formatSettingKey(key: string | null | undefined): string {
      if (!key) return '';
      const parts = key.split('.');
      const lastPart = parts[parts.length - 1] || '';
      return lastPart.replace(/_/g, ' ');
    },

    formatCategoryName(category: string | null | undefined): string {
      if (!category) return '';
      return category.replace(/_/g, ' ');
    },

    formatCategoryTitle(category: string | null | undefined): string {
      if (!category) return 'Settings';
      return category.replace(/_/g, ' ') + ' Settings';
    },

    hasActiveJobsList(): boolean {
      return Boolean(this.activeJobs && this.activeJobs.jobs && this.activeJobs.jobs.length > 0);
    },

    getActiveJobsList(): unknown[] {
      if (!this.activeJobs || !this.activeJobs.jobs) return [];
      return this.activeJobs.jobs;
    },

    hasBrandingLogo(): boolean {
      return Boolean(this.brandingSettings && this.brandingSettings.logo_exists);
    },

    getBrandingLogoUrl(): string {
      if (!this.brandingSettings) return '';
      return this.brandingSettings.logo_url || '';
    },

    getBrandingLogoPath(): string {
      if (!this.brandingSettings) return '';
      return this.brandingSettings.logo_path || '';
    },

    hasBrandingFavicon(): boolean {
      return Boolean(this.brandingSettings && this.brandingSettings.favicon_exists);
    },

    getBrandingFaviconUrl(): string {
      if (!this.brandingSettings) return '';
      return this.brandingSettings.favicon_url || '';
    },

    getBrandingFaviconPath(): string {
      if (!this.brandingSettings) return '';
      return this.brandingSettings.favicon_path || '';
    },

    /**
     * Clean up polling intervals and SSE connections
     * Called when the admin component is destroyed (if ever)
     */
    destroy(): void {
      // Clear all polling intervals
      for (const intervalId of pollingIntervals) {
        clearInterval(intervalId);
      }
      pollingIntervals = [];

      // Close SSE connections
      this.disconnectProgressSSE();
      this.disconnectWorkersSSE();

      // Clean up keyboard manager
      destroyKeyboardManager();
    },

    /**
     * Initialize the admin application
     * Called automatically by Alpine.js on component mount
     */
    async init(): Promise<void> {
      // Initialize toast container
      this.initToastContainer();

      // Initialize keyboard shortcuts
      getKeyboardManager();

      // Check authentication first
      const authOk = await this.checkAuth();
      if (!authOk) {
        // Focus the auth input after modal is shown
        // Note: $nextTick and $refs are provided by Alpine context
        return;
      }

      // Fetch CSRF token for state-changing requests
      await this.fetchCsrfToken();

      // Load initial data
      await Promise.all([
        this.loadVideos(),
        this.loadCategories(),
        this.loadPlaylists(),
      ]);

      // Set up SSE event handlers
      this.onProgressEvent = (event: ProgressSSEEvent) => {
        this.updateProgress(event.video_id, {
          id: event.video_id,
          status: event.status,
          current_step: event.current_step,
          current_progress: event.current_progress,
          qualities: event.qualities,
        });
      };

      this.onWorkerEvent = (event: WorkerSSEEvent) => {
        // Update worker status in list
        if (event.type === 'status' && event.worker_id) {
          const worker = this.workersList.find((w) => w.worker_id === event.worker_id);
          if (worker && event.status) {
            worker.status = event.status;
            this.computeWorkerStats();
          }
        }
      };

      // Connect to SSE for real-time updates
      this.connectProgressSSE(getActiveVideoIds(this.videos));

      // Set up polling intervals as fallback
      setupPolling.call(this);
    },
  };

  return store;
}

/**
 * Set up polling intervals as fallback for SSE
 * Interval IDs are stored for cleanup in destroy()
 */
function setupPolling(this: AdminStore) {
  // Clear any existing intervals first
  for (const intervalId of pollingIntervals) {
    clearInterval(intervalId);
  }
  pollingIntervals = [];

  // Auto-refresh videos every 30 seconds
  pollingIntervals.push(
    setInterval(() => {
      this.loadVideos();
    }, 30000)
  );

  // Fallback polling for progress if SSE not connected
  pollingIntervals.push(
    setInterval(() => {
      if (!this.progressSSE || this.progressSSE.eventSource.readyState !== EventSource.OPEN) {
        this.loadProgressForActiveVideos();
      }
    }, 5000)
  );

  // Auto-refresh workers every 10 seconds when workers tab is active
  pollingIntervals.push(
    setInterval(() => {
      if (this.tab === 'workers' && (!this.workersSSE || this.workersSSE.eventSource.readyState !== EventSource.OPEN)) {
        this.loadWorkers();
      }
    }, 10000)
  );
}

// Export for Alpine.js global access
declare global {
  interface Window {
    createAdminStore: typeof createAdminStore;
  }
}

if (typeof window !== 'undefined') {
  window.createAdminStore = createAdminStore;
}

// Re-export types and individual store creators for testing/extension
export { createAuthStore, type AuthStore } from './auth.store';
export { createUIStore, type UIStore } from './ui.store';
export { createVideosStore, type VideosStore } from './videos.store';
export { createCategoriesStore, type CategoriesStore } from './categories.store';
export { createPlaylistsStore, type PlaylistsStore } from './playlists.store';
export { createUploadStore, type UploadStore } from './upload.store';
export { createWorkersStore, type WorkersStore } from './workers.store';
export { createAnalyticsStore, type AnalyticsStore } from './analytics.store';
export { createSettingsStore, type SettingsStore } from './settings.store';
export { createBulkStore, type BulkStore } from './bulk.store';
export { createSSEStore, type SSEStore } from './sse.store';
export { createChaptersStore, type ChaptersStore } from './chapters.store';
export type { AlpineContext, AdminTab, SettingsTab } from './types';
