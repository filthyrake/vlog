/**
 * Analytics Store
 * Manages analytics data and views
 */

import { analyticsApi } from '@/api/endpoints/analytics';
import type { AnalyticsPeriod } from '@/api/endpoints/analytics';
import type { AnalyticsOverview, VideoAnalytics } from '@/api/types';
import { formatPercent, formatWatchTime, formatHours } from '@/utils/formatters';

export interface AnalyticsState {
  // Overview data - always has a value (defaults provided)
  analyticsOverview: AnalyticsOverview;
  analyticsVideos: VideoAnalytics[];
  analyticsPeriod: AnalyticsPeriod;

  // Loading state
  loading: boolean;
  error: string | null;
}

export interface AnalyticsActions {
  loadAnalytics(): Promise<void>;
  loadAnalyticsOverview(): Promise<void>;
  loadVideoAnalytics(): Promise<void>;
  setPeriod(period: AnalyticsPeriod): Promise<void>;

  // Formatters
  formatPercent: typeof formatPercent;
  formatWatchTime: typeof formatWatchTime;
  formatHours: typeof formatHours;
}

export type AnalyticsStore = AnalyticsState & AnalyticsActions;

export function createAnalyticsStore(): AnalyticsStore {
  return {
    // Initial state - provide defaults to prevent null access errors in templates
    analyticsOverview: {
      total_views: 0,
      total_watch_time: 0,
      unique_viewers: 0,
      avg_watch_duration: 0,
      completion_rate: 0,
      views_by_day: [],
    },
    analyticsVideos: [],
    analyticsPeriod: 'all',
    loading: false,
    error: null,

    // Formatters
    formatPercent,
    formatWatchTime,
    formatHours,

    /**
     * Load all analytics data
     */
    async loadAnalytics(): Promise<void> {
      this.loading = true;
      this.error = null;

      try {
        await Promise.all([
          this.loadAnalyticsOverview(),
          this.loadVideoAnalytics(),
        ]);
      } catch (e) {
        this.error = e instanceof Error ? e.message : 'Failed to load analytics';
      } finally {
        this.loading = false;
      }
    },

    /**
     * Load analytics overview
     */
    async loadAnalyticsOverview(): Promise<void> {
      try {
        this.analyticsOverview = await analyticsApi.getOverview(this.analyticsPeriod);
      } catch (e) {
        console.error('Failed to load analytics overview:', e);
        // Keep existing values on error (don't reset to null)
      }
    },

    /**
     * Load per-video analytics
     */
    async loadVideoAnalytics(): Promise<void> {
      try {
        this.analyticsVideos = await analyticsApi.getVideoAnalytics({
          period: this.analyticsPeriod,
          limit: 20,
          sortBy: 'views',
        });
      } catch (e) {
        console.error('Failed to load video analytics:', e);
        this.analyticsVideos = [];
      }
    },

    /**
     * Set the analytics period and reload data
     */
    async setPeriod(period: AnalyticsPeriod): Promise<void> {
      this.analyticsPeriod = period;
      await this.loadAnalytics();
    },
  };
}
