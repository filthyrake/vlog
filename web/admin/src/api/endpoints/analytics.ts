/**
 * Analytics API Endpoints
 */

import { apiClient } from '../client';
import type { AnalyticsOverview, VideoAnalytics } from '../types';

export type AnalyticsPeriod = 'all' | '7d' | '30d' | '90d' | '365d';

export interface VideoAnalyticsOptions {
  period?: AnalyticsPeriod;
  limit?: number;
  sortBy?: 'views' | 'watch_time' | 'completion_rate';
}

export const analyticsApi = {
  /**
   * Get analytics overview
   */
  async getOverview(period: AnalyticsPeriod = 'all'): Promise<AnalyticsOverview> {
    return apiClient.fetch<AnalyticsOverview>(`/api/analytics/overview?period=${period}`);
  },

  /**
   * Get per-video analytics
   */
  async getVideoAnalytics(options: VideoAnalyticsOptions = {}): Promise<VideoAnalytics[]> {
    const {
      period = 'all',
      limit = 20,
      sortBy = 'views',
    } = options;

    const response = await apiClient.fetch<{ videos: VideoAnalytics[]; total_count: number }>(
      `/api/analytics/videos?period=${period}&limit=${limit}&sort_by=${sortBy}`
    );
    return response.videos || [];
  },
};
