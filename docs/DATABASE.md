# VLog Database Schema

## Overview

VLog uses PostgreSQL as its database backend. PostgreSQL provides concurrent read/write support, making it suitable for multi-instance deployments.

**Connection URL:** Configurable via `VLOG_DATABASE_URL`
**Default:** `postgresql://vlog:vlog_password@localhost/vlog`

**ORM:** SQLAlchemy with async support via `databases` and `asyncpg`

---

## Tables

### categories

Organizes videos into categories.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Auto-increment ID |
| name | VARCHAR(100) | NOT NULL | Display name |
| slug | VARCHAR(100) | UNIQUE, NOT NULL | URL-safe identifier |
| description | TEXT | DEFAULT '' | Category description |
| created_at | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | Creation timestamp |

### videos

Core video metadata and processing status.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Auto-increment ID |
| title | VARCHAR(255) | NOT NULL | Video title |
| slug | VARCHAR(255) | UNIQUE, NOT NULL | URL-safe identifier |
| description | TEXT | DEFAULT '' | Video description |
| category_id | INTEGER | FK(categories.id) | Optional category |
| duration | FLOAT | DEFAULT 0 | Duration in seconds |
| source_width | INTEGER | DEFAULT 0 | Original width |
| source_height | INTEGER | DEFAULT 0 | Original height |
| status | VARCHAR(20) | DEFAULT 'pending' | Processing status |
| error_message | TEXT | NULLABLE | Error details if failed |
| thumbnail_source | VARCHAR(20) | DEFAULT 'auto' | Thumbnail source: 'auto', 'selected', 'custom' |
| thumbnail_timestamp | FLOAT | NULLABLE | Timestamp for 'selected' thumbnails |
| is_featured | BOOLEAN | DEFAULT FALSE | Featured on homepage |
| has_chapters | BOOLEAN | DEFAULT FALSE | Has chapter markers |
| streaming_format | VARCHAR(10) | NOT NULL, DEFAULT 'hls_ts' | Streaming format: hls_ts, cmaf |
| primary_codec | VARCHAR(10) | NOT NULL, DEFAULT 'h264' | Primary codec: h264, hevc, av1 |
| featured_at | TIMESTAMP WITH TIME ZONE | NULLABLE | When marked featured (for ordering) |
| sprite_sheet_status | VARCHAR(20) | NULLABLE | pending, generating, ready, failed |
| sprite_sheet_error | TEXT | NULLABLE | Sprite generation error |
| sprite_sheet_count | INTEGER | DEFAULT 0 | Number of sprite sheets |
| sprite_sheet_interval | INTEGER | NULLABLE | Seconds between frames |
| sprite_sheet_tile_size | INTEGER | NULLABLE | Grid size (e.g., 10 for 10x10) |
| sprite_sheet_frame_width | INTEGER | NULLABLE | Frame width in pixels |
| sprite_sheet_frame_height | INTEGER | NULLABLE | Frame height in pixels |
| created_at | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | Upload timestamp |
| published_at | TIMESTAMP WITH TIME ZONE | NULLABLE | Publication timestamp |
| deleted_at | TIMESTAMP WITH TIME ZONE | NULLABLE | Soft-delete timestamp (NULL = not deleted) |

**Status Values:**
- `pending` - Uploaded, waiting for processing
- `processing` - Currently being transcoded
- `ready` - Ready for playback
- `failed` - Processing failed

**Soft-Delete:**
- When a video is deleted, `deleted_at` is set to the current timestamp
- Videos with non-NULL `deleted_at` are excluded from normal queries
- Files are moved to `archive/` directory
- Archived videos can be restored via the API
- Permanent deletion occurs after `ARCHIVE_RETENTION_DAYS` (default 30)

**Indexes:**
- `ix_videos_status` - Fast status filtering
- `ix_videos_category_id` - Category filtering
- `ix_videos_created_at` - Recent videos
- `ix_videos_published_at` - Publication ordering
- `ix_videos_deleted_at` - Soft-delete filtering

### video_qualities

Available HLS quality variants for each video.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Auto-increment ID |
| video_id | INTEGER | FK(videos.id) CASCADE | Parent video |
| quality | VARCHAR(10) | | Quality name (e.g., "1080p") |
| width | INTEGER | | Actual pixel width |
| height | INTEGER | | Actual pixel height |
| bitrate | INTEGER | | Bitrate in kbps |

### viewers

Cookie-based unique viewer tracking (privacy-friendly).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Auto-increment ID |
| session_id | VARCHAR(64) | UNIQUE, NOT NULL | Browser session ID |
| first_seen | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | First visit |
| last_seen | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | Last visit |

### playback_sessions

Individual video playback tracking for analytics.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Auto-increment ID |
| video_id | INTEGER | FK(videos.id) CASCADE | Video watched |
| viewer_id | INTEGER | FK(viewers.id) SET NULL | Optional viewer link |
| session_token | VARCHAR(64) | UNIQUE, NOT NULL | Unique session token |
| started_at | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | Playback start |
| ended_at | TIMESTAMP WITH TIME ZONE | NULLABLE | Playback end |
| duration_watched | FLOAT | DEFAULT 0 | Seconds watched |
| max_position | FLOAT | DEFAULT 0 | Furthest position |
| quality_used | VARCHAR(10) | NULLABLE | Primary quality used |
| completed | BOOLEAN | DEFAULT FALSE | Watched >= 90% |

**Indexes:**
- `ix_playback_sessions_video_id` - Per-video analytics
- `ix_playback_sessions_viewer_id` - Viewer session history
- `ix_playback_sessions_started_at` - Time-based queries

### transcoding_jobs

Tracks transcoding jobs with checkpoint-based recovery and distributed claiming.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Auto-increment ID |
| video_id | INTEGER | FK(videos.id) CASCADE, UNIQUE | One job per video |
| worker_id | VARCHAR(36) | NULLABLE | Currently assigned worker UUID |
| current_step | VARCHAR(50) | NULLABLE | Current processing step |
| progress_percent | INTEGER | DEFAULT 0 | Overall progress (0-100) |
| started_at | TIMESTAMP | NULLABLE | Job start time |
| last_checkpoint | TIMESTAMP | NULLABLE | Last checkpoint update |
| completed_at | TIMESTAMP | NULLABLE | Job completion time |
| claimed_at | TIMESTAMP | NULLABLE | When job was claimed |
| claim_expires_at | TIMESTAMP | NULLABLE | Job claim expiration |
| attempt_number | INTEGER | DEFAULT 1 | Current attempt (1-N) |
| max_attempts | INTEGER | DEFAULT 3 | Maximum retries |
| last_error | TEXT | NULLABLE | Last error message |
| processed_by_worker_id | VARCHAR(36) | NULLABLE | Worker that completed job (audit) |
| processed_by_worker_name | VARCHAR(100) | NULLABLE | Worker name (audit) |
| retranscode_metadata | TEXT | NULLABLE | JSON cleanup info for deferred retranscode (Issue #408) |

**Indexes:**
- `ix_transcoding_jobs_video_id` - Video lookups
- `ix_transcoding_jobs_claim_expires` - Stale job detection

**Processing Steps:**
- `probe` - Extracting video metadata
- `thumbnail` - Generating thumbnail
- `transcode` - Transcoding to HLS
- `master_playlist` - Generating master.m3u8
- `finalize` - Final cleanup

### quality_progress

Per-quality transcoding progress for detailed tracking.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Auto-increment ID |
| job_id | INTEGER | FK(transcoding_jobs.id) CASCADE | Parent job |
| quality | VARCHAR(10) | NOT NULL | Quality name (e.g., "1080p") |
| status | VARCHAR(20) | NOT NULL, DEFAULT 'pending' | Quality status |
| segments_total | INTEGER | NULLABLE | Total segment count |
| segments_completed | INTEGER | DEFAULT 0 | Completed segments |
| progress_percent | INTEGER | DEFAULT 0 | Progress (0-100) |
| started_at | TIMESTAMP WITH TIME ZONE | NULLABLE | Quality start time |
| completed_at | TIMESTAMP WITH TIME ZONE | NULLABLE | Quality completion time |
| error_message | TEXT | NULLABLE | Error if failed |

**Indexes:**
- `ix_quality_progress_job_id` - Job progress lookup

**Unique Constraint:** `uq_job_quality` (job_id, quality)

**Status Values:**
- `pending` - Not started
- `in_progress` - Currently transcoding
- `completed` - Successfully completed
- `failed` - Transcoding failed
- `skipped` - Skipped (e.g., source resolution too low)

### transcriptions

Whisper transcription tracking and output.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Auto-increment ID |
| video_id | INTEGER | FK(videos.id) CASCADE, UNIQUE | One per video |
| status | VARCHAR(20) | NOT NULL, DEFAULT 'pending' | Status |
| language | VARCHAR(10) | DEFAULT 'en' | Detected/specified language |
| started_at | TIMESTAMP WITH TIME ZONE | NULLABLE | Transcription start |
| completed_at | TIMESTAMP WITH TIME ZONE | NULLABLE | Transcription end |
| duration_seconds | FLOAT | NULLABLE | Processing time |
| transcript_text | TEXT | NULLABLE | Full transcript |
| vtt_path | VARCHAR(255) | NULLABLE | Path to WebVTT file |
| word_count | INTEGER | NULLABLE | Total word count |
| error_message | TEXT | NULLABLE | Error if failed |

**Status Values:**
- `pending` - Queued for transcription
- `processing` - Currently transcribing
- `completed` - Successfully completed
- `failed` - Transcription failed

### workers

Registered remote transcoding workers.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Auto-increment ID |
| worker_id | VARCHAR(36) | UNIQUE, NOT NULL | UUID identifier |
| worker_name | VARCHAR(100) | NULLABLE | Display name |
| worker_type | VARCHAR(20) | DEFAULT 'remote' | Worker type (local/remote) |
| status | VARCHAR(20) | DEFAULT 'active' | Current status |
| registered_at | TIMESTAMP | NOT NULL | Registration time |
| last_heartbeat | TIMESTAMP | NULLABLE | Last heartbeat received |
| current_job_id | INTEGER | FK(transcoding_jobs.id) SET NULL | Currently assigned job |
| capabilities | TEXT | NULLABLE | JSON capabilities metadata |
| metadata | TEXT | NULLABLE | JSON metadata (K8s pod info, etc.) |

**Status Values:**
- `active` - Worker is online and available
- `offline` - No recent heartbeat
- `disabled` - Worker disabled by admin

**Indexes:**
- `ix_workers_worker_id` - Worker UUID lookup
- `ix_workers_status` - Status filtering
- `ix_workers_last_heartbeat` - Heartbeat monitoring

### worker_api_keys

API keys for worker authentication (argon2id hashed).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Auto-increment ID |
| worker_id | INTEGER | FK(workers.id) CASCADE | Parent worker |
| key_hash | VARCHAR(255) | NOT NULL | argon2id hash (or SHA-256 for legacy) |
| key_prefix | VARCHAR(8) | NOT NULL, INDEX | First 8 chars for lookup |
| hash_version | INTEGER | NOT NULL, DEFAULT 2 | Hash algorithm version |
| created_at | TIMESTAMP | NOT NULL | Key creation time |
| expires_at | TIMESTAMP | NULLABLE | Optional expiration time |
| revoked_at | TIMESTAMP | NULLABLE | Revocation time (NULL = active) |
| last_used_at | TIMESTAMP | NULLABLE | Last successful authentication |

**Hash Versions:**
- `1` - SHA-256 (legacy, for backward compatibility)
- `2` - argon2id (current, recommended)

**Indexes:**
- `ix_worker_api_keys_key_prefix` - Efficient key lookup
- `ix_worker_api_keys_worker_id` - Worker key retrieval

**Security Design:**
- Full API key is only shown once at registration
- New keys use argon2id (memory-hard, GPU-resistant)
- Legacy SHA-256 keys still work but should be regenerated
- Prefix enables efficient lookup without full scan
- Revoked keys remain in table for audit trail
- `last_used_at` tracks recent activity

### admin_sessions

Server-side session management for browser-based admin authentication.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Auto-increment ID |
| session_token | VARCHAR(128) | UNIQUE, NOT NULL | Session token (64 chars from secrets.token_urlsafe) |
| created_at | TIMESTAMP WITH TIME ZONE | NOT NULL | Session creation time |
| expires_at | TIMESTAMP WITH TIME ZONE | NOT NULL | Session expiration time |
| last_used_at | TIMESTAMP WITH TIME ZONE | NULLABLE | Last activity timestamp |
| ip_address | VARCHAR(45) | NULLABLE | Client IP address (IPv6 max length) |
| user_agent | VARCHAR(512) | NULLABLE | Browser user agent |

**Indexes:**
- `ix_admin_sessions_session_token` - Fast session lookup
- `ix_admin_sessions_expires_at` - Expired session cleanup

**Security Design:**
- Sessions use HTTP-only cookies (not accessible to JavaScript)
- Session expiry configurable via `VLOG_ADMIN_SESSION_EXPIRY_HOURS`
- Expired sessions are automatically cleaned up

### settings

Runtime configuration stored in database for dynamic updates without restart.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Auto-increment ID |
| key | VARCHAR(255) | UNIQUE, NOT NULL | Setting key (e.g., "transcoding.hls_segment_duration") |
| value | TEXT | NOT NULL | JSON-encoded value |
| category | VARCHAR(100) | NOT NULL | Category for UI grouping |
| description | TEXT | NULLABLE | Human-readable help text |
| value_type | VARCHAR(50) | NOT NULL | Type: string, integer, float, boolean, enum, json |
| constraints | TEXT | NULLABLE | JSON validation constraints (min, max, enum_values, etc.) |
| updated_at | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | Last update timestamp |
| updated_by | VARCHAR(255) | NULLABLE | User/system that made the update |

**Indexes:**
- `ix_settings_key` - Fast key lookup
- `ix_settings_category` - Category grouping queries

**Value Types:**
- `string` - Plain text
- `integer` - Whole numbers
- `float` - Decimal numbers
- `boolean` - true/false
- `enum` - One of predefined values (see constraints.enum_values)
- `json` - Complex JSON objects/arrays

**Setting Precedence:**
1. Database value (if exists and not empty)
2. Environment variable fallback
3. Config default value

### tags

Tag definitions for granular content organization.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Auto-increment ID |
| name | VARCHAR(50) | UNIQUE, NOT NULL | Display name |
| slug | VARCHAR(50) | UNIQUE, NOT NULL | URL-safe identifier |
| created_at | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | Creation timestamp |

**Indexes:**
- `ix_tags_slug` - Fast tag lookup by slug

### video_tags

Many-to-many relationship between videos and tags.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| video_id | INTEGER | FK(videos.id) CASCADE, PK | Video reference |
| tag_id | INTEGER | FK(tags.id) CASCADE, PK | Tag reference |

**Indexes:**
- `ix_video_tags_video_id` - Find tags for a video
- `ix_video_tags_tag_id` - Find videos with a tag

**Cascade Behavior:** Deleting a video or tag removes the association.

### playlists

Playlists and collections for organizing videos.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Auto-increment ID |
| title | VARCHAR(255) | NOT NULL | Playlist title |
| slug | VARCHAR(255) | UNIQUE, NOT NULL | URL-safe identifier |
| description | TEXT | NULLABLE | Playlist description |
| thumbnail_path | VARCHAR(500) | NULLABLE | Custom thumbnail path |
| visibility | VARCHAR(20) | CHECK, DEFAULT 'public' | public, private, unlisted |
| playlist_type | VARCHAR(20) | CHECK, DEFAULT 'playlist' | playlist, collection, series, course |
| is_featured | BOOLEAN | DEFAULT FALSE | Featured on homepage |
| user_id | VARCHAR(100) | NULLABLE | Future: user ownership |
| created_at | TIMESTAMP WITH TIME ZONE | NOT NULL | Creation timestamp |
| updated_at | TIMESTAMP WITH TIME ZONE | NULLABLE | Last update timestamp |
| deleted_at | TIMESTAMP WITH TIME ZONE | NULLABLE | Soft-delete timestamp |

**Indexes:**
- `ix_playlists_slug` - URL lookups
- `ix_playlists_visibility` - Visibility filtering
- `ix_playlists_is_featured` - Featured filtering
- `ix_playlists_deleted_at` - Soft-delete filtering
- `ix_playlists_playlist_type` - Type filtering

**Visibility Options:**
- `public` - Visible to everyone
- `private` - Admin-only access
- `unlisted` - Accessible by direct link

**Playlist Types:**
- `playlist` - General purpose playlist
- `collection` - Curated collection
- `series` - Sequential series
- `course` - Educational course

### playlist_items

Many-to-many relationship between playlists and videos with ordering.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Auto-increment ID |
| playlist_id | INTEGER | FK(playlists.id) CASCADE | Parent playlist |
| video_id | INTEGER | FK(videos.id) CASCADE | Video reference |
| position | INTEGER | DEFAULT 0 | Order position |
| added_at | TIMESTAMP WITH TIME ZONE | NULLABLE | When video was added |

**Indexes:**
- `ix_playlist_items_playlist_id` - Find items for playlist
- `ix_playlist_items_video_id` - Find playlists containing video
- `ix_playlist_items_position` - Ordering queries
- `ix_playlist_items_playlist_position` - Composite for ordered retrieval

**Unique Constraint:** `uq_playlist_video` (playlist_id, video_id)

**Cascade Behavior:** Deleting playlist or video removes the association.

### chapters

Video chapters for timeline navigation.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Auto-increment ID |
| video_id | INTEGER | FK(videos.id) CASCADE | Parent video |
| title | VARCHAR(255) | NOT NULL | Chapter title |
| description | TEXT | NULLABLE | Chapter description |
| start_time | FLOAT | NOT NULL, >= 0 | Start time in seconds |
| end_time | FLOAT | NULLABLE, > start_time | End time in seconds |
| position | INTEGER | DEFAULT 0 | Display order |
| created_at | TIMESTAMP WITH TIME ZONE | NOT NULL | Creation timestamp |
| updated_at | TIMESTAMP WITH TIME ZONE | NULLABLE | Last update timestamp |

**Indexes:**
- `ix_chapters_video_id` - Find chapters for video
- `ix_chapters_position` - Ordering queries
- `ix_chapters_video_position` - Composite for ordered retrieval

**Constraints:**
- `ck_chapters_start_time_positive` - start_time >= 0
- `ck_chapters_end_time_valid` - end_time IS NULL OR end_time > start_time
- `uq_chapter_video_position` - Unique (video_id, position)

**Note:** Videos have a `has_chapters` boolean column for performance optimization.

### sprite_queue

Background job queue for sprite sheet generation.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Auto-increment ID |
| video_id | INTEGER | FK(videos.id) CASCADE | Target video |
| priority | VARCHAR(10) | CHECK, DEFAULT 'normal' | high, normal, low |
| status | VARCHAR(20) | CHECK, DEFAULT 'pending' | pending, processing, completed, failed, cancelled |
| error_message | TEXT | NULLABLE | Error details if failed |
| created_at | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | Queue timestamp |
| started_at | TIMESTAMP WITH TIME ZONE | NULLABLE | Processing start |
| completed_at | TIMESTAMP WITH TIME ZONE | NULLABLE | Completion timestamp |
| processed_by_worker_id | INTEGER | NULLABLE | Worker that processed job |

**Indexes:**
- `ix_sprite_queue_status` - Status filtering
- `ix_sprite_queue_video_id` - Video lookups
- `ix_sprite_queue_priority_created` - Priority ordering
- `ix_sprite_queue_pending_priority` - Partial index for pending jobs

**Status Values:**
- `pending` - Waiting in queue
- `processing` - Currently generating
- `completed` - Successfully completed
- `failed` - Generation failed
- `cancelled` - Cancelled before processing

### custom_field_definitions

User-defined metadata fields for videos (Issue #224).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Auto-increment ID |
| name | VARCHAR(100) | NOT NULL | Display name shown in UI |
| slug | VARCHAR(100) | NOT NULL | URL-safe identifier for API queries |
| field_type | VARCHAR(20) | NOT NULL, CHECK | One of: text, number, date, select, multi_select, url |
| options | TEXT | NULLABLE | JSON array for select/multi_select fields |
| required | BOOLEAN | DEFAULT FALSE | Whether field must have a value |
| category_id | INTEGER | FK(categories.id) CASCADE | NULL for global, category ID for category-specific |
| position | INTEGER | DEFAULT 0 | Display order (lower = first) |
| constraints | TEXT | NULLABLE | JSON validation rules (min, max, pattern, etc.) |
| description | TEXT | NULLABLE | Help text shown in UI |
| created_at | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | Creation timestamp |

**Field Types:**
- `text` - Free-form text input
- `number` - Numeric value (integer or float)
- `date` - Date value (ISO 8601 string)
- `select` - Single choice from options list
- `multi_select` - Multiple choices from options list (JSON array)
- `url` - URL value with validation

**Unique Constraint:** `uq_custom_field_slug_category` (slug, category_id)

**Indexes:**
- `ix_custom_field_definitions_category_id` - Category filtering
- `ix_custom_field_definitions_position` - Display ordering

### video_custom_fields

Custom field values for each video (many-to-many with JSON values).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| video_id | INTEGER | FK(videos.id) CASCADE, PK | Video reference |
| field_id | INTEGER | FK(custom_field_definitions.id) CASCADE, PK | Field definition reference |
| value | TEXT | NULLABLE | JSON-encoded value (supports all types) |

**Indexes:**
- `ix_video_custom_fields_video_id` - Find fields for a video
- `ix_video_custom_fields_field_id` - Find videos with a field value

**Cascade Behavior:** Deleting a video or field definition removes the value.

### webhooks

External webhook endpoints for event notifications.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Auto-increment ID |
| name | VARCHAR(100) | NOT NULL | Webhook display name |
| url | VARCHAR(500) | NOT NULL | Webhook endpoint URL |
| events | TEXT | NOT NULL | JSON array of subscribed events |
| secret | VARCHAR(64) | NULLABLE | HMAC-SHA256 signing key |
| active | BOOLEAN | DEFAULT TRUE | Whether webhook is enabled |
| headers | TEXT | NULLABLE | JSON object of custom headers |
| created_at | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | Creation timestamp |
| updated_at | TIMESTAMP WITH TIME ZONE | NULLABLE | Last modification timestamp |
| last_triggered_at | TIMESTAMP WITH TIME ZONE | NULLABLE | Last delivery attempt |
| total_deliveries | INTEGER | DEFAULT 0 | Total delivery attempts |
| successful_deliveries | INTEGER | DEFAULT 0 | Successful deliveries |
| failed_deliveries | INTEGER | DEFAULT 0 | Failed deliveries |

**Indexes:**
- `ix_webhooks_active` - Filter by active status
- `ix_webhooks_created_at` - Order by creation date

**Supported Events:**
- `video.uploaded`, `video.ready`, `video.failed`, `video.deleted`, `video.restored`
- `transcription.completed`
- `worker.registered`, `worker.offline`

### webhook_deliveries

Tracks individual webhook delivery attempts.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Auto-increment ID |
| webhook_id | INTEGER | FK(webhooks.id) CASCADE | Parent webhook |
| event_type | VARCHAR(50) | NOT NULL | Event that triggered delivery |
| event_data | TEXT | NOT NULL | JSON payload sent |
| request_body | TEXT | NULLABLE | Full request body |
| response_status | INTEGER | NULLABLE | HTTP response status |
| response_body | TEXT | NULLABLE | Response body (truncated) |
| error_message | TEXT | NULLABLE | Error if delivery failed |
| attempt_number | INTEGER | DEFAULT 1 | Retry attempt number |
| status | VARCHAR(20) | DEFAULT 'pending' | Delivery status |
| created_at | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | When delivery was queued |
| next_retry_at | TIMESTAMP WITH TIME ZONE | NULLABLE | Next retry time |
| delivered_at | TIMESTAMP WITH TIME ZONE | NULLABLE | When successfully delivered |
| duration_ms | INTEGER | NULLABLE | Request duration in milliseconds |

**Status Values:**
- `pending` - Queued for delivery
- `delivered` - Successfully delivered
- `failed` - Failed, will retry
- `failed_permanent` - Failed, max retries exceeded

**Indexes:**
- `ix_webhook_deliveries_webhook_id` - Find deliveries for a webhook
- `ix_webhook_deliveries_status` - Filter by status
- `ix_webhook_deliveries_event_type` - Filter by event type
- `ix_webhook_deliveries_next_retry_at` - Find deliveries due for retry
- `ix_webhook_deliveries_status_next_retry` - Composite for retry query

### reencode_queue

Background queue for re-encoding videos to different formats/codecs.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Auto-increment ID |
| video_id | INTEGER | FK(videos.id) CASCADE | Video to re-encode |
| target_format | VARCHAR(20) | DEFAULT 'cmaf' | Target format: hls_ts, cmaf |
| target_codec | VARCHAR(10) | DEFAULT 'hevc' | Target codec: h264, hevc, av1 |
| priority | VARCHAR(10) | DEFAULT 'normal' | Priority: high, normal, low |
| status | VARCHAR(20) | DEFAULT 'pending' | Job status |
| created_at | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | When queued |
| started_at | TIMESTAMP WITH TIME ZONE | NULLABLE | When processing started |
| completed_at | TIMESTAMP WITH TIME ZONE | NULLABLE | When completed |
| error_message | TEXT | NULLABLE | Error if failed |
| retry_count | INTEGER | DEFAULT 0 | Number of retry attempts |
| processed_by_worker_id | INTEGER | NULLABLE | Worker that processed job |

**Status Values:**
- `pending` - Queued for processing
- `in_progress` - Currently processing
- `completed` - Successfully re-encoded
- `failed` - Processing failed
- `cancelled` - Cancelled by user

**Indexes:**
- `ix_reencode_queue_status` - Filter by status
- `ix_reencode_queue_video_id` - Find jobs for a video
- `ix_reencode_queue_priority_created` - Order by priority then date

### deployment_events

Tracks worker lifecycle events for operational monitoring.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Auto-increment ID |
| worker_id | VARCHAR(36) | NOT NULL | Worker UUID |
| worker_name | VARCHAR(100) | NULLABLE | Worker display name |
| event_type | VARCHAR(20) | NOT NULL | Event type |
| old_version | VARCHAR(64) | NULLABLE | Previous code version |
| new_version | VARCHAR(64) | NULLABLE | New code version |
| status | VARCHAR(20) | DEFAULT 'pending' | Event status |
| triggered_by | VARCHAR(100) | NULLABLE | Who/what triggered event |
| details | TEXT | NULLABLE | JSON additional details |
| created_at | TIMESTAMP WITH TIME ZONE | NOT NULL | When event occurred |
| completed_at | TIMESTAMP WITH TIME ZONE | NULLABLE | When event completed |

**Event Types:**
- `restart` - Worker restart requested
- `stop` - Worker stop requested
- `update` - Code update deployed
- `deploy` - New deployment
- `rollback` - Rollback to previous version
- `version_change` - Version changed

**Status Values:**
- `pending` - Waiting to execute
- `in_progress` - Currently executing
- `completed` - Successfully completed
- `failed` - Execution failed

**Indexes:**
- `ix_deployment_events_worker_id` - Find events for a worker
- `ix_deployment_events_created_at` - Order by date

---

## Entity Relationships

```
categories (1) ─────────────────────────── (N) videos
categories (1) ─────────────────────────── (N) custom_field_definitions
                                                │
videos (1) ─────────────────────────────── (N) video_qualities
videos (1) ─────────────────────────────── (N) playback_sessions
videos (1) ─────────────────────────────── (1) transcoding_jobs
videos (1) ─────────────────────────────── (1) transcriptions
videos (1) ─────────────────────────────── (N) chapters
videos (1) ─────────────────────────────── (N) sprite_queue
videos (1) ─────────────────────────────── (N) reencode_queue
videos (N) ─────────────────────────────── (N) tags (via video_tags)
videos (N) ─────────────────────────────── (N) playlists (via playlist_items)
videos (N) ─────────────────────────────── (N) custom_field_definitions (via video_custom_fields)
                                                │
playlists (1) ─────────────────────────── (N) playlist_items
                                                │
transcoding_jobs (1) ──────────────────── (N) quality_progress
transcoding_jobs (N) ──────────────────── (1) workers
                                                │
workers (1) ───────────────────────────── (N) worker_api_keys
                                                │
viewers (1) ────────────────────────────── (N) playback_sessions
                                                │
webhooks (1) ──────────────────────────── (N) webhook_deliveries

admin_sessions ────────────────────────── (standalone, no FKs)
settings ──────────────────────────────── (standalone, no FKs)
deployment_events ─────────────────────── (standalone, references workers.worker_id but no FK)
```

---

## Cascade Behavior

```
categories (1) ─────────────────────────── (N) videos
categories (1) ─────────────────────────── (N) custom_field_definitions
                                                │
videos (1) ─────────────────────────────── (N) video_qualities
videos (1) ─────────────────────────────── (N) playback_sessions
videos (1) ─────────────────────────────── (1) transcoding_jobs
videos (1) ─────────────────────────────── (1) transcriptions
videos (1) ─────────────────────────────── (N) chapters
videos (1) ─────────────────────────────── (N) sprite_queue
videos (N) ─────────────────────────────── (N) tags (via video_tags)
videos (N) ─────────────────────────────── (N) playlists (via playlist_items)
videos (N) ─────────────────────────────── (N) custom_field_definitions (via video_custom_fields)
                                                │
playlists (1) ─────────────────────────── (N) playlist_items
                                                │
transcoding_jobs (1) ──────────────────── (N) quality_progress
transcoding_jobs (N) ──────────────────── (1) workers
                                                │
workers (1) ───────────────────────────── (N) worker_api_keys
                                                │
viewers (1) ────────────────────────────── (N) playback_sessions

admin_sessions ────────────────────────── (standalone, no FKs)
settings ──────────────────────────────── (standalone, no FKs)
```

---

## Cascade Behavior

| Parent | Child | On Delete |
|--------|-------|-----------|
| videos | video_qualities | CASCADE |
| videos | playback_sessions | CASCADE |
| videos | transcoding_jobs | CASCADE |
| videos | transcriptions | CASCADE |
| videos | video_tags | CASCADE |
| videos | chapters | CASCADE |
| videos | sprite_queue | CASCADE |
| videos | playlist_items | CASCADE |
| playlists | playlist_items | CASCADE |
| tags | video_tags | CASCADE |
| videos | video_custom_fields | CASCADE |
| custom_field_definitions | video_custom_fields | CASCADE |
| categories | custom_field_definitions | CASCADE |
| transcoding_jobs | quality_progress | CASCADE |
| workers | worker_api_keys | CASCADE |
| workers | transcoding_jobs.worker_id | SET NULL |
| viewers | playback_sessions | SET NULL |
| categories | videos | SET NULL (handled in app) |
| webhooks | webhook_deliveries | CASCADE |
| videos | reencode_queue | CASCADE |

---

## Common Queries

### Get Ready Videos with Category

```sql
SELECT v.*, c.name as category_name
FROM videos v
LEFT JOIN categories c ON v.category_id = c.id
WHERE v.status = 'ready'
ORDER BY v.published_at DESC
LIMIT 50;
```

### Get Video with Qualities

```sql
SELECT v.*, q.quality, q.width, q.height, q.bitrate
FROM videos v
LEFT JOIN video_qualities q ON v.id = q.video_id
WHERE v.slug = 'my-video';
```

### Get Transcoding Progress

```sql
SELECT j.*, qp.quality, qp.status, qp.progress_percent
FROM transcoding_jobs j
LEFT JOIN quality_progress qp ON j.id = qp.job_id
WHERE j.video_id = 1;
```

### Analytics Overview

```sql
SELECT
    COUNT(*) as total_views,
    COUNT(DISTINCT viewer_id) as unique_viewers,
    SUM(duration_watched) / 3600 as watch_hours,
    AVG(CASE WHEN completed THEN 1.0 ELSE 0.0 END) as completion_rate
FROM playback_sessions;
```

### Find Stale Jobs

```sql
SELECT * FROM transcoding_jobs
WHERE completed_at IS NULL
  AND last_checkpoint < NOW() - INTERVAL '30 minutes';
```

---

## Database Management

### Initialize Database

```bash
# Create PostgreSQL database and user (first time only)
sudo -u postgres psql << EOF
CREATE USER vlog WITH PASSWORD 'vlog_password';
CREATE DATABASE vlog OWNER vlog;
GRANT ALL PRIVILEGES ON DATABASE vlog TO vlog;
EOF

# Create tables
python api/database.py
```

### Migrations

VLog uses Alembic for schema migrations:

```bash
# Check current migration version
alembic current

# Apply all pending migrations
alembic upgrade head

# Rollback one migration
alembic downgrade -1

# Create a new migration (after modifying models)
alembic revision --autogenerate -m "description"
```

### Backup

```bash
# Backup entire database
pg_dump -U vlog vlog > backup_$(date +%Y%m%d).sql

# Backup with compression
pg_dump -U vlog -Fc vlog > backup_$(date +%Y%m%d).dump

# Restore from backup
pg_restore -U vlog -d vlog backup.dump
```

### Query Interactively

```bash
# Connect to database
psql -U vlog -d vlog

# Common commands
\dt          -- List tables
\d videos    -- Describe table schema
\di          -- List indexes

# Example queries
SELECT * FROM videos LIMIT 5;
SELECT COUNT(*) FROM playback_sessions;
```

### Reset Database

```bash
# Drop and recreate database
sudo -u postgres psql -c "DROP DATABASE IF EXISTS vlog;"
sudo -u postgres psql -c "CREATE DATABASE vlog OWNER vlog;"

# Recreate tables
python api/database.py
```
