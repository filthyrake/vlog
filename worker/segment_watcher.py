"""
Segment Watcher for Streaming Upload (Issue #478).

Watches the output directory during FFmpeg transcoding and queues
completed segments for upload. Uses polling to detect when segment
files are fully written (stable file size).

Architecture (Ada's producer-consumer model):
    FFmpeg --writes--> Filesystem
                           |
    SegmentWatcher --polls--> asyncio.Queue(maxsize=10) --> UploadWorker
"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Set

logger = logging.getLogger(__name__)

# Polling interval in seconds (Ada's recommendation: 1000ms, not 500ms)
POLL_INTERVAL_SECONDS = 1.0

# Number of consecutive stable size polls before considering segment complete
STABLE_SIZE_POLLS = 2


@dataclass
class SegmentInfo:
    """Information about a segment file to upload."""

    filepath: Path
    quality: str
    filename: str
    size: int


class SegmentWatcher:
    """
    Watch output directory for new segments during transcoding.

    Polls the directory at regular intervals and detects when segment
    files are fully written (file size stable across consecutive polls).
    Queues completed segments for upload to the server.

    Features (per agent recommendations):
    - 1000ms polling interval (Ada: not 500ms)
    - Track file sizes across 2 consecutive polls for stability
    - Bounded queue (maxsize=10) for backpressure (Ada)
    - Handle both HLS/TS and CMAF formats
    - Detect FFmpeg crashes via process monitoring (Margo)

    Usage:
        watcher = SegmentWatcher(
            output_dir=Path("/tmp/transcode/video-slug"),
            quality_name="1080p",
            streaming_format="cmaf",
            upload_queue=asyncio.Queue(maxsize=10),
        )

        # Start watching in a task
        watcher_task = asyncio.create_task(watcher.watch())

        # ... FFmpeg transcoding runs ...

        # Stop watching when transcode completes
        await watcher.stop()
        await watcher_task
    """

    def __init__(
        self,
        output_dir: Path,
        quality_name: str,
        streaming_format: str,
        upload_queue: asyncio.Queue,
        poll_interval: float = POLL_INTERVAL_SECONDS,
    ):
        """
        Initialize the segment watcher.

        Args:
            output_dir: Directory where FFmpeg writes segments
            quality_name: Quality name (e.g., "1080p", "720p")
            streaming_format: Format being used ("cmaf" or "hls_ts")
            upload_queue: Queue to push completed segments to
            poll_interval: Polling interval in seconds (default: 1.0s)
        """
        self.output_dir = output_dir
        self.quality_name = quality_name
        self.streaming_format = streaming_format
        self.upload_queue = upload_queue
        self.poll_interval = poll_interval

        # Tracking state
        self._file_sizes: Dict[str, int] = {}  # filename -> size at last poll
        self._stable_counts: Dict[str, int] = {}  # filename -> consecutive stable polls
        self._queued_files: Set[str] = set()  # files already queued for upload
        self._running = False
        self._stop_event = asyncio.Event()

        # FFmpeg process monitoring (Margo's recommendation)
        self._ffmpeg_crashed = False

    @property
    def quality_dir(self) -> Path:
        """Get the quality subdirectory path."""
        if self.streaming_format == "cmaf":
            # CMAF uses subdirectories: output_dir/{quality}/
            return self.output_dir / self.quality_name
        else:
            # HLS/TS uses flat structure: output_dir/{quality}_*.ts
            return self.output_dir

    def _get_segment_files(self) -> Dict[str, int]:
        """
        Scan directory for segment files and their sizes.

        Returns:
            Dict mapping filename to file size in bytes
        """
        files = {}
        quality_dir = self.quality_dir

        if not quality_dir.exists():
            return files

        if self.streaming_format == "cmaf":
            # CMAF: init.mp4, seg_XXXX.m4s
            for f in quality_dir.iterdir():
                if f.is_file() and f.suffix in (".m4s", ".mp4"):
                    try:
                        files[f.name] = f.stat().st_size
                    except (OSError, FileNotFoundError):
                        # File was deleted between iterdir and stat
                        pass
        else:
            # HLS/TS: {quality}_XXXX.ts
            pattern = f"{self.quality_name}_*.ts"
            for f in self.output_dir.glob(pattern):
                if f.is_file():
                    try:
                        files[f.name] = f.stat().st_size
                    except (OSError, FileNotFoundError):
                        pass

        return files

    def _check_stable_segments(self) -> list[SegmentInfo]:
        """
        Check for segments with stable file sizes (ready for upload).

        Returns:
            List of SegmentInfo for segments ready to upload
        """
        ready_segments = []
        current_files = self._get_segment_files()

        for filename, current_size in current_files.items():
            # Skip already queued files
            if filename in self._queued_files:
                continue

            # Skip empty files (FFmpeg just created it)
            if current_size == 0:
                continue

            previous_size = self._file_sizes.get(filename, -1)

            if current_size == previous_size:
                # Size is stable, increment counter
                self._stable_counts[filename] = self._stable_counts.get(filename, 0) + 1

                if self._stable_counts[filename] >= STABLE_SIZE_POLLS:
                    # File is stable, ready for upload
                    quality_dir = self.quality_dir
                    filepath = quality_dir / filename

                    ready_segments.append(
                        SegmentInfo(
                            filepath=filepath,
                            quality=self.quality_name,
                            filename=filename,
                            size=current_size,
                        )
                    )
                    self._queued_files.add(filename)
                    logger.debug(f"Segment ready: {self.quality_name}/{filename} ({current_size} bytes)")
            else:
                # Size changed, reset counter
                self._stable_counts[filename] = 0

        # Update tracked sizes for next poll
        self._file_sizes = current_files

        return ready_segments

    async def watch(self) -> None:
        """
        Start watching for new segments.

        This coroutine runs until stop() is called. It polls the directory
        at regular intervals and queues completed segments for upload.

        If the upload queue is full (backpressure), the watcher will block
        on queue.put() which slows detection but doesn't affect FFmpeg.
        """
        self._running = True
        logger.info(
            f"Segment watcher started for {self.quality_name} "
            f"(format={self.streaming_format}, dir={self.quality_dir})"
        )

        try:
            while not self._stop_event.is_set():
                if self._ffmpeg_crashed:
                    logger.warning(f"FFmpeg crashed, stopping watcher for {self.quality_name}")
                    break

                # Check for stable segments
                ready_segments = self._check_stable_segments()

                # Queue ready segments for upload
                for segment in ready_segments:
                    # Put will block if queue is full (backpressure)
                    try:
                        await asyncio.wait_for(
                            self.upload_queue.put(segment),
                            timeout=self.poll_interval,
                        )
                    except asyncio.TimeoutError:
                        # Queue is full, we'll try again next poll
                        logger.debug(f"Upload queue full, will retry {segment.filename}")
                        self._queued_files.discard(segment.filename)

                # Wait for next poll or stop signal
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.poll_interval,
                    )
                    # Stop event was set
                    break
                except asyncio.TimeoutError:
                    # Normal timeout, continue polling
                    pass

        finally:
            self._running = False
            logger.info(
                f"Segment watcher stopped for {self.quality_name} "
                f"(queued {len(self._queued_files)} segments)"
            )

    async def stop(self) -> None:
        """
        Signal the watcher to stop.

        Call this after FFmpeg transcoding completes to stop the watcher.
        After calling stop(), you should await the watch() task to ensure
        clean shutdown.
        """
        self._stop_event.set()

    def notify_ffmpeg_crashed(self) -> None:
        """
        Notify watcher that FFmpeg process crashed (Margo's recommendation).

        When FFmpeg crashes, we should stop watching and not upload
        any more segments since they may be incomplete.
        """
        self._ffmpeg_crashed = True
        self._stop_event.set()
        logger.warning(f"FFmpeg crash notified for {self.quality_name}, stopping watcher")

    async def flush_remaining(self) -> list[SegmentInfo]:
        """
        Get any remaining segments that haven't been queued yet.

        Call this after stopping the watcher to ensure all completed
        segments are captured. This does a final scan with relaxed
        stability requirements for segments that might have been
        written just before FFmpeg finished.

        Returns:
            List of remaining SegmentInfo objects
        """
        if self._ffmpeg_crashed:
            # Don't upload anything if FFmpeg crashed
            return []

        remaining = []
        current_files = self._get_segment_files()

        for filename, size in current_files.items():
            if filename in self._queued_files:
                continue

            if size == 0:
                continue

            quality_dir = self.quality_dir
            filepath = quality_dir / filename

            remaining.append(
                SegmentInfo(
                    filepath=filepath,
                    quality=self.quality_name,
                    filename=filename,
                    size=size,
                )
            )
            self._queued_files.add(filename)
            logger.debug(f"Flushing remaining segment: {self.quality_name}/{filename}")

        return remaining

    @property
    def queued_count(self) -> int:
        """Return the number of segments queued for upload."""
        return len(self._queued_files)

    @property
    def is_running(self) -> bool:
        """Return True if the watcher is currently running."""
        return self._running
