/**
 * Analytics API Endpoints (Issue #530 - Phase 2D)
 */

import { apiClient } from '../client';
import type {
  StreamAnalyticsResponse,
  StreamAnalyticsSummary,
  ViewerHistoryResponse,
} from '../types';

export const analyticsApi = {
  /**
   * Get complete analytics for a stream
   */
  async getAnalytics(streamSlug: string): Promise<StreamAnalyticsResponse> {
    return apiClient.fetch<StreamAnalyticsResponse>(
      `/api/v1/studio/streams/${streamSlug}/analytics`
    );
  },

  /**
   * Get analytics summary only
   */
  async getSummary(streamSlug: string): Promise<StreamAnalyticsSummary> {
    return apiClient.fetch<StreamAnalyticsSummary>(
      `/api/v1/studio/streams/${streamSlug}/analytics/summary`
    );
  },

  /**
   * Get viewer history for a stream
   */
  async getViewerHistory(
    streamSlug: string,
    limit = 1000
  ): Promise<ViewerHistoryResponse> {
    const params = new URLSearchParams({
      limit: String(limit),
    });
    return apiClient.fetch<ViewerHistoryResponse>(
      `/api/v1/studio/streams/${streamSlug}/analytics/viewers?${params}`
    );
  },

  /**
   * Trigger recomputation of analytics
   */
  async recompute(streamSlug: string): Promise<StreamAnalyticsSummary> {
    return apiClient.fetch<StreamAnalyticsSummary>(
      `/api/v1/studio/streams/${streamSlug}/analytics/recompute`,
      { method: 'POST' }
    );
  },
};
