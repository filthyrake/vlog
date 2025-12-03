# VLog API Reference

## Overview

VLog exposes two FastAPI servers:

- **Public API** (port 9000) - Video browsing, playback, and analytics
- **Admin API** (port 9001) - Video management and uploads (internal only)

Both APIs return JSON responses and support CORS.

---

## Public API (Port 9000)

### Videos

#### List Videos
```
GET /api/videos
```

Query parameters:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| category | string | null | Filter by category slug |
| search | string | null | Search in title/description |
| limit | int | 50 | Max items (1-100) |
| offset | int | 0 | Pagination offset |

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

---

## Error Responses

All endpoints return standard HTTP error codes:

| Code | Description |
|------|-------------|
| 400 | Bad Request - Invalid input |
| 404 | Not Found - Resource doesn't exist |
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
