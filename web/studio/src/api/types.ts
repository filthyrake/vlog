/**
 * API Types for VLog Studio
 */

// =============================================================================
// Stream Types
// =============================================================================

export type StreamStatus = 'idle' | 'live' | 'ending' | 'ended';
export type QualityPreset = 'auto' | 'low' | 'medium' | 'high' | 'source';

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
  // Additional controls (Phase 2E)
  stream_delay_seconds: number;
  quality_preset: QualityPreset;
  scheduled_at: string | null;
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
  // Additional controls (Phase 2E)
  stream_delay_seconds?: number;
  quality_preset?: QualityPreset;
  scheduled_at?: string;
}

export interface StreamUpdateRequest {
  title?: string;
  description?: string;
  category_id?: number | null;
  dvr_enabled?: boolean;
  dvr_window_seconds?: number;
  auto_record_vod?: boolean;
  // Additional controls (Phase 2E)
  stream_delay_seconds?: number;
  quality_preset?: QualityPreset;
  scheduled_at?: string | null;
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

// =============================================================================
// VOD Types (Issue #530)
// =============================================================================

export type VODStatus = 'pending' | 'processing' | 'ready' | 'failed';

export interface VOD {
  id: number;
  title: string;
  slug: string;
  description: string;
  status: VODStatus;
  duration: number;
  source_width: number;
  source_height: number;
  category_id: number | null;
  thumbnail_url: string | null;
  created_at: string;
  published_at: string | null;
  // Link to source stream
  stream_id: number | null;
  stream_slug: string | null;
  stream_title: string | null;
}

export interface VODListResponse {
  vods: VOD[];
  total: number;
  page: number;
  page_size: number;
  has_more: boolean;
}

export interface VODUpdateRequest {
  title?: string;
  description?: string;
  category_id?: number | null;
}

export interface VODAnalytics {
  vod_id: number;
  total_views: number;
  unique_viewers: number;
  total_watch_time_seconds: number;
  average_watch_time_seconds: number;
  completion_rate: number;
  peak_concurrent_viewers: number | null;
  view_history: Array<{
    date: string;
    views: number;
  }>;
}

export interface VODDownloadResponse {
  download_url: string;
  filename: string;
  expires_at: string;
}

// =============================================================================
// Chat Types (Issue #530)
// =============================================================================

export interface ChatMessage {
  id: number;
  stream_id: number;
  user_id: string | null;
  username: string | null;
  content: string;
  stream_offset_ms: number | null;
  created_at: string;
  deleted_at: string | null;
  deleted_by_username: string | null;
}

export interface ChatMessageListResponse {
  messages: ChatMessage[];
  total: number;
  has_more: boolean;
  before_id: number | null;
}

export interface ChatMessageSendRequest {
  content: string;
}

export interface ChatSettings {
  stream_id: number;
  chat_enabled: boolean;
  chat_slow_mode_seconds: number;
  chat_subscriber_only: boolean;
  chat_follower_only: boolean;
  chat_follower_min_minutes: number;
  chat_emote_only: boolean;
  chat_links_allowed: boolean;
}

export interface ChatSettingsUpdateRequest {
  chat_enabled?: boolean;
  chat_slow_mode_seconds?: number;
  chat_subscriber_only?: boolean;
  chat_follower_only?: boolean;
  chat_follower_min_minutes?: number;
  chat_emote_only?: boolean;
  chat_links_allowed?: boolean;
}

export interface StreamModerator {
  id: number;
  stream_id: number;
  user_id: string;
  username: string;
  permissions: string[];
  granted_by_id: string | null;
  granted_by_username: string | null;
  granted_at: string;
}

export interface StreamModeratorListResponse {
  moderators: StreamModerator[];
  total: number;
}

export interface StreamModeratorAddRequest {
  user_id: string;
  permissions?: string[];
}

export interface StreamModeratorUpdateRequest {
  permissions: string[];
}

// =============================================================================
// WebSocket Protocol Types (Issue #530)
// =============================================================================

export type WSMessageType =
  | 'message'
  | 'delete'
  | 'ping'
  | 'pong'
  | 'chat_message'
  | 'message_deleted'
  | 'user_timeout'
  | 'user_ban'
  | 'user_unban'
  | 'settings_updated'
  | 'error'
  | 'connected'
  | 'shutdown';

export interface WSClientMessage {
  type: 'message' | 'delete' | 'pong';
  content?: string;
  message_id?: number;
}

export interface WSServerMessage {
  type: WSMessageType;
  // Chat message fields
  id?: number;
  user_id?: string;
  username?: string;
  content?: string;
  timestamp?: string;
  // Moderation fields
  message_id?: number;
  deleted_by?: string;
  target_user_id?: string;
  target_username?: string;
  duration_seconds?: number;
  reason?: string;
  // Settings fields
  settings?: ChatSettings;
  // Error fields
  code?: string;
  error?: string;
  message?: string;
  retry_after?: number;
  // Connected fields
  is_moderator?: boolean;
  is_owner?: boolean;
}

export interface WSConnectedMessage extends WSServerMessage {
  type: 'connected';
  user_id: string;
  username: string;
  is_moderator: boolean;
  is_owner: boolean;
  settings: ChatSettings;
}

// =============================================================================
// Stream Moderation Types (Issue #530 - Phase 2C)
// =============================================================================

export type BanType = 'timeout' | 'permanent';
export type FilterAction = 'delete' | 'timeout' | 'warn';

export interface StreamBan {
  id: number;
  stream_id: number;
  user_id: string;
  username: string | null;
  ban_type: BanType;
  duration_seconds: number | null;
  reason: string | null;
  banned_by_id: string | null;
  banned_by_username: string | null;
  created_at: string;
  expires_at: string | null;
  unbanned_at: string | null;
  is_active: boolean;
}

export interface StreamBanListResponse {
  bans: StreamBan[];
  total: number;
  has_more: boolean;
}

export interface StreamBanCreateRequest {
  user_id: string;
  ban_type: BanType;
  duration_seconds?: number;
  reason?: string;
}

export interface WordFilter {
  id: number;
  stream_id: number;
  pattern: string;
  is_regex: boolean;
  action: FilterAction;
  timeout_seconds: number | null;
  created_at: string;
  created_by_id: string | null;
  created_by_username: string | null;
}

export interface WordFilterListResponse {
  filters: WordFilter[];
  total: number;
}

export interface WordFilterCreateRequest {
  pattern: string;
  is_regex?: boolean;
  action?: FilterAction;
  timeout_seconds?: number;
}

export interface ModerationLog {
  id: number;
  stream_id: number;
  moderator_id: string | null;
  moderator_username: string | null;
  action: string;
  target_user_id: string | null;
  target_username: string | null;
  target_message_id: number | null;
  details: Record<string, unknown> | null;
  created_at: string;
}

export interface ModerationLogListResponse {
  logs: ModerationLog[];
  total: number;
  has_more: boolean;
}

// =============================================================================
// Stream Analytics Types (Issue #530 - Phase 2D)
// =============================================================================

export interface ViewerCount {
  recorded_at: string;
  viewer_count: number;
}

export interface StreamAnalyticsSummary {
  stream_id: number;
  peak_viewers: number;
  average_viewers: number;
  total_unique_viewers: number;
  total_chat_messages: number;
  total_watch_minutes: number;
  average_watch_time_seconds: number;
  stream_duration_seconds: number;
  computed_at: string | null;
}

export interface ViewerHistoryResponse {
  stream_id: number;
  data_points: ViewerCount[];
  total_points: number;
}

export interface StreamAnalyticsResponse {
  summary: StreamAnalyticsSummary;
  viewer_history: ViewerCount[];
}
