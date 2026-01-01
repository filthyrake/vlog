# VLog Admin UI Guide

The VLog Admin UI provides a modern, responsive interface for managing your video platform. This guide covers all features and common workflows.

## Accessing the Admin UI

**URL:** `http://your-server:9001`

If authentication is enabled (`VLOG_ADMIN_API_SECRET` is set), you'll be prompted to log in. The admin panel is intended for internal use and should not be exposed to the public internet.

---

## Navigation

The admin interface has six main tabs:

| Tab | Purpose |
|-----|---------|
| **Videos** | Browse, search, edit, and manage all videos |
| **Categories** | Create and manage video categories |
| **Upload** | Upload new videos with metadata |
| **Workers** | Monitor transcoding workers and job status |
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

### Deployment History

Track container deployments and rolling updates:
- Deployment timestamp
- Image version deployed
- Rollout status

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
