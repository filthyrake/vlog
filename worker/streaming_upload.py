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
import logging
from pathlib import Path
from typing import Awaitable, Callable, List, Optional, Set, Tuple

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
    """

    def __init__(
        self,
        client: WorkerAPIClient,
        video_id: int,
        upload_queue: asyncio.Queue,
        on_segment_uploaded: Optional[Callable[[str, int], None]] = None,
    ):
        """
        Initialize the upload worker.

        Args:
            client: WorkerAPIClient for uploading segments
            video_id: Video ID for the upload endpoint
            upload_queue: Queue from which to consume SegmentInfo objects
            on_segment_uploaded: Optional callback(filename, bytes) called after each upload
        """
        self.client = client
        self.video_id = video_id
        self.upload_queue = upload_queue
        self.on_segment_uploaded = on_segment_uploaded

        # Tracking
        self._uploaded_segments: Set[str] = set()
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

        Raises:
            ClaimExpiredError: If server returns 409 (claim expired)
        """
        self._running = True
        logger.info(f"Segment upload worker started for video {self.video_id}")

        # Track retry counts per segment
        retry_counts: dict[str, int] = {}

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
            logger.info(
                f"Segment upload worker stopped for video {self.video_id} "
                f"({len(self._uploaded_segments)} uploaded, {failed_count} failed, "
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


async def streaming_transcode_and_upload_quality(
    client: WorkerAPIClient,
    video_id: int,
    output_dir: Path,
    quality_name: str,
    streaming_format: str,
    transcode_coro: Awaitable[Tuple[bool, Optional[str]]],
    on_segment_progress: Optional[Callable[[int, int], None]] = None,
) -> Tuple[bool, Optional[str], int]:
    """
    Transcode a quality with streaming segment upload (Issue #478).

    This function orchestrates:
    1. Start segment watcher
    2. Start upload worker
    3. Run transcode (provided as coroutine)
    4. Wait for upload queue to drain
    5. Upload final playlist
    6. Call finalize endpoint

    Args:
        client: WorkerAPIClient for uploading
        video_id: Video ID
        output_dir: Directory where FFmpeg writes output
        quality_name: Quality name (e.g., "1080p")
        streaming_format: Format ("cmaf" or "hls_ts")
        transcode_coro: Coroutine that performs the actual transcoding
        on_segment_progress: Optional callback(segments_completed, bytes_uploaded)
                            called after each segment upload for progress tracking

    Returns:
        Tuple of (success, error_message, segment_count)
    """
    # Create upload queue
    upload_queue: asyncio.Queue = asyncio.Queue(maxsize=UPLOAD_QUEUE_SIZE)

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
    segments_uploaded = [0]  # Use list to allow mutation in nested function
    total_bytes = [0]

    def segment_uploaded_callback(filename: str, bytes_uploaded: int) -> None:
        segments_uploaded[0] += 1
        total_bytes[0] += bytes_uploaded
        if on_segment_progress:
            try:
                on_segment_progress(segments_uploaded[0], total_bytes[0])
            except Exception as e:
                logger.warning(f"Segment progress callback failed: {e}")

    # Create upload worker
    upload_worker = SegmentUploadWorker(
        client=client,
        video_id=video_id,
        upload_queue=upload_queue,
        on_segment_uploaded=segment_uploaded_callback,
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

        logger.info(
            f"Quality {quality_name} streaming upload complete: "
            f"{upload_worker.uploaded_count} segments, "
            f"{upload_worker.total_bytes_uploaded} bytes"
        )

        return True, None, upload_worker.uploaded_count

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
