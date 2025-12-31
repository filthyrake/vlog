# Issue #478: Streaming Segment Upload - Progress Tracker

## Overview
Eliminate tar.gz blocking during large video transcoding by uploading segments individually as FFmpeg writes them.

**Branch:** `feature/478-streaming-segment-upload`
**Issue:** https://github.com/filthyrake/vlog/issues/478

---

## Phase Checklist

### Phase 1: Server-Side Segment Upload Endpoint
- [x] Add `SegmentQuality` enum for quality validation
- [x] Implement filename validation (reject null bytes, `..`, `/`, `\`, Unicode tricks)
- [x] Add magic byte validation for `.ts` (0x47) and `.m4s` (ftyp/moof/styp)
- [x] Implement atomic write (temp file + fsync + rename)
- [x] Add claim verification with DB lock (`FOR UPDATE`)
- [x] Implement `POST /api/worker/upload/{video_id}/segment/{quality}/{filename}`
- [x] Implement `GET /api/worker/upload/{video_id}/segments/status`
- [x] Implement `POST /api/worker/upload/{video_id}/segment/finalize`
- [x] Add idempotency handling (same segment uploaded twice = no error)
- [x] Track uploaded segments via quality_progress table
- [x] Add `VLOG_WORKER_STREAMING_UPLOAD` feature flag to `config.py`
- [ ] Unit tests for endpoint (deferred - will test with integration tests)

### Phase 2: Worker Segment Watcher
- [x] Create `worker/segment_watcher.py`
- [x] Implement 1000ms polling interval
- [x] Track file sizes across 2 consecutive polls
- [x] Handle HLS/TS (`.ts`) and CMAF (`.m4s`, `init.mp4`) formats
- [x] Implement bounded queue (maxsize=10)
- [x] Detect FFmpeg crashes (monitor process exit)
- [ ] Unit tests for segment watcher

### Phase 3: Upload Client Method
- [x] Add `upload_segment()` to `WorkerAPIClient`
- [x] Implement SHA256 checksum computation (caller computes, sent in header)
- [x] Add `X-Content-SHA256` header
- [x] Add 60s timeout
- [x] Implement exponential backoff retry (3 attempts)
- [x] Add `get_segments_status()` method
- [x] Add `finalize_quality_upload()` method
- [x] Handle HTTP 409 (claim expired)
- [ ] Unit tests for client methods

### Phase 4: Integrate into Transcoding Flow
- [x] Create `streaming_transcode_and_upload_quality()` function
- [x] Implement producer-consumer model
- [x] Start segment watcher task during transcode
- [x] Start upload worker task (`SegmentUploadWorker`)
- [x] Delete local files only after server confirms checksum
- [x] Extend claim with each upload (server-side)
- [x] Wait for upload queue to drain after transcode
- [x] Upload final playlist
- [x] Call finalize endpoint
- [ ] Integration tests
- [x] Wire into `remote_transcoder.py` with feature flag check

### Phase 5: Progress Tracking
- [x] Extend `QualityProgressUpdate` schema with `segments_total`, `segments_completed`
- [x] Update progress reporting to include segment counts
- [x] Verify no DB migration needed (uses existing quality_progress table)

### Phase 6: Error Handling & Resume
- [x] Implement transient error retry (3x with exponential backoff) - in client
- [x] Handle claim expired (409) - stop immediately (ClaimExpiredError)
- [x] Persist upload state to disk (`UploadStateManager`)
- [x] On worker restart, query server for received segments (`reconcile_with_server()`)
- [x] Resume upload from last missing segment (skip already-uploaded segments)

### Phase 7: Migration Path
- [x] Add `VLOG_WORKER_STREAMING_UPLOAD` feature flag to `config.py`
- [ ] Add setting to `api/settings.py` (optional, for dynamic toggle)
- [x] Ensure backward compatibility (new endpoints don't affect existing flow)
- [ ] Documentation for rollout

---

## Implementation Notes

### Session 1 - 2025-12-31
- Created branch `feature/478-streaming-segment-upload`
- Created this progress tracking document
- **Completed Phase 1: Server-Side Segment Upload Endpoint**
  - Added `SegmentQuality` class with enum-like validation (no regex per Bruce)
  - Added `SegmentUploadResponse`, `SegmentStatusResponse`, `SegmentFinalizeRequest`, `SegmentFinalizeResponse` schemas
  - Added helper functions:
    - `validate_segment_filename()` - security validation for filenames
    - `validate_segment_magic_bytes()` - validates .ts, .m4s, .mp4 magic bytes
    - `write_segment_atomic()` - atomic write with fsync for durability
  - Added three new endpoints:
    - `POST /api/worker/upload/{video_id}/segment/{quality}/{filename}` - upload single segment
    - `GET /api/worker/upload/{video_id}/segments/status` - get uploaded segments for resume
    - `POST /api/worker/upload/{video_id}/segment/finalize` - finalize quality upload
  - Added `VLOG_WORKER_STREAMING_UPLOAD` feature flag (default: false)
  - All code passes linter and imports successfully

- **Completed Phase 2: Worker Segment Watcher**
  - Created `worker/segment_watcher.py` with `SegmentWatcher` class
  - 1000ms polling interval (Ada's recommendation)
  - Tracks file sizes across 2 consecutive polls for stability detection
  - Handles both HLS/TS and CMAF formats
  - Bounded queue (maxsize=10) for backpressure
  - FFmpeg crash detection via `notify_ffmpeg_crashed()`
  - `flush_remaining()` method for final segment capture

- **Completed Phase 3: Upload Client Method**
  - Added to `WorkerAPIClient` in `worker/http_client.py`:
    - `upload_segment()` - upload single segment with checksum verification
    - `get_segments_status()` - query received segments for resume
    - `finalize_quality_upload()` - finalize quality upload
  - 60s timeout for segment uploads
  - Exponential backoff retry with circuit breaker support

- **Completed Phase 4: Streaming Upload Worker** (partial)
  - Created `worker/streaming_upload.py` with:
    - `ClaimExpiredError` exception class
    - `SegmentUploadWorker` class (consumer)
    - `streaming_transcode_and_upload_quality()` orchestration function
  - Producer-consumer model with asyncio.Queue
  - Deletes local files after server confirms checksum
  - Immediate abort on claim expiration (409)
  - Still need to wire into `remote_transcoder.py`

- **Code Review Fixes Applied:**
  - **Critical: Blocking I/O** - All file operations now use `asyncio.to_thread()` or thread pool executor
  - **Critical: Race condition** - Verify file size hasn't changed before upload
  - **Important: Retry mechanism** - Failed segments re-queued up to 3 times
  - **Important: Validation bounds** - `segment_count` limited to 0-100000
  - **Important: Path validation** - All endpoints use `resolve()` + `relative_to()`
  - **Important: Database transaction** - Finalize endpoint wraps DB ops in transaction
  - **Other: Type hints** - Fixed deprecated `asyncio.coroutine` to `Awaitable`

---

## Files Modified

| File | Phase | Status |
|------|-------|--------|
| `api/worker_api.py` | 1 | **Complete** |
| `api/worker_schemas.py` | 1, 5 | **Complete** |
| `config.py` | 1, 7 | **Complete** (feature flag added) |
| `worker/segment_watcher.py` | 2 | **Complete** (NEW) |
| `worker/http_client.py` | 3 | **Complete** |
| `worker/streaming_upload.py` | 4, 6 | **Complete** (NEW - added `UploadStateManager`) |
| `worker/remote_transcoder.py` | 4, 6 | **Complete** (integrated with feature flag + job_id) |

---

## API Endpoints Added (Phase 1)

### Upload Segment
```
POST /api/worker/upload/{video_id}/segment/{quality}/{filename}

Headers:
  X-Worker-API-Key: <key>
  Content-Type: application/octet-stream
  X-Content-SHA256: <sha256_hex>

Body: Raw segment bytes

Response 200:
{
  "status": "ok",
  "written": true,
  "bytes_written": 4521984,
  "checksum_verified": true
}
Response 400: Invalid filename / magic bytes validation failed
Response 403: Not your job
Response 409: Claim expired
```

### Get Segments Status
```
GET /api/worker/upload/{video_id}/segments/status?quality=1080p

Response 200:
{
  "quality": "1080p",
  "received_segments": ["init.mp4", "seg_0000.m4s", ...],
  "total_size_bytes": 1234567890
}
```

### Finalize Quality
```
POST /api/worker/upload/{video_id}/segment/finalize

Body:
{
  "quality": "1080p",
  "segment_count": 226,
  "manifest_checksum": "sha256:abc123..."
}

Response 200:
{
  "status": "ok",
  "complete": true,
  "missing_segments": []
}
Response 409: Missing segments - returns list of missing filenames
```

---

## Worker Components Added (Phases 2-4)

### SegmentWatcher (producer)
```python
from worker.segment_watcher import SegmentWatcher, SegmentInfo

watcher = SegmentWatcher(
    output_dir=Path("/tmp/transcode/video-slug"),
    quality_name="1080p",
    streaming_format="cmaf",
    upload_queue=asyncio.Queue(maxsize=10),
)

# Start watching
watcher_task = asyncio.create_task(watcher.watch())

# ... FFmpeg runs ...

# Stop and flush
await watcher.stop()
remaining = await watcher.flush_remaining()
```

### SegmentUploadWorker (consumer)
```python
from worker.streaming_upload import SegmentUploadWorker

worker = SegmentUploadWorker(
    client=api_client,
    video_id=123,
    upload_queue=queue,
)

upload_task = asyncio.create_task(worker.run())
# ... segments flow through queue ...
await worker.stop()
```

---

## Next Steps

1. ~~**Wire into remote_transcoder.py**: Add conditional check for `WORKER_STREAMING_UPLOAD` to use new streaming path~~ ✅ Done
2. ~~**Phase 5**: Add segment progress to `QualityProgressUpdate` schema~~ ✅ Done
3. ~~**Phase 5**: Update progress reporting to include segment counts in streaming upload path~~ ✅ Done
4. ~~**Phase 6**: Add resume support with disk-persisted state~~ ✅ Done
5. **Phase 7**: Documentation for rollout
6. **Testing**: Manual test with actual videos

---

### Session 2 - 2025-12-31
- **Wired streaming upload into remote_transcoder.py**:
  - Added conditional import for `streaming_transcode_and_upload_quality` when `WORKER_STREAMING_UPLOAD` is True
  - Added streaming upload path in `transcode_and_upload_quality()` inner function
  - Feature flag check: `if WORKER_STREAMING_UPLOAD and streaming_format == "cmaf"`
  - Streaming path handles: transcode coroutine creation, dimension extraction, quality_info building
  - Proper error handling for `StreamingClaimExpiredError`
- **Extended QualityProgressUpdate schema**:
  - Added `segments_total: Optional[int]` field
  - Added `segments_completed: Optional[int]` field
  - No DB migration needed (fields are optional and stored in JSON column)
- **Implemented segment progress reporting (Phase 5)**:
  - Added `on_segment_progress` callback parameter to `streaming_transcode_and_upload_quality()`
  - Callback receives `(segments_completed, bytes_uploaded)` after each segment upload
  - Wired callback in `remote_transcoder.py` to update `quality_progress_list` with segment counts
  - Rate-limited updates (every 2 seconds) to avoid API flooding
  - Uses `asyncio.create_task()` to schedule async updates from sync callback
  - Final status includes `segments_completed` and `segments_total`

### Session 3 - 2025-12-31
- **Completed Phase 6: Error Handling & Resume**
  - Added `UploadStateManager` class in `worker/streaming_upload.py`:
    - Persists upload state to JSON file: `{output_dir}/.upload_state.json`
    - Atomic writes with fsync for durability (per Margo's requirements)
    - Tracks uploaded segments per quality with timestamps
    - `reconcile_with_server()` method queries server to confirm what's actually uploaded
    - Handles discrepancies between local state and server state
  - Updated `SegmentUploadWorker` to support resume:
    - New parameters: `state_manager`, `already_uploaded`, `quality_name`
    - Skips segments already confirmed by server
    - Persists state after each successful upload
    - New `skipped_count` property for tracking resumed segments
  - Updated `streaming_transcode_and_upload_quality()`:
    - New parameters: `job_id` (required for state persistence), `enable_resume`
    - Loads existing state and reconciles with server on resume
    - Passes state manager and already_uploaded set to upload worker
    - Deletes state file on successful completion
    - Progress tracking starts from already_uploaded count when resuming
  - Updated `remote_transcoder.py`:
    - Passes `job_id` to `streaming_transcode_and_upload_quality()`

---

## Testing

- [ ] Manual test with small video (feature flag on)
- [ ] Manual test with large video (28GB+)
- [ ] Verify heartbeats stay alive
- [ ] Verify resume after simulated worker restart
- [ ] Load test with multiple concurrent uploads
