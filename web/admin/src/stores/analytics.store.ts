/**
 * Analytics Store
 * Manages analytics data and views
 */

import { analyticsApi } from '@/api/endpoints/analytics';
import type { AnalyticsPeriod } from '@/api/endpoints/analytics';
import type { AnalyticsOverview, VideoAnalytics } from '@/api/types';
import { formatPercent, formatWatchTime, formatHours } from '@/utils/formatters';
import type { AlpineContext } from './types';

export interface AnalyticsState {
  // Overview data
  analyticsOverview: AnalyticsOverview | null;
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

export function createAnalyticsStore(_context?: AlpineContext): AnalyticsStore {
  return {
    // Initial state
    analyticsOverview: null,
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
        this.analyticsOverview = null;
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
