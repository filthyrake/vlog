# VLog API Reference

## Overview

VLog exposes three FastAPI servers:

- **Public API** (port 9000) - Video browsing, playback, and analytics
- **Admin API** (port 9001) - Video management and uploads (internal only)
- **Worker API** (port 9002) - Remote worker registration and job management

All APIs return JSON responses and support CORS.

---

## Public API (Port 9000)

### Health Check

```
GET /health
```

Response:
```json
{"status": "healthy"}
```

### Videos

#### List Videos
```
GET /api/videos
```

Query parameters:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| category | string | null | Filter by category slug |
| tag | string | null | Filter by tag slug |
| search | string | null | Search in title/description |
| duration | string | null | Filter by length: short, medium, long (comma-separated) |
| quality | string | null | Filter by available quality: 2160p, 1440p, 1080p, 720p, 480p, 360p (comma-separated) |
| date_from | datetime | null | Filter videos published from this date (ISO 8601) |
| date_to | datetime | null | Filter videos published until this date (ISO 8601) |
| has_transcription | bool | null | Filter by transcription availability (true/false) |
| sort | string | null | Sort by: relevance, date, duration, views, title |
| order | string | desc | Sort order: asc or desc |
| limit | int | 50 | Max items (1-100) |
| offset | int | 0 | Pagination offset |

**Duration Filter Values:**
- `short` - Videos less than 5 minutes
- `medium` - Videos between 5-20 minutes
- `long` - Videos longer than 20 minutes

**Sort Options:**
- `relevance` - Default for text searches, sorts by published date
- `date` - Sort by publication date
- `duration` - Sort by video length
- `views` - Sort by view count
- `title` - Sort alphabetically

**Examples:**
```
# Search for tutorials with transcription
GET /api/videos?search=tutorial&has_transcription=true

# Find short videos in 1080p or 4K
GET /api/videos?duration=short&quality=1080p,2160p

# Most viewed videos this month (adjust date_from to first day of current month)
GET /api/videos?date_from=YYYY-MM-01&sort=views&order=desc

# Longest videos first
GET /api/videos?sort=duration&order=desc

# Multiple filters combined
GET /api/videos?category=tutorials&duration=medium&quality=1080p&sort=views&order=desc
```

Response: `VideoListResponse[]`
```json
[
  {
    "id": 1,
    "title": "My Video",
    "slug": "my-video",
    "description": "Video description",
    "category_id": 1,
    "category_name": "Tutorials",
    "duration": 125.5,
    "status": "ready",
    "created_at": "2024-01-15T10:30:00",
    "published_at": "2024-01-15T12:00:00",
    "thumbnail_url": "/videos/my-video/thumbnail.jpg"
  }
]
```

#### Get Video Details
```
GET /api/videos/{slug}
```

Response: `VideoResponse`
```json
{
  "id": 1,
  "title": "My Video",
  "slug": "my-video",
  "description": "Video description",
  "category_id": 1,
  "category_name": "Tutorials",
  "category_slug": "tutorials",
  "duration": 125.5,
  "source_width": 1920,
  "source_height": 1080,
  "status": "ready",
  "error_message": null,
  "created_at": "2024-01-15T10:30:00",
  "published_at": "2024-01-15T12:00:00",
  "thumbnail_url": "/videos/my-video/thumbnail.jpg",
  "stream_url": "/videos/my-video/master.m3u8",
  "captions_url": "/videos/my-video/captions.vtt",
  "transcription_status": "completed",
  "qualities": [
    {"quality": "1080p", "width": 1920, "height": 1080, "bitrate": 5000},
    {"quality": "720p", "width": 1280, "height": 720, "bitrate": 2500}
  ]
}
```

#### Get Transcoding Progress
```
GET /api/videos/{slug}/progress
```

Response: `TranscodingProgressResponse`
```json
{
  "status": "processing",
  "current_step": "transcode",
  "progress_percent": 45,
  "qualities": [
    {"name": "1080p", "status": "completed", "progress": 100},
    {"name": "720p", "status": "in_progress", "progress": 60},
    {"name": "480p", "status": "pending", "progress": 0}
  ],
  "attempt": 1,
  "max_attempts": 3,
  "started_at": "2024-01-15T10:30:00",
  "last_error": null
}
```

#### Get Transcript
```
GET /api/videos/{slug}/transcript
```

Response: `TranscriptionResponse`
```json
{
  "status": "completed",
  "language": "en",
  "text": "Full transcript text...",
  "vtt_url": "/videos/my-video/captions.vtt",
  "word_count": 1523,
  "duration_seconds": 45.2,
  "started_at": "2024-01-15T12:00:00",
  "completed_at": "2024-01-15T12:00:45",
  "error_message": null
}
```

### Categories

#### List Categories
```
GET /api/categories
```

Response: `CategoryResponse[]`
```json
[
  {
    "id": 1,
    "name": "Tutorials",
    "slug": "tutorials",
    "description": "Tutorial videos",
    "created_at": "2024-01-01T00:00:00",
    "video_count": 15
  }
]
```

#### Get Category
```
GET /api/categories/{slug}
```

Response: `CategoryResponse`

### Analytics

#### Start Playback Session
```
POST /api/analytics/session
```

Request body:
```json
{
  "video_id": 1,
  "quality": "1080p"
}
```

Response:
```json
{
  "session_token": "uuid-string"
}
```

#### Send Heartbeat
```
POST /api/analytics/heartbeat
```

Request body:
```json
{
  "session_token": "uuid-string",
  "position": 45.5,
  "quality": "720p",
  "playing": true
}
```

Response:
```json
{"status": "ok"}
```

#### End Session
```
POST /api/analytics/end
```

Request body:
```json
{
  "session_token": "uuid-string",
  "position": 120.0,
  "completed": true
}
```

Response:
```json
{"status": "ok"}
```

### Static Files

| Path | Description |
|------|-------------|
| `/videos/{slug}/master.m3u8` | HLS master playlist |
| `/videos/{slug}/{quality}.m3u8` | Quality-specific playlist |
| `/videos/{slug}/{quality}_XXXX.ts` | Video segments |
| `/videos/{slug}/thumbnail.jpg` | Video thumbnail |
| `/videos/{slug}/captions.vtt` | WebVTT subtitles |

---

## Admin API (Port 9001)

**Warning:** This API should only be accessible from internal networks.

### Health Check

```
GET /health
```

Response:
```json
{"status": "healthy"}
```

### Categories

#### List Categories
```
GET /api/categories
```

#### Create Category
```
POST /api/categories
```

Request body:
```json
{
  "name": "Tutorials",
  "description": "Tutorial videos"
}
```

#### Delete Category
```
DELETE /api/categories/{category_id}
```

Note: Videos in deleted category become uncategorized.

### Videos

#### List All Videos
```
GET /api/videos
```

Query parameters:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| status | string | null | Filter: pending/processing/ready/failed |
| limit | int | 100 | Max items (1-500) |
| offset | int | 0 | Pagination offset |

#### Get Video
```
GET /api/videos/{video_id}
```

#### Upload Video
```
POST /api/videos
Content-Type: multipart/form-data
```

Form fields:
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| file | file | yes | Video file |
| title | string | yes | Video title |
| description | string | no | Video description |
| category_id | int | no | Category ID |

Response:
```json
{
  "status": "ok",
  "video_id": 1,
  "slug": "my-video",
  "message": "Video queued for processing"
}
```

#### Update Video
```
PUT /api/videos/{video_id}
Content-Type: multipart/form-data
```

Form fields:
| Field | Type | Description |
|-------|------|-------------|
| title | string | New title |
| description | string | New description |
| category_id | int | New category (0 to remove) |
| published_at | string | ISO datetime (empty to clear) |

#### Delete Video
```
DELETE /api/videos/{video_id}
```

Deletes video record, HLS files, and upload source.

#### Retry Failed Video
```
POST /api/videos/{video_id}/retry
```

Resets failed video to pending status for reprocessing.

#### Re-upload Video
```
POST /api/videos/{video_id}/re-upload
Content-Type: multipart/form-data
```

Replace source file for an existing video and restart transcoding.

Form fields:
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| file | file | yes | New video file |

Response:
```json
{
  "status": "ok",
  "video_id": 1,
  "message": "Video re-queued for processing"
}
```

### Soft-Delete / Archive

#### List Archived Videos
```
GET /api/videos/archived
```

Query parameters:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| limit | int | 100 | Max items (1-500) |
| offset | int | 0 | Pagination offset |

Response: Same as video list but with `deleted_at` field populated.

#### Restore Video
```
POST /api/videos/{video_id}/restore
```

Restores a soft-deleted video from archive.

Response:
```json
{
  "status": "ok",
  "message": "Video restored"
}
```

**Note:** When a video is deleted via `DELETE /api/videos/{video_id}`, it is soft-deleted (moved to archive). Videos remain in archive for `ARCHIVE_RETENTION_DAYS` (default 30) before permanent deletion.

#### Get Transcoding Progress
```
GET /api/videos/{video_id}/progress
```

### Transcription

#### Get Transcript
```
GET /api/videos/{video_id}/transcript
```

#### Trigger Transcription
```
POST /api/videos/{video_id}/transcribe
```

Request body (optional):
```json
{
  "language": "en"
}
```

#### Update Transcript
```
PUT /api/videos/{video_id}/transcript
```

Request body:
```json
{
  "text": "Corrected transcript text"
}
```

#### Delete Transcript
```
DELETE /api/videos/{video_id}/transcript
```

### Analytics

#### Overview
```
GET /api/analytics/overview
```

Response:
```json
{
  "total_views": 1500,
  "unique_viewers": 320,
  "total_watch_time_hours": 45.5,
  "completion_rate": 0.65,
  "avg_watch_duration_seconds": 180.5,
  "views_today": 50,
  "views_this_week": 250,
  "views_this_month": 800
}
```

#### Per-Video Analytics
```
GET /api/analytics/videos
```

Query parameters:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| limit | int | 50 | Max items (1-100) |
| offset | int | 0 | Pagination offset |
| sort_by | string | "views" | Sort: views/watch_time/completion_rate |
| period | string | "all" | Filter: all/day/week/month |

#### Video Detail Analytics
```
GET /api/analytics/videos/{video_id}
```

Response includes quality breakdown and views over time.

#### Trends
```
GET /api/analytics/trends
```

Query parameters:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| period | string | "30d" | Period: 7d/30d/90d |
| video_id | int | null | Filter by video |

### Video Qualities

#### Get Video Qualities
```
GET /api/videos/{video_id}/qualities
```

Response:
```json
{
  "video_id": 1,
  "qualities": [
    {"name": "1080p", "width": 1920, "height": 1080, "bitrate": 5000},
    {"name": "720p", "width": 1280, "height": 720, "bitrate": 2500}
  ]
}
```

#### Retranscode Video
```
POST /api/videos/{video_id}/retranscode
```

Request body (optional):
```json
{
  "qualities": ["1080p", "720p"],
  "force": false
}
```

Triggers retranscoding of specific qualities or all qualities if not specified.

### Batch Operations

#### Bulk Delete Videos
```
POST /api/videos/bulk/delete
```

Request body:
```json
{
  "video_ids": [1, 2, 3]
}
```

Response:
```json
{
  "success": true,
  "total": 3,
  "succeeded": 3,
  "failed": 0,
  "results": [
    {"video_id": 1, "success": true, "message": "Video deleted"},
    {"video_id": 2, "success": true, "message": "Video deleted"},
    {"video_id": 3, "success": true, "message": "Video deleted"}
  ]
}
```

#### Bulk Update Videos
```
POST /api/videos/bulk/update
```

Request body:
```json
{
  "video_ids": [1, 2, 3],
  "category_id": 5,
  "published_at": "2024-01-15T12:00:00"
}
```

#### Bulk Retranscode Videos
```
POST /api/videos/bulk/retranscode
```

Request body:
```json
{
  "video_ids": [1, 2, 3],
  "qualities": ["1080p", "720p"]
}
```

#### Bulk Restore Videos
```
POST /api/videos/bulk/restore
```

Request body:
```json
{
  "video_ids": [1, 2, 3]
}
```

Restores multiple soft-deleted videos from archive.

### Video Export

#### Export Video List
```
GET /api/videos/export
```

Query parameters:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| format | string | "json" | Export format: json/csv |
| status | string | null | Filter by status |
| category_id | int | null | Filter by category |

Returns a downloadable export of video metadata.

### Worker Management (Admin)

#### List All Workers
```
GET /api/workers
```

Response:
```json
[
  {
    "id": 1,
    "worker_id": "uuid-string",
    "worker_name": "k8s-worker-1",
    "status": "active",
    "current_job_id": 16
  }
]
```

#### Get Active Jobs
```
GET /api/workers/active-jobs
```

Returns list of currently processing transcoding jobs with worker info.

#### Get Worker Details
```
GET /api/workers/{worker_id}
```

Response includes worker info, capabilities, and job history.

#### Disable Worker
```
PUT /api/workers/{worker_id}/disable
```

Prevents worker from claiming new jobs.

#### Enable Worker
```
PUT /api/workers/{worker_id}/enable
```

Re-enables a disabled worker.

#### Delete Worker
```
DELETE /api/workers/{worker_id}
```

Removes worker registration (does not revoke API key).

### Server-Sent Events (SSE)

Real-time updates for transcoding progress and worker status.

#### Progress Updates
```
GET /api/events/progress?video_ids=1,2,3
```

Streams real-time transcoding progress updates.

Query parameters:
| Parameter | Type | Description |
|-----------|------|-------------|
| video_ids | string | Comma-separated video IDs to monitor |

SSE Event Types:
- `progress` - Transcoding progress update
- `heartbeat` - Keep-alive (every 30s)

Event data format:
```json
{
  "type": "progress",
  "video_id": 1,
  "job_id": 16,
  "current_step": "transcode",
  "progress_percent": 45,
  "qualities": [
    {"name": "1080p", "status": "completed", "progress": 100},
    {"name": "720p", "status": "in_progress", "progress": 60}
  ],
  "status": "processing",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

#### Worker Status Updates
```
GET /api/events/workers
```

Streams real-time worker status changes.

SSE Event Types:
- `worker_status` - Worker status change
- `heartbeat` - Keep-alive (every 30s)

Event data format:
```json
{
  "type": "worker_status",
  "worker_id": "uuid-string",
  "worker_name": "k8s-worker-1",
  "status": "active",
  "current_job_id": 16,
  "timestamp": "2024-01-15T10:30:00Z"
}
```

**Notes:**
- SSE uses Redis Pub/Sub when available for instant updates
- Falls back to database polling if Redis is unavailable
- Client should handle reconnection via `EventSource` API

---

## Rate Limiting

All APIs implement rate limiting via slowapi. Default limits:

**Public API:**
- Default: 100 requests/minute
- Video listing: 60 requests/minute
- Analytics: 120 requests/minute

**Admin API:**
- Default: 200 requests/minute
- Uploads: 10 requests/hour

**Worker API:**
- Default: 300 requests/minute
- Registration: 5 requests/hour
- Progress updates: 600 requests/minute

When rate limited, the response includes:
```json
{
  "detail": "Rate limit exceeded: 100 per 1 minute"
}
```

Rate limiting can be disabled via `VLOG_RATE_LIMIT_ENABLED=false`.

---

## Error Responses

All endpoints return standard HTTP error codes:

| Code | Description |
|------|-------------|
| 400 | Bad Request - Invalid input |
| 404 | Not Found - Resource doesn't exist |
| 429 | Too Many Requests - Rate limit exceeded |
| 500 | Server Error - Internal error |

Error response format:
```json
{
  "detail": "Error message"
}
```

---

## Video Status Values

| Status | Description |
|--------|-------------|
| pending | Uploaded, waiting for processing |
| processing | Currently being transcoded |
| ready | Transcoding complete, playable |
| failed | Transcoding failed (check error_message) |

## Transcription Status Values

| Status | Description |
|--------|-------------|
| none | No transcription exists |
| pending | Queued for transcription |
| processing | Currently being transcribed |
| completed | Transcription complete |
| failed | Transcription failed |

---

## Worker API (Port 9002)

**Authentication:** All endpoints (except `/api/health`) require API key authentication via `X-API-Key` header.

### Health Check

```
GET /api/health
```

Response:
```json
{"status": "healthy"}
```

### Worker Registration

#### Register Worker
```
POST /api/worker/register
```

Request body:
```json
{
  "worker_name": "k8s-worker-1",
  "worker_type": "remote",
  "capabilities": {"ffmpeg_version": "6.0"}
}
```

Response:
```json
{
  "worker_id": "uuid-string",
  "api_key": "generated-api-key",
  "message": "Worker registered successfully"
}
```

**Note:** Save the `api_key` - it cannot be retrieved again.

#### Send Heartbeat
```
POST /api/worker/heartbeat
```

Request body:
```json
{
  "status": "active",
  "metadata": {"cpu_usage": 45.2}
}
```

### Job Management

#### Claim Job
```
POST /api/worker/claim
```

Response (job available):
```json
{
  "job_id": 16,
  "video_id": 15,
  "video_slug": "my-video",
  "video_duration": 300.5,
  "source_width": 1920,
  "source_height": 1080,
  "source_filename": "15.mp4",
  "claim_expires_at": "2024-01-15T11:00:00Z",
  "message": "Job claimed successfully"
}
```

Response (no jobs):
```json
{
  "message": "No jobs available"
}
```

#### Update Progress
```
POST /api/worker/{job_id}/progress
```

Request body:
```json
{
  "current_step": "transcode",
  "progress_percent": 45,
  "quality_progress": [
    {"name": "1080p", "status": "completed", "progress": 100},
    {"name": "720p", "status": "in_progress", "progress": 60}
  ]
}
```

#### Complete Job
```
POST /api/worker/{job_id}/complete
```

Request body:
```json
{
  "qualities": [
    {"name": "1080p", "width": 1920, "height": 1080, "bitrate": 5000},
    {"name": "720p", "width": 1280, "height": 720, "bitrate": 2500}
  ]
}
```

#### Fail Job
```
POST /api/worker/{job_id}/fail
```

Request body:
```json
{
  "error_message": "FFmpeg encoding failed",
  "retry": true
}
```

### File Transfer

#### Download Source
```
GET /api/worker/source/{video_id}
```

Returns the source video file as a streaming download.

#### Upload HLS Output (Single Archive)
```
POST /api/worker/upload/{video_id}
```

Upload a tar.gz archive containing:
- `master.m3u8`
- `{quality}.m3u8` files
- `{quality}_XXXX.ts` segments
- `thumbnail.jpg`

#### Upload Per-Quality Output
```
POST /api/worker/upload/{video_id}/quality/{quality_name}
```

Upload a tar.gz archive containing files for a single quality:
- `{quality}.m3u8` playlist
- `{quality}_XXXX.ts` segments
- `thumbnail.jpg` (optional, included with first quality)

This endpoint supports streaming uploads for large files.

#### Finalize Upload
```
POST /api/worker/upload/{video_id}/finalize
```

After uploading all qualities via per-quality endpoint, call this to:
- Generate the master.m3u8 playlist
- Mark the video as ready

Request body:
```json
{
  "qualities": [
    {"name": "1080p", "width": 1920, "height": 1080, "bitrate": 5000},
    {"name": "720p", "width": 1280, "height": 720, "bitrate": 2500}
  ]
}
```

### Admin Endpoints

#### List Workers
```
GET /api/workers
```

Response:
```json
[
  {
    "id": 1,
    "worker_id": "uuid-string",
    "worker_name": "k8s-worker-1",
    "worker_type": "remote",
    "status": "active",
    "registered_at": "2024-01-15T10:00:00",
    "last_heartbeat": "2024-01-15T10:30:00",
    "current_job_id": 16,
    "current_video_slug": "my-video"
  }
]
```

#### Revoke Worker
```
POST /api/workers/{worker_id}/revoke
```

Revokes the worker's API key, preventing further API access.
