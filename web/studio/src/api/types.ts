/**
 * Studio API Types
 * Types for the broadcaster dashboard
 */

// =============================================================================
// Stream Types
// =============================================================================

export type LiveStreamStatus = 'idle' | 'live' | 'ending' | 'ended';
export type ConnectionHealth = 'good' | 'degraded' | 'poor' | 'unknown';

export interface StudioStream {
  id: number;
  title: string;
  slug: string;
  description: string;
  status: LiveStreamStatus;
  qualities: string[];
  category_id?: number;
  current_bitrate?: number;
  connection_health: ConnectionHealth;
  viewer_count_current: number;
  viewer_count_peak: number;
  viewer_count_total: number;
  created_at: string;
  started_at?: string;
  ended_at?: string;
  last_segment_at?: string;
  last_metric_at?: string;
}

export interface StudioStreamListResponse {
  streams: StudioStream[];
  total: number;
}

// =============================================================================
// Metrics Types
// =============================================================================

export interface MetricDataPoint {
  timestamp: string;
  bitrate_video?: number;
  bitrate_audio?: number;
  bitrate_total?: number;
  segment_push_latency_ms?: number;
  segments_received: number;
  segments_dropped: number;
  interval_seconds: number;
}

export interface StreamMetricsResponse {
  stream_id: number;
  current_bitrate?: number;
  connection_health: ConnectionHealth;
  last_metric_at?: string;
  metrics: MetricDataPoint[];
}

// =============================================================================
// Viewer Types
// =============================================================================

export interface ViewerStats {
  current: number;
  peak: number;
  total: number;
  quality_distribution: Record<string, number>;
}

export interface ActiveViewer {
  session_id_prefix: string;
  user_id?: string;
  joined_at?: string;
  quality?: string;
}

export interface ActiveViewersResponse {
  viewers: ActiveViewer[];
  total: number;
}

// =============================================================================
// Stream Key Types
// =============================================================================

export interface StreamKeyRequest {
  current_password: string;
}

export interface StreamKeyResponse {
  stream_key: string;
}

// =============================================================================
// Update Types
// =============================================================================

export interface StreamUpdateRequest {
  title?: string;
  description?: string;
  category_id?: number;
}

// =============================================================================
// SSE Event Types
// =============================================================================

export interface SSEInitEvent {
  type: 'init';
  stream_id: number;
  current_bitrate?: number;
  connection_health: ConnectionHealth;
  viewer_count_current: number;
  viewer_count_peak: number;
  status: LiveStreamStatus;
  timestamp: string;
}

export interface SSEMetricsEvent {
  type: 'metrics';
  stream_id: number;
  bitrate_total?: number;
  connection_health: ConnectionHealth;
  segment_latency_ms?: number;
  segments_received: number;
  segments_dropped: number;
  timestamp: string;
}

export interface SSEViewersEvent {
  type: 'viewers';
  stream_id: number;
  current: number;
  peak: number;
  total: number;
  timestamp: string;
}

export interface SSEStateEvent {
  type: 'state';
  stream_id: number;
  status: LiveStreamStatus;
  slug: string;
  title: string;
  timestamp: string;
}

export interface SSEHeartbeatEvent {
  type: 'heartbeat';
  timestamp: string;
}

export interface SSEErrorEvent {
  type: 'error';
  error: string;
}

export interface SSECloseEvent {
  type: 'close';
  reason: string;
  timestamp: string;
}

export type SSEEvent =
  | SSEInitEvent
  | SSEMetricsEvent
  | SSEViewersEvent
  | SSEStateEvent
  | SSEHeartbeatEvent
  | SSEErrorEvent
  | SSECloseEvent;

// =============================================================================
// Auth Types
// =============================================================================

export interface CurrentUser {
  id: string;
  username: string;
  email: string;
  display_name?: string;
  avatar_url?: string;
  role: 'admin' | 'editor' | 'viewer';
  permissions: string[];
}

export interface AuthCheckResponse {
  auth_required: boolean;
  authenticated: boolean;
  user?: CurrentUser;
}

// =============================================================================
// Error Types
// =============================================================================

export class ApiClientError extends Error {
  constructor(
    message: string,
    public status: number,
    public detail?: string
  ) {
    super(message);
    this.name = 'ApiClientError';
  }
}

export class AuthenticationError extends ApiClientError {
  constructor(message = 'Authentication required') {
    super(message, 401);
    this.name = 'AuthenticationError';
  }
}
