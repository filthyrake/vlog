/**
 * Studio API Endpoints
 */

import { apiClient } from '../client';
import type {
  StudioStream,
  StudioStreamListResponse,
  StreamMetricsResponse,
  ViewerStats,
  StreamKeyResponse,
  StreamKeyRequest,
  StreamUpdateRequest,
} from '../types';

/**
 * List streams accessible to the current user
 */
export async function listStreams(status?: string): Promise<StudioStreamListResponse> {
  const params = new URLSearchParams();
  if (status) params.set('status', status);
  const query = params.toString();
  return apiClient.fetch(`/api/v1/studio/streams${query ? `?${query}` : ''}`);
}

/**
 * Get a single stream by slug
 */
export async function getStream(slug: string): Promise<StudioStream> {
  return apiClient.fetch(`/api/v1/studio/streams/${slug}`);
}

/**
 * Update stream metadata
 */
export async function updateStream(
  slug: string,
  data: StreamUpdateRequest
): Promise<StudioStream> {
  return apiClient.fetch(`/api/v1/studio/streams/${slug}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

/**
 * End a live stream
 */
export async function endStream(slug: string): Promise<StudioStream> {
  return apiClient.fetch(`/api/v1/studio/streams/${slug}/end`, {
    method: 'POST',
  });
}

/**
 * Regenerate stream key (requires password)
 * Note: Stream keys cannot be retrieved after creation due to secure hashing.
 * This is the only way to get a stream key - by regenerating it.
 */
export async function regenerateStreamKey(
  slug: string,
  password: string
): Promise<StreamKeyResponse> {
  const request: StreamKeyRequest = { current_password: password };
  return apiClient.fetch(`/api/v1/studio/streams/${slug}/key/regenerate`, {
    method: 'POST',
    body: JSON.stringify(request),
  });
}

/**
 * Get stream metrics
 */
export async function getStreamMetrics(
  slug: string,
  minutes: number = 5
): Promise<StreamMetricsResponse> {
  return apiClient.fetch(`/api/v1/studio/streams/${slug}/metrics?minutes=${minutes}`);
}

/**
 * Get viewer stats
 */
export async function getViewerStats(slug: string): Promise<ViewerStats> {
  return apiClient.fetch(`/api/v1/studio/streams/${slug}/viewers`);
}
