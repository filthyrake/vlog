/**
 * API Types for VLog Studio
 */

// =============================================================================
// Stream Types
// =============================================================================

export type StreamStatus = 'idle' | 'live' | 'ending' | 'ended';

export interface Stream {
  id: number;
  title: string;
  slug: string;
  description: string;
  status: StreamStatus;
  qualities: string[] | null;
  category_id: number | null;
  dvr_enabled: boolean;
  dvr_window_seconds: number;
  auto_record_vod: boolean;
  segment_count: number;
  vod_video_id: number | null;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
  last_segment_at: string | null;
}

export interface StreamListResponse {
  streams: Stream[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
}

export interface StreamCreateRequest {
  title: string;
  description?: string;
  category_id?: number;
  dvr_enabled?: boolean;
  dvr_window_seconds?: number;
  auto_record_vod?: boolean;
}

export interface StreamUpdateRequest {
  title?: string;
  description?: string;
  category_id?: number | null;
  dvr_enabled?: boolean;
  dvr_window_seconds?: number;
  auto_record_vod?: boolean;
}

export interface StreamCreatedResponse extends Stream {
  stream_key: string;
  rtmp_url: string;
  warning: string;
}

export interface StreamKeyResponse {
  stream_key: string;
  rtmp_url: string;
  warning: string;
}

// =============================================================================
// Metrics Types
// =============================================================================

export interface StreamMetrics {
  type: string;
  stream_id: number;
  stream_slug: string;
  status: StreamStatus;
  segment_count: number;
  qualities: string[];
  bitrate_kbps: number | null;
  last_segment_at: string | null;
  timestamp: string;
}

// =============================================================================
// Auth Types
// =============================================================================

export interface AuthCheckResponse {
  auth_required: boolean;
  authenticated: boolean;
  user?: CurrentUser;
  auth_mode?: 'legacy' | 'user';
  oidc_enabled?: boolean;
  oidc_provider_name?: string;
}

export interface AuthLoginResponse {
  success: boolean;
  message?: string;
  user?: CurrentUser;
}

export type UserRole = 'admin' | 'editor' | 'viewer';

export interface CurrentUser {
  id: string;
  username: string;
  email: string;
  display_name?: string;
  avatar_url?: string;
  role: UserRole;
  permissions: string[];
}

// =============================================================================
// API Error Types
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

export class CsrfError extends ApiClientError {
  constructor(message = 'CSRF validation failed') {
    super(message, 403);
    this.name = 'CsrfError';
  }
}
