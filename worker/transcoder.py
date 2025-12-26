#!/usr/bin/env python3
"""
Video transcoding worker with checkpoint-based resumable transcoding.
Monitors the uploads directory for new videos and transcodes them to HLS.
Uses filesystem watching (inotify) for event-driven processing instead of polling.
Supports crash recovery and per-quality progress tracking.
"""

import asyncio
import json
import logging
import math
import re
import shutil
import signal
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional, Tuple

import sqlalchemy as sa

if TYPE_CHECKING:
    from worker.hwaccel import GPUCapabilities

# Import at runtime for parallel session calculation
from api.common import ensure_utc, validate_slug
from api.database import (
    configure_database,
    database,
    playback_sessions,
    quality_progress,
    transcoding_jobs,
    transcriptions,
    video_qualities,
    videos,
)
from api.db_retry import DatabaseRetryableError, execute_with_retry, fetch_one_with_retry
from api.enums import QualityStatus, TranscodingStep, VideoStatus
from api.errors import truncate_error

# Import config for backwards compatibility and fallback values
from config import (
    ARCHIVE_DIR,
    ARCHIVE_RETENTION_DAYS,
    CLEANUP_PARTIAL_ON_FAILURE,
    CLEANUP_SOURCE_ON_PERMANENT_FAILURE,
    ERROR_DETAIL_MAX_LENGTH,
    ERROR_SUMMARY_MAX_LENGTH,
    FFMPEG_TIMEOUT_BASE_MULTIPLIER,
    FFMPEG_TIMEOUT_MAXIMUM,
    FFMPEG_TIMEOUT_MINIMUM,
    FFMPEG_TIMEOUT_RESOLUTION_MULTIPLIERS,
    HLS_SEGMENT_DURATION,
    JOB_STALE_TIMEOUT,
    KEEP_COMPLETED_QUALITIES,
    PROGRESS_UPDATE_INTERVAL,
    QUALITY_NAMES,
    QUALITY_PRESETS,
    SUPPORTED_VIDEO_EXTENSIONS,
    UPLOADS_DIR,
    VIDEOS_DIR,
    WORKER_CLAIM_DURATION_MINUTES,
    WORKER_DEBOUNCE_DELAY,
    WORKER_FALLBACK_POLL_INTERVAL,
    WORKER_USE_FILESYSTEM_WATCHER,
)
from worker.alerts import (
    alert_job_failed,
    alert_max_retries_exceeded,
    alert_stale_job_recovered,
    alert_worker_shutdown,
    alert_worker_startup,
    send_alert_fire_and_forget,
)
from worker.alerts import (
    get_metrics as get_alert_metrics,
)
from worker.hwaccel import (
    StreamingFormat,  # noqa: F401 - Used in future CMAF transcoding integration
    VideoCodec,
    get_codec_string,
    get_recommended_parallel_sessions,
)

# Conditional import for filesystem watching
if WORKER_USE_FILESYSTEM_WATCHER:
    try:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        WATCHDOG_AVAILABLE = True
    except ImportError:
        print("Warning: watchdog not installed. Falling back to polling mode.")
        print("Install with: pip install watchdog")
        WATCHDOG_AVAILABLE = False
else:
    WATCHDOG_AVAILABLE = False

logger = logging.getLogger(__name__)

# Maximum video duration allowed (1 week in seconds)
MAX_DURATION_SECONDS = 7 * 24 * 60 * 60  # 604800 seconds

# Cached transcoding settings (refreshed periodically)
_cached_transcoder_settings: Dict[str, Any] = {}
_cached_transcoder_settings_time: float = 0
_TRANSCODER_SETTINGS_CACHE_TTL = 60  # Refresh every 60 seconds


async def get_transcoder_settings() -> Dict[str, Any]:
    """
    Get transcoding settings from database with caching and env var fallback.

    Settings are cached locally for 60 seconds to avoid database round-trips
    on every transcoding operation. The cache is separate from the main
    SettingsService cache to minimize import dependencies in the worker.

    Returns:
        Dict with keys:
        - hls_segment_duration: HLS segment duration in seconds
        - progress_update_interval: How often to update progress (seconds)
        - job_stale_timeout: Time before a job is considered stale (seconds)
        - cleanup_partial_on_failure: Whether to clean up partial files on failure
        - keep_completed_qualities: Whether to keep completed qualities on failure
        - ffmpeg_timeout_multiplier: Base multiplier for ffmpeg timeout
        - ffmpeg_timeout_minimum: Minimum ffmpeg timeout (seconds)
        - ffmpeg_timeout_maximum: Maximum ffmpeg timeout (seconds)
        - fallback_poll_interval: Polling interval when filesystem watcher unavailable
        - debounce_delay: Debounce delay for filesystem events (seconds)

    Falls back to environment variables (via config.py) if database is unavailable.
    """
    global _cached_transcoder_settings, _cached_transcoder_settings_time

    now = time.time()
    if _cached_transcoder_settings and (now - _cached_transcoder_settings_time) < _TRANSCODER_SETTINGS_CACHE_TTL:
        return _cached_transcoder_settings

    try:
        from api.settings_service import get_settings_service

        service = get_settings_service()

        # Fetch settings with fallback to config values
        settings = {
            "hls_segment_duration": await service.get("transcoding.hls_segment_duration", HLS_SEGMENT_DURATION),
            "progress_update_interval": await service.get("workers.progress_update_interval", PROGRESS_UPDATE_INTERVAL),
            "job_stale_timeout": await service.get("transcoding.job_stale_timeout", JOB_STALE_TIMEOUT),
            "cleanup_partial_on_failure": await service.get(
                "transcoding.cleanup_partial_on_failure", CLEANUP_PARTIAL_ON_FAILURE
            ),
            "keep_completed_qualities": await service.get(
                "transcoding.keep_completed_qualities", KEEP_COMPLETED_QUALITIES
            ),
            "ffmpeg_timeout_multiplier": await service.get(
                "transcoding.ffmpeg_timeout_multiplier", FFMPEG_TIMEOUT_BASE_MULTIPLIER
            ),
            "ffmpeg_timeout_minimum": await service.get("transcoding.ffmpeg_timeout_minimum", FFMPEG_TIMEOUT_MINIMUM),
            "ffmpeg_timeout_maximum": await service.get("transcoding.ffmpeg_timeout_maximum", FFMPEG_TIMEOUT_MAXIMUM),
            "fallback_poll_interval": await service.get(
                "workers.fallback_poll_interval", WORKER_FALLBACK_POLL_INTERVAL
            ),
            "debounce_delay": await service.get("workers.debounce_delay", WORKER_DEBOUNCE_DELAY),
            # Streaming format settings (Issue #212)
            "streaming_format": await service.get("streaming.default_format", "hls_ts"),
            "streaming_codec": await service.get("streaming.default_codec", "h264"),
            "streaming_enable_dash": await service.get("streaming.enable_dash", True),
            "streaming_segment_duration": await service.get("streaming.segment_duration", 6),
        }
        _cached_transcoder_settings = settings
        _cached_transcoder_settings_time = now
    except Exception as e:
        # Fall back to config values on error
        logger.debug(f"Failed to get transcoder settings from DB: {e}")
        _cached_transcoder_settings = {
            "hls_segment_duration": HLS_SEGMENT_DURATION,
            "progress_update_interval": PROGRESS_UPDATE_INTERVAL,
            "job_stale_timeout": JOB_STALE_TIMEOUT,
            "cleanup_partial_on_failure": CLEANUP_PARTIAL_ON_FAILURE,
            "keep_completed_qualities": KEEP_COMPLETED_QUALITIES,
            "ffmpeg_timeout_multiplier": FFMPEG_TIMEOUT_BASE_MULTIPLIER,
            "ffmpeg_timeout_minimum": FFMPEG_TIMEOUT_MINIMUM,
            "ffmpeg_timeout_maximum": FFMPEG_TIMEOUT_MAXIMUM,
            "fallback_poll_interval": WORKER_FALLBACK_POLL_INTERVAL,
            "debounce_delay": WORKER_DEBOUNCE_DELAY,
            # Streaming format defaults
            "streaming_format": "hls_ts",
            "streaming_codec": "h264",
            "streaming_enable_dash": True,
            "streaming_segment_duration": 6,
        }
        _cached_transcoder_settings_time = now

    return _cached_transcoder_settings


def reset_transcoder_settings_cache() -> None:
    """Reset the cached transcoder settings. Useful for testing."""
    global _cached_transcoder_settings, _cached_transcoder_settings_time
    _cached_transcoder_settings = {}
    _cached_transcoder_settings_time = 0


def group_qualities_by_resolution(qualities: List[dict], parallel_count: int) -> List[List[dict]]:
    """
    Group qualities into batches for parallel encoding.

    Groups high-res qualities (>= 1080p) together and low-res (< 1080p) together,
    then chunks each group by the parallel count. This keeps similar workloads
    together for better memory usage.

    Args:
        qualities: List of quality preset dicts with 'name' and 'height' keys
        parallel_count: Maximum number of qualities per batch

    Returns:
        List of quality batches, each containing up to parallel_count qualities
    """
    if parallel_count <= 1:
        # Sequential mode: each quality in its own batch
        return [[q] for q in qualities]

    # Split into high-res (1080p+) and low-res groups
    high_res = [q for q in qualities if q["height"] >= 1080]
    low_res = [q for q in qualities if q["height"] < 1080]

    batches = []

    # Create batches from high-res first, then low-res
    for group in [high_res, low_res]:
        for i in range(0, len(group), parallel_count):
            batch = group[i : i + parallel_count]
            if batch:
                batches.append(batch)

    return batches


class WorkerState:
    """
    Encapsulates mutable state for a transcoder worker instance.

    This class replaces global variables with instance state, enabling:
    - Easy test isolation with fresh instances
    - Dependency injection for mocking
    - Clear lifecycle management
    - Multiple workers in same process for testing

    Related Issue: #159
    """

    def __init__(self, worker_id: Optional[str] = None):
        """
        Initialize worker state.

        Args:
            worker_id: Unique identifier for this worker. If not provided,
                       a UUID will be generated.
        """
        self.worker_id = worker_id or str(uuid.uuid4())
        self.shutdown_requested = False
        self.new_upload_event: Optional[asyncio.Event] = None
        self.gpu_caps: Optional["GPUCapabilities"] = None

    def request_shutdown(self):
        """Request graceful shutdown of the worker."""
        self.shutdown_requested = True
        # Wake up any waiting tasks
        if self.new_upload_event is not None:
            self.new_upload_event.set()

    def reset(self):
        """Reset state for testing purposes.

        Note: worker_id is intentionally not reset to maintain worker identity
        across test state resets.
        """
        self.shutdown_requested = False
        self.new_upload_event = None
        self.gpu_caps = None


# Default worker state instance (for backward compatibility)
# New code should create WorkerState instances directly for better testability
_default_worker_state: Optional[WorkerState] = None


def get_worker_state() -> WorkerState:
    """Get the default worker state, creating it if necessary."""
    global _default_worker_state
    if _default_worker_state is None:
        _default_worker_state = WorkerState()
    return _default_worker_state


def set_worker_state(state: WorkerState):
    """Set the default worker state (useful for testing)."""
    global _default_worker_state
    _default_worker_state = state


class ProgressTracker:
    """
    Rate-limits progress updates to prevent database overload during transcoding.
    Only writes to database if enough time has passed since the last update.
    """

    def __init__(self, min_interval: float = PROGRESS_UPDATE_INTERVAL):
        self.min_interval = min_interval
        self.last_update_time: float = 0
        self.last_job_progress: int = -1
        self.last_quality_progress: dict = {}  # quality_name -> progress

    async def update_job(self, job_id: int, progress: int) -> bool:
        """
        Update job progress if enough time has passed.
        Returns True if update was written, False if rate-limited.
        """
        now = time.time()

        # Always update if progress is 100 (completion) or significantly changed
        if progress == 100 or (now - self.last_update_time >= self.min_interval):
            if progress != self.last_job_progress:
                await update_job_progress(job_id, progress)
                self.last_update_time = now
                self.last_job_progress = progress
                return True
        return False

    async def update_quality(self, job_id: int, quality_name: str, progress: int) -> bool:
        """
        Update quality progress if enough time has passed.
        Returns True if update was written, False if rate-limited.
        """
        now = time.time()

        last_progress = self.last_quality_progress.get(quality_name, -1)

        # Always update if progress is 100 (completion) or enough time passed
        if progress == 100 or (now - self.last_update_time >= self.min_interval):
            if progress != last_progress:
                await update_quality_progress(job_id, quality_name, progress)
                self.last_update_time = now
                self.last_quality_progress[quality_name] = progress
                return True
        return False

    async def flush(self, job_id: int, progress: int):
        """Force write the final progress value."""
        if progress != self.last_job_progress:
            await update_job_progress(job_id, progress)
            self.last_job_progress = progress


def calculate_ffmpeg_timeout(duration: float, height: int = 1080) -> float:
    """
    Calculate appropriate timeout for ffmpeg transcoding based on video duration and resolution.

    Higher resolutions take longer to encode, so timeouts scale accordingly.

    Args:
        duration: Video duration in seconds
        height: Target resolution height (e.g., 360, 720, 1080, 2160)

    Returns:
        Timeout in seconds, clamped between min and max values
    """
    # Get resolution multiplier (default to 2.0 for unknown resolutions)
    resolution_multiplier = FFMPEG_TIMEOUT_RESOLUTION_MULTIPLIERS.get(height, 2.0)
    effective_multiplier = FFMPEG_TIMEOUT_BASE_MULTIPLIER * resolution_multiplier
    timeout = duration * effective_multiplier
    return max(FFMPEG_TIMEOUT_MINIMUM, min(timeout, FFMPEG_TIMEOUT_MAXIMUM))


async def cleanup_ffmpeg_process(process: asyncio.subprocess.Process, context: str = "FFmpeg") -> None:
    """
    Clean up an FFmpeg subprocess, handling race conditions where the process
    may exit between checking returncode and calling kill().

    Args:
        process: The asyncio subprocess to clean up
        context: Description for logging (e.g., "FFmpeg", "FFmpeg remux")
    """
    if process.returncode is None:
        try:
            process.kill()
        except (ProcessLookupError, OSError):
            # Process already terminated - this is expected in race conditions
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            print(f"  WARNING: {context} process did not terminate after kill")


async def run_ffmpeg_with_progress(
    cmd: List[str],
    duration: float,
    timeout: float,
    progress_callback: Optional[Callable[[int], Awaitable[None]]] = None,
    context: str = "FFmpeg",
) -> Tuple[bool, Optional[str]]:
    """
    Run an FFmpeg command with timeout and progress tracking.

    This is the shared implementation for both transcoding and remuxing operations.
    It handles:
    - Process spawning with progress output on stdout
    - Progress parsing from FFmpeg's progress output format
    - Timeout handling with graceful process termination
    - Proper cleanup on any exit path

    Args:
        cmd: FFmpeg command as list of arguments
        duration: Video duration in seconds (for progress calculation)
        timeout: Maximum time to wait for FFmpeg to complete
        progress_callback: Optional async callback for progress updates (0-100)
        context: Description for logging (e.g., "FFmpeg", "FFmpeg remux")

    Returns:
        Tuple[bool, Optional[str]]: (success, error_message) where error_message
        is None on success or contains the error details on failure.
    """
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,  # Don't capture stderr - it fills pipe and blocks ffmpeg
    )

    last_progress_update = 0
    start_time = asyncio.get_running_loop().time()
    timed_out = False

    async def read_progress():
        """Read and parse ffmpeg progress output."""
        nonlocal last_progress_update
        while True:
            line = await process.stdout.readline()
            if not line:
                break

            line_str = line.decode("utf-8", errors="ignore").strip()

            # Parse time from progress output (format: out_time_ms=123456789)
            if line_str.startswith("out_time_ms="):
                try:
                    time_ms = int(line_str.split("=")[1])
                    current_seconds = time_ms / 1000000.0
                    if duration > 0:
                        progress = min(100, int(current_seconds / duration * 100))
                        # Only update if progress changed significantly
                        if progress > last_progress_update:
                            last_progress_update = progress
                            if progress_callback:
                                await progress_callback(progress)
                except (ValueError, IndexError):
                    # Ignore malformed progress lines - continue reading ffmpeg output
                    pass

    async def drain_and_wait():
        """Read all output and wait for process to complete."""
        await read_progress()
        await process.wait()

    async def timeout_killer():
        """Kill process after timeout."""
        nonlocal timed_out
        await asyncio.sleep(timeout)
        timed_out = True
        elapsed = asyncio.get_running_loop().time() - start_time
        print(f"  TIMEOUT: {context} exceeded {timeout:.0f}s limit (ran for {elapsed:.0f}s)")
        try:
            process.kill()
        except ProcessLookupError:
            pass  # Process already terminated

    # Run drain_and_wait with a timeout killer running concurrently
    # The timeout_killer will kill the process if needed, which causes
    # drain_and_wait to complete (stdout closes when process dies)
    timeout_task = asyncio.create_task(timeout_killer())
    try:
        await drain_and_wait()
    except Exception as e:
        # Log unexpected exceptions before cleanup
        print(f"  ERROR: Unexpected exception during {context}: {e}")
        raise
    finally:
        # Cancel the timeout task
        timeout_task.cancel()
        try:
            await timeout_task
        except asyncio.CancelledError:
            pass

        # Ensure FFmpeg process is cleaned up on any exception or early exit
        await cleanup_ffmpeg_process(process, context)

    if timed_out:
        elapsed = asyncio.get_running_loop().time() - start_time
        return False, f"{context} timed out after {elapsed:.0f} seconds (limit: {timeout:.0f}s)"

    if process.returncode != 0:
        error_msg = f"{context} exited with code {process.returncode}"
        print(f"  ERROR: {error_msg}")
        return False, error_msg

    return True, None


def signal_handler(sig, frame):
    """Handle shutdown signals gracefully."""
    sig_name = signal.strsignal(sig) if hasattr(signal, "strsignal") else str(sig)
    print(f"\n{sig_name} received, finishing current job and shutting down gracefully...")
    state = get_worker_state()
    state.request_shutdown()


def validate_duration(duration: Any) -> float:
    """
    Validate and normalize video duration from ffprobe.

    Args:
        duration: Duration value from ffprobe (accepts any input type)

    Returns:
        Validated duration as float

    Raises:
        ValueError: If duration is invalid, missing, or out of acceptable range
    """
    if duration is None:
        raise ValueError("Could not determine video duration")

    # Convert to float if possible
    if not isinstance(duration, (int, float)):
        try:
            duration = float(duration)
        except (ValueError, TypeError) as e:
            raise ValueError(f"Could not convert duration to float: {type(duration).__name__}") from e

    if math.isnan(duration) or math.isinf(duration):
        raise ValueError(f"Invalid duration value: {duration}")

    if duration <= 0:
        raise ValueError(f"Invalid duration: {duration} seconds (must be positive)")

    # Prevent potential memory issues and catch corrupted metadata
    if duration > MAX_DURATION_SECONDS:
        raise ValueError(f"Duration too long: {duration} seconds (max {MAX_DURATION_SECONDS})")

    return float(duration)


# ============================================================================
# Filesystem Watcher (Event-Driven Processing)
# ============================================================================


class UploadEventHandler(FileSystemEventHandler):
    """
    Handles filesystem events in the uploads directory.
    Sets an asyncio event when new video files are detected.
    """

    VIDEO_EXTENSIONS = SUPPORTED_VIDEO_EXTENSIONS

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        event: asyncio.Event,
        debounce_delay: float = WORKER_DEBOUNCE_DELAY,
    ):
        super().__init__()
        self.loop = loop
        self.event = event
        self.debounce_delay = debounce_delay
        self._debounce_timer = None
        self._lock = threading.Lock()

    def _is_video_file(self, path: str) -> bool:
        """Check if the file is a video file we care about."""
        return Path(path).suffix.lower() in self.VIDEO_EXTENSIONS

    def _trigger_event(self):
        """Thread-safe way to set the asyncio event from the watchdog thread."""

        def set_event():
            if not self.event.is_set():
                self.event.set()

        self.loop.call_soon_threadsafe(set_event)

    def _schedule_trigger(self):
        """
        Debounce file events to avoid triggering multiple times for a single upload.
        Large files may generate multiple write events during upload.
        """
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
            self._debounce_timer = threading.Timer(self.debounce_delay, self._trigger_event)
            self._debounce_timer.start()

    def on_created(self, event):
        """Called when a file is created in the uploads directory."""
        if not event.is_directory and self._is_video_file(event.src_path):
            print(f"  [watcher] New file detected: {Path(event.src_path).name}")
            self._schedule_trigger()

    def on_modified(self, event):
        """Called when a file is modified (handles uploads that create then write)."""
        if not event.is_directory and self._is_video_file(event.src_path):
            self._schedule_trigger()

    def on_moved(self, event):
        """Called when a file is moved into the uploads directory."""
        if not event.is_directory and self._is_video_file(event.dest_path):
            print(f"  [watcher] File moved in: {Path(event.dest_path).name}")
            self._schedule_trigger()

    def cleanup(self):
        """Cancel any pending debounce timer."""
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
                self._debounce_timer = None


def start_filesystem_watcher(
    loop: asyncio.AbstractEventLoop,
    event: asyncio.Event,
    debounce_delay: float = WORKER_DEBOUNCE_DELAY,
) -> Optional[Observer]:
    """
    Start the filesystem watcher for the uploads directory.

    Args:
        loop: The asyncio event loop
        event: The asyncio event to signal on file detection
        debounce_delay: Delay to debounce file events (seconds)

    Returns:
        The Observer instance or None if watchdog is not available.
    """
    if not WATCHDOG_AVAILABLE:
        return None

    try:
        handler = UploadEventHandler(loop, event, debounce_delay=debounce_delay)
        observer = Observer()
        observer.schedule(handler, str(UPLOADS_DIR), recursive=False)
        observer.start()
        print(f"  Filesystem watcher started on: {UPLOADS_DIR}")
        return observer
    except Exception as e:
        print(f"  Warning: Failed to start filesystem watcher: {e}")
        print("  Falling back to polling mode.")
        return None


def stop_filesystem_watcher(observer: Optional[Observer]):
    """Stop the filesystem watcher gracefully."""
    if observer is not None:
        try:
            observer.stop()
            observer.join(timeout=5)
            # Clean up the event handler
            for handler_list in observer._handlers.values():
                for handler in handler_list:
                    if hasattr(handler, "cleanup"):
                        handler.cleanup()
        except Exception as e:
            print(f"  Warning: Error stopping filesystem watcher: {e}")


async def get_video_info(input_path: Path, timeout: float = 30.0) -> dict:
    """Get video metadata using ffprobe (async with timeout).

    Args:
        input_path: Path to the video file
        timeout: Maximum time to wait for ffprobe (default 30 seconds)

    Returns:
        Dictionary with video metadata (width, height, duration, codec)

    Raises:
        RuntimeError: If ffprobe fails or times out
    """
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(input_path)]

    process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError(f"ffprobe timed out after {timeout}s (file may be on slow storage or corrupted)")

    if process.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {stderr.decode('utf-8', errors='ignore')}")

    data = json.loads(stdout.decode("utf-8", errors="ignore"))

    # Find video stream
    video_stream = None
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            video_stream = stream
            break

    if not video_stream:
        raise RuntimeError("No video stream found")

    # Get and validate duration (validate_duration handles conversion to float)
    raw_duration = data.get("format", {}).get("duration")
    duration = validate_duration(raw_duration)

    return {
        "width": int(video_stream.get("width", 0)),
        "height": int(video_stream.get("height", 0)),
        "duration": duration,
        "codec": video_stream.get("codec_name", "unknown"),
    }


def get_applicable_qualities(source_height: int) -> list:
    """Get quality presets that are <= source resolution."""
    return [q for q in QUALITY_PRESETS if q["height"] <= source_height]


async def get_output_dimensions(segment_path: Path, timeout: float = 10.0) -> Tuple[int, int]:
    """Get actual dimensions from a transcoded segment file (async with timeout).

    Args:
        segment_path: Path to the segment file
        timeout: Maximum time to wait for ffprobe (default 10 seconds)

    Returns:
        Tuple of (width, height), or (0, 0) on failure
    """
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "json",
        str(segment_path),
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )

        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)

        if process.returncode != 0:
            logger.warning(f"ffprobe failed for {segment_path.name} (exit code {process.returncode})")
            return (0, 0)

        data = json.loads(stdout.decode("utf-8", errors="ignore"))
        streams = data.get("streams", [])
        if not streams:
            logger.warning(f"No video streams found in {segment_path.name}")
            return (0, 0)
        stream = streams[0]
        width = int(stream.get("width", 0))
        height = int(stream.get("height", 0))
        return (width, height)
    except asyncio.TimeoutError:
        logger.warning(f"ffprobe timed out for {segment_path.name} after {timeout}s")
        return (0, 0)
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        logger.warning(f"Failed to parse dimensions from {segment_path.name}: {e}")
        return (0, 0)


async def validate_hls_playlist(playlist_path: Path, check_segments: bool = True) -> Tuple[bool, Optional[str]]:
    """
    Validate an HLS playlist is complete and well-formed.

    Args:
        playlist_path: Path to the .m3u8 playlist file
        check_segments: If True, also verify all referenced segments exist and are non-empty

    Returns:
        Tuple[bool, Optional[str]]: (is_valid, error_message)
        error_message is None if valid, otherwise describes the issue
    """
    if not playlist_path.exists():
        return False, "Playlist file does not exist"

    try:
        content = playlist_path.read_text()

        # Check for required HLS header
        if not content.startswith("#EXTM3U"):
            return False, "Missing #EXTM3U header"

        # Check for end marker (indicates transcoding completed)
        if "#EXT-X-ENDLIST" not in content:
            return False, "Missing #EXT-X-ENDLIST (incomplete transcode)"

        if not check_segments:
            return True, None

        # Validate all referenced segment files exist and are non-empty
        segment_count = 0
        first_segment_path = None
        for line in content.splitlines():
            line = line.strip()
            # Skip empty lines and comments/tags
            if not line or line.startswith("#"):
                continue

            # This should be a segment filename (.ts for HLS/MPEG-TS, .m4s for CMAF/fMP4)
            if line.endswith(".ts") or line.endswith(".m4s"):
                segment_path = playlist_path.parent / line
                if not segment_path.exists():
                    return False, f"Missing segment file: {line}"
                if segment_path.stat().st_size == 0:
                    return False, f"Empty segment file: {line}"
                if first_segment_path is None:
                    first_segment_path = segment_path
                segment_count += 1

        # Sanity check - playlist should have at least one segment
        if segment_count == 0:
            return False, "Playlist contains no segment references"

        # Validate first segment actually contains a video stream
        # This catches cases where encoding failed but audio-only output was produced
        if first_segment_path:
            try:
                proc = await asyncio.wait_for(
                    asyncio.create_subprocess_exec(
                        "ffprobe",
                        "-v",
                        "quiet",
                        "-select_streams",
                        "v:0",
                        "-show_entries",
                        "stream=codec_type",
                        "-of",
                        "csv=p=0",
                        str(first_segment_path),
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    ),
                    timeout=10,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
                if proc.returncode != 0 or b"video" not in stdout:
                    return False, f"Segment {first_segment_path.name} has no video stream (encoding may have failed)"
            except asyncio.TimeoutError:
                return False, f"Timeout probing segment {first_segment_path.name}"
            except OSError as e:
                return False, f"Error probing segment: {e}"

        return True, None

    except (IOError, OSError) as e:
        return False, f"Error reading playlist: {e}"


async def is_hls_playlist_complete(playlist_path: Path) -> bool:
    """
    Check if an HLS playlist is complete and valid.
    Validates the playlist structure and ensures all segment files exist.

    This is a convenience wrapper around validate_hls_playlist().
    """
    is_valid, error = await validate_hls_playlist(playlist_path, check_segments=True)
    if not is_valid and error:
        # Log validation failures for debugging
        print(f"      Playlist validation failed: {error}")
    return is_valid


async def generate_thumbnail(input_path: Path, output_path: Path, timestamp: float = 5.0, timeout: float = 60.0):
    """Generate a thumbnail from the video (async with timeout).

    Args:
        input_path: Path to the video file
        output_path: Path to save the thumbnail
        timestamp: Time position to capture (default 5 seconds)
        timeout: Maximum time to wait for ffmpeg (default 60 seconds)

    Raises:
        RuntimeError: If ffmpeg fails or times out
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Use input seeking (-ss before -i) for fast seeking to timestamp
    # This seeks directly to the nearest keyframe without decoding the entire stream
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(timestamp),
        "-i",
        str(input_path),
        "-vframes",
        "1",
        "-vf",
        "scale=640:-1",
        str(output_path),
    ]

    process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError(f"Thumbnail generation timed out after {timeout}s")

    if process.returncode != 0:
        error_msg = truncate_error(stderr.decode("utf-8", errors="ignore"), ERROR_DETAIL_MAX_LENGTH)
        raise RuntimeError(f"Thumbnail generation failed: {error_msg}")


async def transcode_quality_with_progress(
    input_path: Path,
    output_dir: Path,
    quality: dict,
    duration: float,
    progress_callback: Optional[Callable[[int], Awaitable[None]]] = None,
    gpu_caps: Optional["GPUCapabilities"] = None,
    streaming_format: str = "hls_ts",
) -> Tuple[bool, Optional[str]]:
    """
    Transcode a single quality variant with progress tracking and timeout.

    Args:
        input_path: Source video file
        output_dir: Output directory for HLS/CMAF files
        quality: Quality preset dict with name, height, bitrate, audio_bitrate
        duration: Video duration in seconds
        progress_callback: Optional async callback for progress updates (0-100)
        gpu_caps: GPU capabilities from hwaccel module for hardware encoding
        streaming_format: Output format - "hls_ts" for MPEG-TS or "cmaf" for fMP4

    Returns:
        Tuple[bool, Optional[str]]: (success, error_message) where error_message
        is None on success or contains the ffmpeg error details on failure.
    """
    name = quality["name"]
    height = quality["height"]
    bitrate = quality["bitrate"]
    audio_bitrate = quality["audio_bitrate"]

    use_cmaf = streaming_format == "cmaf"

    # Calculate timeout based on video duration and resolution
    timeout = calculate_ffmpeg_timeout(duration, height)
    print(f"      Timeout set to {timeout:.0f}s ({timeout / 60:.1f} min) for {name}")
    if use_cmaf:
        print("      Output format: CMAF (fMP4)")

    # Use hardware acceleration if GPU capabilities provided
    if gpu_caps is not None:
        from worker.hwaccel import build_cmaf_transcode_command, build_transcode_command, select_encoder

        selection = select_encoder(gpu_caps, height)
        encoder_name = selection.encoder.name
        encoder_type = "GPU" if selection.encoder.is_hardware else "CPU"
        print(f"      Using encoder: {encoder_name} ({encoder_type})")

        if use_cmaf:
            # Create quality subdirectory for CMAF output
            quality_dir = output_dir / name
            quality_dir.mkdir(parents=True, exist_ok=True)
            cmd = build_cmaf_transcode_command(
                input_path,
                output_dir,
                quality,
                selection,
                HLS_SEGMENT_DURATION,
            )
        else:
            cmd = build_transcode_command(
                input_path,
                output_dir,
                quality,
                selection,
                HLS_SEGMENT_DURATION,
            )
    else:
        # Default CPU encoding (no GPU available)
        scale_filter = f"scale=-2:{height}"

        if use_cmaf:
            # CMAF output structure: {output_dir}/{quality_name}/stream.m3u8 + init.mp4 + seg_*.m4s
            quality_dir = output_dir / name
            quality_dir.mkdir(parents=True, exist_ok=True)
            playlist_name = "stream.m3u8"
            init_segment = "init.mp4"
            segment_pattern = str(quality_dir / "seg_%04d.m4s")

            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "23",
                "-b:v",
                bitrate,
                "-maxrate",
                bitrate,
                "-bufsize",
                f"{int(bitrate.replace('k', '')) * 2}k",
                "-vf",
                scale_filter,
                "-c:a",
                "aac",
                "-b:a",
                audio_bitrate,
                "-ac",
                "2",
                "-hls_time",
                str(HLS_SEGMENT_DURATION),
                "-hls_list_size",
                "0",
                "-hls_segment_type",
                "fmp4",
                "-hls_fmp4_init_filename",
                init_segment,
                "-hls_segment_filename",
                segment_pattern,
                "-movflags",
                "+cmaf+faststart",
                "-progress",
                "pipe:1",
                "-f",
                "hls",
                str(quality_dir / playlist_name),
            ]
        else:
            # HLS/TS output structure: {output_dir}/{quality_name}.m3u8 + {quality_name}_*.ts
            playlist_name = f"{name}.m3u8"
            segment_pattern = f"{name}_%04d.ts"

            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "23",
                "-b:v",
                bitrate,
                "-maxrate",
                bitrate,
                "-bufsize",
                f"{int(bitrate.replace('k', '')) * 2}k",
                "-vf",
                scale_filter,
                "-c:a",
                "aac",
                "-b:a",
                audio_bitrate,
                "-ac",
                "2",
                "-hls_time",
                str(HLS_SEGMENT_DURATION),
                "-hls_list_size",
                "0",
                "-hls_segment_filename",
                str(output_dir / segment_pattern),
                "-progress",
                "pipe:1",
                "-f",
                "hls",
                str(output_dir / playlist_name),
            ]

    # Use shared helper for running FFmpeg with progress and timeout
    success, error_msg = await run_ffmpeg_with_progress(
        cmd=cmd,
        duration=duration,
        timeout=timeout,
        progress_callback=progress_callback,
        context=f"FFmpeg transcode {name}",
    )

    if not success and error_msg:
        print(f"  ERROR: Failed to transcode {name}: {error_msg}")

    return success, error_msg


async def create_original_quality(
    input_path: Path,
    output_dir: Path,
    duration: float,
    progress_callback: Optional[Callable[[int], Awaitable[None]]] = None,
) -> Tuple[bool, Optional[str], Optional[dict]]:
    """
    Create 'original' quality by remuxing source to HLS without re-encoding.
    Preserves original video/audio quality with no generation loss.

    Returns:
        Tuple[bool, Optional[str], Optional[dict]]: (success, error_message, quality_info)
        where quality_info contains width, height, bitrate for the master playlist.
    """
    playlist_name = "original.m3u8"
    segment_pattern = "original_%04d.ts"

    # Calculate timeout based on duration (remuxing is much faster than transcoding)
    timeout = calculate_ffmpeg_timeout(duration) / 3  # Remux is ~3x faster
    timeout = max(FFMPEG_TIMEOUT_MINIMUM, timeout)
    print(f"      Timeout set to {timeout:.0f}s ({timeout / 60:.1f} min) for remux")

    # Use copy codec to remux without re-encoding
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-c:v",
        "copy",  # Copy video stream as-is
        "-c:a",
        "copy",  # Copy audio stream as-is
        "-hls_time",
        str(HLS_SEGMENT_DURATION),
        "-hls_list_size",
        "0",
        "-hls_segment_filename",
        str(output_dir / segment_pattern),
        "-progress",
        "pipe:1",
        "-f",
        "hls",
        str(output_dir / playlist_name),
    ]

    # Use shared helper for running FFmpeg with progress and timeout
    success, error_msg = await run_ffmpeg_with_progress(
        cmd=cmd,
        duration=duration,
        timeout=timeout,
        progress_callback=progress_callback,
        context="FFmpeg remux original",
    )

    if not success:
        if error_msg:
            print(f"  ERROR: Failed to create original quality: {error_msg}")
        return False, error_msg, None

    # Get the actual bitrate from the source for master playlist
    # We'll estimate based on file size and duration
    try:
        source_size = input_path.stat().st_size
        bitrate_bps = int((source_size * 8) / duration) if duration > 0 else 10000000
    except Exception:
        bitrate_bps = 10000000  # Default 10Mbps if can't calculate

    return True, None, {"bitrate_bps": bitrate_bps}


async def generate_master_playlist(output_dir: Path, completed_qualities: List[dict]):
    """Generate master HLS playlist from completed quality variants.

    Qualities are sorted by bandwidth (highest first) so players pick the best quality.
    The 'original' quality uses bitrate_bps if available, others use bitrate string.

    Verifies actual dimensions from first segment of each quality to ensure accuracy.

    Note: This function modifies the quality dictionaries in-place to update width/height
    with actual values from the transcoded segments.

    Args:
        output_dir: Directory containing the HLS segments and playlists
        completed_qualities: List of quality dicts with name, width, height, bitrate fields
    """
    # Verify actual dimensions from first segment of each quality
    for quality in completed_qualities:
        first_segment = output_dir / f"{quality['name']}_0000.ts"
        if first_segment.exists():
            actual_width, actual_height = await get_output_dimensions(first_segment)
            if actual_width > 0 and actual_height > 0:
                quality["width"] = actual_width
                quality["height"] = actual_height

    master_content = "#EXTM3U\n#EXT-X-VERSION:3\n\n"

    # Calculate bandwidth for each quality and sort by bandwidth (highest first)
    qualities_with_bandwidth = []
    for quality in completed_qualities:
        name = quality["name"]
        width = quality["width"]
        height = quality["height"]

        # Handle original quality (has bitrate_bps) vs transcoded (has bitrate string)
        if quality.get("is_original") and quality.get("bitrate_bps"):
            bandwidth = quality["bitrate_bps"]
        elif quality.get("bitrate_bps"):
            bandwidth = quality["bitrate_bps"]
        else:
            bandwidth = int(quality["bitrate"].replace("k", "")) * 1000

        qualities_with_bandwidth.append(
            {
                "name": name,
                "width": width,
                "height": height,
                "bandwidth": bandwidth,
            }
        )

    # Sort by bandwidth descending (highest quality first)
    qualities_with_bandwidth.sort(key=lambda q: q["bandwidth"], reverse=True)

    for quality in qualities_with_bandwidth:
        master_content += (
            f"#EXT-X-STREAM-INF:BANDWIDTH={quality['bandwidth']},RESOLUTION={quality['width']}x{quality['height']}\n"
        )
        master_content += f"{quality['name']}.m3u8\n"

    (output_dir / "master.m3u8").write_text(master_content)


async def generate_master_playlist_cmaf(
    output_dir: Path,
    completed_qualities: List[dict],
    codec: VideoCodec = VideoCodec.H264,
):
    """
    Generate master HLS playlist for CMAF output structure.

    CMAF uses subdirectories per quality with stream.m3u8 playlists.
    Also includes CODECS attribute for proper codec signaling.

    Output structure expected:
        output_dir/
            master.m3u8
            1080p/
                stream.m3u8
                init.mp4
                seg_*.m4s
            720p/
                stream.m3u8
                ...

    Args:
        output_dir: Directory containing the CMAF quality subdirectories
        completed_qualities: List of quality dicts with name, width, height, bitrate fields
        codec: Video codec used for encoding (affects CODECS attribute)
    """
    # Verify actual dimensions from init segment of each quality
    for quality in completed_qualities:
        quality_dir = output_dir / quality["name"]
        init_segment = quality_dir / "init.mp4"
        if init_segment.exists():
            actual_width, actual_height = await get_output_dimensions(init_segment)
            if actual_width > 0 and actual_height > 0:
                quality["width"] = actual_width
                quality["height"] = actual_height

    # HLS version 7 required for fMP4 segments
    master_content = "#EXTM3U\n#EXT-X-VERSION:7\n\n"

    # Calculate bandwidth for each quality and sort by bandwidth (highest first)
    qualities_with_bandwidth = []
    for quality in completed_qualities:
        name = quality["name"]
        width = quality["width"]
        height = quality["height"]

        # Handle original quality (has bitrate_bps) vs transcoded (has bitrate string)
        if quality.get("is_original") and quality.get("bitrate_bps"):
            bandwidth = quality["bitrate_bps"]
        elif quality.get("bitrate_bps"):
            bandwidth = quality["bitrate_bps"]
        else:
            bandwidth = int(quality["bitrate"].replace("k", "")) * 1000

        qualities_with_bandwidth.append(
            {
                "name": name,
                "width": width,
                "height": height,
                "bandwidth": bandwidth,
            }
        )

    # Sort by bandwidth descending (highest quality first)
    qualities_with_bandwidth.sort(key=lambda q: q["bandwidth"], reverse=True)

    # Get codec string for manifest
    codec_string = get_codec_string(codec)

    for quality in qualities_with_bandwidth:
        master_content += (
            f'#EXT-X-STREAM-INF:BANDWIDTH={quality["bandwidth"]},'
            f'RESOLUTION={quality["width"]}x{quality["height"]},'
            f'CODECS="{codec_string}"\n'
        )
        # Reference subdirectory playlist
        master_content += f"{quality['name']}/stream.m3u8\n"

    (output_dir / "master.m3u8").write_text(master_content)


async def generate_dash_manifest(
    output_dir: Path,
    completed_qualities: List[dict],
    segment_duration: int = 6,
    codec: VideoCodec = VideoCodec.H264,
):
    """
    Generate DASH MPD manifest for CMAF segments.

    Creates a simple DASH manifest that references the same fMP4 segments
    used by HLS, enabling dual-protocol streaming from a single encode.

    Args:
        output_dir: Directory containing the CMAF quality subdirectories
        completed_qualities: List of quality dicts with name, width, height, bitrate fields
        segment_duration: Segment duration in seconds
        codec: Video codec used for encoding
    """
    # Calculate total duration from first quality's playlist
    total_duration = 0
    segment_count = 0
    for quality in completed_qualities:
        quality_dir = output_dir / quality["name"]
        playlist = quality_dir / "stream.m3u8"
        if playlist.exists():
            content = playlist.read_text()
            # Count segments and calculate duration
            for line in content.split("\n"):
                if line.startswith("#EXTINF:"):
                    try:
                        duration = float(line.split(":")[1].rstrip(","))
                        total_duration += duration
                        segment_count += 1
                    except (ValueError, IndexError):
                        pass
            break  # Only need to check one quality

    if total_duration == 0:
        total_duration = segment_count * segment_duration

    # Format duration as ISO 8601
    hours = int(total_duration // 3600)
    minutes = int((total_duration % 3600) // 60)
    seconds = total_duration % 60
    duration_str = f"PT{hours}H{minutes}M{seconds:.3f}S"

    # Determine codec-specific codecs string
    if codec == VideoCodec.HEVC:
        video_codecs = "hvc1.1.6.L120.90"
    elif codec == VideoCodec.AV1:
        video_codecs = "av01.0.08M.08"
    else:
        video_codecs = "avc1.640028"

    # Build adaptation sets for each quality
    adaptation_sets = []
    seg_duration_ms = segment_duration * 1000

    # Sort qualities by bandwidth descending
    sorted_qualities = sorted(
        completed_qualities,
        key=lambda q: q.get("bitrate_bps", int(q.get("bitrate", "0").replace("k", "")) * 1000),
        reverse=True,
    )

    for i, quality in enumerate(sorted_qualities):
        name = quality["name"]
        width = quality["width"]
        height = quality["height"]
        bandwidth = quality.get("bitrate_bps", int(quality.get("bitrate", "0").replace("k", "")) * 1000)

        adaptation_sets.append(
            f'    <AdaptationSet id="{i}" mimeType="video/mp4" codecs="{video_codecs}" '
            f'startWithSAP="1" segmentAlignment="true">\n'
            f'      <Representation id="{name}" bandwidth="{bandwidth}" '
            f'width="{width}" height="{height}">\n'
            f'        <SegmentTemplate media="{name}/seg_$Number%04d$.m4s" '
            f'initialization="{name}/init.mp4" startNumber="1" '
            f'duration="{seg_duration_ms}" timescale="1000"/>\n'
            f"      </Representation>\n"
            f"    </AdaptationSet>"
        )

    mpd_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" \
mediaPresentationDuration="{duration_str}" minBufferTime="PT2S" \
profiles="urn:mpeg:dash:profile:isoff-on-demand:2011">
  <Period duration="{duration_str}">
{chr(10).join(adaptation_sets)}
  </Period>
</MPD>
"""

    (output_dir / "manifest.mpd").write_text(mpd_content)


async def cleanup_partial_output(
    video_slug: str, keep_completed_qualities: bool = True, completed_quality_names: Optional[List[str]] = None
):
    """Clean up partial transcoding output."""
    # Validate slug to prevent path traversal attacks
    if not validate_slug(video_slug):
        logger.error(f"Invalid video slug in cleanup_partial_output: {video_slug}")
        return

    output_dir = VIDEOS_DIR / video_slug

    if not output_dir.exists():
        return

    if not keep_completed_qualities or not completed_quality_names:
        # Full cleanup
        shutil.rmtree(output_dir, ignore_errors=True)
        output_dir.mkdir(exist_ok=True)
        return

    # Build dynamic regex pattern from QUALITY_NAMES
    # Escape each quality name and join with | for alternation
    quality_pattern = "|".join(re.escape(name) for name in sorted(QUALITY_NAMES))
    # Match quality files like "1080p.m3u8", "1080p_0001.ts", "original.m3u8", "original_0001.ts"
    file_pattern = re.compile(f"({quality_pattern})(_\\d+\\.ts|\\.m3u8)$")

    # Selective cleanup - keep completed quality files
    for file in output_dir.iterdir():
        quality_match = file_pattern.match(file.name)
        if quality_match:
            quality = quality_match.group(1)
            if quality not in completed_quality_names:
                file.unlink()  # Remove incomplete quality files

    # Always remove master.m3u8 (regenerate at end)
    master_path = output_dir / "master.m3u8"
    if master_path.exists():
        master_path.unlink()


def cleanup_source_file(video_id: int) -> bool:
    """
    Clean up source file from uploads directory for a video.

    Called when a transcoding job permanently fails (max retries exceeded).
    Controlled by CLEANUP_SOURCE_ON_PERMANENT_FAILURE config option.

    Args:
        video_id: The video ID to clean up source file for

    Returns:
        True if a file was deleted, False otherwise
    """
    if not CLEANUP_SOURCE_ON_PERMANENT_FAILURE:
        return False

    for ext in SUPPORTED_VIDEO_EXTENSIONS:
        source_file = UPLOADS_DIR / f"{video_id}{ext}"
        if source_file.exists():
            try:
                source_file.unlink()
                logger.info(f"Cleaned up source file for failed video {video_id}: {source_file}")
                return True
            except OSError as e:
                logger.error(f"Failed to clean up source file {source_file}: {e}")
                return False
    return False


# ============================================================================
# Job Management Functions
# ============================================================================


async def get_existing_job(video_id: int) -> Optional[dict]:
    """Get and claim existing transcoding job for a video.

    Uses database-level locking to prevent race conditions with remote workers.
    Returns None if:
    - No job exists (upload still in progress)
    - Job is already claimed by another worker
    - Job is already completed

    This function atomically claims the job for the local worker, preventing
    remote workers from claiming it simultaneously.

    Args:
        video_id: Database ID of the video

    Returns:
        Job dict if exists and successfully claimed, None otherwise
    """
    now = datetime.now(timezone.utc)
    claim_duration = timedelta(minutes=WORKER_CLAIM_DURATION_MINUTES)
    expires_at = now + claim_duration

    # Use LOCAL_WORKER as the worker_id for local workers
    # This distinguishes local worker claims from remote workers
    local_worker_id = "LOCAL_WORKER"

    result = {"job": None}

    async def do_claim():
        async with database.transaction():
            # Check the database URL to determine if we need PostgreSQL-specific syntax
            db_url = str(database.url)
            is_postgresql = db_url.startswith("postgresql")

            if is_postgresql:
                # Use FOR UPDATE SKIP LOCKED to atomically claim the job
                # This prevents race conditions with remote workers
                job = await database.fetch_one(
                    sa.text("""
                        SELECT tj.*
                        FROM transcoding_jobs tj
                        WHERE tj.video_id = :video_id
                          AND tj.completed_at IS NULL
                          AND (
                              tj.claimed_at IS NULL
                              OR tj.claim_expires_at < :now
                              OR tj.worker_id = :local_worker_id
                          )
                        FOR UPDATE SKIP LOCKED
                    """).bindparams(video_id=video_id, now=now, local_worker_id=local_worker_id)
                )
            else:
                # SQLite: use regular SELECT within transaction
                # SQLite's transaction isolation prevents concurrent modifications
                job = await database.fetch_one(
                    sa.text("""
                        SELECT *
                        FROM transcoding_jobs
                        WHERE video_id = :video_id
                          AND completed_at IS NULL
                          AND (
                              claimed_at IS NULL
                              OR claim_expires_at < :now
                              OR worker_id = :local_worker_id
                          )
                    """).bindparams(video_id=video_id, now=now, local_worker_id=local_worker_id)
                )

            if not job:
                # No job, or job is claimed by remote worker and not expired
                result["job"] = None
                return

            # Claim the job for the local worker
            await database.execute(
                transcoding_jobs.update()
                .where(transcoding_jobs.c.id == job["id"])
                .values(
                    worker_id=local_worker_id,
                    claimed_at=now,
                    claim_expires_at=expires_at,
                    # Permanent record of which worker processed this job
                    processed_by_worker_id=local_worker_id,
                    processed_by_worker_name="Local Worker",
                )
            )

            result["job"] = dict(job)

    try:
        # Use execute_with_retry to handle transient database errors (deadlocks, locking)
        await execute_with_retry(do_claim)
    except DatabaseRetryableError as e:
        logger.warning(f"Failed to claim job for video {video_id} after retries: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error claiming job for video {video_id}: {e}")
        raise

    return result["job"]


async def update_job_step(job_id: int, step: str):
    """Update the current processing step and extend claim."""
    now = datetime.now(timezone.utc)
    claim_duration = timedelta(minutes=WORKER_CLAIM_DURATION_MINUTES)
    expires_at = now + claim_duration

    async def do_update():
        await database.execute(
            transcoding_jobs.update()
            .where(transcoding_jobs.c.id == job_id)
            .values(
                current_step=step,
                last_checkpoint=now,
                claim_expires_at=expires_at,
            )
        )

    try:
        await execute_with_retry(do_update)
    except Exception as e:
        # Log but continue - progress update failure shouldn't stop the job
        logger.warning(f"Failed to update job step for job {job_id}: {e}")


async def update_job_progress(job_id: int, progress: int):
    """Update overall job progress percentage and extend claim."""
    now = datetime.now(timezone.utc)
    claim_duration = timedelta(minutes=WORKER_CLAIM_DURATION_MINUTES)
    expires_at = now + claim_duration

    async def do_update():
        await database.execute(
            transcoding_jobs.update()
            .where(transcoding_jobs.c.id == job_id)
            .values(
                progress_percent=progress,
                last_checkpoint=now,
                claim_expires_at=expires_at,
            )
        )

    try:
        await execute_with_retry(do_update)
    except Exception as e:
        # Log but continue - progress update failure shouldn't stop the job
        logger.warning(f"Failed to update job progress for job {job_id}: {e}")


async def checkpoint(job_id: int):
    """Update the checkpoint timestamp and extend claim."""
    now = datetime.now(timezone.utc)
    claim_duration = timedelta(minutes=WORKER_CLAIM_DURATION_MINUTES)
    expires_at = now + claim_duration

    async def do_update():
        await database.execute(
            transcoding_jobs.update()
            .where(transcoding_jobs.c.id == job_id)
            .values(
                last_checkpoint=now,
                claim_expires_at=expires_at,
            )
        )

    try:
        await execute_with_retry(do_update)
    except Exception as e:
        # Log but continue - checkpoint failure shouldn't stop the job
        logger.warning(f"Failed to update checkpoint for job {job_id}: {e}")


async def mark_job_completed(job_id: int):
    """Mark job as successfully completed."""
    await database.execute(
        transcoding_jobs.update()
        .where(transcoding_jobs.c.id == job_id)
        .values(
            completed_at=datetime.now(timezone.utc),
            progress_percent=100,
            last_checkpoint=datetime.now(timezone.utc),
        )
    )


async def mark_job_failed(job_id: int, error: str, final: bool = False):
    """Mark job as failed.

    Args:
        job_id: The job ID
        error: Error message
        final: If True, sets completed_at to indicate job is finished (no more retries)
    """
    values = {
        "last_error": error[:500],
        "last_checkpoint": datetime.now(timezone.utc),
    }
    if final:
        values["completed_at"] = datetime.now(timezone.utc)

    await database.execute(transcoding_jobs.update().where(transcoding_jobs.c.id == job_id).values(**values))


async def reset_job_for_retry(job_id: int, state: Optional[WorkerState] = None):
    """Reset a job for retry, incrementing attempt number.

    Args:
        job_id: Database ID of the transcoding job
        state: Optional WorkerState instance. If not provided, uses/creates the
               default global state.
    """
    if state is None:
        state = get_worker_state()

    job = await database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.id == job_id))

    if not job:
        return

    new_attempt = (job["attempt_number"] or 1) + 1

    await database.execute(
        transcoding_jobs.update()
        .where(transcoding_jobs.c.id == job_id)
        .values(
            worker_id=state.worker_id,
            attempt_number=new_attempt,
            started_at=datetime.now(timezone.utc),
            last_checkpoint=datetime.now(timezone.utc),
            completed_at=None,
        )
    )


# ============================================================================
# Quality Progress Functions
# ============================================================================


async def init_quality_progress(job_id: int, qualities: List[dict]):
    """Initialize progress records for all qualities."""
    for quality in qualities:
        # Check if record already exists
        existing = await database.fetch_one(
            quality_progress.select().where(
                (quality_progress.c.job_id == job_id) & (quality_progress.c.quality == quality["name"])
            )
        )

        if not existing:
            await database.execute(
                quality_progress.insert().values(
                    job_id=job_id,
                    quality=quality["name"],
                    status=QualityStatus.PENDING,
                    progress_percent=0,
                )
            )


async def get_quality_status(job_id: int, quality_name: str) -> Optional[dict]:
    """Get the progress status for a specific quality."""
    row = await database.fetch_one(
        quality_progress.select().where(
            (quality_progress.c.job_id == job_id) & (quality_progress.c.quality == quality_name)
        )
    )
    return dict(row) if row else None


async def update_quality_status(job_id: int, quality_name: str, status: str, error_message: Optional[str] = None):
    """Update quality transcoding status."""
    values = {
        "status": status,
    }

    if status == QualityStatus.IN_PROGRESS:
        values["started_at"] = datetime.now(timezone.utc)
    elif status == QualityStatus.COMPLETED:
        values["completed_at"] = datetime.now(timezone.utc)
        values["progress_percent"] = 100
    elif status == QualityStatus.FAILED and error_message:
        values["error_message"] = error_message[:500]

    await database.execute(
        quality_progress.update()
        .where((quality_progress.c.job_id == job_id) & (quality_progress.c.quality == quality_name))
        .values(**values)
    )


async def update_quality_progress(job_id: int, quality_name: str, progress: int):
    """Update quality transcoding progress percentage."""
    await database.execute(
        quality_progress.update()
        .where((quality_progress.c.job_id == job_id) & (quality_progress.c.quality == quality_name))
        .values(progress_percent=progress)
    )


async def get_completed_qualities(job_id: int) -> List[str]:
    """Get list of completed quality names for a job."""
    rows = await database.fetch_all(
        quality_progress.select().where(
            (quality_progress.c.job_id == job_id) & (quality_progress.c.status == QualityStatus.COMPLETED)
        )
    )
    return [row["quality"] for row in rows]


# ============================================================================
# Crash Recovery
# ============================================================================


async def recover_interrupted_jobs(state: Optional[WorkerState] = None):
    """
    Check for jobs that were interrupted (worker crashed) and reset them for retry.
    Called on worker startup.

    Args:
        state: Optional WorkerState instance. If not provided, uses/creates the
               default global state.
    """
    if state is None:
        state = get_worker_state()
    print(f"Worker {state.worker_id[:8]} checking for interrupted jobs...")

    # Get stale timeout from settings service
    settings = await get_transcoder_settings()
    job_stale_timeout = settings["job_stale_timeout"]

    # Find jobs that have a checkpoint but no completion and are stale
    stale_threshold = datetime.now(timezone.utc) - timedelta(seconds=job_stale_timeout)

    stale_jobs = await database.fetch_all(
        transcoding_jobs.select().where(
            transcoding_jobs.c.completed_at.is_(None)
            & transcoding_jobs.c.last_checkpoint.isnot(None)
            & (transcoding_jobs.c.last_checkpoint < stale_threshold)
        )
    )

    for job in stale_jobs:
        # Double-check staleness with timezone normalization as a safety measure.
        # This ensures we handle edge cases where timezone info might affect the
        # comparison (e.g., DST transitions, server timezone changes).
        last_checkpoint = ensure_utc(job["last_checkpoint"])
        if last_checkpoint >= stale_threshold:
            # Not actually stale after timezone normalization
            continue

        video = await database.fetch_one(videos.select().where(videos.c.id == job["video_id"]))

        if not video:
            continue

        print(f"  Found stale job for video '{video['slug']}' (attempt {job['attempt_number']})")

        if job["attempt_number"] >= job["max_attempts"]:
            # Max retries exceeded - use transaction to ensure consistency
            print("    Max retries exceeded, marking as failed")
            async with database.transaction():
                await mark_job_failed(job["id"], "Max retry attempts exceeded", final=True)
                await database.execute(
                    videos.update()
                    .where(videos.c.id == job["video_id"])
                    .values(status=VideoStatus.FAILED, error_message="Max retry attempts exceeded")
                )
            # Clean up source file after permanent failure
            if cleanup_source_file(job["video_id"]):
                print(f"    Cleaned up source file for video {job['video_id']}")
            # Send alert for max retries exceeded (fire-and-forget)
            send_alert_fire_and_forget(
                alert_max_retries_exceeded(
                    video_id=job["video_id"],
                    video_slug=video["slug"],
                    max_attempts=job["max_attempts"],
                    last_error=job.get("last_error"),
                )
            )
        else:
            # Reset for retry - use transaction to ensure consistency
            print(f"    Resetting for retry (attempt {job['attempt_number'] + 1})")
            async with database.transaction():
                await reset_job_for_retry(job["id"])
                # Also reset the video status to pending so it gets picked up
                await database.execute(
                    videos.update().where(videos.c.id == job["video_id"]).values(status=VideoStatus.PENDING)
                )

            # Optionally clean up partial output
            if CLEANUP_PARTIAL_ON_FAILURE:
                completed = await get_completed_qualities(job["id"])
                await cleanup_partial_output(
                    video["slug"], keep_completed_qualities=KEEP_COMPLETED_QUALITIES, completed_quality_names=completed
                )

            # Send alert for stale job recovered (fire-and-forget)
            send_alert_fire_and_forget(
                alert_stale_job_recovered(
                    video_id=job["video_id"],
                    video_slug=video["slug"],
                    attempt_number=job["attempt_number"],
                    worker_id=job.get("worker_id"),
                )
            )

    if stale_jobs:
        print(f"  Recovered {len(stale_jobs)} interrupted job(s)")
    else:
        print("  No interrupted jobs found")


# ============================================================================
# Main Processing with Checkpoints
# ============================================================================


async def reset_video_to_pending(video_id: int):
    """Reset a video status back to pending (for graceful shutdown/retry)."""
    await database.execute(videos.update().where(videos.c.id == video_id).values(status=VideoStatus.PENDING))


async def process_video_resumable(video_id: int, video_slug: str, state: Optional[WorkerState] = None):
    """
    Process a video with checkpoint-based resumable transcoding.
    Can resume from the last successful step if interrupted.

    Args:
        video_id: Database ID of the video to process
        video_slug: URL slug for the video
        state: Optional WorkerState instance. If not provided, uses/creates the
               default global state.
    """
    if state is None:
        state = get_worker_state()

    print(f"Processing video: {video_slug} (id={video_id})")

    # Validate slug to prevent path traversal attacks
    if not validate_slug(video_slug):
        logger.error(f"Invalid video slug: {video_slug}")
        await database.execute(
            videos.update()
            .where(videos.c.id == video_id)
            .values(status=VideoStatus.FAILED, error_message="Invalid video slug")
        )
        return False

    # Check for shutdown at the start
    if state.shutdown_requested:
        print("  Shutdown requested, skipping this video")
        return False

    # Find the source file
    source_file = None
    for ext in SUPPORTED_VIDEO_EXTENSIONS:
        candidate = UPLOADS_DIR / f"{video_id}{ext}"
        if candidate.exists():
            source_file = candidate
            break

    if not source_file:
        await database.execute(
            videos.update()
            .where(videos.c.id == video_id)
            .values(status=VideoStatus.FAILED, error_message="Source file not found")
        )
        return False

    # Get existing job record (created by admin API during upload)
    # If no job exists, the upload is still in progress - skip and retry later
    job = await get_existing_job(video_id)
    if job is None:
        print("  No transcoding job found - upload still in progress, will retry later")
        return False
    job_id = job["id"]

    # Always mark video as processing when we start/resume
    await database.execute(videos.update().where(videos.c.id == video_id).values(status=VideoStatus.PROCESSING))

    try:
        # ----------------------------------------------------------------
        # Step 1: Probe (skip if already done)
        # ----------------------------------------------------------------
        # Include "pending" and "claimed" which are set by admin API and worker API
        # when jobs are created or claimed but not yet started
        if job["current_step"] in [None, "pending", "claimed", TranscodingStep.PROBE]:
            await update_job_step(job_id, TranscodingStep.PROBE)
            print("  Step 1: Probing video info...")

            try:
                info = await get_video_info(source_file)
            except Exception as e:
                error_msg = f"Failed to probe video file: {e}"
                print(f"  ERROR: {error_msg}")
                # Probe failures are typically unrecoverable (corrupted/unsupported file)
                # Mark as final failure immediately
                await mark_job_failed(job_id, error_msg, final=True)
                await database.execute(
                    videos.update()
                    .where(videos.c.id == video_id)
                    .values(status=VideoStatus.FAILED, error_message=error_msg[:500])
                )
                return False

            print(f"  Source: {info['width']}x{info['height']}, {info['duration']:.1f}s")

            # Update video metadata
            await database.execute(
                videos.update()
                .where(videos.c.id == video_id)
                .values(
                    status=VideoStatus.PROCESSING,
                    duration=info["duration"],
                    source_width=info["width"],
                    source_height=info["height"],
                )
            )
            await checkpoint(job_id)

            # Check for shutdown after probe
            if state.shutdown_requested:
                print("  Shutdown requested, resetting video to pending...")
                await reset_video_to_pending(video_id)
                return False
        else:
            # Load existing video info
            video_row = await database.fetch_one(videos.select().where(videos.c.id == video_id))
            info = {
                "width": video_row["source_width"],
                "height": video_row["source_height"],
                "duration": video_row["duration"],
            }

        output_dir = VIDEOS_DIR / video_slug
        output_dir.mkdir(parents=True, exist_ok=True)

        # ----------------------------------------------------------------
        # Step 2: Thumbnail (generate if missing, even when resuming)
        # ----------------------------------------------------------------
        # Always check if thumbnail exists, regardless of current_step.
        # This handles cases where a remote worker crashed after updating
        # current_step but before uploading the thumbnail it generated locally.
        thumb_path = output_dir / "thumbnail.jpg"

        if not thumb_path.exists():
            await update_job_step(job_id, TranscodingStep.THUMBNAIL)
            print("  Step 2: Generating thumbnail...")
            thumbnail_time = min(5.0, info["duration"] / 4)
            await generate_thumbnail(source_file, thumb_path, thumbnail_time)
            await checkpoint(job_id)
        elif job["current_step"] in [None, TranscodingStep.PROBE, TranscodingStep.THUMBNAIL]:
            # Thumbnail exists but we're at an early step - just checkpoint
            await update_job_step(job_id, TranscodingStep.THUMBNAIL)
            print("  Step 2: Thumbnail already exists, skipping...")
            await checkpoint(job_id)
        else:
            # Thumbnail exists and job is resuming from a later step
            print("  Step 2: Thumbnail already exists, already past this step...")

        # Check for shutdown after thumbnail
        if state.shutdown_requested:
            print("  Shutdown requested, resetting video to pending...")
            await reset_video_to_pending(video_id)
            return False

        # ----------------------------------------------------------------
        # Step 3: Transcode each quality
        # ----------------------------------------------------------------
        await update_job_step(job_id, TranscodingStep.TRANSCODE)

        # Get streaming format from settings (Issue #222)
        transcoder_settings = await get_transcoder_settings()
        streaming_format = transcoder_settings.get("streaming_format", "hls_ts")
        print(f"  Output format: {streaming_format}")

        qualities = get_applicable_qualities(info["height"])
        if not qualities:
            qualities = [QUALITY_PRESETS[-1]]

        # Add "original" as a pseudo-quality for tracking
        original_quality = {"name": "original", "height": info["height"], "bitrate": "0k", "audio_bitrate": "0k"}
        all_qualities_for_tracking = [original_quality] + qualities

        print(f"  Step 3: Creating original + transcoding to: {[q['name'] for q in qualities]}")

        # Initialize quality progress records (including original)
        await init_quality_progress(job_id, all_qualities_for_tracking)

        successful_qualities = []
        failed_qualities = []
        total_qualities = len(qualities) + 1  # +1 for original

        # Create progress tracker for rate-limited database updates
        progress_tracker = ProgressTracker()

        # ----------------------------------------------------------------
        # Step 3a: Create "original" quality (remux without re-encoding)
        # ----------------------------------------------------------------
        original_status = await get_quality_status(job_id, "original")
        if original_status and original_status["status"] == QualityStatus.COMPLETED:
            print("    original: Already completed, skipping...")
            # Add to successful with source dimensions
            successful_qualities.append(
                {
                    "name": "original",
                    "width": info["width"],
                    "height": info["height"],
                    "bitrate": "0k",  # Will be calculated from file size
                    "is_original": True,
                }
            )
        elif await is_hls_playlist_complete(output_dir / "original.m3u8"):
            print("    original: Found complete playlist, marking complete...")
            await update_quality_status(job_id, "original", QualityStatus.COMPLETED)
            successful_qualities.append(
                {
                    "name": "original",
                    "width": info["width"],
                    "height": info["height"],
                    "bitrate": "0k",
                    "is_original": True,
                }
            )
        else:
            print("    original: Remuxing source to HLS (no re-encoding)...")
            await update_quality_status(job_id, "original", QualityStatus.IN_PROGRESS)

            async def original_progress_cb(progress: int):
                await progress_tracker.update_quality(job_id, "original", progress)
                # Original is first, so its progress is direct
                overall = int(progress / total_qualities)
                await progress_tracker.update_job(job_id, overall)

            try:
                success, error_detail, quality_info = await create_original_quality(
                    source_file, output_dir, info["duration"], original_progress_cb
                )

                if success:
                    await update_quality_status(job_id, "original", QualityStatus.COMPLETED)
                    successful_qualities.append(
                        {
                            "name": "original",
                            "width": info["width"],
                            "height": info["height"],
                            "bitrate": "0k",
                            "bitrate_bps": quality_info["bitrate_bps"] if quality_info else 0,
                            "is_original": True,
                        }
                    )
                    print(f"    original: Done ({info['width']}x{info['height']})")
                else:
                    error_msg = error_detail or "Remux failed"
                    await update_quality_status(job_id, "original", QualityStatus.FAILED, error_msg)
                    failed_qualities.append({"name": "original", "error": error_msg})
                    print(f"    original: Failed - {truncate_error(error_msg, ERROR_SUMMARY_MAX_LENGTH)}")
            except Exception as e:
                error_msg = str(e)
                await update_quality_status(job_id, "original", QualityStatus.FAILED, error_msg)
                failed_qualities.append({"name": "original", "error": error_msg})
                print(f"    original: Error - {e}")

            await checkpoint(job_id)

        # ----------------------------------------------------------------
        # Step 3b: Transcode to lower qualities (with parallel batching)
        # ----------------------------------------------------------------

        # Get parallel encoding count based on GPU capabilities
        parallel_count = get_recommended_parallel_sessions(state.gpu_caps)
        if parallel_count > 1:
            print(f"  Using parallel encoding: {parallel_count} qualities at a time")

        # Group qualities into batches for parallel processing
        quality_batches = group_qualities_by_resolution(qualities, parallel_count)

        # Shared progress tracking for parallel qualities (with lock for coroutine safety)
        quality_progresses: Dict[str, int] = {}
        progress_lock = asyncio.Lock()

        async def transcode_single_quality(
            quality: dict,
        ) -> Tuple[Optional[dict], Optional[dict]]:
            """
            Transcode a single quality.

            Returns:
                Tuple of (success_info, failure_info):
                - On success: (quality_info_dict, None)
                - On failure: (None, {"name": ..., "error": ...})
                - On skip (already complete): (quality_info_dict, None)
            """
            # Check for shutdown at the start to avoid starting new work
            if state.shutdown_requested:
                return (None, {"name": quality["name"], "error": "Shutdown requested"})

            quality_name = quality["name"]

            # Check if already completed
            status = await get_quality_status(job_id, quality_name)
            if status and status["status"] == QualityStatus.COMPLETED:
                print(f"    {quality_name}: Already completed, skipping...")
                # Get actual dimensions from existing segment
                # CMAF uses init.mp4 for track info (m4s segments are fragmented)
                if streaming_format == "cmaf":
                    first_segment = output_dir / quality_name / "init.mp4"
                else:
                    first_segment = output_dir / f"{quality_name}_0000.ts"
                if first_segment.exists():
                    actual_width, actual_height = await get_output_dimensions(first_segment)
                else:
                    actual_width = int(quality["height"] * 16 / 9)
                    if actual_width % 2 != 0:
                        actual_width += 1
                    actual_height = quality["height"]
                return (
                    {
                        "name": quality_name,
                        "width": actual_width,
                        "height": actual_height,
                        "bitrate": quality["bitrate"],
                    },
                    None,
                )

            # Check if playlist file is complete (from previous attempt)
            if streaming_format == "cmaf":
                playlist_path = output_dir / quality_name / "stream.m3u8"
            else:
                playlist_path = output_dir / f"{quality_name}.m3u8"
            if await is_hls_playlist_complete(playlist_path):
                print(f"    {quality_name}: Found complete playlist, marking complete...")
                await update_quality_status(job_id, quality_name, QualityStatus.COMPLETED)
                # Get actual dimensions from existing segment
                # CMAF uses init.mp4 for track info (m4s segments are fragmented)
                if streaming_format == "cmaf":
                    first_segment = output_dir / quality_name / "init.mp4"
                else:
                    first_segment = output_dir / f"{quality_name}_0000.ts"
                if first_segment.exists():
                    actual_width, actual_height = await get_output_dimensions(first_segment)
                else:
                    actual_width = int(quality["height"] * 16 / 9)
                    if actual_width % 2 != 0:
                        actual_width += 1
                    actual_height = quality["height"]
                return (
                    {
                        "name": quality_name,
                        "width": actual_width,
                        "height": actual_height,
                        "bitrate": quality["bitrate"],
                    },
                    None,
                )

            # Transcode this quality
            print(f"    {quality_name}: Transcoding...")
            await update_quality_status(job_id, quality_name, QualityStatus.IN_PROGRESS)

            async def progress_cb(progress: int, q_name=quality_name):
                # Update per-quality progress
                await progress_tracker.update_quality(job_id, q_name, progress)
                # Track in shared dict for overall progress calculation (with lock)
                async with progress_lock:
                    quality_progresses[q_name] = progress
                    # Calculate overall progress: original is done (100%), plus average of all qualities
                    # Original contributes 1/total_qualities, remaining qualities share the rest
                    original_contribution = 100 / total_qualities
                    quality_avg = sum(quality_progresses.values()) / len(qualities) if qualities else 0
                    quality_contribution = quality_avg * (1 - 1 / total_qualities)
                    overall = int(original_contribution + quality_contribution)
                await progress_tracker.update_job(job_id, min(overall, 99))  # Cap at 99 until finalized

            try:
                success, error_detail = await transcode_quality_with_progress(
                    source_file,
                    output_dir,
                    quality,
                    info["duration"],
                    progress_cb,
                    gpu_caps=state.gpu_caps,
                    streaming_format=streaming_format,
                )

                if success:
                    await update_quality_status(job_id, quality_name, QualityStatus.COMPLETED)
                    # Get actual dimensions from transcoded segment
                    # CMAF uses init.mp4 for track info (m4s segments are fragmented)
                    if streaming_format == "cmaf":
                        first_segment = output_dir / quality_name / "init.mp4"
                    else:
                        first_segment = output_dir / f"{quality_name}_0000.ts"
                    if first_segment.exists():
                        actual_width, actual_height = await get_output_dimensions(first_segment)
                    else:
                        actual_width = int(quality["height"] * 16 / 9)
                        if actual_width % 2 != 0:
                            actual_width += 1
                        actual_height = quality["height"]
                    print(f"    {quality_name}: Done ({actual_width}x{actual_height})")
                    return (
                        {
                            "name": quality_name,
                            "width": actual_width,
                            "height": actual_height,
                            "bitrate": quality["bitrate"],
                        },
                        None,
                    )
                else:
                    error_msg = error_detail or "Transcoding process returned non-zero exit code"
                    await update_quality_status(job_id, quality_name, QualityStatus.FAILED, error_msg)
                    print(f"    {quality_name}: Failed")
                    return (None, {"name": quality_name, "error": error_msg})
            except Exception as e:
                error_msg = str(e)
                await update_quality_status(job_id, quality_name, QualityStatus.FAILED, error_msg)
                print(f"    {quality_name}: Error - {e}")
                return (None, {"name": quality_name, "error": error_msg})

        # Process each batch of qualities
        for batch_idx, batch in enumerate(quality_batches):
            # Check for shutdown before processing each batch
            if state.shutdown_requested:
                print("  Shutdown requested, resetting video to pending...")
                await reset_video_to_pending(video_id)
                return False

            if len(batch) > 1:
                print(f"  Processing batch {batch_idx + 1}/{len(quality_batches)}: {[q['name'] for q in batch]}")

            # Run batch in parallel
            tasks = [transcode_single_quality(q) for q in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results - collect successes and failures from returned tuples
            for quality, result in zip(batch, results):
                if isinstance(result, Exception):
                    # Unexpected exception during task execution
                    error_msg = str(result)
                    await update_quality_status(job_id, quality["name"], QualityStatus.FAILED, error_msg)
                    failed_qualities.append({"name": quality["name"], "error": error_msg})
                    print(f"    {quality['name']}: Unexpected error - {result}")
                elif isinstance(result, tuple):
                    success_info, failure_info = result
                    if success_info is not None:
                        successful_qualities.append(success_info)
                    if failure_info is not None:
                        failed_qualities.append(failure_info)

            # Checkpoint after each batch
            await checkpoint(job_id)

        # ----------------------------------------------------------------
        # Step 3c: Re-verify all qualities are complete before finalizing
        # This catches any qualities that were reset mid-flight or missed
        # ----------------------------------------------------------------
        max_verification_passes = 3
        for verification_pass in range(max_verification_passes):
            # Check for any incomplete qualities
            incomplete_qualities = []
            for quality in all_qualities_for_tracking:
                quality_name = quality["name"]
                # Skip if already in successful list
                if any(sq["name"] == quality_name for sq in successful_qualities):
                    continue
                # Skip if already in failed list
                if any(fq["name"] == quality_name for fq in failed_qualities):
                    continue

                # Check database status
                status = await get_quality_status(job_id, quality_name)
                if status and status["status"] != QualityStatus.COMPLETED:
                    incomplete_qualities.append(quality)

            if not incomplete_qualities:
                break

            print(
                f"  Verification pass {verification_pass + 1}: Found {len(incomplete_qualities)} incomplete qualities"
            )

            for quality in incomplete_qualities:
                quality_name = quality["name"]

                # Check for shutdown
                if state.shutdown_requested:
                    print("  Shutdown requested, resetting video to pending...")
                    await reset_video_to_pending(video_id)
                    return False

                # Check if playlist file is actually complete on disk
                playlist_path = output_dir / f"{quality_name}.m3u8"
                if await is_hls_playlist_complete(playlist_path):
                    print(f"    {quality_name}: Found complete playlist on disk, marking complete...")
                    await update_quality_status(job_id, quality_name, QualityStatus.COMPLETED)
                    # Add to successful qualities
                    if quality_name == "original":
                        successful_qualities.append(
                            {
                                "name": "original",
                                "width": info["width"],
                                "height": info["height"],
                                "bitrate": "0k",
                                "is_original": True,
                            }
                        )
                    else:
                        first_segment = output_dir / f"{quality_name}_0000.ts"
                        if first_segment.exists():
                            actual_width, actual_height = await get_output_dimensions(first_segment)
                        else:
                            actual_width = int(quality["height"] * 16 / 9)
                            if actual_width % 2 != 0:
                                actual_width += 1
                            actual_height = quality["height"]
                        successful_qualities.append(
                            {
                                "name": quality_name,
                                "width": actual_width,
                                "height": actual_height,
                                "bitrate": quality["bitrate"],
                            }
                        )
                    continue

                # Need to transcode this quality
                print(f"    {quality_name}: Re-processing...")
                await update_quality_status(job_id, quality_name, QualityStatus.IN_PROGRESS)

                try:
                    if quality_name == "original":
                        success, error_detail, quality_info = await create_original_quality(
                            source_file, output_dir, info["duration"], None
                        )
                        if success:
                            await update_quality_status(job_id, "original", QualityStatus.COMPLETED)
                            successful_qualities.append(
                                {
                                    "name": "original",
                                    "width": info["width"],
                                    "height": info["height"],
                                    "bitrate": "0k",
                                    "bitrate_bps": quality_info["bitrate_bps"] if quality_info else 0,
                                    "is_original": True,
                                }
                            )
                            print(f"    {quality_name}: Done")
                        else:
                            error_msg = error_detail or "Remux failed"
                            await update_quality_status(job_id, "original", QualityStatus.FAILED, error_msg)
                            failed_qualities.append({"name": "original", "error": error_msg})
                            print(f"    {quality_name}: Failed")
                    else:
                        success, error_detail = await transcode_quality_with_progress(
                            source_file,
                            output_dir,
                            quality,
                            info["duration"],
                            None,
                            gpu_caps=state.gpu_caps,
                            streaming_format=streaming_format,
                        )
                        if success:
                            await update_quality_status(job_id, quality_name, QualityStatus.COMPLETED)
                            # CMAF uses init.mp4 for track info (m4s segments are fragmented)
                            if streaming_format == "cmaf":
                                first_segment = output_dir / quality_name / "init.mp4"
                            else:
                                first_segment = output_dir / f"{quality_name}_0000.ts"
                            if first_segment.exists():
                                actual_width, actual_height = await get_output_dimensions(first_segment)
                            else:
                                actual_width = int(quality["height"] * 16 / 9)
                                if actual_width % 2 != 0:
                                    actual_width += 1
                                actual_height = quality["height"]
                            successful_qualities.append(
                                {
                                    "name": quality_name,
                                    "width": actual_width,
                                    "height": actual_height,
                                    "bitrate": quality["bitrate"],
                                }
                            )
                            print(f"    {quality_name}: Done")
                        else:
                            error_msg = error_detail or "Transcoding failed"
                            await update_quality_status(job_id, quality_name, QualityStatus.FAILED, error_msg)
                            failed_qualities.append({"name": quality_name, "error": error_msg})
                            print(f"    {quality_name}: Failed")
                except Exception as e:
                    error_msg = str(e)
                    await update_quality_status(job_id, quality_name, QualityStatus.FAILED, error_msg)
                    failed_qualities.append({"name": quality_name, "error": error_msg})
                    print(f"    {quality_name}: Error - {e}")

                await checkpoint(job_id)

        # Report results
        if not successful_qualities:
            # All quality variants failed
            failed_summary = ", ".join(
                [f"{q['name']}: {truncate_error(q['error'], ERROR_SUMMARY_MAX_LENGTH)}" for q in failed_qualities]
            )
            error_message = f"All {len(failed_qualities)} quality variant(s) failed. Details: {failed_summary}"
            print(f"  FAILURE: {error_message}")
            raise RuntimeError(error_message)
        elif failed_qualities:
            # Partial success - some qualities failed
            completed = len(successful_qualities)
            print(
                f"  WARNING: Partial transcoding success - {completed}/{total_qualities} quality variants completed"
            )
            print(f"  Failed variants: {', '.join([q['name'] for q in failed_qualities])}")
            for failed in failed_qualities:
                print(f"    - {failed['name']}: {truncate_error(failed['error'], ERROR_DETAIL_MAX_LENGTH)}")

        # ----------------------------------------------------------------
        # Step 4: Generate master playlist
        # ----------------------------------------------------------------
        await update_job_step(job_id, TranscodingStep.MASTER_PLAYLIST)
        print("  Step 4: Generating master playlist...")

        if streaming_format == "cmaf":
            # Use CMAF-specific master playlist generator
            # Get codec from settings for CMAF manifest
            primary_codec = transcoder_settings.get("streaming_codec", "h264")
            await generate_master_playlist_cmaf(output_dir, successful_qualities, primary_codec)
            # Also generate DASH manifest if enabled
            enable_dash = transcoder_settings.get("streaming_enable_dash", True)
            if enable_dash:
                print("  Generating DASH manifest...")
                await generate_dash_manifest(output_dir, successful_qualities, primary_codec)
        else:
            await generate_master_playlist(output_dir, successful_qualities)
        await checkpoint(job_id)

        # ----------------------------------------------------------------
        # Step 5: Finalize
        # ----------------------------------------------------------------
        await update_job_step(job_id, TranscodingStep.FINALIZE)
        print("  Step 5: Finalizing...")

        # Save quality info to database
        for q in successful_qualities:
            # Check if quality record already exists
            existing = await database.fetch_one(
                video_qualities.select().where(
                    (video_qualities.c.video_id == video_id) & (video_qualities.c.quality == q["name"])
                )
            )

            if not existing:
                await database.execute(
                    video_qualities.insert().values(
                        video_id=video_id,
                        quality=q["name"],
                        width=q["width"],
                        height=q["height"],
                        bitrate=int(q["bitrate"].replace("k", "")),
                    )
                )

        # Mark video as ready
        # Only set published_at if not already set (preserve date for re-transcoded videos)
        video_row = await fetch_one_with_retry(videos.select().where(videos.c.id == video_id))
        video_updates = {
            "status": VideoStatus.READY,
            "streaming_format": streaming_format,
        }
        # Set primary_codec for CMAF (from settings)
        if streaming_format == "cmaf":
            video_updates["primary_codec"] = transcoder_settings.get("streaming_codec", "h264")
        else:
            video_updates["primary_codec"] = "h264"  # HLS/TS always uses H.264
        if video_row and video_row["published_at"] is None:
            video_updates["published_at"] = datetime.now(timezone.utc)

        await database.execute(videos.update().where(videos.c.id == video_id).values(**video_updates))

        # Mark job completed
        await mark_job_completed(job_id)

        # NOTE: Source file is intentionally kept for potential future re-transcoding
        # (e.g., if new quality presets are added or original quality is needed)
        print(f"  Done! Video is ready. Source file preserved at: {source_file}")
        return True

    except Exception as e:
        print(f"  Error: {e}")

        # Check if we should retry
        job = await database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.id == job_id))

        if job and job["attempt_number"] < job["max_attempts"]:
            # Will be retried on next worker restart or stale job check
            await mark_job_failed(job_id, str(e), final=False)
            await database.execute(
                videos.update()
                .where(videos.c.id == video_id)
                .values(
                    status=VideoStatus.FAILED,
                    error_message=f"Attempt {job['attempt_number']} failed: {str(e)[:400]}",
                )
            )
            # Send alert for job failure (fire-and-forget, will retry)
            send_alert_fire_and_forget(
                alert_job_failed(
                    video_id=video_id,
                    video_slug=video_slug,
                    attempt_number=job["attempt_number"],
                    error=str(e),
                    will_retry=True,
                )
            )
        else:
            # Final failure - mark job as completed (finished, even though failed)
            await mark_job_failed(job_id, str(e), final=True)
            await database.execute(
                videos.update()
                .where(videos.c.id == video_id)
                .values(
                    status=VideoStatus.FAILED,
                    error_message=str(e)[:500],
                )
            )
            # Send alert for max retries exceeded (fire-and-forget)
            if job:
                send_alert_fire_and_forget(
                    alert_max_retries_exceeded(
                        video_id=video_id,
                        video_slug=video_slug,
                        max_attempts=job["max_attempts"],
                        last_error=str(e),
                    )
                )

        return False


async def check_stale_jobs(state: Optional[WorkerState] = None):
    """
    Periodic check for stale jobs that might need recovery.
    Called periodically during the worker loop.

    Args:
        state: Optional WorkerState instance. If not provided, uses/creates the
               default global state.
    """
    if state is None:
        state = get_worker_state()

    # Get stale timeout from settings service
    settings = await get_transcoder_settings()
    job_stale_timeout = settings["job_stale_timeout"]

    stale_threshold = datetime.now(timezone.utc) - timedelta(seconds=job_stale_timeout)

    stale_jobs = await database.fetch_all(
        transcoding_jobs.select().where(
            transcoding_jobs.c.completed_at.is_(None)
            & transcoding_jobs.c.last_checkpoint.isnot(None)
            & (transcoding_jobs.c.last_checkpoint < stale_threshold)
            & (transcoding_jobs.c.worker_id != state.worker_id)  # Not our own jobs
        )
    )

    for job in stale_jobs:
        # Double-check staleness with timezone normalization as a safety measure.
        # This ensures we handle edge cases where timezone info might affect the
        # comparison (e.g., DST transitions, server timezone changes).
        last_checkpoint = ensure_utc(job["last_checkpoint"])
        if last_checkpoint >= stale_threshold:
            # Not actually stale after timezone normalization
            continue

        video = await database.fetch_one(videos.select().where(videos.c.id == job["video_id"]))

        if not video:
            continue

        if job["attempt_number"] >= job["max_attempts"]:
            print(f"Stale job for '{video['slug']}' exceeded max retries, marking failed")
            async with database.transaction():
                await mark_job_failed(job["id"], "Max retry attempts exceeded (stale)", final=True)
                await database.execute(
                    videos.update()
                    .where(videos.c.id == job["video_id"])
                    .values(status=VideoStatus.FAILED, error_message="Max retry attempts exceeded")
                )
            # Clean up source file after permanent failure
            if cleanup_source_file(job["video_id"]):
                print(f"  Cleaned up source file for video {job['video_id']}")
            # Send alert for max retries exceeded (fire-and-forget)
            send_alert_fire_and_forget(
                alert_max_retries_exceeded(
                    video_id=job["video_id"],
                    video_slug=video["slug"],
                    max_attempts=job["max_attempts"],
                    last_error=job.get("last_error"),
                )
            )
        else:
            print(f"Found stale job for '{video['slug']}', resetting for retry")
            async with database.transaction():
                await reset_job_for_retry(job["id"])
                await database.execute(
                    videos.update().where(videos.c.id == job["video_id"]).values(status=VideoStatus.PENDING)
                )
            # Send alert for stale job recovered (fire-and-forget)
            send_alert_fire_and_forget(
                alert_stale_job_recovered(
                    video_id=job["video_id"],
                    video_slug=video["slug"],
                    attempt_number=job["attempt_number"],
                    worker_id=job.get("worker_id"),
                )
            )


async def cleanup_expired_archives():
    """
    Delete archived videos that have exceeded the retention period.
    Called periodically during the worker loop.
    """
    if ARCHIVE_RETENTION_DAYS <= 0:
        return  # Cleanup disabled

    cutoff = datetime.now(timezone.utc) - timedelta(days=ARCHIVE_RETENTION_DAYS)

    expired = await database.fetch_all(
        videos.select().where(videos.c.deleted_at.isnot(None) & (videos.c.deleted_at < cutoff))
    )

    if not expired:
        return

    print(f"Found {len(expired)} archived video(s) past retention period, cleaning up...")

    for video in expired:
        video_id = video["id"]
        slug = video["slug"]

        try:
            # Delete database records atomically
            async with database.transaction():
                # Get job ID for quality_progress cleanup
                job = await database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.video_id == video_id))
                if job:
                    await database.execute(quality_progress.delete().where(quality_progress.c.job_id == job["id"]))
                await database.execute(transcoding_jobs.delete().where(transcoding_jobs.c.video_id == video_id))
                await database.execute(playback_sessions.delete().where(playback_sessions.c.video_id == video_id))
                await database.execute(transcriptions.delete().where(transcriptions.c.video_id == video_id))
                await database.execute(video_qualities.delete().where(video_qualities.c.video_id == video_id))
                # Delete video record last
                await database.execute(videos.delete().where(videos.c.id == video_id))

            # Delete files after successful transaction
            video_dir = VIDEOS_DIR / slug
            if video_dir.exists():
                shutil.rmtree(video_dir)

            archive_dir = ARCHIVE_DIR / slug
            if archive_dir.exists():
                shutil.rmtree(archive_dir)

            # Delete source file from uploads if still there
            for ext in SUPPORTED_VIDEO_EXTENSIONS:
                upload_file = UPLOADS_DIR / f"{video_id}{ext}"
                if upload_file.exists():
                    upload_file.unlink()

            print(f"  Permanently deleted expired archive: {slug}")

        except Exception as e:
            print(f"  Error cleaning up expired archive '{slug}': {e}")


async def worker_loop(state: Optional[WorkerState] = None):
    """
    Main worker loop - process pending videos using event-driven architecture.

    Uses filesystem watching (inotify via watchdog) to detect new uploads immediately,
    with a fallback poll interval for edge cases. This eliminates the constant 5-second
    polling that wasted resources and added latency.

    Args:
        state: Optional WorkerState instance. If not provided, uses/creates the
               default global state. Pass a custom state for testing.
    """
    # Use provided state or get/create default
    if state is None:
        state = get_worker_state()
    else:
        # Register provided state as the default for signal handlers
        set_worker_state(state)

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    await database.connect()
    await configure_database()
    print(f"Transcoding worker started (ID: {state.worker_id[:8]})")

    # Detect GPU capabilities for hardware-accelerated encoding
    from worker.hwaccel import detect_gpu_capabilities

    state.gpu_caps = await detect_gpu_capabilities()
    if state.gpu_caps:
        print(f"  GPU detected: {state.gpu_caps.device_name}")
        print(f"    Type: {state.gpu_caps.hwaccel_type.value}")
        encoders = [e.name for codec_encoders in state.gpu_caps.encoders.values() for e in codec_encoders]
        print(f"    Encoders: {', '.join(encoders)}")
        print(f"    Max sessions: {state.gpu_caps.max_concurrent_sessions}")
    else:
        print("  No GPU acceleration available, using CPU encoding")

    # Initialize the upload event for signaling between filesystem watcher and main loop
    loop = asyncio.get_running_loop()
    state.new_upload_event = asyncio.Event()

    # Get worker settings from database with caching
    worker_settings = await get_transcoder_settings()
    fallback_poll_interval = worker_settings["fallback_poll_interval"]
    debounce_delay = worker_settings["debounce_delay"]

    # Start filesystem watcher if available
    observer = None
    if WORKER_USE_FILESYSTEM_WATCHER and WATCHDOG_AVAILABLE:
        observer = start_filesystem_watcher(loop, state.new_upload_event, debounce_delay=debounce_delay)
        if observer:
            print(f"  Mode: Event-driven (inotify) with {fallback_poll_interval}s fallback poll")
        else:
            print(f"  Mode: Polling every {fallback_poll_interval}s (watcher failed)")
    else:
        if not WORKER_USE_FILESYSTEM_WATCHER:
            print(f"  Mode: Polling every {fallback_poll_interval}s (watcher disabled)")
        else:
            print(f"  Mode: Polling every {fallback_poll_interval}s (watchdog not available)")

    print("Watching for new videos...")

    # Recover any interrupted jobs from previous crashes
    await recover_interrupted_jobs(state)

    # Get count of recovered jobs from alert metrics for startup notification
    recovered_count = get_alert_metrics().stale_jobs_recovered

    # Send worker startup alert (fire-and-forget)
    gpu_info = state.gpu_caps.device_name if state.gpu_caps else None
    send_alert_fire_and_forget(
        alert_worker_startup(
            worker_id=state.worker_id,
            gpu_info=gpu_info,
            recovered_jobs=recovered_count,
        )
    )

    last_stale_check = datetime.now(timezone.utc)
    stale_check_interval = 300  # Check every 5 minutes

    last_archive_cleanup = datetime.now(timezone.utc)
    archive_cleanup_interval = 3600  # Check every hour

    # Determine wait behavior based on watcher availability
    use_event_driven = observer is not None

    try:
        while not state.shutdown_requested:
            # Find pending videos
            query = videos.select().where(videos.c.status == VideoStatus.PENDING).order_by(videos.c.created_at)
            pending = await database.fetch_all(query)

            for video in pending:
                if state.shutdown_requested:
                    print("Shutdown requested, stopping worker loop...")
                    break
                result = await process_video_resumable(video["id"], video["slug"], state)
                if result:
                    print(f"Successfully completed: {video['slug']}")
                elif state.shutdown_requested:
                    print(f"Shutdown interrupted: {video['slug']}")
                else:
                    print(f"Failed to process: {video['slug']}")

            # Periodic stale job check
            if (
                not state.shutdown_requested
                and (datetime.now(timezone.utc) - last_stale_check).total_seconds() > stale_check_interval
            ):
                await check_stale_jobs(state)
                last_stale_check = datetime.now(timezone.utc)

            # Periodic archive cleanup
            if (
                not state.shutdown_requested
                and (datetime.now(timezone.utc) - last_archive_cleanup).total_seconds() > archive_cleanup_interval
            ):
                await cleanup_expired_archives()
                last_archive_cleanup = datetime.now(timezone.utc)

            # Wait for new uploads or fallback timeout
            if not state.shutdown_requested:
                # Refresh settings periodically (cache has 60s TTL)
                worker_settings = await get_transcoder_settings()
                poll_interval = worker_settings["fallback_poll_interval"]

                if use_event_driven:
                    # Event-driven: wait for filesystem event OR fallback timeout
                    try:
                        await asyncio.wait_for(state.new_upload_event.wait(), timeout=poll_interval)
                        # Event was set - new file detected
                        state.new_upload_event.clear()
                    except asyncio.TimeoutError:
                        # Timeout - fallback poll, this is expected
                        pass
                else:
                    # Polling mode: just wait the fallback interval
                    await asyncio.sleep(poll_interval)

    except KeyboardInterrupt:
        print("\nKeyboardInterrupt received.")
    finally:
        # Stop filesystem watcher
        if observer is not None:
            print("Stopping filesystem watcher...")
            stop_filesystem_watcher(observer)

        # On shutdown, reset all state for jobs being processed by this worker
        print("Cleaning up: resetting this worker's in-progress jobs...")
        try:
            # Find incomplete jobs for this worker
            jobs_query = transcoding_jobs.select().where(
                (transcoding_jobs.c.worker_id == state.worker_id) & (transcoding_jobs.c.completed_at.is_(None))
            )
            jobs = await database.fetch_all(jobs_query)

            reset_count = 0
            for job in jobs:
                video_id = job["video_id"]
                job_id = job["id"]

                # Reset video status to pending (only if still processing)
                video = await database.fetch_one(videos.select().where(videos.c.id == video_id))
                if video and video["status"] == VideoStatus.PROCESSING:
                    await database.execute(
                        videos.update().where(videos.c.id == video_id).values(status=VideoStatus.PENDING)
                    )

                # Reset job so it can be picked up again
                await database.execute(
                    transcoding_jobs.update()
                    .where(transcoding_jobs.c.id == job_id)
                    .values(started_at=None, current_step=None, worker_id=None, progress_percent=0)
                )

                # Reset quality_progress records that were in_progress
                await database.execute(
                    quality_progress.update()
                    .where(quality_progress.c.job_id == job_id)
                    .where(quality_progress.c.status == "in_progress")
                    .values(status="pending", progress_percent=0)
                )

                reset_count += 1

            if reset_count > 0:
                print(f"Reset {reset_count} job(s) to pending state.")
            else:
                print("No jobs to reset.")

            # Send worker shutdown alert (fire-and-forget)
            send_alert_fire_and_forget(
                alert_worker_shutdown(
                    worker_id=state.worker_id,
                    jobs_reset=reset_count,
                )
            )
        except Exception as e:
            print(f"Error during cleanup: {e}")

        await database.disconnect()
        print("Worker stopped gracefully.")


if __name__ == "__main__":
    asyncio.run(worker_loop())
