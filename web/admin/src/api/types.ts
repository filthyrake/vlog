/**
 * API Types for VLog Admin
 * These types represent the data structures returned by the VLog API
 */

// =============================================================================
// Video Types
// =============================================================================

export type VideoStatus = 'pending' | 'processing' | 'ready' | 'failed';

export interface QualityInfo {
  quality: string;
  width: number;
  height: number;
  bitrate?: number;
  size?: number;
  status?: 'ready' | 'pending' | 'processing' | 'failed';
}

export interface Video {
  id: number;
  title: string;
  description?: string;
  slug: string;
  status: VideoStatus;
  category_id?: number;
  category_name?: string;
  published_at?: string;
  created_at: string;
  updated_at?: string;
  deleted_at?: string;
  duration?: number;
  qualities?: QualityInfo[];
  thumbnail_url?: string;
  has_custom_thumbnail?: boolean;
  view_count?: number;
  streaming_format?: 'hls' | 'cmaf';
  primary_codec?: 'h264' | 'hevc' | 'av1';
  current_step?: string;
  current_progress?: number;
}

export interface VideoProgress {
  id: number;
  status: VideoStatus;
  current_step?: string;
  current_progress?: number;
  qualities?: QualityInfo[];
}

export interface ThumbnailFrame {
  timestamp: number;
  url: string;
}

export interface VideoCustomFields {
  [fieldId: string]: string | number | boolean | string[] | null;
}

// =============================================================================
// Category Types
// =============================================================================

export interface Category {
  id: number;
  name: string;
  description?: string;
  video_count?: number;
}

// =============================================================================
// Worker Types
// =============================================================================

export type WorkerStatus = 'active' | 'idle' | 'offline' | 'disabled';

export interface Worker {
  worker_id: string;
  worker_name?: string;
  status: WorkerStatus;
  hwaccel_type?: string;
  gpu_name?: string;
  code_version?: string;
  deployment_type?: string;
  seconds_since_heartbeat?: number;
  jobs_completed: number;
  jobs_failed: number;
  current_video_title?: string;
  current_step?: string;
  current_progress?: number;
  last_seen?: string;
}

export interface ActiveJob {
  job_id: number;
  video_id: number;
  video_slug: string;
  video_title: string;
  thumbnail_url?: string;
  worker_id: string | null;
  worker_name?: string;
  worker_hwaccel_type?: string;
  status: string;
  current_step?: string;
  progress_percent: number;
  qualities?: Array<{ name: string; status: string; progress: number }>;
  started_at?: string;
  claimed_at?: string;
  attempt: number;
  max_attempts: number;
}

export interface ActiveJobsResponse {
  jobs: ActiveJob[];
  total_count: number;
  processing_count: number;
  pending_count: number;
}

export interface WorkerStats {
  active_count: number;
  idle_count: number;
  offline_count: number;
  disabled_count: number;
  total_count: number;
}

export interface WorkerLogs {
  logs: string;
  worker_name: string;
}

export interface WorkerMetrics {
  cpu_percent?: number;
  memory_percent?: number;
  memory_used?: number;
  memory_total?: number;
  gpu_utilization?: number;
  gpu_memory_used?: number;
  gpu_memory_total?: number;
  disk_read_rate?: number;
  disk_write_rate?: number;
  network_recv_rate?: number;
  network_send_rate?: number;
}

export interface DeploymentEvent {
  id: number;
  event_type: 'deployed' | 'restarted' | 'updated' | 'deleted';
  worker_id: string;
  worker_name?: string;
  old_version?: string;
  new_version?: string;
  timestamp: string;
  details?: string;
}

// =============================================================================
// Analytics Types
// =============================================================================

export interface AnalyticsOverview {
  total_views: number;
  total_watch_time: number;
  unique_viewers: number;
  avg_watch_duration: number;
  completion_rate: number;
  views_by_day?: Array<{ date: string; views: number }>;
}

export interface VideoAnalytics {
  video_id: number;
  title: string;
  views: number;
  watch_time: number;
  avg_watch_duration: number;
  completion_rate: number;
}

// =============================================================================
// Settings Types
// =============================================================================

export interface SettingDefinition {
  key: string;
  value: string | number | boolean | null;
  type: 'string' | 'int' | 'float' | 'bool' | 'json';
  category: string;
  description?: string;
  default_value?: string | number | boolean | null;
}

export interface SettingsCategory {
  name: string;
  description?: string;
  settings: SettingDefinition[];
}

export interface WatermarkSettings {
  enabled: boolean;
  type: 'image' | 'text';
  position: 'top-left' | 'top-right' | 'bottom-left' | 'bottom-right';
  opacity: number;
  image_url?: string;
  text?: string;
  font_size?: number;
  font_color?: string;
}

// =============================================================================
// Custom Fields Types
// =============================================================================

export type CustomFieldType = 'text' | 'number' | 'boolean' | 'date' | 'select' | 'url';

export interface CustomFieldOption {
  value: string;
  label: string;
}

export interface CustomFieldConstraint {
  min?: number;
  max?: number;
  min_length?: number;
  max_length?: number;
  pattern?: string;
  options?: CustomFieldOption[];
}

export interface CustomField {
  id: number;
  name: string;
  field_key: string;
  field_type: CustomFieldType;
  description?: string;
  required: boolean;
  constraints?: CustomFieldConstraint;
  applies_to_categories?: number[];
  display_order: number;
  created_at: string;
  updated_at?: string;
}

// =============================================================================
// Auth Types
// =============================================================================

export interface AuthCheckResponse {
  auth_required: boolean;
  authenticated: boolean;
}

export interface AuthLoginResponse {
  success: boolean;
  message?: string;
}

export interface CsrfTokenResponse {
  csrf_token: string;
}

// =============================================================================
// Bulk Operation Types
// =============================================================================

export interface BulkOperationResult {
  success: boolean;
  processed: number;
  failed: number;
  errors?: Array<{ id: number; error: string }>;
}

export interface BulkDeleteRequest {
  video_ids: number[];
  permanent?: boolean;
}

export interface BulkUpdateRequest {
  video_ids: number[];
  category_id?: number;
  published_at?: string | null;
  unpublish?: boolean;
}

export interface BulkRetranscodeRequest {
  video_ids: number[];
  quality: string;
}

export interface BulkRestoreRequest {
  video_ids: number[];
}

export interface BulkCustomFieldsRequest {
  video_ids: number[];
  values: VideoCustomFields;
}

// =============================================================================
// API Error Types
// =============================================================================

export interface ApiError {
  detail: string;
  status?: number;
}

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
// SSE Event Types
// =============================================================================

export interface ProgressSSEEvent {
  video_id: number;
  status: VideoStatus;
  current_step?: string;
  current_progress?: number;
  qualities?: QualityInfo[];
}

export interface WorkerSSEEvent {
  type: 'status' | 'job_started' | 'job_completed' | 'job_failed';
  worker_id: string;
  worker_name?: string;
  status?: WorkerStatus;
  video_id?: number;
  video_title?: string;
  step?: string;
  progress?: number;
}

// =============================================================================
// Export/Import Types
// =============================================================================

export interface ExportResponse {
  format: 'json' | 'csv';
  data: string;
  filename: string;
}

export interface SettingsExportResponse {
  version: string;
  exported_at: string;
  settings: Record<string, SettingDefinition[]>;
}
