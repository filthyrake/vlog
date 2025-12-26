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

API keys for worker authentication (SHA-256 hashed).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Auto-increment ID |
| worker_id | INTEGER | FK(workers.id) CASCADE | Parent worker |
| key_hash | VARCHAR(64) | NOT NULL | SHA-256 hash of full key |
| key_prefix | VARCHAR(8) | NOT NULL, INDEX | First 8 chars for lookup |
| created_at | TIMESTAMP | NOT NULL | Key creation time |
| expires_at | TIMESTAMP | NULLABLE | Optional expiration time |
| revoked_at | TIMESTAMP | NULLABLE | Revocation time (NULL = active) |
| last_used_at | TIMESTAMP | NULLABLE | Last successful authentication |

**Indexes:**
- `ix_worker_api_keys_key_prefix` - Efficient key lookup
- `ix_worker_api_keys_worker_id` - Worker key retrieval

**Security Design:**
- Full API key is only shown once at registration
- Key is stored as SHA-256 hash
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
videos (N) ─────────────────────────────── (N) tags (via video_tags)
videos (N) ─────────────────────────────── (N) custom_field_definitions (via video_custom_fields)
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
| tags | video_tags | CASCADE |
| videos | video_custom_fields | CASCADE |
| custom_field_definitions | video_custom_fields | CASCADE |
| categories | custom_field_definitions | CASCADE |
| transcoding_jobs | quality_progress | CASCADE |
| workers | worker_api_keys | CASCADE |
| workers | transcoding_jobs.worker_id | SET NULL |
| viewers | playback_sessions | SET NULL |
| categories | videos | SET NULL (handled in app) |

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
