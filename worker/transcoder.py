#!/usr/bin/env python3
"""
Video transcoding worker with checkpoint-based resumable transcoding.
Monitors the uploads directory for new videos and transcodes them to HLS.
Uses filesystem watching (inotify) for event-driven processing instead of polling.
Supports crash recovery and per-quality progress tracking.
"""
import asyncio
import json
import math
import re
import shutil
import signal
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Callable, Tuple, Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import (
    VIDEOS_DIR, UPLOADS_DIR, QUALITY_PRESETS, HLS_SEGMENT_DURATION,
    CHECKPOINT_INTERVAL, JOB_STALE_TIMEOUT, MAX_RETRY_ATTEMPTS,
    RETRY_BACKOFF_BASE, CLEANUP_PARTIAL_ON_FAILURE, KEEP_COMPLETED_QUALITIES,
    WORKER_USE_FILESYSTEM_WATCHER, WORKER_FALLBACK_POLL_INTERVAL, WORKER_DEBOUNCE_DELAY,
)
from api.database import database, videos, video_qualities, transcoding_jobs, quality_progress

# Conditional import for filesystem watching
if WORKER_USE_FILESYSTEM_WATCHER:
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileModifiedEvent
        WATCHDOG_AVAILABLE = True
    except ImportError:
        print("Warning: watchdog not installed. Falling back to polling mode.")
        print("Install with: pip install watchdog")
        WATCHDOG_AVAILABLE = False
else:
    WATCHDOG_AVAILABLE = False

# Generate unique worker ID for this instance
WORKER_ID = str(uuid.uuid4())

# Global shutdown flag for graceful shutdown
shutdown_requested = False

# Global event for signaling new uploads (used by filesystem watcher)
new_upload_event = None  # Will be initialized as asyncio.Event in worker_loop

# Maximum video duration allowed (1 week in seconds)
MAX_DURATION_SECONDS = 7 * 24 * 60 * 60  # 604800 seconds

# Error message truncation limits for logging
MAX_ERROR_SUMMARY_LENGTH = 100  # Characters per quality in total failure summary
MAX_ERROR_DETAIL_LENGTH = 200   # Characters per quality in partial failure details


def signal_handler(sig, frame):
    """Handle shutdown signals gracefully."""
    global shutdown_requested
    sig_name = signal.strsignal(sig) if hasattr(signal, 'strsignal') else str(sig)
    print(f"\n{sig_name} received, finishing current job and shutting down gracefully...")
    shutdown_requested = True


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

    VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.webm', '.mov', '.avi'}

    def __init__(self, loop: asyncio.AbstractEventLoop, event: asyncio.Event):
        super().__init__()
        self.loop = loop
        self.event = event
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
            self._debounce_timer = threading.Timer(WORKER_DEBOUNCE_DELAY, self._trigger_event)
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


def start_filesystem_watcher(loop: asyncio.AbstractEventLoop, event: asyncio.Event) -> Optional[Observer]:
    """
    Start the filesystem watcher for the uploads directory.
    Returns the Observer instance or None if watchdog is not available.
    """
    if not WATCHDOG_AVAILABLE:
        return None

    try:
        handler = UploadEventHandler(loop, event)
        observer = Observer()
        observer.schedule(handler, str(UPLOADS_DIR), recursive=False)
        observer.start()
        print(f"  Filesystem watcher started on: {UPLOADS_DIR}")
        return observer
    except Exception as e:
        print(f"  Warning: Failed to start filesystem watcher: {e}")
        print(f"  Falling back to polling mode.")
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
                    if hasattr(handler, 'cleanup'):
                        handler.cleanup()
        except Exception as e:
            print(f"  Warning: Error stopping filesystem watcher: {e}")


def get_video_info(input_path: Path) -> dict:
    """Get video metadata using ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(input_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")

    data = json.loads(result.stdout)

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


def get_output_dimensions(segment_path: Path) -> Tuple[int, int]:
    """Get actual dimensions from a transcoded segment file."""
    cmd = [
        "ffprobe", "-v", "quiet", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json", str(segment_path)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return (0, 0)

    try:
        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        if not streams:
            return (0, 0)
        stream = streams[0]
        width = int(stream.get("width", 0))
        height = int(stream.get("height", 0))
        return (width, height)
    except (json.JSONDecodeError, ValueError, KeyError):
        return (0, 0)


def generate_thumbnail(input_path: Path, output_path: Path, timestamp: float = 5.0):
    """Generate a thumbnail from the video."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-ss", str(timestamp),
        "-vframes", "1",
        "-vf", "scale=640:-1",
        str(output_path)
    ]
    subprocess.run(cmd, capture_output=True, check=True)


async def transcode_quality_with_progress(
    input_path: Path,
    output_dir: Path,
    quality: dict,
    duration: float,
    progress_callback: Optional[Callable[[int], None]] = None
) -> Tuple[bool, Optional[str]]:
    """
    Transcode a single quality variant with progress tracking.
    
    Returns:
        Tuple[bool, Optional[str]]: (success, error_message) where error_message 
        is None on success or contains the ffmpeg error details on failure.
    """
    name = quality["name"]
    height = quality["height"]
    bitrate = quality["bitrate"]
    audio_bitrate = quality["audio_bitrate"]

    playlist_name = f"{name}.m3u8"
    segment_pattern = f"{name}_%04d.ts"
    scale_filter = f"scale=-2:{height}"

    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", f"{int(bitrate.replace('k', '')) * 2}k",
        "-vf", scale_filter,
        "-c:a", "aac", "-b:a", audio_bitrate, "-ac", "2",
        "-hls_time", str(HLS_SEGMENT_DURATION),
        "-hls_list_size", "0",
        "-hls_segment_filename", str(output_dir / segment_pattern),
        "-progress", "pipe:1",  # Output progress to stdout
        "-f", "hls",
        str(output_dir / playlist_name)
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    last_progress_update = 0

    # Parse progress from stdout
    while True:
        line = await process.stdout.readline()
        if not line:
            break

        line_str = line.decode('utf-8', errors='ignore').strip()

        # Parse time from progress output (format: out_time_ms=123456789)
        if line_str.startswith('out_time_ms='):
            try:
                time_ms = int(line_str.split('=')[1])
                current_seconds = time_ms / 1000000.0
                if duration > 0:
                    progress = min(100, int(current_seconds / duration * 100))
                    # Only update if progress changed significantly
                    if progress > last_progress_update:
                        last_progress_update = progress
                        if progress_callback:
                            await progress_callback(progress)
            except (ValueError, IndexError):
                pass

    await process.wait()

    if process.returncode != 0:
        stderr = await process.stderr.read()
        error_msg = stderr.decode('utf-8', errors='ignore')
        print(f"  ERROR: Failed to transcode {name}")
        print(f"  Full error output: {error_msg}")
        return False, error_msg

    return True, None


def generate_master_playlist(output_dir: Path, completed_qualities: List[dict]):
    """Generate master HLS playlist from completed quality variants."""
    master_content = "#EXTM3U\n#EXT-X-VERSION:3\n\n"

    for quality in completed_qualities:
        name = quality["name"]
        width = quality["width"]
        height = quality["height"]
        bitrate = int(quality["bitrate"].replace("k", "")) * 1000

        master_content += f'#EXT-X-STREAM-INF:BANDWIDTH={bitrate},RESOLUTION={width}x{height}\n'
        master_content += f'{name}.m3u8\n'

    (output_dir / "master.m3u8").write_text(master_content)


async def cleanup_partial_output(video_slug: str, keep_completed_qualities: bool = True, completed_quality_names: Optional[List[str]] = None):
    """Clean up partial transcoding output."""
    output_dir = VIDEOS_DIR / video_slug

    if not output_dir.exists():
        return

    if not keep_completed_qualities or not completed_quality_names:
        # Full cleanup
        shutil.rmtree(output_dir, ignore_errors=True)
        output_dir.mkdir(exist_ok=True)
        return

    # Selective cleanup - keep completed quality files
    for file in output_dir.iterdir():
        # Match quality files like "1080p.m3u8" or "1080p_0001.ts"
        quality_match = re.match(r'(\d+p)(_\d+\.ts|\.m3u8)$', file.name)
        if quality_match:
            quality = quality_match.group(1)
            if quality not in completed_quality_names:
                file.unlink()  # Remove incomplete quality files

    # Always remove master.m3u8 (regenerate at end)
    master_path = output_dir / "master.m3u8"
    if master_path.exists():
        master_path.unlink()


# ============================================================================
# Job Management Functions
# ============================================================================

async def get_or_create_job(video_id: int) -> dict:
    """Get existing job or create a new one for the video."""
    # Check for existing job
    query = transcoding_jobs.select().where(transcoding_jobs.c.video_id == video_id)
    job = await database.fetch_one(query)

    if job:
        return dict(job)

    # Create new job
    result = await database.execute(
        transcoding_jobs.insert().values(
            video_id=video_id,
            worker_id=WORKER_ID,
            current_step=None,
            progress_percent=0,
            started_at=datetime.utcnow(),
            last_checkpoint=datetime.utcnow(),
            attempt_number=1,
            max_attempts=MAX_RETRY_ATTEMPTS,
        )
    )

    query = transcoding_jobs.select().where(transcoding_jobs.c.id == result)
    return dict(await database.fetch_one(query))


async def update_job_step(job_id: int, step: str):
    """Update the current processing step."""
    await database.execute(
        transcoding_jobs.update()
        .where(transcoding_jobs.c.id == job_id)
        .values(
            current_step=step,
            last_checkpoint=datetime.utcnow(),
        )
    )


async def update_job_progress(job_id: int, progress: int):
    """Update overall job progress percentage."""
    await database.execute(
        transcoding_jobs.update()
        .where(transcoding_jobs.c.id == job_id)
        .values(
            progress_percent=progress,
            last_checkpoint=datetime.utcnow(),
        )
    )


async def checkpoint(job_id: int):
    """Update the checkpoint timestamp."""
    await database.execute(
        transcoding_jobs.update()
        .where(transcoding_jobs.c.id == job_id)
        .values(last_checkpoint=datetime.utcnow())
    )


async def mark_job_completed(job_id: int):
    """Mark job as successfully completed."""
    await database.execute(
        transcoding_jobs.update()
        .where(transcoding_jobs.c.id == job_id)
        .values(
            completed_at=datetime.utcnow(),
            progress_percent=100,
            last_checkpoint=datetime.utcnow(),
        )
    )


async def mark_job_failed(job_id: int, error: str):
    """Mark job as failed."""
    await database.execute(
        transcoding_jobs.update()
        .where(transcoding_jobs.c.id == job_id)
        .values(
            last_error=error[:500],
            last_checkpoint=datetime.utcnow(),
        )
    )


async def reset_job_for_retry(job_id: int):
    """Reset a job for retry, incrementing attempt number."""
    job = await database.fetch_one(
        transcoding_jobs.select().where(transcoding_jobs.c.id == job_id)
    )

    if not job:
        return

    new_attempt = (job["attempt_number"] or 1) + 1

    await database.execute(
        transcoding_jobs.update()
        .where(transcoding_jobs.c.id == job_id)
        .values(
            worker_id=WORKER_ID,
            attempt_number=new_attempt,
            started_at=datetime.utcnow(),
            last_checkpoint=datetime.utcnow(),
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
                (quality_progress.c.job_id == job_id) &
                (quality_progress.c.quality == quality["name"])
            )
        )

        if not existing:
            await database.execute(
                quality_progress.insert().values(
                    job_id=job_id,
                    quality=quality["name"],
                    status="pending",
                    progress_percent=0,
                )
            )


async def get_quality_status(job_id: int, quality_name: str) -> Optional[dict]:
    """Get the progress status for a specific quality."""
    row = await database.fetch_one(
        quality_progress.select().where(
            (quality_progress.c.job_id == job_id) &
            (quality_progress.c.quality == quality_name)
        )
    )
    return dict(row) if row else None


async def update_quality_status(
    job_id: int,
    quality_name: str,
    status: str,
    error_message: Optional[str] = None
):
    """Update quality transcoding status."""
    values = {
        "status": status,
    }

    if status == "in_progress":
        values["started_at"] = datetime.utcnow()
    elif status == "completed":
        values["completed_at"] = datetime.utcnow()
        values["progress_percent"] = 100
    elif status == "failed" and error_message:
        values["error_message"] = error_message[:500]

    await database.execute(
        quality_progress.update()
        .where(
            (quality_progress.c.job_id == job_id) &
            (quality_progress.c.quality == quality_name)
        )
        .values(**values)
    )


async def update_quality_progress(job_id: int, quality_name: str, progress: int):
    """Update quality transcoding progress percentage."""
    await database.execute(
        quality_progress.update()
        .where(
            (quality_progress.c.job_id == job_id) &
            (quality_progress.c.quality == quality_name)
        )
        .values(progress_percent=progress)
    )


async def get_completed_qualities(job_id: int) -> List[str]:
    """Get list of completed quality names for a job."""
    rows = await database.fetch_all(
        quality_progress.select().where(
            (quality_progress.c.job_id == job_id) &
            (quality_progress.c.status == "completed")
        )
    )
    return [row["quality"] for row in rows]


# ============================================================================
# Crash Recovery
# ============================================================================

async def recover_interrupted_jobs():
    """
    Check for jobs that were interrupted (worker crashed) and reset them for retry.
    Called on worker startup.
    """
    print(f"Worker {WORKER_ID[:8]} checking for interrupted jobs...")

    # Find jobs that have a checkpoint but no completion and are stale
    stale_threshold = datetime.utcnow() - timedelta(seconds=JOB_STALE_TIMEOUT)

    stale_jobs = await database.fetch_all(
        transcoding_jobs.select().where(
            (transcoding_jobs.c.completed_at == None) &
            (transcoding_jobs.c.last_checkpoint != None) &
            (transcoding_jobs.c.last_checkpoint < stale_threshold)
        )
    )

    for job in stale_jobs:
        video = await database.fetch_one(
            videos.select().where(videos.c.id == job["video_id"])
        )

        if not video:
            continue

        print(f"  Found stale job for video '{video['slug']}' (attempt {job['attempt_number']})")

        if job["attempt_number"] >= job["max_attempts"]:
            # Max retries exceeded
            print(f"    Max retries exceeded, marking as failed")
            await mark_job_failed(job["id"], "Max retry attempts exceeded")
            await database.execute(
                videos.update().where(videos.c.id == job["video_id"]).values(
                    status="failed",
                    error_message="Max retry attempts exceeded"
                )
            )
        else:
            # Reset for retry
            print(f"    Resetting for retry (attempt {job['attempt_number'] + 1})")
            await reset_job_for_retry(job["id"])

            # Also reset the video status to pending so it gets picked up
            await database.execute(
                videos.update().where(videos.c.id == job["video_id"]).values(
                    status="pending"
                )
            )

            # Optionally clean up partial output
            if CLEANUP_PARTIAL_ON_FAILURE:
                completed = await get_completed_qualities(job["id"])
                await cleanup_partial_output(
                    video["slug"],
                    keep_completed_qualities=KEEP_COMPLETED_QUALITIES,
                    completed_quality_names=completed
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
    await database.execute(
        videos.update().where(videos.c.id == video_id).values(status="pending")
    )


async def process_video_resumable(video_id: int, video_slug: str):
    """
    Process a video with checkpoint-based resumable transcoding.
    Can resume from the last successful step if interrupted.
    """
    print(f"Processing video: {video_slug} (id={video_id})")

    # Check for shutdown at the start
    if shutdown_requested:
        print("  Shutdown requested, skipping this video")
        return False

    # Find the source file
    source_file = None
    for ext in [".mp4", ".mkv", ".webm", ".mov", ".avi"]:
        candidate = UPLOADS_DIR / f"{video_id}{ext}"
        if candidate.exists():
            source_file = candidate
            break

    if not source_file:
        await database.execute(
            videos.update().where(videos.c.id == video_id).values(
                status="failed",
                error_message="Source file not found"
            )
        )
        return False

    # Get or create job record
    job = await get_or_create_job(video_id)
    job_id = job["id"]

    try:
        # ----------------------------------------------------------------
        # Step 1: Probe (skip if already done)
        # ----------------------------------------------------------------
        if job["current_step"] in [None, "probe"]:
            await update_job_step(job_id, "probe")
            print("  Step 1: Probing video info...")

            info = get_video_info(source_file)
            print(f"  Source: {info['width']}x{info['height']}, {info['duration']:.1f}s")

            # Update video metadata
            await database.execute(
                videos.update().where(videos.c.id == video_id).values(
                    status="processing",
                    duration=info["duration"],
                    source_width=info["width"],
                    source_height=info["height"],
                )
            )
            await checkpoint(job_id)
            
            # Check for shutdown after probe
            if shutdown_requested:
                print("  Shutdown requested, resetting video to pending...")
                await reset_video_to_pending(video_id)
                return False
        else:
            # Load existing video info
            video_row = await database.fetch_one(
                videos.select().where(videos.c.id == video_id)
            )
            info = {
                "width": video_row["source_width"],
                "height": video_row["source_height"],
                "duration": video_row["duration"],
            }

        output_dir = VIDEOS_DIR / video_slug
        output_dir.mkdir(parents=True, exist_ok=True)

        # ----------------------------------------------------------------
        # Step 2: Thumbnail (skip if exists)
        # ----------------------------------------------------------------
        if job["current_step"] in [None, "probe", "thumbnail"]:
            await update_job_step(job_id, "thumbnail")
            thumb_path = output_dir / "thumbnail.jpg"

            if not thumb_path.exists():
                print("  Step 2: Generating thumbnail...")
                thumbnail_time = min(5.0, info["duration"] / 4)
                generate_thumbnail(source_file, thumb_path, thumbnail_time)
            else:
                print("  Step 2: Thumbnail already exists, skipping...")

            await checkpoint(job_id)
            
            # Check for shutdown after thumbnail
            if shutdown_requested:
                print("  Shutdown requested, resetting video to pending...")
                await reset_video_to_pending(video_id)
                return False

        # ----------------------------------------------------------------
        # Step 3: Transcode each quality
        # ----------------------------------------------------------------
        await update_job_step(job_id, "transcode")

        qualities = get_applicable_qualities(info["height"])
        if not qualities:
            qualities = [QUALITY_PRESETS[-1]]

        print(f"  Step 3: Transcoding to: {[q['name'] for q in qualities]}")

        # Initialize quality progress records
        await init_quality_progress(job_id, qualities)

        successful_qualities = []
        failed_qualities = []
        total_qualities = len(qualities)

        for idx, quality in enumerate(qualities):
            quality_name = quality["name"]
            
            # Check for shutdown before processing each quality
            if shutdown_requested:
                print("  Shutdown requested, resetting video to pending...")
                await reset_video_to_pending(video_id)
                return False

            # Check if already completed
            status = await get_quality_status(job_id, quality_name)
            if status and status["status"] == "completed":
                print(f"    {quality_name}: Already completed, skipping...")
                # Get actual dimensions from existing segment
                first_segment = output_dir / f"{quality_name}_0000.ts"
                if first_segment.exists():
                    actual_width, actual_height = get_output_dimensions(first_segment)
                else:
                    actual_width = int(quality["height"] * 16 / 9)
                    if actual_width % 2 != 0:
                        actual_width += 1
                    actual_height = quality["height"]
                successful_qualities.append({
                    "name": quality_name,
                    "width": actual_width,
                    "height": actual_height,
                    "bitrate": quality["bitrate"],
                })
                continue

            # Check if playlist file already exists (from previous attempt)
            playlist_path = output_dir / f"{quality_name}.m3u8"
            if playlist_path.exists():
                print(f"    {quality_name}: Found existing playlist, marking complete...")
                await update_quality_status(job_id, quality_name, "completed")
                # Get actual dimensions from existing segment
                first_segment = output_dir / f"{quality_name}_0000.ts"
                if first_segment.exists():
                    actual_width, actual_height = get_output_dimensions(first_segment)
                else:
                    actual_width = int(quality["height"] * 16 / 9)
                    if actual_width % 2 != 0:
                        actual_width += 1
                    actual_height = quality["height"]
                successful_qualities.append({
                    "name": quality_name,
                    "width": actual_width,
                    "height": actual_height,
                    "bitrate": quality["bitrate"],
                })
                continue

            # Transcode this quality
            print(f"    {quality_name}: Transcoding...")
            await update_quality_status(job_id, quality_name, "in_progress")

            async def progress_cb(progress: int):
                await update_quality_progress(job_id, quality_name, progress)
                # Update overall progress
                base_progress = int((idx / total_qualities) * 100)
                quality_contribution = int((progress / 100) * (100 / total_qualities))
                overall = base_progress + quality_contribution
                await update_job_progress(job_id, overall)

            try:
                success, error_detail = await transcode_quality_with_progress(
                    source_file, output_dir, quality, info["duration"], progress_cb
                )

                if success:
                    await update_quality_status(job_id, quality_name, "completed")
                    # Get actual dimensions from transcoded segment
                    first_segment = output_dir / f"{quality_name}_0000.ts"
                    if first_segment.exists():
                        actual_width, actual_height = get_output_dimensions(first_segment)
                    else:
                        actual_width = int(quality["height"] * 16 / 9)
                        if actual_width % 2 != 0:
                            actual_width += 1
                        actual_height = quality["height"]
                    successful_qualities.append({
                        "name": quality_name,
                        "width": actual_width,
                        "height": actual_height,
                        "bitrate": quality["bitrate"],
                    })
                    print(f"    {quality_name}: Done ({actual_width}x{actual_height})")
                else:
                    error_msg = error_detail or "Transcoding process returned non-zero exit code"
                    await update_quality_status(job_id, quality_name, "failed", error_msg)
                    failed_qualities.append({"name": quality_name, "error": error_msg})
                    print(f"    {quality_name}: Failed")
            except Exception as e:
                error_msg = str(e)
                await update_quality_status(job_id, quality_name, "failed", error_msg)
                failed_qualities.append({"name": quality_name, "error": error_msg})
                print(f"    {quality_name}: Error - {e}")

            await checkpoint(job_id)

        # Report results
        if not successful_qualities:
            # All quality variants failed
            failed_summary = ", ".join([f"{q['name']}: {q['error'][:MAX_ERROR_SUMMARY_LENGTH]}" for q in failed_qualities])
            error_message = f"All {len(failed_qualities)} quality variant(s) failed. Details: {failed_summary}"
            print(f"  FAILURE: {error_message}")
            raise RuntimeError(error_message)
        elif failed_qualities:
            # Partial success - some qualities failed
            print(f"  WARNING: Partial transcoding success - {len(successful_qualities)}/{total_qualities} quality variants completed")
            print(f"  Failed variants: {', '.join([q['name'] for q in failed_qualities])}")
            for failed in failed_qualities:
                print(f"    - {failed['name']}: {failed['error'][:MAX_ERROR_DETAIL_LENGTH]}")

        # ----------------------------------------------------------------
        # Step 4: Generate master playlist
        # ----------------------------------------------------------------
        await update_job_step(job_id, "master_playlist")
        print("  Step 4: Generating master playlist...")
        generate_master_playlist(output_dir, successful_qualities)
        await checkpoint(job_id)

        # ----------------------------------------------------------------
        # Step 5: Finalize
        # ----------------------------------------------------------------
        await update_job_step(job_id, "finalize")
        print("  Step 5: Finalizing...")

        # Save quality info to database
        for q in successful_qualities:
            # Check if quality record already exists
            existing = await database.fetch_one(
                video_qualities.select().where(
                    (video_qualities.c.video_id == video_id) &
                    (video_qualities.c.quality == q["name"])
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
        await database.execute(
            videos.update().where(videos.c.id == video_id).values(
                status="ready",
                published_at=datetime.utcnow(),
            )
        )

        # Mark job completed
        await mark_job_completed(job_id)

        # Clean up source file
        source_file.unlink()
        print(f"  Done! Video is ready.")
        return True

    except Exception as e:
        print(f"  Error: {e}")
        await mark_job_failed(job_id, str(e))

        # Check if we should retry
        job = await database.fetch_one(
            transcoding_jobs.select().where(transcoding_jobs.c.id == job_id)
        )

        if job and job["attempt_number"] < job["max_attempts"]:
            # Will be retried on next worker restart or stale job check
            await database.execute(
                videos.update().where(videos.c.id == video_id).values(
                    status="failed",
                    error_message=f"Attempt {job['attempt_number']} failed: {str(e)[:400]}",
                )
            )
        else:
            # Final failure
            await database.execute(
                videos.update().where(videos.c.id == video_id).values(
                    status="failed",
                    error_message=str(e)[:500],
                )
            )
        
        return False


async def check_stale_jobs():
    """
    Periodic check for stale jobs that might need recovery.
    Called periodically during the worker loop.
    """
    stale_threshold = datetime.utcnow() - timedelta(seconds=JOB_STALE_TIMEOUT)

    stale_jobs = await database.fetch_all(
        transcoding_jobs.select().where(
            (transcoding_jobs.c.completed_at == None) &
            (transcoding_jobs.c.last_checkpoint != None) &
            (transcoding_jobs.c.last_checkpoint < stale_threshold) &
            (transcoding_jobs.c.worker_id != WORKER_ID)  # Not our own jobs
        )
    )

    for job in stale_jobs:
        video = await database.fetch_one(
            videos.select().where(videos.c.id == job["video_id"])
        )

        if not video:
            continue

        if job["attempt_number"] >= job["max_attempts"]:
            print(f"Stale job for '{video['slug']}' exceeded max retries, marking failed")
            await mark_job_failed(job["id"], "Max retry attempts exceeded (stale)")
            await database.execute(
                videos.update().where(videos.c.id == job["video_id"]).values(
                    status="failed",
                    error_message="Max retry attempts exceeded"
                )
            )
        else:
            print(f"Found stale job for '{video['slug']}', resetting for retry")
            await reset_job_for_retry(job["id"])
            await database.execute(
                videos.update().where(videos.c.id == job["video_id"]).values(
                    status="pending"
                )
            )


async def worker_loop():
    """
    Main worker loop - process pending videos using event-driven architecture.

    Uses filesystem watching (inotify via watchdog) to detect new uploads immediately,
    with a fallback poll interval for edge cases. This eliminates the constant 5-second
    polling that wasted resources and added latency.
    """
    global new_upload_event

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    await database.connect()
    print(f"Transcoding worker started (ID: {WORKER_ID[:8]})")

    # Initialize the upload event for signaling between filesystem watcher and main loop
    loop = asyncio.get_running_loop()
    new_upload_event = asyncio.Event()

    # Start filesystem watcher if available
    observer = None
    if WORKER_USE_FILESYSTEM_WATCHER and WATCHDOG_AVAILABLE:
        observer = start_filesystem_watcher(loop, new_upload_event)
        if observer:
            print(f"  Mode: Event-driven (inotify) with {WORKER_FALLBACK_POLL_INTERVAL}s fallback poll")
        else:
            print(f"  Mode: Polling every {WORKER_FALLBACK_POLL_INTERVAL}s (watcher failed)")
    else:
        if not WORKER_USE_FILESYSTEM_WATCHER:
            print(f"  Mode: Polling every {WORKER_FALLBACK_POLL_INTERVAL}s (watcher disabled)")
        else:
            print(f"  Mode: Polling every {WORKER_FALLBACK_POLL_INTERVAL}s (watchdog not available)")

    print("Watching for new videos...")

    # Recover any interrupted jobs from previous crashes
    await recover_interrupted_jobs()

    last_stale_check = datetime.utcnow()
    stale_check_interval = 300  # Check every 5 minutes

    # Determine wait behavior based on watcher availability
    use_event_driven = observer is not None

    try:
        while not shutdown_requested:
            # Find pending videos
            query = videos.select().where(videos.c.status == "pending").order_by(videos.c.created_at)
            pending = await database.fetch_all(query)

            for video in pending:
                if shutdown_requested:
                    print("Shutdown requested, stopping worker loop...")
                    break
                result = await process_video_resumable(video["id"], video["slug"])
                if result:
                    print(f"Successfully completed: {video['slug']}")
                elif shutdown_requested:
                    print(f"Shutdown interrupted: {video['slug']}")
                else:
                    print(f"Failed to process: {video['slug']}")

            # Periodic stale job check
            if not shutdown_requested and (datetime.utcnow() - last_stale_check).total_seconds() > stale_check_interval:
                await check_stale_jobs()
                last_stale_check = datetime.utcnow()

            # Wait for new uploads or fallback timeout
            if not shutdown_requested:
                if use_event_driven:
                    # Event-driven: wait for filesystem event OR fallback timeout
                    try:
                        await asyncio.wait_for(
                            new_upload_event.wait(),
                            timeout=WORKER_FALLBACK_POLL_INTERVAL
                        )
                        # Event was set - new file detected
                        new_upload_event.clear()
                    except asyncio.TimeoutError:
                        # Timeout - fallback poll, this is expected
                        pass
                else:
                    # Polling mode: just wait the fallback interval
                    await asyncio.sleep(WORKER_FALLBACK_POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\nKeyboardInterrupt received.")
    finally:
        # Stop filesystem watcher
        if observer is not None:
            print("Stopping filesystem watcher...")
            stop_filesystem_watcher(observer)

        # On shutdown, reset videos being processed by this worker instance to "pending"
        print("Cleaning up: resetting this worker's processing videos to pending...")
        try:
            # Find videos being processed by this worker through transcoding_jobs
            jobs_query = transcoding_jobs.select().where(
                (transcoding_jobs.c.worker_id == WORKER_ID) &
                (transcoding_jobs.c.completed_at.is_(None))
            )
            jobs = await database.fetch_all(jobs_query)

            # Reset those videos to pending
            for job in jobs:
                video = await database.fetch_one(
                    videos.select().where(videos.c.id == job["video_id"])
                )
                if video and video["status"] == "processing":
                    await database.execute(
                        videos.update()
                        .where(videos.c.id == job["video_id"])
                        .values(status="pending")
                    )

            if jobs:
                print(f"Reset {len(jobs)} video(s) to pending.")
            else:
                print("No videos to reset.")
        except Exception as e:
            print(f"Error during cleanup: {e}")

        await database.disconnect()
        print("Worker stopped gracefully.")


if __name__ == "__main__":
    asyncio.run(worker_loop())
