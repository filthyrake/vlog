# VLog Database Schema

## Overview

VLog uses SQLite for data storage. The database file (`vlog.db`) is kept local for performance, while video files are stored on NAS.

**Location:** `./vlog.db` (configurable via `VLOG_DATABASE_PATH`)

**ORM:** SQLAlchemy with async support via `databases` and `aiosqlite`

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
| created_at | DATETIME | DEFAULT now | Creation timestamp |

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
| created_at | DATETIME | DEFAULT now | Upload timestamp |
| published_at | DATETIME | NULLABLE | Publication timestamp |
| deleted_at | DATETIME | NULLABLE | Soft-delete timestamp (NULL = not deleted) |

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
| first_seen | DATETIME | DEFAULT now | First visit |
| last_seen | DATETIME | DEFAULT now | Last visit |

### playback_sessions

Individual video playback tracking for analytics.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Auto-increment ID |
| video_id | INTEGER | FK(videos.id) CASCADE | Video watched |
| viewer_id | INTEGER | FK(viewers.id) SET NULL | Optional viewer link |
| session_token | VARCHAR(64) | NOT NULL | Unique session token |
| started_at | DATETIME | DEFAULT now | Playback start |
| ended_at | DATETIME | NULLABLE | Playback end |
| duration_watched | FLOAT | DEFAULT 0 | Seconds watched |
| max_position | FLOAT | DEFAULT 0 | Furthest position |
| quality_used | VARCHAR(10) | NULLABLE | Primary quality used |
| completed | BOOLEAN | DEFAULT FALSE | Watched >= 90% |

**Indexes:**
- `ix_playback_sessions_video_id` - Per-video analytics
- `ix_playback_sessions_started_at` - Time-based queries
- `ix_playback_sessions_session_token` - Session lookups

### transcoding_jobs

Tracks transcoding jobs with checkpoint-based recovery.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Auto-increment ID |
| video_id | INTEGER | FK(videos.id) CASCADE, UNIQUE | One job per video |
| worker_id | VARCHAR(36) | NULLABLE | Processing worker UUID |
| current_step | VARCHAR(50) | NULLABLE | Current processing step |
| progress_percent | INTEGER | DEFAULT 0 | Overall progress (0-100) |
| started_at | DATETIME | NULLABLE | Job start time |
| last_checkpoint | DATETIME | NULLABLE | Last checkpoint update |
| completed_at | DATETIME | NULLABLE | Job completion time |
| attempt_number | INTEGER | DEFAULT 1 | Current attempt (1-N) |
| max_attempts | INTEGER | DEFAULT 3 | Maximum retries |
| last_error | TEXT | NULLABLE | Last error message |

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
| started_at | DATETIME | NULLABLE | Quality start time |
| completed_at | DATETIME | NULLABLE | Quality completion time |
| error_message | TEXT | NULLABLE | Error if failed |

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
| started_at | DATETIME | NULLABLE | Transcription start |
| completed_at | DATETIME | NULLABLE | Transcription end |
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
| worker_name | VARCHAR(100) | NOT NULL | Display name |
| worker_type | VARCHAR(20) | DEFAULT 'remote' | Worker type (local/remote) |
| status | VARCHAR(20) | DEFAULT 'idle' | Current status |
| registered_at | DATETIME | DEFAULT now | Registration time |
| last_heartbeat | DATETIME | NULLABLE | Last heartbeat received |
| current_job_id | INTEGER | FK(transcoding_jobs.id) | Currently assigned job |
| capabilities | TEXT | NULLABLE | JSON capabilities metadata |

**Status Values:**
- `idle` - Ready for work
- `active` - Currently processing
- `offline` - No recent heartbeat
- `revoked` - API key revoked

**Indexes:**
- `ix_workers_worker_id` - Worker UUID lookup
- `ix_workers_status` - Status filtering

### worker_api_keys

API keys for worker authentication (SHA-256 hashed).

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| id | INTEGER | PRIMARY KEY | Auto-increment ID |
| worker_id | INTEGER | FK(workers.id) CASCADE | Parent worker |
| key_prefix | VARCHAR(8) | NOT NULL, INDEX | First 8 chars for lookup |
| key_hash | VARCHAR(64) | NOT NULL | SHA-256 hash of full key |
| created_at | DATETIME | DEFAULT now | Key creation time |
| revoked_at | DATETIME | NULLABLE | Revocation time (NULL = active) |

**Security Design:**
- Full API key is only shown once at registration
- Key is stored as SHA-256 hash
- Prefix enables efficient lookup without full scan
- Revoked keys remain in table for audit trail

---

## Entity Relationships

```
categories (1) ─────────────────────────── (N) videos
                                                │
videos (1) ─────────────────────────────── (N) video_qualities
videos (1) ─────────────────────────────── (N) playback_sessions
videos (1) ─────────────────────────────── (1) transcoding_jobs
videos (1) ─────────────────────────────── (1) transcriptions
                                                │
transcoding_jobs (1) ──────────────────── (N) quality_progress
transcoding_jobs (N) ──────────────────── (1) workers
                                                │
workers (1) ───────────────────────────── (N) worker_api_keys
                                                │
viewers (1) ────────────────────────────── (N) playback_sessions
```

---

## Cascade Behavior

| Parent | Child | On Delete |
|--------|-------|-----------|
| videos | video_qualities | CASCADE |
| videos | playback_sessions | CASCADE |
| videos | transcoding_jobs | CASCADE |
| videos | transcriptions | CASCADE |
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
  AND last_checkpoint < datetime('now', '-30 minutes');
```

---

## Database Management

### Initialize Tables

```bash
python api/database.py
```

### Backup

```bash
sqlite3 vlog.db ".backup 'backup.db'"
```

### Query Interactively

```bash
sqlite3 vlog.db
sqlite> .tables
sqlite> .schema videos
sqlite> SELECT * FROM videos LIMIT 5;
```

### Reset Database

```bash
rm vlog.db
python api/database.py
```
