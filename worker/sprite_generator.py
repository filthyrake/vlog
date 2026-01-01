"""
Sprite Sheet Generator Worker (Issue #413 Phase 7B)

Background worker for generating timeline thumbnail sprite sheets.
Processes jobs from the sprite_queue table.

Usage:
    python -m worker.sprite_generator

Features:
- Async queue processing (non-blocking video availability)
- Atomic generation (temp dir → rename)
- Configurable frame interval, tile size, quality
- Timeout protection with min/max bounds
- Stale job recovery for crashed workers
- Graceful FFmpeg shutdown on SIGTERM
- Memory threshold check before claiming jobs (OOM prevention)
- Video duration limit (skip extremely long videos)
"""

import asyncio
import logging
import shutil
import signal
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import asyncpg
import psutil

import config

# Stale job threshold - jobs processing for longer than this are considered stale
STALE_JOB_THRESHOLD_HOURS = 2
# How often to check for stale jobs (seconds)
STALE_JOB_CHECK_INTERVAL = 300  # 5 minutes

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("sprite_generator")


def check_memory_available() -> bool:
    """Check if there's enough memory available to process a sprite job.

    Returns True if available memory is above the configured threshold.
    """
    try:
        mem = psutil.virtual_memory()
        available_percent = mem.available * 100 / mem.total
        threshold = config.SPRITE_SHEET_MEMORY_THRESHOLD_PERCENT

        if available_percent < threshold:
            logger.warning(
                f"Low memory: {available_percent:.1f}% available "
                f"(threshold: {threshold}%)"
            )
            return False
        return True
    except Exception as e:
        logger.error(f"Error checking memory: {e}")
        # Fail open - allow processing if we can't check memory
        return True


class SpriteGenerator:
    """Sprite sheet generator worker."""

    def __init__(self):
        self.running = False
        self.db_pool: Optional[asyncpg.Pool] = None
        self.current_job_id: Optional[int] = None
        self.current_process: Optional[asyncio.subprocess.Process] = None
        self._last_stale_check: float = 0

    async def start(self):
        """Start the sprite generator worker."""
        logger.info("Starting sprite generator worker...")

        # Connect to database
        self.db_pool = await asyncpg.create_pool(
            config.DATABASE_URL,
            min_size=1,
            max_size=5,
        )

        self.running = True

        # Set up signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._handle_shutdown)

        logger.info("Sprite generator worker started")

        # Main processing loop
        while self.running:
            try:
                # Periodically check for stale jobs
                await self._maybe_recover_stale_jobs()

                # Check memory before claiming a job to prevent OOM
                if not check_memory_available():
                    logger.info("Waiting for memory to become available...")
                    await asyncio.sleep(30)
                    continue

                job = await self._claim_next_job()
                if job:
                    await self._process_job(job)
                else:
                    # No jobs available, wait before checking again
                    await asyncio.sleep(10)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
                await asyncio.sleep(5)

        await self._shutdown()

    def _handle_shutdown(self):
        """Handle shutdown signal."""
        logger.info("Shutdown signal received")
        self.running = False

    async def _shutdown(self):
        """Clean up resources."""
        logger.info("Shutting down sprite generator...")

        # Gracefully terminate any running FFmpeg process
        if self.current_process:
            logger.info("Terminating FFmpeg process...")
            try:
                self.current_process.terminate()
                try:
                    await asyncio.wait_for(self.current_process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning("FFmpeg did not terminate gracefully, killing...")
                    self.current_process.kill()
                    await self.current_process.wait()
            except Exception as e:
                logger.error(f"Error stopping FFmpeg process: {e}")

        if self.db_pool:
            await self.db_pool.close()

        logger.info("Sprite generator stopped")

    async def _maybe_recover_stale_jobs(self):
        """Periodically check for and recover stale jobs."""
        import time

        now = time.time()
        if now - self._last_stale_check < STALE_JOB_CHECK_INTERVAL:
            return

        self._last_stale_check = now

        try:
            await self._recover_stale_jobs()
        except Exception as e:
            logger.error(f"Error recovering stale jobs: {e}", exc_info=True)

    async def _recover_stale_jobs(self):
        """Recover jobs stuck in processing state from crashed workers."""
        stale_threshold = datetime.now(timezone.utc) - timedelta(hours=STALE_JOB_THRESHOLD_HOURS)

        async with self.db_pool.acquire() as conn:
            stale_jobs = await conn.fetch(
                """
                UPDATE sprite_queue
                SET status = 'pending',
                    started_at = NULL
                WHERE status = 'processing'
                  AND started_at < $1
                RETURNING id, video_id
                """,
                stale_threshold,
            )

            for job in stale_jobs:
                logger.warning(f"Recovered stale sprite job {job['id']} for video {job['video_id']}")
                # Reset video status too
                await conn.execute(
                    """
                    UPDATE videos
                    SET sprite_sheet_status = 'pending'
                    WHERE id = $1
                    """,
                    job["video_id"],
                )

    async def _claim_next_job(self) -> Optional[dict]:
        """Claim the next pending job from the queue."""
        async with self.db_pool.acquire() as conn:
            # Use SELECT FOR UPDATE SKIP LOCKED for concurrent worker safety
            row = await conn.fetchrow(
                """
                UPDATE sprite_queue
                SET status = 'processing',
                    started_at = NOW()
                WHERE id = (
                    SELECT id FROM sprite_queue
                    WHERE status = 'pending'
                    ORDER BY
                        CASE priority
                            WHEN 'high' THEN 1
                            WHEN 'normal' THEN 2
                            WHEN 'low' THEN 3
                        END,
                        created_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING id, video_id, priority
                """
            )

            if row:
                self.current_job_id = row["id"]
                return dict(row)
            return None

    async def _process_job(self, job: dict):
        """Process a sprite generation job."""
        job_id = job["id"]
        video_id = job["video_id"]

        logger.info(f"Processing sprite job {job_id} for video {video_id}")

        try:
            # Get video info
            async with self.db_pool.acquire() as conn:
                video = await conn.fetchrow(
                    """
                    SELECT id, slug, duration, source_width, source_height
                    FROM videos
                    WHERE id = $1 AND status = 'ready' AND deleted_at IS NULL
                    """,
                    video_id,
                )

            if not video:
                raise ValueError(f"Video {video_id} not found or not ready")

            # Check video duration limit to prevent OOM on very long videos
            duration = video["duration"] or 0
            max_duration = config.SPRITE_SHEET_MAX_VIDEO_DURATION
            if duration > max_duration:
                raise ValueError(
                    f"Video duration ({duration}s) exceeds maximum "
                    f"({max_duration}s) for sprite generation"
                )

            # Update video sprite status to generating
            await self._update_video_sprite_status(video_id, "generating")

            # Generate sprite sheets
            result = await self._generate_sprites(video)

            # Update video with sprite info
            async with self.db_pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE videos
                    SET sprite_sheet_status = 'ready',
                        sprite_sheet_error = NULL,
                        sprite_sheet_count = $2,
                        sprite_sheet_interval = $3,
                        sprite_sheet_tile_size = $4,
                        sprite_sheet_frame_width = $5,
                        sprite_sheet_frame_height = $6
                    WHERE id = $1
                    """,
                    video_id,
                    result["count"],
                    result["interval"],
                    result["tile_size"],
                    result["frame_width"],
                    result["frame_height"],
                )

            # Mark job as completed
            await self._complete_job(job_id)
            logger.info(f"Sprite job {job_id} completed: {result['count']} sheets generated")

        except Exception as e:
            error_msg = str(e)[:500]  # Truncate error message
            logger.error(f"Sprite job {job_id} failed: {error_msg}", exc_info=True)

            # Update video status to failed
            await self._update_video_sprite_status(video_id, "failed", error_msg)

            # Mark job as failed
            await self._fail_job(job_id, error_msg)

        finally:
            self.current_job_id = None

    async def _generate_sprites(self, video: dict) -> dict:
        """Generate sprite sheets for a video using FFmpeg."""
        slug = video["slug"]
        duration = video["duration"] or 0
        source_width = video["source_width"] or 1920
        source_height = video["source_height"] or 1080

        # Calculate frame dimensions maintaining aspect ratio
        thumb_width = config.SPRITE_SHEET_THUMBNAIL_WIDTH
        aspect_ratio = source_height / source_width if source_width > 0 else 0.5625
        thumb_height = int(thumb_width * aspect_ratio)
        # Ensure even dimensions for FFmpeg
        thumb_height = thumb_height if thumb_height % 2 == 0 else thumb_height + 1

        # Configuration
        interval = config.SPRITE_SHEET_FRAME_INTERVAL
        tile_size = config.SPRITE_SHEET_TILE_SIZE
        quality = config.SPRITE_SHEET_JPEG_QUALITY

        # Paths
        video_dir = config.VIDEOS_DIR / slug
        sprites_dir = video_dir / "sprites"
        source_video = video_dir / "original" / "source.mp4"

        # Find source video file
        if not source_video.exists():
            # Try to find any video file in the original directory
            original_dir = video_dir / "original"
            if original_dir.exists():
                for ext in [".mp4", ".mkv", ".webm", ".mov"]:
                    candidate = original_dir / f"source{ext}"
                    if candidate.exists():
                        source_video = candidate
                        break
                else:
                    # Try any file in the directory
                    for f in original_dir.iterdir():
                        if f.suffix.lower() in config.SUPPORTED_VIDEO_EXTENSIONS:
                            source_video = f
                            break

        if not source_video.exists():
            raise FileNotFoundError(f"Source video not found for {slug}")

        # Calculate timeout
        timeout = int(duration * config.SPRITE_SHEET_TIMEOUT_MULTIPLIER)
        timeout = max(config.SPRITE_SHEET_TIMEOUT_MINIMUM, min(timeout, config.SPRITE_SHEET_TIMEOUT_MAXIMUM))

        # Create temp directory for atomic generation
        with tempfile.TemporaryDirectory(prefix="vlog_sprites_") as temp_dir:
            temp_sprites = Path(temp_dir) / "sprites"
            temp_sprites.mkdir()

            # Build FFmpeg command
            # fps=1/{interval} - capture one frame every N seconds
            # scale={width}:-1 - scale to width, auto height
            # tile={tile_size}x{tile_size} - arrange into grid
            ffmpeg_cmd = [
                "ffmpeg",
                "-y",  # Overwrite output
                "-i", str(source_video),
                "-vf", f"fps=1/{interval},scale={thumb_width}:-1,tile={tile_size}x{tile_size}",
                "-q:v", str(100 - quality),  # FFmpeg uses inverse quality scale
                str(temp_sprites / "sprite_%02d.jpg"),
            ]

            logger.debug(f"Running FFmpeg: {' '.join(ffmpeg_cmd)}")

            # Run FFmpeg with timeout
            try:
                process = await asyncio.create_subprocess_exec(
                    *ffmpeg_cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                # Track process for graceful shutdown
                self.current_process = process
                try:
                    _, stderr = await asyncio.wait_for(
                        process.communicate(),
                        timeout=timeout,
                    )
                finally:
                    self.current_process = None

                if process.returncode != 0:
                    error = stderr.decode()[-500:] if stderr else "Unknown error"
                    raise RuntimeError(f"FFmpeg failed: {error}")

            except asyncio.TimeoutError:
                self.current_process = None
                process.kill()
                await process.wait()  # Ensure process is cleaned up
                raise RuntimeError(f"Sprite generation timed out after {timeout}s")

            # Count generated sprite sheets
            generated_files = list(temp_sprites.glob("sprite_*.jpg"))
            actual_count = len(generated_files)

            if actual_count == 0:
                raise RuntimeError("No sprite sheets were generated")

            # Atomic move: remove old sprites dir if exists, then rename temp
            if sprites_dir.exists():
                shutil.rmtree(sprites_dir)
            shutil.move(str(temp_sprites), str(sprites_dir))

            logger.info(f"Generated {actual_count} sprite sheets for {slug}")

            return {
                "count": actual_count,
                "interval": interval,
                "tile_size": tile_size,
                "frame_width": thumb_width,
                "frame_height": thumb_height,
            }

    async def _update_video_sprite_status(
        self,
        video_id: int,
        status: str,
        error: Optional[str] = None,
    ):
        """Update video sprite sheet status."""
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE videos
                SET sprite_sheet_status = $2,
                    sprite_sheet_error = $3
                WHERE id = $1
                """,
                video_id,
                status,
                error,
            )

    async def _complete_job(self, job_id: int):
        """Mark a job as completed."""
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE sprite_queue
                SET status = 'completed',
                    completed_at = NOW()
                WHERE id = $1
                """,
                job_id,
            )

    async def _fail_job(self, job_id: int, error: str):
        """Mark a job as failed."""
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE sprite_queue
                SET status = 'failed',
                    completed_at = NOW(),
                    error_message = $2
                WHERE id = $1
                """,
                job_id,
                error,
            )


async def main():
    """Entry point for sprite generator worker."""
    if not config.SPRITE_SHEET_ENABLED:
        logger.warning("Sprite sheet generation is disabled. Set VLOG_SPRITE_SHEET_ENABLED=true to enable.")
        return

    generator = SpriteGenerator()
    await generator.start()


if __name__ == "__main__":
    asyncio.run(main())
