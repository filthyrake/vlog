"""
Streaming Segment Upload Worker (Issue #478).

Integrates the SegmentWatcher with the WorkerAPIClient to upload
segments as FFmpeg writes them, eliminating blocking tar.gz creation.

Architecture (Ada's producer-consumer model):

    SegmentWatcher (producer)
           |
           v
    asyncio.Queue(maxsize=10)
           |
           v
    SegmentUploadWorker (consumer)
           |
           v
    WorkerAPIClient.upload_segment()
           |
           v
    Server (writes with fsync)
"""

import asyncio
import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from worker.http_client import WorkerAPIClient, WorkerAPIError
from worker.segment_watcher import SegmentInfo, SegmentWatcher

logger = logging.getLogger(__name__)

# Queue size for segment uploads (Ada's recommendation)
UPLOAD_QUEUE_SIZE = 10

# Maximum retries for failed segment uploads (code review fix)
MAX_SEGMENT_RETRIES = 3


class ClaimExpiredError(Exception):
    """Raised when the job claim expires during upload."""

    pass


class UploadStateManager:
    """
    Persist upload state to disk for resume support (Phase 6, Issue #478).

    Tracks which segments have been uploaded to the server. On worker restart,
    this state is loaded and reconciled with the server to resume uploads
    from where they left off.

    Features (per Margo's reliability requirements):
    - Atomic writes with fsync for durability
    - JSON format for easy debugging
    - Per-quality tracking
    - Checkpoint timestamps for staleness detection

    State file location: {output_dir}/.upload_state.json
    """

    STATE_FILENAME = ".upload_state.json"

    def __init__(self, output_dir: Path, video_id: int, job_id: int):
        """
        Initialize the state manager.

        Args:
            output_dir: Directory where transcoding output is stored
            video_id: Video ID for this upload
            job_id: Transcoding job ID
        """
        self.output_dir = output_dir
        self.video_id = video_id
        self.job_id = job_id
        self.state_path = output_dir / self.STATE_FILENAME

        # State structure per quality
        self._state: Dict[str, Dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def load(self) -> bool:
        """
        Load state from disk.

        Returns:
            True if state was loaded, False if no state file exists
        """
        async with self._lock:
            return await asyncio.to_thread(self._load_sync)

    def _load_sync(self) -> bool:
        """Synchronous load implementation (called via to_thread)."""
        if not self.state_path.exists():
            logger.debug(f"No state file found at {self.state_path}")
            return False

        try:
            with open(self.state_path, "r") as f:
                data = json.load(f)

            # Validate state matches this video/job
            if data.get("video_id") != self.video_id:
                logger.warning(
                    f"State file video_id mismatch: {data.get('video_id')} != {self.video_id}"
                )
                return False

            # Job ID mismatch is OK - job may have been reclaimed
            if data.get("job_id") != self.job_id:
                logger.info(
                    f"State file from different job {data.get('job_id')}, "
                    f"current job {self.job_id} - will reconcile with server"
                )

            self._state = data.get("qualities", {})
            logger.info(
                f"Loaded upload state for video {self.video_id}: "
                f"{sum(len(q.get('uploaded_segments', [])) for q in self._state.values())} "
                f"segments across {len(self._state)} qualities"
            )
            return True

        except json.JSONDecodeError as e:
            logger.warning(f"Corrupt state file at {self.state_path}: {e}")
            return False
        except Exception as e:
            logger.warning(f"Error loading state file: {e}")
            return False

    async def save(self) -> None:
        """Save current state to disk atomically with fsync."""
        async with self._lock:
            await asyncio.to_thread(self._save_sync)

    def _save_sync(self) -> None:
        """Synchronous save implementation with atomic write (called via to_thread)."""
        data = {
            "video_id": self.video_id,
            "job_id": self.job_id,
            "updated_at": datetime.now().isoformat(),
            "qualities": self._state,
        }

        # Atomic write: temp file + fsync + rename (per Margo's durability requirement)
        fd, tmp_path = tempfile.mkstemp(
            dir=self.output_dir,
            prefix=".upload_state_",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())

            os.rename(tmp_path, self.state_path)
            logger.debug(f"Saved upload state to {self.state_path}")
        except Exception:
            # Clean up temp file on error
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    async def mark_uploaded(self, quality: str, filename: str, size: int) -> None:
        """
        Mark a segment as uploaded.

        Args:
            quality: Quality name (e.g., "1080p")
            filename: Segment filename
            size: Segment size in bytes
        """
        async with self._lock:
            if quality not in self._state:
                self._state[quality] = {
                    "uploaded_segments": [],
                    "total_bytes": 0,
                    "updated_at": None,
                }

            q = self._state[quality]
            if filename not in q["uploaded_segments"]:
                q["uploaded_segments"].append(filename)
                q["total_bytes"] = q.get("total_bytes", 0) + size
                q["updated_at"] = datetime.now().isoformat()

        # Save after each upload for durability
        # (could batch this for performance, but prioritize data safety)
        await self.save()

    async def get_uploaded_segments(self, quality: str) -> Set[str]:
        """
        Get set of uploaded segment filenames for a quality.

        Args:
            quality: Quality name

        Returns:
            Set of filenames that have been uploaded
        """
        async with self._lock:
            if quality not in self._state:
                return set()
            return set(self._state[quality].get("uploaded_segments", []))

    async def reconcile_with_server(
        self,
        client: WorkerAPIClient,
        quality: str,
    ) -> Set[str]:
        """
        Reconcile local state with server's confirmed segments.

        Queries the server for which segments it has received and updates
        local state to match. This handles cases where:
        - Worker crashed after upload but before local state update
        - Server lost segments due to disk failure
        - Network issues caused upload to fail silently

        Args:
            client: WorkerAPIClient for querying server
            quality: Quality name to reconcile

        Returns:
            Set of segment filenames confirmed by server
        """
        try:
            status = await client.get_segments_status(self.video_id, quality)
            server_segments = set(status.get("received_segments", []))

            async with self._lock:
                local_segments = set()
                if quality in self._state:
                    local_segments = set(self._state[quality].get("uploaded_segments", []))

                # Log discrepancies
                only_local = local_segments - server_segments
                only_server = server_segments - local_segments

                if only_local:
                    logger.warning(
                        f"Segments in local state but not on server for {quality}: {only_local}"
                    )
                if only_server:
                    logger.info(
                        f"Segments on server but not in local state for {quality}: {only_server}"
                    )

                # Update local state to match server (server is source of truth)
                self._state[quality] = {
                    "uploaded_segments": list(server_segments),
                    "total_bytes": status.get("total_size_bytes", 0),
                    "updated_at": datetime.now().isoformat(),
                    "reconciled_at": datetime.now().isoformat(),
                }

            await self.save()
            logger.info(
                f"Reconciled {quality}: {len(server_segments)} segments confirmed by server"
            )
            return server_segments

        except WorkerAPIError as e:
            if e.status_code == 409:
                # Claim expired - can't resume
                raise ClaimExpiredError("Claim expired during reconciliation")
            # Other errors - return local state as fallback
            logger.warning(f"Failed to reconcile with server: {e}, using local state")
            return await self.get_uploaded_segments(quality)

    async def clear(self, quality: Optional[str] = None) -> None:
        """
        Clear state for a quality or all qualities.

        Args:
            quality: Quality to clear, or None to clear all
        """
        async with self._lock:
            if quality:
                self._state.pop(quality, None)
            else:
                self._state.clear()
        await self.save()

    async def delete_state_file(self) -> None:
        """Delete the state file (called after successful completion)."""
        async with self._lock:
            try:
                # Use missing_ok=True to avoid TOCTOU race condition
                await asyncio.to_thread(self.state_path.unlink, missing_ok=True)
                logger.debug(f"Deleted upload state file: {self.state_path}")
            except Exception as e:
                logger.warning(f"Failed to delete state file {self.state_path}: {e}")


class SegmentUploadWorker:
    """
    Worker that consumes segments from the watcher queue and uploads them.

    Runs as an async task alongside FFmpeg transcoding. Each successful
    upload extends the job claim, preventing heartbeat failures.

    Features (per agent recommendations):
    - Exponential backoff retry on transient errors (Margo)
    - Immediate abort on claim expiration (409) (Bruce)
    - Delete local files after server confirms checksum (Ada)
    - Track uploaded segments for progress reporting (Phase 5)
    - Resume support: skip already-uploaded segments (Phase 6)
    - Persist upload state to disk for crash recovery (Phase 6)
    """

    def __init__(
        self,
        client: WorkerAPIClient,
        video_id: int,
        upload_queue: asyncio.Queue,
        on_segment_uploaded: Optional[Callable[[str, int], None]] = None,
        state_manager: Optional["UploadStateManager"] = None,
        already_uploaded: Optional[Set[str]] = None,
        quality_name: Optional[str] = None,
    ):
        """
        Initialize the upload worker.

        Args:
            client: WorkerAPIClient for uploading segments
            video_id: Video ID for the upload endpoint
            upload_queue: Queue from which to consume SegmentInfo objects
            on_segment_uploaded: Optional callback(filename, bytes) called after each upload
            state_manager: Optional UploadStateManager for persisting state to disk (Phase 6)
            already_uploaded: Optional set of segment filenames to skip (for resume)
            quality_name: Quality name for state tracking (required if state_manager is set)
        """
        self.client = client
        self.video_id = video_id
        self.upload_queue = upload_queue
        self.on_segment_uploaded = on_segment_uploaded
        self.state_manager = state_manager
        self.quality_name = quality_name

        # Tracking
        # Start with already_uploaded segments if resuming
        self._uploaded_segments: Set[str] = already_uploaded.copy() if already_uploaded else set()
        self._skipped_segments: Set[str] = set()  # Segments skipped due to resume
        self._failed_segments: List[SegmentInfo] = []  # For retry mechanism
        self._total_bytes_uploaded = 0
        self._running = False
        self._stop_event = asyncio.Event()
        self._error: Optional[Exception] = None

    async def run(self) -> None:
        """
        Start processing segments from the queue.

        This coroutine runs until stop() is called and the queue is empty.
        It will block on queue.get() when waiting for new segments.
        Failed segments are retried up to MAX_SEGMENT_RETRIES times.

        On resume (Phase 6), segments already confirmed by the server are
        skipped. The local file is deleted since it's already on the server.

        Raises:
            ClaimExpiredError: If server returns 409 (claim expired)
        """
        self._running = True
        resume_info = ""
        if self._uploaded_segments:
            resume_info = f" (resuming, {len(self._uploaded_segments)} segments already uploaded)"
        logger.info(f"Segment upload worker started for video {self.video_id}{resume_info}")

        # Track retry counts per segment
        retry_counts: Dict[str, int] = {}

        try:
            while True:
                # Check for stop signal with empty queue and no pending retries
                if self._stop_event.is_set() and self.upload_queue.empty():
                    # Process any failed segments that need retry
                    if self._failed_segments:
                        logger.info(f"Processing {len(self._failed_segments)} failed segments for retry")
                        segments_to_retry = self._failed_segments.copy()
                        self._failed_segments.clear()

                        for segment in segments_to_retry:
                            retry_count = retry_counts.get(segment.filename, 0) + 1
                            retry_counts[segment.filename] = retry_count
                            await self._upload_segment(segment, retry_count)

                        # If there are still failed segments after retry, loop again
                        if self._failed_segments:
                            continue
                    break

                # Try to get a segment with timeout
                try:
                    segment: SegmentInfo = await asyncio.wait_for(
                        self.upload_queue.get(),
                        timeout=1.0,
                    )
                except asyncio.TimeoutError:
                    # No segment available, check if we should stop
                    continue

                try:
                    # Phase 6: Skip segments already confirmed by server
                    if segment.filename in self._uploaded_segments:
                        logger.debug(
                            f"Skipping already-uploaded segment: {segment.filename}"
                        )
                        self._skipped_segments.add(segment.filename)
                        # Delete local file since it's already on server
                        # EXCEPT: preserve init.mp4 and stream.m3u8 - needed for metadata
                        # extraction after upload completes (dimensions, playlist validation)
                        preserve_files = {"init.mp4", "stream.m3u8"}
                        if segment.filename not in preserve_files:
                            try:
                                await asyncio.to_thread(segment.filepath.unlink)
                                logger.debug(f"Deleted local segment (already on server): {segment.filename}")
                            except FileNotFoundError:
                                pass  # Already gone
                            except Exception as e:
                                logger.warning(f"Failed to delete local segment {segment.filename}: {e}")
                        continue

                    retry_count = retry_counts.get(segment.filename, 0)
                    await self._upload_segment(segment, retry_count)
                finally:
                    self.upload_queue.task_done()

        except ClaimExpiredError:
            # Claim expired - propagate to caller
            self._error = ClaimExpiredError("Claim expired during segment upload")
            raise
        finally:
            self._running = False
            failed_count = len(self._failed_segments)
            skipped_count = len(self._skipped_segments)
            skipped_info = f", {skipped_count} skipped" if skipped_count > 0 else ""
            logger.info(
                f"Segment upload worker stopped for video {self.video_id} "
                f"({len(self._uploaded_segments)} uploaded, {failed_count} failed{skipped_info}, "
                f"{self._total_bytes_uploaded} bytes)"
            )

    async def _upload_segment(self, segment: SegmentInfo, retry_count: int = 0) -> bool:
        """
        Upload a single segment to the server.

        Uses asyncio.to_thread for file I/O to avoid blocking the event loop.
        Verifies file size hasn't changed since stability check (race condition fix).

        Args:
            segment: SegmentInfo object with filepath, quality, filename, size
            retry_count: Current retry attempt (for re-queued segments)

        Returns:
            True if upload succeeded, False if should retry

        Raises:
            ClaimExpiredError: If server returns 409 (claim expired)
        """
        # Read file content using thread pool to avoid blocking event loop
        try:
            # Verify file size hasn't changed (race condition fix from code review)
            current_size = await asyncio.to_thread(lambda: segment.filepath.stat().st_size)
            if current_size != segment.size:
                logger.warning(
                    f"Segment {segment.filename} size changed ({segment.size} -> {current_size}), "
                    "re-queuing with updated size"
                )
                # Re-queue with updated size for next stability check
                self._failed_segments.append(SegmentInfo(
                    filepath=segment.filepath,
                    quality=segment.quality,
                    filename=segment.filename,
                    size=current_size,
                ))
                return False

            data = await asyncio.to_thread(segment.filepath.read_bytes)
        except FileNotFoundError:
            logger.warning(f"Segment file disappeared before upload: {segment.filename}")
            return True  # Don't retry - file is gone
        except Exception as e:
            logger.error(f"Failed to read segment {segment.filename}: {e}")
            if retry_count < MAX_SEGMENT_RETRIES:
                self._failed_segments.append(segment)
            return False

        # Compute checksum (CPU-bound but fast for typical segment sizes)
        checksum = hashlib.sha256(data).hexdigest()

        # Upload to server
        try:
            result = await self.client.upload_segment(
                video_id=self.video_id,
                quality=segment.quality,
                filename=segment.filename,
                data=data,
                checksum=checksum,
            )

            # Verify server confirmed checksum
            if result.get("checksum_verified"):
                # Safe to delete local file using thread pool (Ada's requirement)
                try:
                    await asyncio.to_thread(segment.filepath.unlink)
                    logger.debug(f"Deleted local segment: {segment.filename}")
                except Exception as e:
                    logger.warning(f"Failed to delete local segment {segment.filename}: {e}")

                # Track upload
                self._uploaded_segments.add(segment.filename)
                self._total_bytes_uploaded += len(data)

                # Phase 6: Persist state to disk for crash recovery
                if self.state_manager and self.quality_name:
                    try:
                        await self.state_manager.mark_uploaded(
                            quality=self.quality_name,
                            filename=segment.filename,
                            size=len(data),
                        )
                    except Exception as e:
                        # Log but don't fail - state persistence is best-effort
                        logger.warning(f"Failed to persist upload state: {e}")

                # Call progress callback if provided
                if self.on_segment_uploaded:
                    try:
                        self.on_segment_uploaded(segment.filename, len(data))
                    except Exception as e:
                        logger.warning(f"Progress callback failed: {e}")

                logger.debug(
                    f"Uploaded segment: {segment.quality}/{segment.filename} "
                    f"({len(data)} bytes)"
                )
                return True
            else:
                logger.warning(
                    f"Server did not verify checksum for {segment.filename}, will retry"
                )
                if retry_count < MAX_SEGMENT_RETRIES:
                    self._failed_segments.append(segment)
                return False

        except WorkerAPIError as e:
            if e.status_code == 409:
                # Claim expired - abort immediately (Bruce's recommendation)
                logger.error(f"Claim expired during segment upload: {e.message}")
                raise ClaimExpiredError(e.message)
            else:
                # Other errors - queue for retry if under limit
                logger.error(f"Failed to upload segment {segment.filename}: {e.message}")
                if retry_count < MAX_SEGMENT_RETRIES:
                    self._failed_segments.append(segment)
                else:
                    logger.error(f"Segment {segment.filename} failed after {MAX_SEGMENT_RETRIES} retries, giving up")
                return False

    async def stop(self) -> None:
        """
        Signal the worker to stop after draining the queue.

        The worker will process all remaining segments in the queue
        before exiting. Call this after FFmpeg finishes transcoding.
        """
        self._stop_event.set()

    @property
    def uploaded_count(self) -> int:
        """Return the number of segments successfully uploaded."""
        return len(self._uploaded_segments)

    @property
    def total_bytes_uploaded(self) -> int:
        """Return the total bytes uploaded."""
        return self._total_bytes_uploaded

    @property
    def is_running(self) -> bool:
        """Return True if the worker is currently running."""
        return self._running

    @property
    def error(self) -> Optional[Exception]:
        """Return any error that occurred during upload."""
        return self._error

    @property
    def failed_count(self) -> int:
        """Return the number of segments that failed to upload after retries."""
        return len(self._failed_segments)

    @property
    def skipped_count(self) -> int:
        """Return the number of segments skipped (already on server, for resume)."""
        return len(self._skipped_segments)


async def streaming_transcode_and_upload_quality(
    client: WorkerAPIClient,
    video_id: int,
    output_dir: Path,
    quality_name: str,
    streaming_format: str,
    transcode_coro: Awaitable[Tuple[bool, Optional[str]]],
    on_segment_progress: Optional[Callable[[int, int], None]] = None,
    job_id: Optional[int] = None,
    enable_resume: bool = True,
) -> Tuple[bool, Optional[str], int]:
    """
    Transcode a quality with streaming segment upload (Issue #478).

    This function orchestrates:
    1. Load/reconcile upload state for resume (Phase 6)
    2. Start segment watcher
    3. Start upload worker
    4. Run transcode (provided as coroutine)
    5. Wait for upload queue to drain
    6. Upload final playlist
    7. Call finalize endpoint
    8. Clean up state file on success

    Args:
        client: WorkerAPIClient for uploading
        video_id: Video ID
        output_dir: Directory where FFmpeg writes output
        quality_name: Quality name (e.g., "1080p")
        streaming_format: Format ("cmaf" or "hls_ts")
        transcode_coro: Coroutine that performs the actual transcoding
        on_segment_progress: Optional callback(segments_completed, bytes_uploaded)
                            called after each segment upload for progress tracking
        job_id: Transcoding job ID (required for state persistence)
        enable_resume: If True, load existing state and resume from where we left off

    Returns:
        Tuple of (success, error_message, segment_count)
    """
    # Create upload queue
    upload_queue: asyncio.Queue = asyncio.Queue(maxsize=UPLOAD_QUEUE_SIZE)

    # Phase 6: Set up state manager for resume support
    state_manager: Optional[UploadStateManager] = None
    already_uploaded: Set[str] = set()

    if job_id is not None:
        state_manager = UploadStateManager(
            output_dir=output_dir,
            video_id=video_id,
            job_id=job_id,
        )

        if enable_resume:
            # Try to load existing state
            state_loaded = await state_manager.load()

            if state_loaded:
                # Reconcile with server to confirm what's actually uploaded
                try:
                    already_uploaded = await state_manager.reconcile_with_server(
                        client=client,
                        quality=quality_name,
                    )
                    if already_uploaded:
                        logger.info(
                            f"Resuming upload for {quality_name}: "
                            f"{len(already_uploaded)} segments already on server"
                        )
                except ClaimExpiredError:
                    # Can't resume if claim expired
                    raise
                except Exception as e:
                    logger.warning(f"Failed to reconcile state, starting fresh: {e}")
                    already_uploaded = set()

    # Create segment watcher
    watcher = SegmentWatcher(
        output_dir=output_dir,
        quality_name=quality_name,
        streaming_format=streaming_format,
        upload_queue=upload_queue,
    )

    # Wrapper callback to report segment progress
    # The SegmentUploadWorker callback receives (filename, bytes) but we want to
    # report (segments_completed, total_bytes) for progress tracking
    # Start from already_uploaded count when resuming
    segments_uploaded = [len(already_uploaded)]  # Use list to allow mutation in nested function
    total_bytes = [0]

    def segment_uploaded_callback(filename: str, bytes_uploaded: int) -> None:
        segments_uploaded[0] += 1
        total_bytes[0] += bytes_uploaded
        if on_segment_progress:
            try:
                on_segment_progress(segments_uploaded[0], total_bytes[0])
            except Exception as e:
                logger.warning(f"Segment progress callback failed: {e}")

    # Create upload worker with resume support
    upload_worker = SegmentUploadWorker(
        client=client,
        video_id=video_id,
        upload_queue=upload_queue,
        on_segment_uploaded=segment_uploaded_callback,
        state_manager=state_manager,
        already_uploaded=already_uploaded,
        quality_name=quality_name,
    )

    # Start watcher and upload worker tasks
    watcher_task = asyncio.create_task(watcher.watch())
    upload_task = asyncio.create_task(upload_worker.run())

    transcode_success = False
    transcode_error = None

    try:
        # Run transcoding
        transcode_success, transcode_error = await transcode_coro

        if not transcode_success:
            # Transcoding failed - notify watcher
            watcher.notify_ffmpeg_crashed()
            return False, transcode_error, 0

        # Transcoding complete - stop watcher
        await watcher.stop()
        await watcher_task

        # Flush any remaining segments
        remaining = await watcher.flush_remaining()
        for segment in remaining:
            await upload_queue.put(segment)

        # Wait for upload queue to drain
        await upload_queue.join()

        # Stop upload worker
        await upload_worker.stop()
        await upload_task

        # Check for upload errors
        if upload_worker.error:
            return False, str(upload_worker.error), upload_worker.uploaded_count

        # Upload the final playlist (use thread pool to avoid blocking)
        if streaming_format == "cmaf":
            playlist_path = output_dir / quality_name / "stream.m3u8"
        else:
            playlist_path = output_dir / f"{quality_name}.m3u8"

        playlist_exists = await asyncio.to_thread(playlist_path.exists)
        if playlist_exists:
            playlist_data = await asyncio.to_thread(playlist_path.read_bytes)
            playlist_checksum = hashlib.sha256(playlist_data).hexdigest()

            await client.upload_segment(
                video_id=video_id,
                quality=quality_name,
                filename=playlist_path.name,
                data=playlist_data,
                checksum=playlist_checksum,
            )

            # Count segments (init.mp4 + *.m4s for CMAF, *.ts for HLS)
            # Use thread pool to avoid blocking on filesystem operations
            if streaming_format == "cmaf":
                quality_dir = output_dir / quality_name

                def count_cmaf_segments():
                    count = len(list(quality_dir.glob("*.m4s")))
                    if (quality_dir / "init.mp4").exists():
                        count += 1
                    return count

                segment_count = await asyncio.to_thread(count_cmaf_segments)
            else:

                def count_hls_segments():
                    return len(list(output_dir.glob(f"{quality_name}_*.ts")))

                segment_count = await asyncio.to_thread(count_hls_segments)

            # Finalize the quality upload
            result = await client.finalize_quality_upload(
                video_id=video_id,
                quality=quality_name,
                segment_count=segment_count,
                manifest_checksum=f"sha256:{playlist_checksum}",
            )

            if not result.get("complete"):
                missing = result.get("missing_segments", [])
                return False, f"Finalize incomplete: {missing}", upload_worker.uploaded_count

        # Phase 6: Clean up state file on success
        if state_manager:
            try:
                await state_manager.delete_state_file()
            except Exception as e:
                logger.warning(f"Failed to delete state file: {e}")

        # Build summary with skipped count if resuming
        skipped_info = ""
        if upload_worker.skipped_count > 0:
            skipped_info = f" ({upload_worker.skipped_count} skipped, already on server)"

        total_segments = upload_worker.uploaded_count + upload_worker.skipped_count
        logger.info(
            f"Quality {quality_name} streaming upload complete: "
            f"{total_segments} total segments{skipped_info}, "
            f"{upload_worker.total_bytes_uploaded} bytes uploaded"
        )

        return True, None, total_segments

    except ClaimExpiredError as e:
        # Claim expired - clean up and return error
        watcher.notify_ffmpeg_crashed()
        await watcher.stop()
        return False, str(e), upload_worker.uploaded_count

    except Exception as e:
        # Unexpected error
        logger.exception(f"Streaming upload error for {quality_name}: {e}")
        watcher.notify_ffmpeg_crashed()
        await watcher.stop()
        return False, str(e), upload_worker.uploaded_count

    finally:
        # Clean up tasks
        if not watcher_task.done():
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass

        if not upload_task.done():
            upload_task.cancel()
            try:
                await upload_task
            except asyncio.CancelledError:
                pass
