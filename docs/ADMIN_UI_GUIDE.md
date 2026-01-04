# VLog Admin UI Guide

The VLog Admin UI provides a modern, responsive interface for managing your video platform. This guide covers all features and common workflows.

## Accessing the Admin UI

**URL:** `http://your-server:9001`

If authentication is enabled (`VLOG_ADMIN_API_SECRET` is set), you'll be prompted to log in. The admin panel is intended for internal use and should not be exposed to the public internet.

---

## Navigation

The admin interface has eight main tabs:

| Tab | Purpose |
|-----|---------|
| **Videos** | Browse, search, edit, and manage all videos |
| **Categories** | Create and manage video categories |
| **Playlists** | Create and manage playlists and collections |
| **Upload** | Upload new videos with metadata |
| **Workers** | Monitor transcoding workers and job status |
| **Webhooks** | Configure event notifications |
| **Analytics** | View playback statistics and trends |
| **Settings** | Configure runtime settings and watermarks |

---

## Videos Tab

### Browsing Videos

The Videos tab displays all videos with their status, category, and metadata.

**Features:**
- **Search:** Full-text search by title or description
- **Filter by Status:** pending, processing, ready, failed
- **Filter by Category:** Filter videos by category
- **Bulk Selection:** Select multiple videos for batch operations

### Video Status Indicators

| Status | Color | Description |
|--------|-------|-------------|
| Ready | Green | Transcoding complete, video is playable |
| Processing | Blue | Currently being transcoded |
| Pending | Yellow | Waiting for transcoding to start |
| Failed | Red | Transcoding failed (click to see error) |

### Editing a Video

Click on any video to open the edit panel:

1. **Metadata:** Edit title, description, category
2. **Thumbnail:** Select from video frames or upload custom image
3. **Custom Fields:** Fill in category-specific metadata fields
4. **Tags:** Add or remove tags
5. **Actions:** Delete, restore, retry transcoding

### Thumbnail Selection

VLog offers two ways to set video thumbnails:

**Option 1: Select from Video Frames**
1. Open the video edit panel
2. Click "Select from Video"
3. Use the timeline scrubber to find the perfect frame
4. Click "Use This Frame"

**Option 2: Upload Custom Image**
1. Open the video edit panel
2. Click "Upload Custom"
3. Select an image file (JPEG, PNG, WebP)
4. Images are automatically resized to optimal dimensions

### Bulk Operations

Select multiple videos using checkboxes, then:
- **Delete Selected:** Move videos to archive
- **Change Category:** Assign a new category
- **Retry Failed:** Re-queue failed transcoding jobs

### Video Actions

| Action | Description |
|--------|-------------|
| **Edit** | Open video metadata editor |
| **Delete** | Soft-delete (moves to archive) |
| **Restore** | Restore from archive |
| **Retry** | Retry failed transcoding |
| **Re-upload** | Replace source file |
| **Re-transcribe** | Regenerate captions |
| **Generate Sprites** | Create timeline preview thumbnails |
| **Queue Re-encode** | Convert to CMAF format |

### Video Chapters

Chapters provide timeline navigation within videos.

**Adding Chapters:**
1. Open the video edit panel
2. Click the "Chapters" tab
3. Click "Add Chapter"
4. Enter:
   - **Title:** Chapter name
   - **Start Time:** When chapter begins (seconds or MM:SS format)
   - **End Time:** Optional end time
5. Click "Save"

**Managing Chapters:**
- Drag and drop to reorder chapters
- Click to edit chapter details
- Click X to remove a chapter
- Maximum 50 chapters per video

**Chapter Display:**
- Chapters appear in the video player timeline
- Viewers can click to jump to specific chapters
- Chapter titles shown on hover

### Sprite Sheet Generation

Sprite sheets enable thumbnail previews when scrubbing the video timeline.

**Generating Sprites:**
1. Open the video edit panel
2. Click "Generate Sprites" in actions menu
3. Wait for background processing
4. Preview thumbnails appear on timeline hover

**Configuration:**
- Frame interval: seconds between captures (default: 5)
- Grid size: thumbnails per sheet (default: 10x10)
- See [CONFIGURATION.md](CONFIGURATION.md#sprite-sheet-settings)

### Re-encode Queue

Convert legacy HLS/TS videos to modern CMAF format.

**Queue Individual Video:**
1. Open the video edit panel
2. Click "Queue Re-encode" in actions menu
3. Select priority (high, normal, low)
4. Video joins re-encode queue

**Bulk Queue:**
1. From Videos tab, filter by format = "hls_ts"
2. Select multiple videos
3. Click "Queue Re-encode"
4. Choose priority level

**Monitor Queue:**
- View queue status in Workers tab
- See pending, in-progress, and completed jobs
- Cancel pending jobs if needed

---

## Categories Tab

Categories help organize videos and can have custom metadata fields.

### Creating a Category

1. Click "New Category"
2. Enter the category name
3. Optionally add a description
4. Click "Create"

### Custom Metadata Fields

Categories can have custom fields that appear when editing videos in that category.

**Supported Field Types:**

| Type | Description | Example Use |
|------|-------------|-------------|
| **text** | Short text input | Director, Producer |
| **number** | Numeric value | Release Year, Rating |
| **date** | Date picker | Release Date |
| **select** | Dropdown with options | Genre, Rating |
| **multi_select** | Multiple selection | Tags, Languages |
| **url** | URL with validation | IMDb Link, Website |

**Creating Custom Fields:**

1. Open a category for editing
2. Click "Add Custom Field"
3. Configure:
   - **Name:** Internal field identifier
   - **Label:** Display name shown in UI
   - **Type:** Select field type
   - **Required:** Whether the field is mandatory
   - **Options:** For select/multi_select types

**Example: Movie Category Fields**
```
Field: release_year (number) - "Release Year"
Field: director (text) - "Director"
Field: genre (multi_select) - "Genre" [Action, Comedy, Drama, ...]
Field: imdb_link (url) - "IMDb Page"
```

---

## Playlists Tab

Playlists allow you to organize videos into curated collections, series, or courses.

### Creating a Playlist

1. Click "New Playlist"
2. Enter a title (required)
3. Add an optional description
4. Select playlist type:
   - **Playlist** - General purpose
   - **Collection** - Curated collection
   - **Series** - Sequential series (numbered)
   - **Course** - Educational course
5. Set visibility:
   - **Public** - Visible to all viewers
   - **Private** - Admin-only
   - **Unlisted** - Accessible by direct link
6. Toggle "Featured" to highlight on homepage
7. Click "Create"

### Managing Playlist Videos

**Adding Videos:**
1. Open a playlist
2. Click "Add Videos"
3. Search or browse videos
4. Select videos to add
5. Click "Add Selected"

**Reordering Videos:**
1. Open the playlist
2. Drag and drop videos to reorder
3. Changes save automatically

**Removing Videos:**
1. Hover over a video in the playlist
2. Click the remove button (X)
3. Confirm removal

### Playlist Settings

| Setting | Description |
|---------|-------------|
| **Title** | Display name for the playlist |
| **Description** | Optional description shown on playlist page |
| **Thumbnail** | Uses first video's thumbnail (auto) or custom |
| **Type** | playlist, collection, series, course |
| **Visibility** | public, private, unlisted |
| **Featured** | Show on homepage featured section |

### Playlist Types

| Type | Best For | Display |
|------|----------|---------|
| **Playlist** | Loose grouping | Grid layout |
| **Collection** | Related content | Grid layout |
| **Series** | Sequential viewing | Numbered list |
| **Course** | Learning paths | Numbered list with progress |

---

## Upload Tab

### Uploading a Video

1. **Select File:** Drag and drop or click to browse
2. **Enter Title:** Required, will be used in URLs
3. **Add Description:** Optional, shown on video page
4. **Select Category:** Optional, helps organize videos
5. **Click Upload:** Progress bar shows upload status

### Supported Formats

VLog accepts most video formats that FFmpeg can process:
- MP4, MKV, AVI, MOV, WebM
- Most codecs (H.264, H.265, VP9, AV1, etc.)

### Upload Limits

- **Maximum File Size:** 100 GB (configurable via `VLOG_MAX_UPLOAD_SIZE`)
- **Chunk Size:** 1 MB (for reliable uploads)

### After Upload

After successful upload:
1. Video appears in Videos tab with "pending" status
2. Worker picks up the job and begins transcoding
3. Status changes to "processing" with progress indicator
4. When complete, status changes to "ready"
5. Transcription runs automatically (if enabled)

---

## Workers Tab

Monitor distributed transcoding workers and job status.

### Worker Status

| Status | Description |
|--------|-------------|
| **Online** | Worker is connected and sending heartbeats |
| **Offline** | No heartbeat received (> 2 minutes) |
| **Busy** | Currently processing a job |
| **Idle** | Online but not processing |

### Worker Information

For each worker:
- **Name:** Worker identifier
- **Type:** nvidia, intel, or cpu
- **Current Job:** Video being processed (if any)
- **Progress:** Transcoding progress percentage
- **Last Heartbeat:** Time since last health check

### Real-Time Updates

The Workers tab uses Server-Sent Events (SSE) for real-time updates:
- Worker status changes immediately
- Progress updates every few seconds
- No manual refresh needed

### Job Queue

View the transcoding queue:
- **Pending Jobs:** Waiting for an available worker
- **Active Jobs:** Currently being processed
- **Queue Priority:** High, Normal, Low

### Worker Management

**Registering Workers:**
```bash
vlog worker register --name "k8s-worker-1"
```
Save the returned API key for the worker configuration.

**Revoking Workers:**
1. Find the worker in the list
2. Click "Revoke" to disable API key
3. Worker will be unable to claim new jobs

### Re-encode Queue Monitoring

The Workers tab shows the re-encode queue status:

| Section | Description |
|---------|-------------|
| **Pending** | Jobs waiting for workers |
| **In Progress** | Currently being re-encoded |
| **Completed** | Recently finished jobs |
| **Failed** | Jobs that encountered errors |

**Queue Actions:**
- Cancel pending jobs
- Retry failed jobs
- View job details and error messages

### Deployment History

Track container deployments and rolling updates:
- Deployment timestamp
- Image version deployed
- Rollout status
- Worker pod names

---

## Webhooks Tab

Configure webhook notifications for external integrations.

### Creating a Webhook

1. Click "New Webhook"
2. Enter webhook URL (HTTPS recommended)
3. Select events to subscribe to
4. Optionally add a description
5. Click "Create"

A unique secret key will be generated for signature verification.

### Webhook Events

| Event | Description |
|-------|-------------|
| `video.ready` | Transcoding completed |
| `video.processing` | Transcoding started |
| `video.failed` | Transcoding failed |
| `video.deleted` | Video soft-deleted |
| `video.restored` | Video restored from archive |
| `video.purged` | Video permanently deleted |
| `transcription.complete` | Captions generated |
| `transcription.failed` | Caption generation failed |
| `worker.connected` | Worker came online |
| `worker.disconnected` | Worker went offline |

### Managing Webhooks

**Testing:**
1. Open webhook details
2. Click "Test"
3. Select an event type
4. View the response

**Viewing Deliveries:**
1. Click on a webhook
2. See delivery history with status codes
3. View request/response details
4. Retry failed deliveries

**Status Indicators:**
- Green checkmark: Successful delivery
- Red X: Failed (will retry)
- Yellow clock: Pending retry
- Gray circle: Circuit breaker open

### Webhook Security

Each webhook has a unique secret for signature verification. See [CONFIGURATION.md](CONFIGURATION.md#webhook-notification-settings) for verification examples.

---

## Analytics Tab

View playback statistics and trends.

### Available Metrics

| Metric | Description |
|--------|-------------|
| **Total Views** | All-time video views |
| **Unique Viewers** | Distinct viewer count |
| **Watch Time** | Total hours watched |
| **Completion Rate** | Average video completion percentage |

### Time Periods

Filter analytics by:
- Last 24 hours
- Last 7 days
- Last 30 days
- All time

### Per-Video Analytics

Click on any video to see:
- View count over time
- Average watch duration
- Completion rate
- Geographic distribution (if available)

### Top Videos

See your most popular content:
- Most viewed
- Highest completion rate
- Most watch time

---

## Settings Tab

Configure runtime settings without restarting services.

### Watermark Settings

Add a watermark overlay to all video playback:

**Image Watermark:**
1. Enable watermark
2. Select "Image" type
3. Upload watermark image (PNG recommended)
4. Set position (corner or center)
5. Adjust opacity (0-100%)

**Text Watermark:**
1. Enable watermark
2. Select "Text" type
3. Enter watermark text
4. Set font size and color
5. Set position and opacity

### General Settings

Configurable settings include:

| Setting | Description |
|---------|-------------|
| **HLS Segment Duration** | Segment length in seconds |
| **Transcription Enabled** | Auto-transcribe new uploads |
| **Archive Retention** | Days before permanent deletion |
| **Worker Poll Interval** | How often workers check for jobs |

### Settings Persistence

- Settings are stored in the database
- Changes take effect within 60 seconds (cache TTL)
- No service restart required
- Previous values are logged for audit

---

## Mobile Support

The Admin UI is fully responsive and works on mobile devices:

### Mobile Features
- Collapsible navigation menu
- Touch-friendly controls
- Swipe gestures for video cards
- Mobile-optimized video preview

### Mobile Actions

On mobile, action buttons appear as floating action buttons (FAB):
- Edit video
- Delete video
- More options menu

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `/` | Focus search |
| `Esc` | Close modal/panel |
| `Enter` | Confirm action |
| `?` | Show keyboard shortcuts |

---

## Troubleshooting

### Videos Not Loading

1. Check browser console for errors
2. Verify Admin API is running: `curl http://localhost:9001/health`
3. Check authentication if enabled

### Upload Fails

1. Check file size limits
2. Verify storage directory is writable
3. Check Admin API logs: `journalctl -u vlog-admin -f`

### Worker Status Not Updating

1. Verify Worker API is running: `curl http://localhost:9002/api/health`
2. Check Redis connection (for real-time updates)
3. Fallback: Click "Refresh" button

### Settings Not Saving

1. Check database connection
2. Verify user has write permissions
3. Check browser network tab for error responses

---

## Security Best Practices

1. **Never expose port 9001 to the internet**
2. Use a VPN or internal network for admin access
3. Enable authentication via `VLOG_ADMIN_API_SECRET`
4. Rotate admin secrets periodically
5. Review audit logs regularly

---

## Related Documentation

- [API.md](API.md) - API endpoint reference
- [CONFIGURATION.md](CONFIGURATION.md) - All configuration options
- [MONITORING.md](MONITORING.md) - Prometheus metrics
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) - Common issues
