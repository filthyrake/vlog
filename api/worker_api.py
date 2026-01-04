"""
Worker API - Separate FastAPI service for distributed transcoding workers.

Provides endpoints for:
- Worker registration and heartbeat
- Job claiming with distributed locking
- Source file download and HLS upload
- Progress reporting and job completion

Run with: uvicorn api.worker_api:app --host 0.0.0.0 --port 9002

STATE MACHINE OVERVIEW
======================

Video Status States:
- pending: Uploaded, waiting for transcoding
- processing: Being transcoded by a worker
- ready: Transcoding complete, ready to stream
- failed: Transcoding failed permanently (max retries exceeded)

Job States: See api/job_state.py for explicit state machine implementation.
States: unclaimed, claimed, expired, completed, failed, retrying

Worker States:
- active: Recently heartbeated, available for work
- idle: Active but not currently processing (used for GPU priority)
- busy: Currently processing a job
- offline: No recent heartbeat (threshold: WORKER_OFFLINE_THRESHOLD_MINUTES)
- disabled: Manually disabled by admin

Key State Transitions:
1. Job Creation: Upload → pending video + unclaimed job
2. Job Claiming: Atomic claim with FOR UPDATE SKIP LOCKED (PostgreSQL)
   - Updates: video.status = processing, job.claimed_at/claim_expires_at/worker_id
   - GPU workers have priority over CPU workers
3. Progress Updates: Extend claim by WORKER_CLAIM_DURATION_MINUTES on each update
4. Job Completion: Set completed_at, video.status = ready
5. Job Failure: Increment attempt_number, retry if < max_attempts, else failed
6. Claim Expiration: Stale job checker releases claims from offline workers
   - Runs every VLOG_STALE_JOB_CHECK_INTERVAL seconds
   - Only releases jobs with expired claims (claim_expires_at < NOW())
7. Worker Offline: No heartbeat for WORKER_OFFLINE_THRESHOLD_MINUTES
   - Atomic conditional update prevents race with concurrent heartbeat

Distributed Safety:
- PostgreSQL: FOR UPDATE SKIP LOCKED prevents double-claiming
- SQLite: Database-level transaction locking (single-instance only)
- Claim expiration: Automatic timeout after 30 minutes without progress
- Stale detection: Background task releases expired claims from offline workers
- Grace period: 2-minute delay after API startup allows workers to reconnect

For detailed documentation including edge cases and race condition handling,
see: docs/TRANSCODING_ARCHITECTURE.md
"""

import asyncio
import functools
import hashlib
import hmac
import json
import logging
import os
import secrets
import shutil
import tarfile
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import sqlalchemy as sa
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette.background import BackgroundTask

from api.common import (
    HTTPMetricsMiddleware,
    RequestIDMiddleware,
    check_health,
    ensure_utc,
    get_real_ip,
    get_storage_status,
    rate_limit_exceeded_handler,
)
from api.database import (
    configure_database,
    database,
    quality_progress,
    reencode_queue,
    sprite_queue,
    transcoding_jobs,
    transcriptions,
    video_qualities,
    videos,
    worker_api_keys,
    workers,
)
from api.db_retry import DatabaseLockedError, execute_with_retry, fetch_all_with_retry, fetch_one_with_retry
from api.metrics import (
    STORAGE_VIDEOS_BYTES,
    TRANSCODING_JOBS_TOTAL,
    WORKER_JOBS_COMPLETED_TOTAL,
    get_metrics,
    sanitize_label,
)
from api.pubsub import Publisher
from api.redis_client import get_redis
from api.settings_service import get_setting as get_db_setting
from api.webhook_service import trigger_webhook_event
from api.worker_auth import get_key_prefix, hash_api_key, verify_worker_key
from api.worker_schemas import (
    ClaimJobResponse,
    CompleteJobRequest,
    CompleteJobResponse,
    FailJobRequest,
    FailJobResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    ProgressUpdateRequest,
    ProgressUpdateResponse,
    SegmentFinalizeRequest,
    SegmentFinalizeResponse,
    SegmentQuality,
    SegmentStatusResponse,
    SegmentUploadResponse,
    StatusResponse,
    WorkerListResponse,
    WorkerRegisterRequest,
    WorkerRegisterResponse,
    WorkerStatusResponse,
)
from config import (
    MAX_HLS_ARCHIVE_FILES,
    MAX_HLS_ARCHIVE_SIZE,
    MAX_HLS_SINGLE_FILE_SIZE,
    ORPHAN_CLEANUP_ENABLED,
    ORPHAN_CLEANUP_INTERVAL,
    ORPHAN_CLEANUP_MIN_AGE,
    QUALITY_NAMES,
    RATE_LIMIT_ENABLED,
    RATE_LIMIT_STORAGE_URL,
    RATE_LIMIT_WORKER_DEFAULT,
    RATE_LIMIT_WORKER_PROGRESS,
    RATE_LIMIT_WORKER_REGISTER,
    SPRITE_SHEET_AUTO_GENERATE,
    SPRITE_SHEET_ENABLED,
    STALE_JOB_CHECK_INTERVAL,
    SUPPORTED_VIDEO_EXTENSIONS,
    TAR_EXTRACTION_TIMEOUT,
    UPLOADS_DIR,
    VIDEOS_DIR,
    WORKER_ADMIN_SECRET,
    WORKER_API_PORT,
    WORKER_CLAIM_DURATION_MINUTES,
    WORKER_HEARTBEAT_INTERVAL,
    WORKER_OFFLINE_THRESHOLD_MINUTES,
)

logger = logging.getLogger(__name__)
security_logger = logging.getLogger("security.worker_auth")

# Thread pool for blocking I/O operations (tar extraction to NAS)
# This prevents slow NAS operations from blocking the event loop and
# causing heartbeat failures. See issue: tar extraction to slow NAS
# was blocking entire API for 3+ minutes.
_io_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="worker_api_io")


# =============================================================================
# Streaming Segment Upload Security Helpers (Issue #478)
# =============================================================================

# Magic bytes for segment file validation (Bruce's recommendation)
# MPEG-TS sync byte (0x47) appears at start of every TS packet
TS_MAGIC_BYTE = b"\x47"
# fMP4/CMAF segments start with 'ftyp' or 'moof' box
FTYP_MAGIC = b"ftyp"
MOOF_MAGIC = b"moof"
STYP_MAGIC = b"styp"  # Segment type box (common in CMAF)
SIDX_MAGIC = b"sidx"  # Segment index box (code review fix)
EMSG_MAGIC = b"emsg"  # Event message box (code review fix)


def validate_segment_filename(filename: str) -> bool:
    """
    Validate segment filename for security (Bruce's recommendations).

    Rejects:
    - Null bytes
    - Percent encoding (%)
    - Path traversal (..)
    - Directory separators (/, \\)
    - Unicode normalization tricks

    Args:
        filename: The filename to validate

    Returns:
        True if filename is safe, False otherwise
    """
    # Reject null bytes
    if "\x00" in filename:
        return False

    # Reject percent encoding (potential for bypass)
    if "%" in filename:
        return False

    # Reject path traversal
    if ".." in filename:
        return False

    # Reject directory separators
    if "/" in filename or "\\" in filename:
        return False

    # Reject leading/trailing whitespace
    if filename != filename.strip():
        return False

    # Reject empty filenames
    if not filename:
        return False

    # Only allow expected extensions
    allowed_extensions = (".ts", ".m4s", ".mp4", ".m3u8", ".mpd")
    if not any(filename.endswith(ext) for ext in allowed_extensions):
        return False

    # Limit filename length
    if len(filename) > 255:
        return False

    return True


def validate_segment_magic_bytes(data: bytes, filename: str) -> bool:
    """
    Validate segment file magic bytes (Bruce's recommendation).

    Verifies that the file content matches expected format based on extension:
    - .ts files start with MPEG-TS sync byte (0x47)
    - .m4s files start with 'ftyp', 'moof', or 'styp' box
    - .mp4 files start with 'ftyp' box

    Args:
        data: First few bytes of the file (at least 8 bytes)
        filename: The filename (used to determine expected format)

    Returns:
        True if magic bytes are valid, False otherwise
    """
    if len(data) < 8:
        return False

    if filename.endswith(".ts"):
        # MPEG-TS sync byte
        return data[0:1] == TS_MAGIC_BYTE

    if filename.endswith(".m4s"):
        # fMP4 segment: check for 'ftyp', 'moof', or 'styp' at offset 4
        # ISO base media file format: [size (4 bytes)][type (4 bytes)]
        box_type = data[4:8]
        return box_type in (FTYP_MAGIC, MOOF_MAGIC, STYP_MAGIC, SIDX_MAGIC, EMSG_MAGIC)

    if filename.endswith(".mp4"):
        # MP4 init segment: check for 'ftyp' at offset 4
        box_type = data[4:8]
        return box_type == FTYP_MAGIC

    if filename.endswith(".m3u8") or filename.endswith(".mpd"):
        # Playlist files - just verify they're text (ASCII/UTF-8)
        try:
            data.decode("utf-8")
            return True
        except UnicodeDecodeError:
            return False

    return False


def _write_segment_sync(
    data: bytes,
    dest_path: Path,
    checksum: str,
) -> tuple[bool, int, bool]:
    """
    Write segment file atomically with fsync (Margo's reliability requirements).

    This is the synchronous version that runs in a thread pool.
    Uses temp file + fsync + rename pattern for durability guarantee.
    Only returns success if data is safely on disk.

    Args:
        data: The segment file data
        dest_path: Final destination path
        checksum: Expected SHA256 checksum (hex string, without 'sha256:' prefix)

    Returns:
        Tuple of (written, bytes_written, checksum_verified)
    """
    # Verify checksum before writing (Ada's integrity verification)
    actual_checksum = hashlib.sha256(data).hexdigest()
    checksum_verified = actual_checksum == checksum

    if not checksum_verified:
        logger.warning(
            f"Checksum mismatch for {dest_path.name}: expected {checksum[:16]}..., got {actual_checksum[:16]}..."
        )
        return False, 0, False

    # Ensure parent directory exists
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    # Write to temp file in same directory (for atomic rename)
    temp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")

    try:
        # Write data to temp file
        with open(temp_path, "wb") as f:
            f.write(data)
            # fsync before returning 200 (Margo's durability guarantee)
            f.flush()
            os.fsync(f.fileno())

        # Atomic rename to final location
        temp_path.rename(dest_path)

        # Set file permissions
        dest_path.chmod(0o644)

        return True, len(data), True

    except Exception as e:
        logger.error(f"Failed to write segment {dest_path}: {e}")
        # Clean up temp file on failure
        temp_path.unlink(missing_ok=True)
        raise


async def write_segment_atomic(
    data: bytes,
    dest_path: Path,
    checksum: str,
) -> tuple[bool, int, bool]:
    """
    Async wrapper for atomic segment write - runs in thread pool.

    This is critical for reliability: writing large segments to NAS can take
    seconds. Running it synchronously blocks the entire event loop, preventing
    heartbeat processing. (Code review fix for Issue #478)

    Args:
        data: The segment file data
        dest_path: Final destination path
        checksum: Expected SHA256 checksum (hex string, without 'sha256:' prefix)

    Returns:
        Tuple of (written, bytes_written, checksum_verified)
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _io_executor,
        functools.partial(_write_segment_sync, data, dest_path, checksum),
    )


def _extract_tar_sync(
    tmp_path: Path,
    output_dir: Path,
    allowed_extensions: tuple,
    max_files: int,
    max_size: int,
    max_single_file: int,
    strict_filenames: Optional[tuple] = None,
) -> None:
    """
    Synchronous tar extraction - runs in thread pool to avoid blocking event loop.

    Args:
        tmp_path: Path to the tar.gz file
        output_dir: Directory to extract to
        allowed_extensions: Tuple of allowed file extensions
        max_files: Maximum number of files allowed
        max_size: Maximum total extracted size
        max_single_file: Maximum size per file
        strict_filenames: If set, only allow these exact filenames (for finalize)

    Raises:
        ValueError: If validation fails
    """
    output_dir_resolved = output_dir.resolve()
    extracted_count = 0
    extracted_size = 0

    with tarfile.open(tmp_path, "r:gz") as tar:
        for member in tar.getmembers():
            extracted_count += 1
            if extracted_count > max_files:
                raise ValueError(f"Archive contains too many files (limit: {max_files})")

            if member.isfile() and member.size > max_single_file:
                raise ValueError(f"File too large: {member.name} ({member.size} bytes)")

            extracted_size += member.size
            if extracted_size > max_size:
                raise ValueError(f"Archive too large (limit: {max_size} bytes)")

            if member.issym() or member.islnk():
                raise ValueError(f"Invalid archive: symlinks not allowed ({member.name})")

            if not (member.isfile() or member.isdir()):
                raise ValueError(f"Invalid archive: unsupported file type ({member.name})")

            if member.isfile():
                # Check strict filenames if specified
                if strict_filenames and member.name not in strict_filenames:
                    raise ValueError(f"Unexpected file in archive: {member.name}")
                # Check extensions
                if not any(member.name.endswith(ext) for ext in allowed_extensions):
                    raise ValueError(f"Invalid file type: {member.name}")

            # Validate path traversal
            member_path = output_dir / member.name
            try:
                if member_path.exists():
                    dest_resolved = member_path.resolve()
                else:
                    dest_resolved = member_path.parent.resolve() / member_path.name
            except (ValueError, OSError) as e:
                raise ValueError(f"Invalid path {member.name}: {e}")

            try:
                dest_resolved.relative_to(output_dir_resolved)
            except ValueError:
                raise ValueError(f"Path traversal detected ({member.name})")

            # Extract and fix permissions
            tar.extract(member, output_dir)
            extracted_path = output_dir / member.name
            if extracted_path.is_file():
                extracted_path.chmod(0o644)
            elif extracted_path.is_dir():
                extracted_path.chmod(0o755)


async def extract_tar_async(
    tmp_path: Path,
    output_dir: Path,
    allowed_extensions: tuple,
    max_files: int,
    max_size: int,
    max_single_file: int,
    strict_filenames: Optional[tuple] = None,
    timeout: Optional[float] = None,
) -> None:
    """
    Async wrapper for tar extraction - runs in thread pool with timeout.

    This is critical for reliability: tar extraction to NAS can take minutes
    when NAS is slow. Running it synchronously blocks the entire event loop,
    preventing heartbeat processing and causing workers to go offline.

    The timeout prevents thread pool exhaustion when NAS hangs indefinitely
    (Issue #451). Without it, 4 simultaneous slow extractions exhaust the
    thread pool and block all new uploads.

    Args:
        tmp_path: Path to tar.gz file
        output_dir: Directory to extract to
        allowed_extensions: Allowed file extensions
        max_files: Max files allowed
        max_size: Max total extracted size
        max_single_file: Max size per file
        strict_filenames: If set, only allow these exact filenames
        timeout: Extraction timeout in seconds (default: TAR_EXTRACTION_TIMEOUT)

    Raises:
        ValueError: If validation fails or extraction times out
    """
    if timeout is None:
        timeout = TAR_EXTRACTION_TIMEOUT

    loop = asyncio.get_event_loop()
    try:
        await asyncio.wait_for(
            loop.run_in_executor(
                _io_executor,
                functools.partial(
                    _extract_tar_sync,
                    tmp_path,
                    output_dir,
                    allowed_extensions,
                    max_files,
                    max_size,
                    max_single_file,
                    strict_filenames,
                ),
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.error(
            f"Tar extraction timed out after {timeout}s - possible stale NFS mount. "
            f"Source: {tmp_path}, Target: {output_dir}"
        )
        raise ValueError(f"Tar extraction timed out after {timeout}s - storage may be unresponsive")


# Initialize rate limiter
limiter = Limiter(
    key_func=get_real_ip,
    storage_uri=RATE_LIMIT_STORAGE_URL if RATE_LIMIT_ENABLED else None,
    enabled=RATE_LIMIT_ENABLED,
)

# Global flag to signal background task to stop
_shutdown_event: Optional[asyncio.Event] = None
# Track when the API started for grace period on stale job detection
_api_start_time: Optional[datetime] = None
# Grace period after API startup before running stale job detection (seconds)
# This allows workers to send heartbeats after API recovers from downtime
STALE_CHECK_STARTUP_GRACE_PERIOD = 120  # 2 minutes
# Redis key prefix for vlog application (prevents collisions if Redis is shared)
REDIS_KEY_PREFIX = "vlog:"
# Redis key for tracking last stale check time (Issue #456)
STALE_CHECK_LAST_RUN_KEY = f"{REDIS_KEY_PREFIX}stale_job_checker:last_run"
# TTL for completion tokens (15 minutes to cover extended retry scenarios)
COMPLETION_TOKEN_TTL = 900  # 15 minutes


async def verify_admin_secret(x_admin_secret: Optional[str] = Header(None, alias="X-Admin-Secret")):
    """
    Verify admin secret for worker management endpoints.

    This dependency protects sensitive endpoints:
    - POST /api/worker/register (worker registration)
    - GET /api/workers (list all workers)
    - POST /api/workers/{id}/revoke (revoke worker API key)

    Set VLOG_WORKER_ADMIN_SECRET environment variable to enable authentication.
    If not set, these endpoints will return 503 Service Unavailable.

    Raises:
        HTTPException 503: If WORKER_ADMIN_SECRET is not configured
        HTTPException 401: If X-Admin-Secret header is missing
        HTTPException 403: If X-Admin-Secret header is invalid
    """
    if not WORKER_ADMIN_SECRET:
        logger.warning("Worker admin endpoint called but VLOG_WORKER_ADMIN_SECRET is not configured")
        raise HTTPException(
            status_code=503,
            detail="Worker admin endpoints require VLOG_WORKER_ADMIN_SECRET to be configured",
        )

    if not x_admin_secret:
        raise HTTPException(
            status_code=401,
            detail="X-Admin-Secret header required",
        )

    # Use constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(x_admin_secret, WORKER_ADMIN_SECRET):
        logger.warning("Invalid admin secret provided for worker management endpoint")
        raise HTTPException(
            status_code=403,
            detail="Invalid admin secret",
        )


async def _should_skip_stale_check_grace_period() -> bool:
    """
    Determine if we should skip the startup grace period based on Redis state.

    Issue #456: The grace period was previously based on API process uptime,
    which reset on every restart. If the API restarts frequently, stale job
    checking would never run. Now we track the last check time in Redis,
    so a recent check by a previous API instance satisfies the grace period.

    Returns:
        True if grace period should be skipped (a recent check was done),
        False if we should still wait
    """
    redis = await get_redis()
    if not redis:
        # Redis not available, fall back to API startup time check
        return False

    try:
        last_check_str = await redis.get(STALE_CHECK_LAST_RUN_KEY)
        if last_check_str:
            # Redis client uses decode_responses=True, so this is already a string
            last_check = datetime.fromisoformat(last_check_str)
            seconds_since_last = (datetime.now(timezone.utc) - last_check).total_seconds()
            if seconds_since_last < STALE_CHECK_STARTUP_GRACE_PERIOD:
                # A recent check was done, no need for grace period
                logger.debug(
                    f"Recent stale check found in Redis ({seconds_since_last:.0f}s ago), skipping startup grace period"
                )
                return True
    except Exception as e:
        logger.warning(f"Failed to check last stale run time from Redis: {e}")

    return False


async def _record_stale_check_time():
    """
    Record the current time as the last stale check time in Redis.

    Issue #456: This allows other API instances to know that a recent check
    was performed, so they don't need to wait for their own grace period.
    """
    redis = await get_redis()
    if not redis:
        return

    try:
        now = datetime.now(timezone.utc).isoformat()
        # TTL of 5 minutes - longer than grace period, allows for handoff
        await redis.set(STALE_CHECK_LAST_RUN_KEY, now, ex=300)
    except Exception as e:
        logger.warning(f"Failed to record stale check time in Redis: {e}")


async def _detect_and_release_stale_jobs():
    """
    Core logic for detecting and releasing stale jobs from offline workers.

    This function is extracted for testability - it performs one check without looping.
    Returns the number of stale workers found and processed.

    IMPORTANT: Uses atomic conditional updates to prevent race conditions with
    heartbeat updates. A worker is only marked offline if its last_heartbeat
    is still stale at update time (prevents marking workers offline right after
    they sent a valid heartbeat).

    Jobs are only released if the job's claim has expired (claim_expires_at < now),
    not just based on worker heartbeat age. This prevents releasing jobs when
    the API was temporarily unresponsive but workers were actively processing.

    Issue #456: Grace period now checks Redis for recent checks by other API
    instances, preventing stale jobs from accumulating during restart storms.
    """
    global _api_start_time
    now = datetime.now(timezone.utc)

    # Issue #456: Check if a recent stale check was done (in Redis)
    # If so, we can skip the local startup grace period
    skip_grace = await _should_skip_stale_check_grace_period()

    # Skip stale check during startup grace period (unless Redis shows recent check)
    # This allows workers to send heartbeats after API recovers from downtime
    if not skip_grace and _api_start_time:
        time_since_startup = (now - _api_start_time).total_seconds()
        if time_since_startup < STALE_CHECK_STARTUP_GRACE_PERIOD:
            logger.debug(
                f"Skipping stale check during startup grace period "
                f"({time_since_startup:.0f}s < {STALE_CHECK_STARTUP_GRACE_PERIOD}s)"
            )
            return 0

    offline_threshold = now - timedelta(minutes=WORKER_OFFLINE_THRESHOLD_MINUTES)

    # Find workers that are not already offline but haven't sent heartbeat
    # Also catch workers that never sent a heartbeat (last_heartbeat IS NULL)
    # by checking if they were registered before the offline threshold
    stale_workers = await database.fetch_all(
        workers.select()
        .where(workers.c.status != "offline")
        .where(
            sa.or_(
                workers.c.last_heartbeat < offline_threshold,
                sa.and_(
                    workers.c.last_heartbeat.is_(None),
                    workers.c.registered_at < offline_threshold,
                ),
            )
        )
    )

    processed_count = 0
    for worker in stale_workers:
        worker_name = worker["worker_name"] or worker["worker_id"][:8]
        worker_last_hb = worker["last_heartbeat"]

        # ATOMIC CONDITIONAL UPDATE: Only mark offline if last_heartbeat is STILL old
        # This prevents race condition where heartbeat arrives between our fetch and update
        # Also handle workers that never sent a heartbeat (last_heartbeat IS NULL)
        result = await database.execute(
            workers.update()
            .where(workers.c.id == worker["id"])
            .where(workers.c.status != "offline")  # Don't update if already offline
            .where(
                sa.or_(
                    workers.c.last_heartbeat < offline_threshold,
                    sa.and_(
                        workers.c.last_heartbeat.is_(None),
                        workers.c.registered_at < offline_threshold,
                    ),
                )
            )
            .values(status="offline", current_job_id=None)
        )

        if result == 0:
            # Worker was updated (heartbeat received) between fetch and update
            logger.info(f"Worker '{worker_name}' recovered (heartbeat received since stale check started)")
            continue

        processed_count += 1
        logger.warning(f"Worker '{worker_name}' went offline (no heartbeat since {worker_last_hb})")

        # Trigger webhook for worker going offline (Issue #203)
        try:
            await trigger_webhook_event(
                "worker.offline",
                {
                    "worker_id": worker["worker_id"],
                    "worker_name": worker["worker_name"],
                    "worker_type": worker["worker_type"],
                    "last_heartbeat": worker_last_hb.isoformat() if worker_last_hb else None,
                    "registered_at": worker["registered_at"].isoformat() if worker["registered_at"] else None,
                },
            )
        except Exception as e:
            # Don't fail stale job check if webhook fails
            logger.warning(f"Failed to trigger worker offline webhook: {e}")

        # Find jobs claimed by this worker that have EXPIRED claims
        # Don't release jobs where the claim hasn't expired yet - the worker might still complete them
        stale_jobs = await database.fetch_all(
            transcoding_jobs.select()
            .where(transcoding_jobs.c.worker_id == worker["worker_id"])
            .where(transcoding_jobs.c.completed_at.is_(None))
            .where(
                sa.or_(
                    transcoding_jobs.c.claim_expires_at.is_(None),  # No expiry set
                    transcoding_jobs.c.claim_expires_at < now,  # Claim has expired
                )
            )
        )

        for job in stale_jobs:
            claim_expires = job["claim_expires_at"]
            logger.info(
                f"Releasing stale job {job['id']} from offline worker '{worker_name}' (claim expired: {claim_expires})"
            )

            # Release the job claim
            await database.execute(
                transcoding_jobs.update()
                .where(transcoding_jobs.c.id == job["id"])
                .values(
                    claimed_at=None,
                    claim_expires_at=None,
                    worker_id=None,
                    current_step=None,
                )
            )

            # Reset video status back to pending so it can be reclaimed
            video = await database.fetch_one(videos.select().where(videos.c.id == job["video_id"]))
            if video and video["status"] == "processing":
                await database.execute(videos.update().where(videos.c.id == job["video_id"]).values(status="pending"))
                logger.info(f"Reset video {job['video_id']} status to pending")

    # Issue #456: Record successful check time in Redis for cross-instance coordination
    await _record_stale_check_time()

    return processed_count


async def check_stale_jobs():
    """
    Background task to detect and release stale jobs from offline workers.

    Runs periodically to:
    1. Find workers that haven't sent heartbeats recently
    2. Mark them as offline
    3. Release any jobs they had claimed
    4. Reset video status back to pending so jobs can be reclaimed
    """
    global _shutdown_event
    logger.info(f"Stale job checker started (interval: {STALE_JOB_CHECK_INTERVAL}s)")

    while not _shutdown_event.is_set():
        try:
            await _detect_and_release_stale_jobs()
        except Exception as e:
            logger.exception(f"Error in stale job checker: {e}")

        # Wait for the next check interval or shutdown
        try:
            await asyncio.wait_for(_shutdown_event.wait(), timeout=STALE_JOB_CHECK_INTERVAL)
            # If we get here, shutdown was requested
            break
        except asyncio.TimeoutError:
            # Normal timeout, continue checking
            pass

    logger.info("Stale job checker stopped")


# Known quality directory names that may contain transcoded content
QUALITY_DIRECTORY_NAMES = {"2160p", "1440p", "1080p", "720p", "480p", "360p", "original"}


async def _cleanup_orphaned_quality_directories() -> int:
    """
    Scan video directories and remove orphaned quality directories.

    A quality directory is considered orphaned if:
    1. It matches a known quality name (1080p, 720p, etc.)
    2. There's no corresponding record in the video_qualities table
    3. The directory is older than ORPHAN_CLEANUP_MIN_AGE

    This handles the case where a worker uploads partial qualities before
    abandoning a job (Issue #450). Without cleanup, these directories
    accumulate and waste disk space.

    Returns:
        Number of orphaned directories cleaned up
    """
    global _api_start_time

    now = datetime.now(timezone.utc)

    # Issue #456: Check if a recent stale check was done (in Redis)
    # If so, we can skip the local startup grace period
    skip_grace = await _should_skip_stale_check_grace_period()

    # Skip during startup grace period (same as stale job checker)
    if not skip_grace and _api_start_time:
        time_since_startup = (now - _api_start_time).total_seconds()
        if time_since_startup < STALE_CHECK_STARTUP_GRACE_PERIOD:
            return 0

    cleaned_count = 0
    min_age_seconds = ORPHAN_CLEANUP_MIN_AGE

    try:
        # Get all videos with their quality records
        videos_with_qualities = await database.fetch_all(
            sa.select(
                videos.c.id,
                videos.c.slug,
                videos.c.status,
            ).where(videos.c.deleted_at.is_(None))
        )

        # Build a lookup of video_id -> set of quality names
        quality_lookup: dict[int, set[str]] = {}
        all_qualities = await database.fetch_all(video_qualities.select())
        for q in all_qualities:
            vid = q["video_id"]
            if vid not in quality_lookup:
                quality_lookup[vid] = set()
            quality_lookup[vid].add(q["quality"])

        # Also check for active transcoding jobs - don't cleanup while job is running
        active_jobs = await database.fetch_all(
            transcoding_jobs.select().where(transcoding_jobs.c.completed_at.is_(None))
        )
        active_video_ids = {job["video_id"] for job in active_jobs}

        for video in videos_with_qualities:
            video_id = video["id"]
            video_slug = video["slug"]
            video_dir = VIDEOS_DIR / video_slug

            # Skip videos with active transcoding jobs
            if video_id in active_video_ids:
                continue

            if not video_dir.exists() or not video_dir.is_dir():
                continue

            # Check each subdirectory
            registered_qualities = quality_lookup.get(video_id, set())

            for subdir in video_dir.iterdir():
                if not subdir.is_dir():
                    continue

                # Only process known quality directory names
                if subdir.name not in QUALITY_DIRECTORY_NAMES:
                    continue

                # Check if this quality is registered in the database
                if subdir.name in registered_qualities:
                    continue

                # This quality directory has no database record - check age
                try:
                    dir_mtime = subdir.stat().st_mtime
                    age_seconds = now.timestamp() - dir_mtime
                except OSError:
                    continue

                if age_seconds < min_age_seconds:
                    # Too new - might still be in progress
                    logger.debug(
                        f"Orphaned quality dir {subdir} is only {age_seconds / 3600:.1f}h old, "
                        f"threshold is {min_age_seconds / 3600:.1f}h - skipping"
                    )
                    continue

                # Old enough and no database record - delete it
                logger.info(
                    f"Cleaning orphaned quality directory: {subdir} "
                    f"(age: {age_seconds / 3600:.1f}h, no database record)"
                )
                try:
                    shutil.rmtree(subdir)
                    cleaned_count += 1
                except Exception as e:
                    logger.error(f"Failed to remove orphaned directory {subdir}: {e}")

    except Exception as e:
        logger.exception(f"Error during orphan cleanup scan: {e}")

    return cleaned_count


async def cleanup_orphaned_files():
    """
    Background task to clean up orphaned quality directories (Issue #450).

    Runs periodically to detect and remove quality directories that were
    uploaded during transcoding but never had their job completed.
    This prevents disk space leaks from abandoned partial uploads.
    """
    global _shutdown_event

    if not ORPHAN_CLEANUP_ENABLED:
        logger.info("Orphan cleanup is disabled (VLOG_ORPHAN_CLEANUP_ENABLED=false)")
        return

    logger.info(
        f"Orphan cleanup started (interval: {ORPHAN_CLEANUP_INTERVAL}s, min_age: {ORPHAN_CLEANUP_MIN_AGE / 3600:.1f}h)"
    )

    while not _shutdown_event.is_set():
        try:
            cleaned = await _cleanup_orphaned_quality_directories()
            if cleaned > 0:
                logger.info(f"Orphan cleanup removed {cleaned} orphaned quality directories")
        except Exception as e:
            logger.exception(f"Error in orphan cleanup: {e}")

        # Wait for the next check interval or shutdown
        try:
            await asyncio.wait_for(_shutdown_event.wait(), timeout=ORPHAN_CLEANUP_INTERVAL)
            break
        except asyncio.TimeoutError:
            pass

    logger.info("Orphan cleanup stopped")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage database connection lifecycle and graceful shutdown."""
    global _shutdown_event, _api_start_time
    _shutdown_event = asyncio.Event()
    _api_start_time = datetime.now(timezone.utc)

    # Startup
    await database.connect()
    await configure_database()
    logger.info(
        f"Worker API started - database connected. Stale check grace period: {STALE_CHECK_STARTUP_GRACE_PERIOD}s"
    )

    # Warn about in-memory rate limiting limitations (security issue #446)
    if RATE_LIMIT_ENABLED and RATE_LIMIT_STORAGE_URL == "memory://":
        logger.warning(
            "SECURITY: Rate limiting is using in-memory storage. "
            "With multiple API instances, attackers can bypass rate limits by distributing "
            "requests across instances. For production with load balancing, configure Redis: "
            "VLOG_RATE_LIMIT_STORAGE_URL=redis://localhost:6379 "
            "(or set VLOG_REDIS_URL which will be auto-detected)"
        )

    # Start background tasks
    stale_job_task = asyncio.create_task(check_stale_jobs())
    orphan_cleanup_task = asyncio.create_task(cleanup_orphaned_files())

    yield

    # Signal background tasks to stop
    _shutdown_event.set()

    # Wait for stale job checker
    try:
        await asyncio.wait_for(stale_job_task, timeout=5.0)
    except asyncio.TimeoutError:
        logger.warning("Stale job checker did not stop in time, cancelling...")
        stale_job_task.cancel()
        try:
            await stale_job_task
        except asyncio.CancelledError:
            pass  # Expected when cancelling

    # Wait for orphan cleanup task
    try:
        await asyncio.wait_for(orphan_cleanup_task, timeout=5.0)
    except asyncio.TimeoutError:
        logger.warning("Orphan cleanup did not stop in time, cancelling...")
        orphan_cleanup_task.cancel()
        try:
            await orphan_cleanup_task
        except asyncio.CancelledError:
            pass  # Expected when cancelling

    # Shutdown - release claimed jobs that haven't been completed
    logger.info("Worker API shutting down - releasing claimed jobs...")
    try:
        # Find all jobs that are still claimed but not completed
        claimed_jobs = await database.fetch_all(
            transcoding_jobs.select()
            .where(transcoding_jobs.c.claimed_at.isnot(None))
            .where(transcoding_jobs.c.completed_at.is_(None))
        )

        if claimed_jobs:
            logger.info(f"Found {len(claimed_jobs)} claimed jobs to release")

            for job in claimed_jobs:
                # Release the job claim
                await database.execute(
                    transcoding_jobs.update()
                    .where(transcoding_jobs.c.id == job["id"])
                    .values(
                        claimed_at=None,
                        claim_expires_at=None,
                        worker_id=None,
                        current_step=None,
                    )
                )

                # Reset video status back to pending if it was processing
                video = await database.fetch_one(videos.select().where(videos.c.id == job["video_id"]))
                if video and video["status"] == "processing":
                    await database.execute(
                        videos.update().where(videos.c.id == job["video_id"]).values(status="pending")
                    )

            logger.info(f"Released {len(claimed_jobs)} claimed job(s)")
        else:
            logger.info("No claimed jobs to release")

        # Clear current_job_id from all workers
        await database.execute(workers.update().where(workers.c.current_job_id.isnot(None)).values(current_job_id=None))

    except Exception as e:
        logger.exception(f"Error during shutdown cleanup: {e}")

    # Close database connection
    await database.disconnect()
    logger.info("Worker API shutdown complete")


app = FastAPI(
    title="VLog Worker API",
    description="API for distributed transcoding workers",
    version="1.0.0",
    lifespan=lifespan,
)

# Request ID middleware for tracing
app.add_middleware(RequestIDMiddleware)

# CORS - allow all origins since workers use API key auth (not cookies)
# Note: allow_credentials must be False with wildcard origins per CORS spec
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)

# HTTP metrics middleware (outermost - captures all requests including CORS preflight)
# Issue #207: Tracks requests in progress, duration, and total count
app.add_middleware(HTTPMetricsMiddleware, api_name="worker")

# Rate limiting setup
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)


# =============================================================================
# Worker Registration
# =============================================================================


@app.post("/api/worker/register", response_model=WorkerRegisterResponse)
@limiter.limit(RATE_LIMIT_WORKER_REGISTER)
async def register_worker(
    request: Request,
    data: WorkerRegisterRequest,
    _admin: None = Depends(verify_admin_secret),
):
    """
    Register a new transcoding worker and generate API key.

    Requires X-Admin-Secret header with VLOG_WORKER_ADMIN_SECRET value.

    The API key is only returned once at registration - store it securely.

    Validates:
    - Worker capabilities schema (GPU, encoders, codecs)
    - Worker metadata schema (Kubernetes pod info, etc.)
    - JSON size limits (10KB max per field)
    """
    worker_id = str(uuid.uuid4())
    api_key = secrets.token_urlsafe(32)  # 256-bit key
    key_hash, hash_version = hash_api_key(api_key)  # Returns (hash, version) tuple
    key_prefix = get_key_prefix(api_key)
    now = datetime.now(timezone.utc)

    worker_name = data.worker_name or f"worker-{worker_id[:8]}"

    # Validate and serialize capabilities
    capabilities_json = None
    if data.capabilities:  # None check before accessing model_dump()
        capabilities_json = json.dumps(data.capabilities.model_dump())
        if len(capabilities_json) > 10000:  # 10KB limit
            raise HTTPException(status_code=400, detail="Capabilities JSON too large (max 10KB)")

    # Validate and serialize metadata
    metadata_json = None
    if data.metadata:  # None check before accessing model_dump()
        metadata_json = json.dumps(data.metadata.model_dump())
        if len(metadata_json) > 10000:  # 10KB limit
            raise HTTPException(status_code=400, detail="Metadata JSON too large (max 10KB)")

    # Track worker_db_id in a mutable container for the transaction
    result = {"worker_db_id": None}

    async def do_register_transaction():
        """Execute the registration transaction - wrapped with retry logic."""
        async with database.transaction():
            # Create worker record
            result["worker_db_id"] = await database.execute(
                workers.insert().values(
                    worker_id=worker_id,
                    worker_name=worker_name,
                    worker_type=data.worker_type,
                    registered_at=now,
                    last_heartbeat=now,
                    status="active",
                    capabilities=capabilities_json,
                    metadata=metadata_json,
                )
            )

            # Create API key record with argon2id hash (Issue #445)
            await database.execute(
                worker_api_keys.insert().values(
                    worker_id=result["worker_db_id"],
                    key_hash=key_hash,
                    hash_version=hash_version,
                    key_prefix=key_prefix,
                    created_at=now,
                )
            )

    try:
        await execute_with_retry(do_register_transaction)
    except DatabaseLockedError as e:
        raise HTTPException(
            status_code=503,
            detail="Database temporarily unavailable, please retry",
        ) from e

    # Trigger webhook for worker registration (Issue #203)
    try:
        # Parse capabilities for webhook payload
        capabilities_dict = None
        if data.capabilities:
            capabilities_dict = data.capabilities.model_dump()

        await trigger_webhook_event(
            "worker.registered",
            {
                "worker_id": worker_id,
                "worker_name": worker_name,
                "worker_type": data.worker_type,
                "registered_at": now.isoformat(),
                "capabilities": capabilities_dict,
            },
        )
    except Exception as e:
        # Don't fail registration if webhook fails
        logger.warning(f"Failed to trigger worker registration webhook: {e}")

    return WorkerRegisterResponse(
        worker_id=worker_id,
        api_key=api_key,
        message="Worker registered successfully. Store the API key securely - it won't be shown again.",
    )


# =============================================================================
# Heartbeat
# =============================================================================


@app.post("/api/worker/heartbeat", response_model=HeartbeatResponse)
@limiter.limit(RATE_LIMIT_WORKER_DEFAULT)
async def worker_heartbeat(
    request: Request,
    data: HeartbeatRequest,
    worker: dict = Depends(verify_worker_key),
):
    """
    Update worker heartbeat timestamp.

    Validates metadata.capabilities if provided to ensure workers don't store
    arbitrarily large or malicious JSON blobs.

    If the worker was previously offline, this heartbeat will bring it back online
    and log the recovery for debugging connectivity issues.

    Also performs code version checking:
    - If workers.require_version_match is enabled, compares worker's code_version
      against the server's CODE_VERSION
    - Returns version_ok=False if mismatch, signaling worker should exit
    """
    from code_version import CODE_VERSION

    now = datetime.now(timezone.utc)
    worker_name = worker["worker_name"] or worker["worker_id"][:8]
    was_offline = worker["status"] == "offline"

    update_values = {
        "last_heartbeat": now,
        "status": data.status,
    }

    # Validate and serialize metadata with size limit
    # Include worker's reported code_version in metadata for tracking
    metadata = data.metadata or {}
    if data.code_version:
        metadata["code_version"] = data.code_version
    if metadata:
        metadata_json = json.dumps(metadata)
        if len(metadata_json) > 10000:  # 10KB limit
            raise HTTPException(status_code=400, detail="Metadata JSON too large (max 10KB)")
        update_values["metadata"] = metadata_json

    await database.execute(workers.update().where(workers.c.id == worker["id"]).values(**update_values))

    # Check code version compatibility
    # Get settings for version enforcement
    from api.settings_service import get_settings_service

    settings = get_settings_service()
    require_version_match = await settings.get("workers.require_version_match", True)
    require_version_field = await settings.get("workers.require_version_field", False)

    # Determine if version is ok
    version_ok = True
    required_version = CODE_VERSION

    # Check if version field is required but missing
    if require_version_field and not data.code_version:
        version_ok = False
        logger.warning(
            f"Worker '{worker_name}' did not send code_version field. "
            f"workers.require_version_field is enabled - worker must be updated."
        )
    elif require_version_match and data.code_version:
        # Version mismatch - worker should exit and be restarted with new image
        if data.code_version != CODE_VERSION:
            version_ok = False
            logger.warning(
                f"Worker '{worker_name}' has outdated code version: "
                f"{data.code_version} (required: {CODE_VERSION}). "
                f"Worker should exit and update."
            )

    # Log recovery from offline status for debugging
    if was_offline:
        logger.info(
            f"Worker '{worker_name}' recovered from offline status "
            f"(was offline since last_heartbeat={worker['last_heartbeat']})"
        )

    # Publish worker status to Redis pub/sub for real-time UI updates
    # Get current job info for the status update
    current_video_slug = None
    hwaccel_type = None
    if worker.get("current_job_id"):
        job = await database.fetch_one(
            transcoding_jobs.select().where(transcoding_jobs.c.id == worker["current_job_id"])
        )
        if job:
            video = await database.fetch_one(videos.select().where(videos.c.id == job["video_id"]))
            current_video_slug = video["slug"] if video else None

    # Extract hwaccel type from metadata
    if data.metadata and data.metadata.get("capabilities"):
        caps = data.metadata["capabilities"]
        if caps.get("hwaccel_available"):
            hwaccel_type = caps.get("hwaccel_type")

    await Publisher.publish_worker_status(
        worker_id=worker["worker_id"],
        worker_name=worker_name,
        status=data.status,
        current_job_id=worker.get("current_job_id"),
        current_video_slug=current_video_slug,
        hwaccel_type=hwaccel_type,
    )

    # Issue #458: Return server's view of worker state for stale data detection
    # This allows the worker to detect if the server's DB state is stale
    # (e.g., database write failed but HTTP 200 was returned)
    return HeartbeatResponse(
        status="ok",
        server_time=now,
        required_version=required_version,
        version_ok=version_ok,
        worker_status=data.status,  # What we just wrote
        current_job_id=worker.get("current_job_id"),  # What DB has for this worker
        last_heartbeat_recorded=now,  # What we just wrote
    )


# =============================================================================
# Job Claiming
# =============================================================================


def worker_has_gpu(worker: dict) -> bool:
    """Check if a worker has GPU acceleration enabled based on capabilities or metadata."""
    # First check the capabilities column (set at registration)
    if worker.get("capabilities"):
        try:
            caps = (
                json.loads(worker["capabilities"])
                if isinstance(worker["capabilities"], str)
                else worker["capabilities"]
            )
            if caps.get("hwaccel_enabled"):
                return True
        except (json.JSONDecodeError, TypeError):
            pass  # Malformed JSON, try metadata instead

    # Fall back to checking metadata.capabilities (set via heartbeat)
    if worker.get("metadata"):
        try:
            metadata = json.loads(worker["metadata"]) if isinstance(worker["metadata"], str) else worker["metadata"]
            capabilities = metadata.get("capabilities", {})
            if capabilities.get("hwaccel_enabled"):
                return True
        except (json.JSONDecodeError, TypeError):
            pass  # Malformed JSON, assume no GPU

    return False


@app.post("/api/worker/claim", response_model=ClaimJobResponse)
@limiter.limit(RATE_LIMIT_WORKER_DEFAULT)
async def claim_job(
    request: Request,
    job_id: Optional[int] = None,
    worker: dict = Depends(verify_worker_key),
):
    """
    Atomically claim a transcoding job.

    Uses database transaction for distributed safety.
    Claims expire after WORKER_CLAIM_DURATION_MINUTES (extended on progress updates).

    Args:
        job_id: Optional specific job ID to claim (for Redis-dispatched jobs).
                If provided, will only claim this specific job.
                If not provided, claims any available job from the database.

    GPU workers have priority over CPU workers. If a CPU worker requests a job
    but idle GPU workers are available, the CPU worker will be told to wait.

    Also enforces code version matching if workers.require_version_match is enabled.
    Workers with outdated code will be rejected from claiming jobs.
    """
    from api.settings_service import get_settings_service
    from code_version import CODE_VERSION

    # Check code version before allowing job claim (defense in depth)
    settings = get_settings_service()
    require_version_match = await settings.get("workers.require_version_match", True)
    require_version_field = await settings.get("workers.require_version_field", False)

    # Get worker's code version from metadata
    worker_metadata = worker.get("metadata")
    worker_version = None
    if worker_metadata:
        try:
            metadata = json.loads(worker_metadata) if isinstance(worker_metadata, str) else worker_metadata
            worker_version = metadata.get("code_version")
        except (json.JSONDecodeError, TypeError):
            pass  # No metadata or invalid format

    worker_name = worker.get("worker_name") or worker["worker_id"][:8]

    # Check if version field is required but missing
    if require_version_field and not worker_version:
        logger.warning(
            f"Rejecting job claim from worker '{worker_name}' - "
            f"no code_version in metadata (workers.require_version_field is enabled)"
        )
        return ClaimJobResponse(message="Version field required: worker must send code_version in heartbeat")

    # Check for version mismatch
    if require_version_match and worker_version and worker_version != CODE_VERSION:
        logger.warning(
            f"Rejecting job claim from worker '{worker_name}' "
            f"due to version mismatch: {worker_version} != {CODE_VERSION}"
        )
        return ClaimJobResponse(
            message=f"Version mismatch: worker has {worker_version}, server requires {CODE_VERSION}"
        )

    now = datetime.now(timezone.utc)
    claim_duration = timedelta(minutes=WORKER_CLAIM_DURATION_MINUTES)
    expires_at = now + claim_duration

    # Check worker priority - GPU workers get priority over CPU workers
    requesting_worker_has_gpu = worker_has_gpu(worker)

    if not requesting_worker_has_gpu:
        # This is a CPU worker - check if any GPU workers are idle AND recently active
        # Only defer to GPU workers that have sent a heartbeat within 2x the heartbeat interval
        # This prevents CPU workers from being starved by unresponsive GPU workers
        gpu_worker_active_threshold = now - timedelta(seconds=WORKER_HEARTBEAT_INTERVAL * 2)

        idle_gpu_workers = await database.fetch_all(
            workers.select()
            .where(workers.c.status == "idle")
            .where(workers.c.id != worker["id"])  # Exclude self
            .where(workers.c.last_heartbeat.isnot(None))  # Exclude workers without heartbeat
            .where(workers.c.last_heartbeat >= gpu_worker_active_threshold)  # Must have recent heartbeat
        )

        # Check if any idle workers have GPU
        for idle_worker in idle_gpu_workers:
            if worker_has_gpu(dict(idle_worker)):
                # GPU worker is available and recently active, CPU worker should wait
                return ClaimJobResponse(message="Waiting for GPU workers")

    # Store job data in mutable container for the transaction
    claim_result = {"job": None}

    async def do_claim_transaction():
        """Execute the claim transaction - wrapped with retry logic."""
        async with database.transaction():
            # First, clean up any expired claims - reset video status to 'pending'
            # so they can be picked up by the normal claim query
            await database.execute(
                sa.text("""
                    UPDATE videos SET status = 'pending'
                    WHERE status = 'processing'
                      AND id IN (
                          SELECT video_id FROM transcoding_jobs
                          WHERE claim_expires_at < :now
                            AND completed_at IS NULL
                      )
                """).bindparams(now=now)
            )

            # Also clear the stale claim data from the jobs
            await database.execute(
                sa.text("""
                    UPDATE transcoding_jobs
                    SET worker_id = NULL, claimed_at = NULL, claim_expires_at = NULL
                    WHERE claim_expires_at < :now
                      AND completed_at IS NULL
                """).bindparams(now=now)
            )

            # Find job to claim with row locking
            # FOR UPDATE SKIP LOCKED is critical for distributed workers:
            # - FOR UPDATE: locks the selected row for the transaction duration
            # - SKIP LOCKED: if another worker already locked a row, skip it
            # Without this, multiple workers can SELECT the same job simultaneously,
            # then both UPDATE it, with the second overwriting the first's claim.
            #
            # Note: FOR UPDATE SKIP LOCKED is PostgreSQL-specific. SQLite uses
            # database-level locking via transactions, which is sufficient for
            # single-instance testing but not for distributed workers.
            # Check the actual database URL being used (tests may patch this)
            db_url = str(database.url)
            is_postgresql = db_url.startswith("postgresql")

            # Job is claimable if:
            # - video status is 'pending' (normal new upload), OR
            # - video status is 'ready' AND job has retranscode_metadata (Issue #408)
            if job_id is not None:
                # Targeted claim for a specific job (Redis-dispatched)
                if is_postgresql:
                    job = await database.fetch_one(
                        sa.text("""
                            SELECT tj.id, tj.video_id, v.slug, v.duration, v.source_width, v.source_height,
                                   tj.retranscode_metadata
                            FROM transcoding_jobs tj
                            JOIN videos v ON tj.video_id = v.id
                            WHERE tj.id = :job_id
                              AND (v.status = 'pending' OR (v.status = 'ready' AND tj.retranscode_metadata IS NOT NULL))
                              AND v.deleted_at IS NULL
                              AND tj.claimed_at IS NULL
                              AND tj.completed_at IS NULL
                            FOR UPDATE OF tj SKIP LOCKED
                        """).bindparams(job_id=job_id)
                    )
                else:
                    job = await database.fetch_one(
                        sa.text("""
                            SELECT tj.id, tj.video_id, v.slug, v.duration, v.source_width, v.source_height,
                                   tj.retranscode_metadata
                            FROM transcoding_jobs tj
                            JOIN videos v ON tj.video_id = v.id
                            WHERE tj.id = :job_id
                              AND (v.status = 'pending' OR (v.status = 'ready' AND tj.retranscode_metadata IS NOT NULL))
                              AND v.deleted_at IS NULL
                              AND tj.claimed_at IS NULL
                              AND tj.completed_at IS NULL
                        """).bindparams(job_id=job_id)
                    )
            elif is_postgresql:
                # Find oldest unclaimed pending job
                job = await database.fetch_one(
                    sa.text("""
                        SELECT tj.id, tj.video_id, v.slug, v.duration, v.source_width, v.source_height,
                               tj.retranscode_metadata
                        FROM transcoding_jobs tj
                        JOIN videos v ON tj.video_id = v.id
                        WHERE (v.status = 'pending' OR (v.status = 'ready' AND tj.retranscode_metadata IS NOT NULL))
                          AND v.deleted_at IS NULL
                          AND tj.claimed_at IS NULL
                          AND tj.completed_at IS NULL
                        ORDER BY v.created_at ASC
                        LIMIT 1
                        FOR UPDATE OF tj SKIP LOCKED
                    """)
                )
            else:
                # SQLite: use regular SELECT within transaction
                # SQLite's transaction isolation prevents concurrent modifications
                job = await database.fetch_one(
                    sa.text("""
                        SELECT tj.id, tj.video_id, v.slug, v.duration, v.source_width, v.source_height,
                               tj.retranscode_metadata
                        FROM transcoding_jobs tj
                        JOIN videos v ON tj.video_id = v.id
                        WHERE (v.status = 'pending' OR (v.status = 'ready' AND tj.retranscode_metadata IS NOT NULL))
                          AND v.deleted_at IS NULL
                          AND tj.claimed_at IS NULL
                          AND tj.completed_at IS NULL
                        ORDER BY v.created_at ASC
                        LIMIT 1
                    """)
                )

            if not job:
                claim_result["job"] = None
                return

            claim_result["job"] = dict(job)

            # Get worker name for permanent record
            worker_name = worker["worker_name"] or f"worker-{worker['worker_id'][:8]}"

            # Update job with claim and permanent worker record
            await database.execute(
                transcoding_jobs.update()
                .where(transcoding_jobs.c.id == job["id"])
                .values(
                    worker_id=worker["worker_id"],
                    claimed_at=now,
                    claim_expires_at=expires_at,
                    started_at=now,
                    current_step="claimed",
                    # Permanent record of which worker processed this job
                    processed_by_worker_id=worker["worker_id"],
                    processed_by_worker_name=worker_name,
                )
            )

            # Update video status
            await database.execute(videos.update().where(videos.c.id == job["video_id"]).values(status="processing"))

            # Update worker's current job
            await database.execute(
                workers.update().where(workers.c.id == worker["id"]).values(current_job_id=job["id"])
            )

    try:
        await execute_with_retry(do_claim_transaction)
    except DatabaseLockedError as e:
        raise HTTPException(
            status_code=503,
            detail="Database temporarily unavailable, please retry",
        ) from e

    job = claim_result["job"]
    if not job:
        return ClaimJobResponse(message="No jobs available")

    # Issue #207: Record job started metric
    TRANSCODING_JOBS_TOTAL.labels(status="started").inc()

    # Handle retranscode cleanup if metadata is present (Issue #408)
    # This performs the deferred cleanup that was postponed when the video was queued
    if job.get("retranscode_metadata"):
        try:
            metadata = json.loads(job["retranscode_metadata"])
            video_id = job["video_id"]
            video_dir = Path(metadata.get("video_dir", ""))
            qualities_to_delete = metadata.get("qualities_to_delete", [])
            retranscode_all = metadata.get("retranscode_all", False)
            delete_transcription = metadata.get("delete_transcription", False)

            # Delete quality files from disk
            if video_dir.exists():
                for quality in qualities_to_delete:
                    # Delete quality playlist (.m3u8)
                    playlist = video_dir / f"{quality}.m3u8"
                    if playlist.exists():
                        playlist.unlink()

                    # Delete quality segments (pattern: {quality}_XXXX.ts for HLS/TS)
                    for segment in video_dir.glob(f"{quality}_*.ts"):
                        segment.unlink()

                    # Delete CMAF segments if present (pattern: {quality}/*.m4s and init.mp4)
                    quality_dir = video_dir / quality
                    if quality_dir.exists() and quality_dir.is_dir():
                        shutil.rmtree(quality_dir, ignore_errors=True)

                # If retranscoding all, also delete master playlist and thumbnail
                if retranscode_all:
                    for master_file in ["master.m3u8", "manifest.mpd"]:
                        master = video_dir / master_file
                        if master.exists():
                            master.unlink()
                    thumb = video_dir / "thumbnail.jpg"
                    if thumb.exists():
                        thumb.unlink()

                # Delete VTT file if deleting transcription
                if delete_transcription:
                    vtt_path = video_dir / "captions.vtt"
                    if vtt_path.exists():
                        vtt_path.unlink()

            # Database cleanup in a transaction for consistency
            async with database.transaction():
                # Delete video_qualities records
                if retranscode_all:
                    await database.execute(video_qualities.delete().where(video_qualities.c.video_id == video_id))
                else:
                    await database.execute(
                        video_qualities.delete().where(
                            (video_qualities.c.video_id == video_id)
                            & (video_qualities.c.quality.in_(qualities_to_delete))
                        )
                    )

                # Delete transcription records if needed
                if delete_transcription:
                    await database.execute(transcriptions.delete().where(transcriptions.c.video_id == video_id))

                # Clear the retranscode_metadata now that cleanup is done
                await database.execute(
                    transcoding_jobs.update()
                    .where(transcoding_jobs.c.id == job["id"])
                    .values(retranscode_metadata=None)
                )

            logger.info(
                "Retranscode cleanup completed for job %d, video %d: "
                "deleted %d qualities (%s), retranscode_all=%s, delete_transcription=%s",
                job["id"],
                video_id,
                len(qualities_to_delete),
                ", ".join(qualities_to_delete),
                retranscode_all,
                delete_transcription,
            )
        except Exception as e:
            # Log but don't fail the claim - worker can still proceed
            logger.error(
                "Failed to perform retranscode cleanup for job %d, video %d: %s. "
                "Metadata: retranscode_all=%s, qualities=%s",
                job["id"],
                video_id,
                e,
                retranscode_all,
                qualities_to_delete,
            )

    # Find source filename
    source_filename = None
    for ext in SUPPORTED_VIDEO_EXTENSIONS:
        candidate = UPLOADS_DIR / f"{job['video_id']}{ext}"
        if candidate.exists():
            source_filename = candidate.name
            break

    # Query existing qualities to skip during transcoding (for selective re-transcode)
    existing_quality_rows = await database.fetch_all(
        video_qualities.select().where(video_qualities.c.video_id == job["video_id"])
    )
    existing_qualities = [row["quality"] for row in existing_quality_rows] if existing_quality_rows else None

    return ClaimJobResponse(
        job_id=job["id"],
        video_id=job["video_id"],
        video_slug=job["slug"],
        video_duration=job["duration"],
        source_width=job["source_width"],
        source_height=job["source_height"],
        source_filename=source_filename,
        claim_expires_at=expires_at,
        existing_qualities=existing_qualities,
        message="Job claimed successfully",
    )


# =============================================================================
# Progress Updates
# =============================================================================


@app.post("/api/worker/{job_id}/progress", response_model=ProgressUpdateResponse)
@limiter.limit(RATE_LIMIT_WORKER_PROGRESS)
async def update_progress(
    request: Request,
    job_id: int,
    data: ProgressUpdateRequest,
    worker: dict = Depends(verify_worker_key),
):
    """Update job progress and extend claim."""
    # Verify worker owns this job
    job = await database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.id == job_id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["worker_id"] != worker["worker_id"]:
        raise HTTPException(status_code=403, detail="Not your job")

    # Check if claim has already expired
    now = datetime.now(timezone.utc)
    if job["claim_expires_at"]:
        # Normalize datetime to ensure timezone awareness (defensive programming)
        claim_expiry = job["claim_expires_at"]
        if claim_expiry.tzinfo is None:
            claim_expiry = claim_expiry.replace(tzinfo=timezone.utc)
        if claim_expiry < now:
            raise HTTPException(
                status_code=409,
                detail="Claim expired - job may have been reassigned",
            )

    # Extend claim on progress update
    new_expiry = now + timedelta(minutes=WORKER_CLAIM_DURATION_MINUTES)

    await database.execute(
        transcoding_jobs.update()
        .where(transcoding_jobs.c.id == job_id)
        .values(
            current_step=data.current_step,
            progress_percent=data.progress_percent,
            last_checkpoint=now,
            claim_expires_at=new_expiry,
        )
    )

    # Update quality progress if provided
    # Use upsert (INSERT ... ON CONFLICT) for PostgreSQL compatibility
    # The databases library returns None for UPDATE regardless of rows affected,
    # so UPDATE-then-INSERT doesn't work reliably.
    if data.quality_progress:
        db_url = str(database.url)
        is_postgresql = db_url.startswith("postgresql")

        for qp in data.quality_progress:
            if is_postgresql:
                # PostgreSQL upsert using ON CONFLICT
                await database.execute(
                    sa.text("""
                        INSERT INTO quality_progress (job_id, quality, status, progress_percent)
                        VALUES (:job_id, :quality, :status, :progress)
                        ON CONFLICT (job_id, quality) DO UPDATE
                        SET status = :status, progress_percent = :progress
                    """).bindparams(
                        job_id=job_id,
                        quality=qp.name,
                        status=qp.status,
                        progress=qp.progress,
                    )
                )
            else:
                # SQLite upsert using INSERT OR REPLACE
                await database.execute(
                    sa.text("""
                        INSERT OR REPLACE INTO quality_progress (job_id, quality, status, progress_percent)
                        VALUES (:job_id, :quality, :status, :progress)
                    """).bindparams(
                        job_id=job_id,
                        quality=qp.name,
                        status=qp.status,
                        progress=qp.progress,
                    )
                )

    # Update video metadata if provided (prevents data loss if worker crashes after probing)
    if data.duration is not None or data.source_width is not None or data.source_height is not None:
        video_updates = {}
        if data.duration is not None:
            video_updates["duration"] = data.duration
        if data.source_width is not None:
            video_updates["source_width"] = data.source_width
        if data.source_height is not None:
            video_updates["source_height"] = data.source_height

        await database.execute(videos.update().where(videos.c.id == job["video_id"]).values(**video_updates))

    # Publish progress to Redis pub/sub for real-time UI updates
    qualities_data = None
    if data.quality_progress:
        qualities_data = [
            {"name": qp.name, "status": qp.status, "progress": qp.progress} for qp in data.quality_progress
        ]

    await Publisher.publish_progress(
        video_id=job["video_id"],
        job_id=job_id,
        current_step=data.current_step,
        progress_percent=data.progress_percent,
        qualities=qualities_data,
        status="processing",
    )

    return ProgressUpdateResponse(status="ok", claim_expires_at=new_expiry)


# =============================================================================
# Job Completion
# =============================================================================


@app.post("/api/worker/{job_id}/complete", response_model=CompleteJobResponse)
@limiter.limit(RATE_LIMIT_WORKER_DEFAULT)
async def complete_job(
    request: Request,
    job_id: int,
    data: CompleteJobRequest,
    worker: dict = Depends(verify_worker_key),
):
    """Mark job as complete after HLS files uploaded.

    Idempotency (Issue #455):
    - If completion_token is provided and was already processed, returns early
    - If job is already completed, returns early
    - Metadata fields only update if not already set (idempotent on retry)
    """
    job = await database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.id == job_id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["worker_id"] != worker["worker_id"]:
        raise HTTPException(status_code=403, detail="Not your job")

    now = datetime.now(timezone.utc)

    # Issue #455: Idempotency check - if job already completed, return early
    if job["completed_at"] is not None:
        logger.info(f"Job {job_id} already completed, returning idempotent response")
        return CompleteJobResponse(status="ok", message="Job already completed")

    # Issue #455: Atomic token check-and-set using SETNX (set if not exists)
    # This prevents race conditions where two requests pass the check before either stores
    # Token key is scoped to job_id to prevent cross-job collisions
    token_acquired = False
    token_key = None  # Defined at outer scope for cleanup in exception handler
    if data.completion_token:
        redis = await get_redis()
        if redis:
            # Key format: vlog:completion_token:{job_id}:{token}
            token_key = f"{REDIS_KEY_PREFIX}completion_token:{job_id}:{data.completion_token}"
            try:
                # SETNX: Set only if key doesn't exist (atomic check-and-set)
                # Returns True if set succeeded (we own the token), False if key already exists
                token_acquired = await redis.set(token_key, "processing", ex=COMPLETION_TOKEN_TTL, nx=True)
                if not token_acquired:
                    logger.info(f"Job {job_id} completion token already in use, returning idempotent response")
                    return CompleteJobResponse(status="ok", message="Job already completed")
            except Exception as e:
                # Redis failure shouldn't block completion - log and continue
                # Fall back to DB-based idempotency (completed_at check)
                logger.warning(f"Failed to acquire completion token in Redis: {e}")

    # Check if claim has expired
    if job["claim_expires_at"]:
        claim_expiry = job["claim_expires_at"]
        if claim_expiry.tzinfo is None:
            claim_expiry = claim_expiry.replace(tzinfo=timezone.utc)
        if claim_expiry < now:
            raise HTTPException(
                status_code=409,
                detail="Claim expired - job may have been reassigned",
            )

    async def do_complete_transaction():
        """Execute the completion transaction - wrapped with retry logic."""
        async with database.transaction():
            # Save quality info
            for q in data.qualities:
                existing = await database.fetch_one(
                    video_qualities.select()
                    .where(video_qualities.c.video_id == job["video_id"])
                    .where(video_qualities.c.quality == q.name)
                )
                if not existing:
                    await database.execute(
                        video_qualities.insert().values(
                            video_id=job["video_id"],
                            quality=q.name,
                            width=q.width,
                            height=q.height,
                            bitrate=q.bitrate,
                        )
                    )

            # Update video metadata if provided
            # Issue #455: Only set fields if not already set (idempotent on retry)
            video_row = await database.fetch_one(videos.select().where(videos.c.id == job["video_id"]))
            video_updates = {"status": "ready"}
            if video_row:
                if video_row["published_at"] is None:
                    video_updates["published_at"] = now
                # Only update metadata if not already set (idempotent retry safety)
                if data.duration is not None and video_row["duration"] is None:
                    video_updates["duration"] = data.duration
                if data.source_width is not None and video_row["source_width"] is None:
                    video_updates["source_width"] = data.source_width
                if data.source_height is not None and video_row["source_height"] is None:
                    video_updates["source_height"] = data.source_height
                if data.streaming_format is not None and video_row["streaming_format"] is None:
                    video_updates["streaming_format"] = data.streaming_format
                if data.streaming_codec is not None and video_row["primary_codec"] is None:
                    video_updates["primary_codec"] = data.streaming_codec

            # Mark job complete
            await database.execute(
                transcoding_jobs.update()
                .where(transcoding_jobs.c.id == job_id)
                .values(
                    completed_at=now,
                    progress_percent=100,
                    current_step="finalize",
                    claimed_at=None,
                    claim_expires_at=None,
                )
            )

            # Mark video ready
            await database.execute(videos.update().where(videos.c.id == job["video_id"]).values(**video_updates))

            # Clear worker's current job
            await database.execute(workers.update().where(workers.c.id == worker["id"]).values(current_job_id=None))

    try:
        await execute_with_retry(do_complete_transaction)
    except DatabaseLockedError as e:
        # Clean up token so retry can succeed (Gafton review feedback)
        # Without this, the token stays in Redis for 15 min blocking retries
        if token_acquired and token_key:
            try:
                redis = await get_redis()
                if redis:
                    await redis.delete(token_key)
                    logger.info(f"Cleaned up completion token after transaction failure for job {job_id}")
            except Exception as cleanup_err:
                # Best effort cleanup - don't mask the original error
                logger.warning(f"Failed to clean up completion token: {cleanup_err}")
        raise HTTPException(
            status_code=503,
            detail="Database temporarily unavailable, please retry",
        ) from e

    # Issue #455: Update token status to "completed" after successful completion
    # The token was already set with SETNX before the transaction (status: "processing")
    # Now we update it to "completed" to indicate success
    if data.completion_token and token_acquired:
        redis = await get_redis()
        if redis:
            try:
                token_key = f"{REDIS_KEY_PREFIX}completion_token:{job_id}:{data.completion_token}"
                await redis.set(token_key, "completed", ex=COMPLETION_TOKEN_TTL)
            except Exception as e:
                # Redis failure shouldn't block - token was already set, DB is source of truth
                logger.warning(f"Failed to update completion token status in Redis: {e}")

    # Issue #207: Record job completion metrics (sanitize label to prevent injection)
    worker_label = sanitize_label(worker["worker_name"] or worker["worker_id"])
    WORKER_JOBS_COMPLETED_TOTAL.labels(worker_name=worker_label).inc()
    TRANSCODING_JOBS_TOTAL.labels(status="completed").inc()

    # Publish job completion to Redis pub/sub for real-time UI updates
    video = await database.fetch_one(videos.select().where(videos.c.id == job["video_id"]))
    worker_name = worker["worker_name"] or worker["worker_id"][:8]

    # Queue sprite sheet generation if enabled (Issue #413 Phase 7B)
    if SPRITE_SHEET_ENABLED and SPRITE_SHEET_AUTO_GENERATE:
        try:
            # Check if sprite job already exists for this video
            existing_sprite = await database.fetch_one(
                sa.text(
                    "SELECT id FROM sprite_queue WHERE video_id = :video_id AND status IN ('pending', 'processing')"
                ).bindparams(video_id=job["video_id"])
            )
            if not existing_sprite:
                await database.execute(
                    sprite_queue.insert().values(
                        video_id=job["video_id"],
                        priority="normal",
                        status="pending",
                        created_at=datetime.now(timezone.utc),
                    )
                )
                await database.execute(
                    videos.update()
                    .where(videos.c.id == job["video_id"])
                    .values(
                        sprite_sheet_status="pending",
                        sprite_sheet_error=None,
                    )
                )
                logger.info(f"Queued sprite sheet generation for video {job['video_id']}")
        except Exception as sprite_err:
            logger.warning(f"Failed to queue sprite generation: {sprite_err}")

    await Publisher.publish_job_completed(
        job_id=job_id,
        video_id=job["video_id"],
        video_slug=video["slug"] if video else "unknown",
        worker_id=worker["worker_id"],
        worker_name=worker_name,
        qualities=[
            {"name": q.name, "width": q.width, "height": q.height, "bitrate": q.bitrate} for q in data.qualities
        ],
    )

    return CompleteJobResponse(status="ok", message="Job completed successfully")


# =============================================================================
# Job Failure
# =============================================================================


@app.post("/api/worker/{job_id}/fail", response_model=FailJobResponse)
@limiter.limit(RATE_LIMIT_WORKER_DEFAULT)
async def fail_job(
    request: Request,
    job_id: int,
    data: FailJobRequest,
    worker: dict = Depends(verify_worker_key),
):
    """Report job failure."""
    job = await database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.id == job_id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["worker_id"] != worker["worker_id"]:
        raise HTTPException(status_code=403, detail="Not your job")

    # Check if claim has expired
    now = datetime.now(timezone.utc)
    if job["claim_expires_at"]:
        claim_expiry = job["claim_expires_at"]
        if claim_expiry.tzinfo is None:
            claim_expiry = claim_expiry.replace(tzinfo=timezone.utc)
        if claim_expiry < now:
            raise HTTPException(
                status_code=409,
                detail="Claim expired - job may have been reassigned",
            )

    will_retry = data.retry and job["attempt_number"] < job["max_attempts"]

    async def do_fail_transaction():
        """Execute the failure transaction - wrapped with retry logic."""
        async with database.transaction():
            if will_retry:
                # Reset for retry
                await database.execute(
                    transcoding_jobs.update()
                    .where(transcoding_jobs.c.id == job_id)
                    .values(
                        last_error=data.error_message[:500],
                        claimed_at=None,
                        claim_expires_at=None,
                        worker_id=None,
                        current_step=None,
                        attempt_number=job["attempt_number"] + 1,
                    )
                )
                # Clean up quality_progress records from previous attempt
                # to prevent stale progress data affecting the retry
                await database.execute(quality_progress.delete().where(quality_progress.c.job_id == job_id))
                await database.execute(videos.update().where(videos.c.id == job["video_id"]).values(status="pending"))
            else:
                # Final failure
                await database.execute(
                    transcoding_jobs.update()
                    .where(transcoding_jobs.c.id == job_id)
                    .values(
                        last_error=data.error_message[:500],
                        completed_at=now,
                        claimed_at=None,
                        claim_expires_at=None,
                    )
                )
                await database.execute(
                    videos.update()
                    .where(videos.c.id == job["video_id"])
                    .values(status="failed", error_message=data.error_message[:500])
                )

            # Clear worker's current job
            await database.execute(workers.update().where(workers.c.id == worker["id"]).values(current_job_id=None))

    try:
        await execute_with_retry(do_fail_transaction)
    except DatabaseLockedError as e:
        raise HTTPException(
            status_code=503,
            detail="Database temporarily unavailable, please retry",
        ) from e

    # Issue #207: Record job failure/retry metrics
    if will_retry:
        TRANSCODING_JOBS_TOTAL.labels(status="retried").inc()
    else:
        TRANSCODING_JOBS_TOTAL.labels(status="failed").inc()

    # Clean up source file after permanent failure (outside transaction)
    if not will_retry:
        from worker.transcoder import cleanup_source_file

        cleanup_source_file(job["video_id"])

    # Publish job failure to Redis pub/sub for real-time UI updates
    video = await database.fetch_one(videos.select().where(videos.c.id == job["video_id"]))
    worker_name = worker["worker_name"] or worker["worker_id"][:8]

    await Publisher.publish_job_failed(
        job_id=job_id,
        video_id=job["video_id"],
        video_slug=video["slug"] if video else "unknown",
        worker_id=worker["worker_id"],
        worker_name=worker_name,
        error=data.error_message[:200] if data.error_message else "Unknown error",
        will_retry=will_retry,
        attempt=job["attempt_number"] + (1 if will_retry else 0),
        max_attempts=job["max_attempts"],
    )

    return FailJobResponse(
        status="ok",
        will_retry=will_retry,
        attempt_number=job["attempt_number"] + (1 if will_retry else 0),
    )


# =============================================================================
# File Transfer
# =============================================================================


@app.get("/api/worker/source/{video_id}")
@limiter.limit(RATE_LIMIT_WORKER_DEFAULT)
async def download_source(
    request: Request,
    video_id: int,
    worker: dict = Depends(verify_worker_key),
):
    """Stream source file to worker."""
    # Verify worker has claimed this video's job
    job = await database.fetch_one(
        transcoding_jobs.select()
        .where(transcoding_jobs.c.video_id == video_id)
        .where(transcoding_jobs.c.worker_id == worker["worker_id"])
    )
    if not job:
        raise HTTPException(status_code=403, detail="Not your job or job not found")

    # Check if claim has expired
    now = datetime.now(timezone.utc)
    if job["claim_expires_at"]:
        claim_expiry = job["claim_expires_at"]
        if claim_expiry.tzinfo is None:
            claim_expiry = claim_expiry.replace(tzinfo=timezone.utc)
        if claim_expiry < now:
            raise HTTPException(
                status_code=409,
                detail="Claim expired - job may have been reassigned",
            )

    # Find source file
    source_file: Optional[Path] = None
    for ext in SUPPORTED_VIDEO_EXTENSIONS:
        candidate = UPLOADS_DIR / f"{video_id}{ext}"
        if candidate.exists():
            source_file = candidate
            break

    if not source_file:
        raise HTTPException(status_code=404, detail="Source file not found")

    return FileResponse(
        source_file,
        media_type="application/octet-stream",
        filename=source_file.name,
    )


@app.post("/api/worker/upload/{video_id}/quality/{quality_name}", response_model=StatusResponse)
@limiter.limit(RATE_LIMIT_WORKER_DEFAULT)
async def upload_quality(
    request: Request,
    video_id: int,
    quality_name: str,
    file: UploadFile = File(...),
    worker: dict = Depends(verify_worker_key),
):
    """
    Upload a single quality's HLS files (tar.gz of playlist + segments).

    Called after each quality finishes transcoding. This allows:
    - Incremental upload as qualities complete
    - Reduced disk space on worker (delete after upload)
    - Partial progress saved if worker crashes
    - Server can start serving partial content earlier

    Expected files in archive:
    - {quality_name}.m3u8 (quality playlist)
    - {quality_name}_XXXX.ts (segments)
    """
    job = await database.fetch_one(
        transcoding_jobs.select()
        .where(transcoding_jobs.c.video_id == video_id)
        .where(transcoding_jobs.c.worker_id == worker["worker_id"])
    )
    if not job:
        raise HTTPException(status_code=403, detail="Not your job or job not found")

    # Check if claim has expired
    now = datetime.now(timezone.utc)
    if job["claim_expires_at"]:
        claim_expiry = job["claim_expires_at"]
        if claim_expiry.tzinfo is None:
            claim_expiry = claim_expiry.replace(tzinfo=timezone.utc)
        if claim_expiry < now:
            raise HTTPException(
                status_code=409,
                detail="Claim expired - job may have been reassigned",
            )

    video = await database.fetch_one(videos.select().where(videos.c.id == video_id))
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    output_dir = VIDEOS_DIR / video["slug"]
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save uploaded tar.gz to temp file using streaming writes
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        with open(tmp_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)
    except HTTPException:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
        raise
    except Exception as e:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
        logger.exception(f"Failed to save quality upload for video {video_id}/{quality_name}: {e}")
        raise HTTPException(status_code=500, detail="Failed to save upload")

    try:
        # Run tar extraction in thread pool to avoid blocking event loop
        # This is critical: slow NAS I/O was blocking the entire API for minutes
        await extract_tar_async(
            tmp_path,
            output_dir,
            allowed_extensions=(".m3u8", ".ts", ".m4s", ".mp4", ".jpg", ".vtt"),
            max_files=MAX_HLS_ARCHIVE_FILES,
            max_size=MAX_HLS_ARCHIVE_SIZE,
            max_single_file=MAX_HLS_SINGLE_FILE_SIZE,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        tmp_path.unlink(missing_ok=True)

    # Update quality_progress to mark as uploaded
    await database.execute(
        quality_progress.update()
        .where(quality_progress.c.job_id == job["id"])
        .where(quality_progress.c.quality == quality_name)
        .values(status="uploaded")
    )

    logger.info(f"Quality {quality_name} uploaded for video {video['slug']}")
    return StatusResponse(status="ok", message=f"Quality {quality_name} uploaded successfully")


@app.post("/api/worker/upload/{video_id}/finalize", response_model=StatusResponse)
@limiter.limit(RATE_LIMIT_WORKER_DEFAULT)
async def upload_finalize(
    request: Request,
    video_id: int,
    file: UploadFile = File(...),
    worker: dict = Depends(verify_worker_key),
):
    """
    Upload final files after all qualities: master.m3u8, manifest.mpd, and thumbnail.jpg.

    Called after all quality uploads complete.
    """
    job = await database.fetch_one(
        transcoding_jobs.select()
        .where(transcoding_jobs.c.video_id == video_id)
        .where(transcoding_jobs.c.worker_id == worker["worker_id"])
    )
    if not job:
        raise HTTPException(status_code=403, detail="Not your job or job not found")

    # Check if claim has expired
    now = datetime.now(timezone.utc)
    if job["claim_expires_at"]:
        claim_expiry = job["claim_expires_at"]
        if claim_expiry.tzinfo is None:
            claim_expiry = claim_expiry.replace(tzinfo=timezone.utc)
        if claim_expiry < now:
            raise HTTPException(
                status_code=409,
                detail="Claim expired - job may have been reassigned",
            )

    video = await database.fetch_one(videos.select().where(videos.c.id == video_id))
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    output_dir = VIDEOS_DIR / video["slug"]
    output_dir.mkdir(parents=True, exist_ok=True)

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        with open(tmp_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)
    except HTTPException:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
        raise
    except Exception as e:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
        logger.exception(f"Failed to save finalize upload for video {video_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to save upload")

    try:
        # Run tar extraction in thread pool to avoid blocking event loop
        await extract_tar_async(
            tmp_path,
            output_dir,
            allowed_extensions=(".m3u8", ".mpd", ".jpg"),
            max_files=10,  # master.m3u8, manifest.mpd, and thumbnail.jpg
            max_size=MAX_HLS_SINGLE_FILE_SIZE,  # Small files
            max_single_file=MAX_HLS_SINGLE_FILE_SIZE,
            strict_filenames=("master.m3u8", "manifest.mpd", "thumbnail.jpg"),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        tmp_path.unlink(missing_ok=True)

    logger.info(f"Finalize files uploaded for video {video['slug']}")
    return StatusResponse(status="ok", message="Finalize files uploaded successfully")


@app.post("/api/worker/upload/{video_id}", response_model=StatusResponse)
@limiter.limit(RATE_LIMIT_WORKER_DEFAULT)
async def upload_hls(
    request: Request,
    video_id: int,
    file: UploadFile = File(...),
    worker: dict = Depends(verify_worker_key),
):
    """
    Upload HLS output files (tar.gz archive of video directory).

    Worker packages: master.m3u8, quality playlists, .ts segments, thumbnail.jpg

    DEPRECATED: Use /upload/{video_id}/quality/{name} for incremental uploads.
    This endpoint remains for backwards compatibility.
    """
    job = await database.fetch_one(
        transcoding_jobs.select()
        .where(transcoding_jobs.c.video_id == video_id)
        .where(transcoding_jobs.c.worker_id == worker["worker_id"])
    )
    if not job:
        raise HTTPException(status_code=403, detail="Not your job or job not found")

    video = await database.fetch_one(videos.select().where(videos.c.id == video_id))
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    output_dir = VIDEOS_DIR / video["slug"]
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save uploaded tar.gz to temp file using streaming writes to avoid memory exhaustion
    tmp_path = None
    try:
        # Create temp file path
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        # Stream file contents to disk in chunks
        with open(tmp_path, "wb") as f:
            while chunk := await file.read(1024 * 1024):  # 1MB chunks
                f.write(chunk)
    except HTTPException:
        # Cleanup temp file on error
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
        raise
    except Exception as e:
        # Cleanup temp file on error
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
        logger.exception(f"Failed to save upload for video {video_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to save upload")

    try:
        # Run tar extraction in thread pool to avoid blocking event loop
        # This is critical: slow NAS I/O was blocking the entire API for minutes
        await extract_tar_async(
            tmp_path,
            output_dir,
            allowed_extensions=(".m3u8", ".ts", ".m4s", ".mp4", ".jpg", ".vtt"),
            max_files=MAX_HLS_ARCHIVE_FILES,
            max_size=MAX_HLS_ARCHIVE_SIZE,
            max_single_file=MAX_HLS_SINGLE_FILE_SIZE,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        tmp_path.unlink(missing_ok=True)

    return StatusResponse(status="ok", message="HLS files uploaded successfully")


# =============================================================================
# Streaming Segment Upload (Issue #478)
# =============================================================================


@app.post(
    "/api/worker/upload/{video_id}/segment/{quality}/{filename}",
    response_model=SegmentUploadResponse,
)
@limiter.limit(RATE_LIMIT_WORKER_DEFAULT)
async def upload_segment(
    request: Request,
    video_id: int,
    quality: str,
    filename: str,
    x_content_sha256: str = Header(..., alias="X-Content-SHA256"),
    worker: dict = Depends(verify_worker_key),
):
    """
    Upload a single segment file during streaming transcoding (Issue #478).

    This endpoint allows workers to upload segments as FFmpeg writes them,
    eliminating the blocking tar.gz creation that causes heartbeat failures.

    Security features (Bruce's recommendations):
    - Quality validated against enum (no regex)
    - Filename validated for path traversal, null bytes, etc.
    - Magic byte validation for file type
    - Atomic write with fsync for durability
    - Checksum verification before writing

    Reliability features (Margo's recommendations):
    - fsync() before returning 200
    - Idempotent: same segment uploaded twice = no error
    - Returns checksum for verification

    Args:
        video_id: The video ID
        quality: Quality name (e.g., "1080p", "720p")
        filename: Segment filename (e.g., "seg_0001.m4s", "init.mp4")
        x_content_sha256: SHA256 checksum of the content (hex string)

    Returns:
        SegmentUploadResponse with write status and verification
    """
    # Validate quality using enum (Bruce's recommendation - no regex)
    try:
        SegmentQuality.validate(quality)
    except ValueError:
        # Generic error message (Bruce's recommendation)
        raise HTTPException(status_code=400, detail="Invalid request")

    # Validate filename for security (Bruce's recommendations)
    if not validate_segment_filename(filename):
        logger.warning(f"Invalid segment filename rejected: {filename!r}")
        raise HTTPException(status_code=400, detail="Invalid request")

    # Verify worker owns this job with DB lock (Bruce's recommendation)
    # FOR UPDATE prevents race conditions during claim verification
    db_url = str(database.url)
    is_postgresql = db_url.startswith("postgresql")

    if is_postgresql:
        job = await database.fetch_one(
            sa.text("""
                SELECT tj.id, tj.claim_expires_at, v.slug
                FROM transcoding_jobs tj
                JOIN videos v ON tj.video_id = v.id
                WHERE tj.video_id = :video_id
                  AND tj.worker_id = :worker_id
                FOR UPDATE OF tj
            """).bindparams(video_id=video_id, worker_id=worker["worker_id"])
        )
    else:
        job = await database.fetch_one(
            sa.text("""
                SELECT tj.id, tj.claim_expires_at, v.slug
                FROM transcoding_jobs tj
                JOIN videos v ON tj.video_id = v.id
                WHERE tj.video_id = :video_id
                  AND tj.worker_id = :worker_id
            """).bindparams(video_id=video_id, worker_id=worker["worker_id"])
        )

    if not job:
        raise HTTPException(status_code=403, detail="Not your job")

    # Check if claim has expired
    now = datetime.now(timezone.utc)
    if job["claim_expires_at"]:
        claim_expiry = job["claim_expires_at"]
        if claim_expiry.tzinfo is None:
            claim_expiry = claim_expiry.replace(tzinfo=timezone.utc)
        if claim_expiry < now:
            raise HTTPException(
                status_code=409,
                detail="Claim expired",
            )

    # Build destination path with canonical resolution (Bruce's recommendation)
    video_slug = job["slug"]
    output_dir = VIDEOS_DIR / video_slug / quality
    dest_path = (output_dir / filename).resolve()

    # Verify path is within allowed directory (prevent path traversal)
    try:
        dest_path.relative_to(VIDEOS_DIR.resolve())
    except ValueError:
        logger.warning(f"Path traversal attempt blocked: {dest_path}")
        raise HTTPException(status_code=400, detail="Invalid request")

    # Stream the raw body with size limit (code review fix - prevents memory exhaustion)
    # Don't use request.body() which loads everything into memory before size check
    chunks = []
    total_size = 0
    async for chunk in request.stream():
        total_size += len(chunk)
        if total_size > MAX_HLS_SINGLE_FILE_SIZE:
            raise HTTPException(status_code=400, detail="File too large")
        chunks.append(chunk)
    data = b"".join(chunks)

    # Validate magic bytes (Bruce's recommendation)
    if not validate_segment_magic_bytes(data, filename):
        logger.warning(f"Magic byte validation failed for {filename}")
        raise HTTPException(status_code=400, detail="Invalid file format")

    # Parse checksum (remove 'sha256:' prefix if present)
    checksum = x_content_sha256
    if checksum.startswith("sha256:"):
        checksum = checksum[7:]

    # Check for idempotency (Margo's recommendation)
    # If file already exists with same content, return success
    # Use thread pool to avoid blocking event loop on NAS (code review fix)
    loop = asyncio.get_event_loop()
    dest_exists = await loop.run_in_executor(_io_executor, dest_path.exists)
    old_file_size = 0  # Track old size for storage metric adjustment on overwrite
    if dest_exists:
        try:
            # Add timeout to handle NFS hangs (code review fix)
            # Get both checksum and file size to properly track storage on overwrite
            def get_existing_file_info():
                file_bytes = dest_path.read_bytes()
                return hashlib.sha256(file_bytes).hexdigest(), len(file_bytes)

            existing_checksum, old_file_size = await asyncio.wait_for(
                loop.run_in_executor(_io_executor, get_existing_file_info),
                timeout=30.0,
            )
        except asyncio.TimeoutError:
            logger.warning(f"Timeout reading existing segment {filename}, treating as non-existent")
            dest_exists = False
            existing_checksum = None
            old_file_size = 0
        if dest_exists and existing_checksum == checksum:
            logger.debug(f"Segment {filename} already exists with matching checksum")
            return SegmentUploadResponse(
                status="ok",
                written=False,  # Not written, already existed
                bytes_written=len(data),
                checksum_verified=True,
            )
        else:
            # File exists with different content - overwrite
            # old_file_size is already captured above for storage metric adjustment
            logger.warning(f"Segment {filename} exists with different checksum, overwriting")

    # Atomic write with fsync (Margo's durability requirement)
    try:
        written, bytes_written, checksum_verified = await write_segment_atomic(data, dest_path, checksum)
    except Exception as e:
        logger.exception(f"Failed to write segment {filename}: {e}")
        raise HTTPException(status_code=500, detail="Write failed")

    if not written:
        raise HTTPException(status_code=400, detail="Checksum verification failed")

    # Issue #207: Track storage bytes for new/overwritten segments
    # If overwriting, adjust for the old file size to maintain accuracy
    if old_file_size > 0:
        # Overwrite case: net change = new size - old size
        net_change = bytes_written - old_file_size
        if net_change > 0:
            STORAGE_VIDEOS_BYTES.inc(net_change)
        elif net_change < 0:
            STORAGE_VIDEOS_BYTES.dec(abs(net_change))
        # If net_change == 0, no adjustment needed
    else:
        # New file case
        STORAGE_VIDEOS_BYTES.inc(bytes_written)

    # Extend claim on successful upload (each upload keeps claim alive)
    new_expiry = now + timedelta(minutes=WORKER_CLAIM_DURATION_MINUTES)
    await database.execute(
        transcoding_jobs.update().where(transcoding_jobs.c.id == job["id"]).values(claim_expires_at=new_expiry)
    )

    logger.debug(f"Segment {quality}/{filename} uploaded for video {video_slug}")
    return SegmentUploadResponse(
        status="ok",
        written=True,
        bytes_written=bytes_written,
        checksum_verified=checksum_verified,
    )


@app.get(
    "/api/worker/upload/{video_id}/segments/status",
    response_model=SegmentStatusResponse,
)
@limiter.limit(RATE_LIMIT_WORKER_DEFAULT)
async def get_segments_status(
    request: Request,
    video_id: int,
    quality: str,
    worker: dict = Depends(verify_worker_key),
):
    """
    Get status of uploaded segments for resume support (Issue #478).

    Returns list of segment files already received for a quality,
    allowing workers to resume uploads after restart.

    Args:
        video_id: The video ID
        quality: Quality name to check (query parameter)

    Returns:
        SegmentStatusResponse with received segments and total size
    """
    # Validate quality
    try:
        SegmentQuality.validate(quality)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid quality")

    # Verify worker owns this job
    job = await database.fetch_one(
        transcoding_jobs.select()
        .where(transcoding_jobs.c.video_id == video_id)
        .where(transcoding_jobs.c.worker_id == worker["worker_id"])
    )
    if not job:
        raise HTTPException(status_code=403, detail="Not your job")

    # Check if claim has expired (code review fix - consistency with other endpoints)
    now = datetime.now(timezone.utc)
    if job["claim_expires_at"]:
        claim_expiry = job["claim_expires_at"]
        if claim_expiry.tzinfo is None:
            claim_expiry = claim_expiry.replace(tzinfo=timezone.utc)
        if claim_expiry < now:
            raise HTTPException(status_code=409, detail="Claim expired")

    # Get video slug
    video = await database.fetch_one(videos.select().where(videos.c.id == video_id))
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Build and validate path (code review fix - defense in depth)
    quality_dir = (VIDEOS_DIR / video["slug"] / quality).resolve()

    # Verify path is within allowed directory (prevent path traversal)
    try:
        quality_dir.relative_to(VIDEOS_DIR.resolve())
    except ValueError:
        logger.warning(f"Path traversal attempt blocked in get_segments_status: {quality_dir}")
        raise HTTPException(status_code=400, detail="Invalid request")

    # Scan quality directory for segments using thread pool (code review fix)
    def scan_segments():
        segments = []
        size = 0
        if quality_dir.exists():
            for f in quality_dir.iterdir():
                if f.is_file() and f.suffix in (".m4s", ".ts", ".mp4", ".m3u8"):
                    segments.append(f.name)
                    size += f.stat().st_size
        return segments, size

    loop = asyncio.get_event_loop()
    received_segments, total_size = await loop.run_in_executor(_io_executor, scan_segments)

    return SegmentStatusResponse(
        quality=quality,
        received_segments=sorted(received_segments),
        total_size_bytes=total_size,
    )


@app.post(
    "/api/worker/upload/{video_id}/segment/finalize",
    response_model=SegmentFinalizeResponse,
)
@limiter.limit(RATE_LIMIT_WORKER_DEFAULT)
async def finalize_segment_upload(
    request: Request,
    video_id: int,
    data: SegmentFinalizeRequest,
    worker: dict = Depends(verify_worker_key),
):
    """
    Finalize a quality's segment upload (Issue #478, Ada's recommendation).

    Called after all segments for a quality are uploaded.
    Server verifies segment count matches expected before marking complete.

    Args:
        video_id: The video ID
        data: Finalize request with quality, segment_count, and optional manifest_checksum

    Returns:
        SegmentFinalizeResponse indicating completion status and any missing segments
    """
    quality = data.quality

    # Verify worker owns this job with claim check
    job = await database.fetch_one(
        transcoding_jobs.select()
        .where(transcoding_jobs.c.video_id == video_id)
        .where(transcoding_jobs.c.worker_id == worker["worker_id"])
    )
    if not job:
        raise HTTPException(status_code=403, detail="Not your job")

    # Check if claim has expired
    now = datetime.now(timezone.utc)
    if job["claim_expires_at"]:
        claim_expiry = job["claim_expires_at"]
        if claim_expiry.tzinfo is None:
            claim_expiry = claim_expiry.replace(tzinfo=timezone.utc)
        if claim_expiry < now:
            raise HTTPException(status_code=409, detail="Claim expired")

    # Get video slug
    video = await database.fetch_one(videos.select().where(videos.c.id == video_id))
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Build and validate path (code review fix - defense in depth)
    quality_dir = (VIDEOS_DIR / video["slug"] / quality).resolve()

    # Verify path is within allowed directory (prevent path traversal)
    try:
        quality_dir.relative_to(VIDEOS_DIR.resolve())
    except ValueError:
        logger.warning(f"Path traversal attempt blocked in finalize: {quality_dir}")
        raise HTTPException(status_code=400, detail="Invalid request")

    # Count segments and verify manifest using thread pool (code review fix)
    def check_segments_and_manifest():
        if not quality_dir.exists():
            return None, None, "directory_not_found"

        # Count actual segment files (init.mp4 + *.m4s for CMAF, *.ts for HLS)
        segment_files = []
        for f in quality_dir.iterdir():
            if f.is_file() and f.suffix in (".m4s", ".ts", ".mp4"):
                segment_files.append(f.name)

        # Verify manifest checksum if provided
        manifest_checksum = None
        if data.manifest_checksum:
            # Try CMAF naming first (more common with this feature), then quality-based
            manifest_path = quality_dir / "stream.m3u8"
            if not manifest_path.exists():
                manifest_path = quality_dir / f"{quality}.m3u8"

            if manifest_path.exists():
                manifest_checksum = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

        return segment_files, manifest_checksum, None

    loop = asyncio.get_event_loop()
    segment_files, actual_manifest_checksum, error = await loop.run_in_executor(
        _io_executor, check_segments_and_manifest
    )

    if error == "directory_not_found":
        return SegmentFinalizeResponse(
            status="incomplete",
            complete=False,
            missing_segments=["<quality directory not found>"],
        )

    actual_count = len(segment_files)
    expected_count = data.segment_count

    if actual_count < expected_count:
        return SegmentFinalizeResponse(
            status="incomplete",
            complete=False,
            missing_segments=[f"Expected {expected_count} segments, found {actual_count}"],
        )

    # Verify manifest checksum if provided
    if data.manifest_checksum and actual_manifest_checksum:
        expected_checksum = data.manifest_checksum.removeprefix("sha256:")
        if actual_manifest_checksum != expected_checksum:
            return SegmentFinalizeResponse(
                status="incomplete",
                complete=False,
                missing_segments=["Manifest checksum mismatch"],
            )

    # Update quality_progress and extend claim in a transaction (code review fix)
    db_url = str(database.url)
    is_postgresql = db_url.startswith("postgresql")

    async with database.transaction():
        if is_postgresql:
            await database.execute(
                sa.text("""
                    INSERT INTO quality_progress (job_id, quality, status, progress_percent)
                    VALUES (:job_id, :quality, 'uploaded', 100)
                    ON CONFLICT (job_id, quality) DO UPDATE
                    SET status = 'uploaded', progress_percent = 100
                """).bindparams(job_id=job["id"], quality=quality)
            )
        else:
            await database.execute(
                sa.text("""
                    INSERT OR REPLACE INTO quality_progress (job_id, quality, status, progress_percent)
                    VALUES (:job_id, :quality, 'uploaded', 100)
                """).bindparams(job_id=job["id"], quality=quality)
            )

        # Extend claim
        new_expiry = now + timedelta(minutes=WORKER_CLAIM_DURATION_MINUTES)
        await database.execute(
            transcoding_jobs.update().where(transcoding_jobs.c.id == job["id"]).values(claim_expires_at=new_expiry)
        )

    logger.info(f"Quality {quality} finalized for video {video['slug']} ({actual_count} segments)")
    return SegmentFinalizeResponse(
        status="ok",
        complete=True,
        missing_segments=[],
    )


# =============================================================================
# Worker Management (for admin/CLI)
# =============================================================================


@app.get("/api/workers", response_model=WorkerListResponse)
@limiter.limit(RATE_LIMIT_WORKER_DEFAULT)
async def list_workers(
    request: Request,
    _admin: None = Depends(verify_admin_secret),
):
    """
    List all registered workers with their status.

    Requires X-Admin-Secret header with VLOG_WORKER_ADMIN_SECRET value.
    """
    now = datetime.now(timezone.utc)
    offline_threshold = now - timedelta(minutes=WORKER_OFFLINE_THRESHOLD_MINUTES)

    # Get all workers
    rows = await database.fetch_all(workers.select().order_by(workers.c.last_heartbeat.desc()))

    worker_list = []
    active_count = 0
    offline_count = 0

    for row in rows:
        # Determine status
        status = row["status"]
        if status == "active" and row["last_heartbeat"]:
            # Normalize datetime to ensure timezone awareness (defensive programming)
            last_hb = ensure_utc(row["last_heartbeat"])
            if last_hb < offline_threshold:
                status = "offline"
                offline_count += 1
            else:
                active_count += 1
        elif status == "disabled":
            pass  # Keep as disabled
        else:
            offline_count += 1

        # Get current video slug if working
        current_video_slug = None
        if row["current_job_id"]:
            job = await database.fetch_one(
                sa.select(videos.c.slug)
                .select_from(transcoding_jobs.join(videos))
                .where(transcoding_jobs.c.id == row["current_job_id"])
            )
            if job:
                current_video_slug = job["slug"]

        worker_list.append(
            WorkerStatusResponse(
                id=row["id"],
                worker_id=row["worker_id"],
                worker_name=row["worker_name"],
                worker_type=row["worker_type"],
                status=status,
                registered_at=row["registered_at"],
                last_heartbeat=row["last_heartbeat"],
                current_job_id=row["current_job_id"],
                current_video_slug=current_video_slug,
                capabilities=json.loads(row["capabilities"]) if row["capabilities"] else None,
                metadata=json.loads(row["metadata"]) if row["metadata"] else None,
            )
        )

    return WorkerListResponse(
        workers=worker_list,
        total_count=len(worker_list),
        active_count=active_count,
        offline_count=offline_count,
    )


@app.post("/api/workers/{worker_id}/revoke", response_model=StatusResponse)
@limiter.limit(RATE_LIMIT_WORKER_REGISTER)
async def revoke_worker(
    request: Request,
    worker_id: str,
    _admin: None = Depends(verify_admin_secret),
):
    """
    Revoke a worker's API keys.

    Requires X-Admin-Secret header with VLOG_WORKER_ADMIN_SECRET value.
    """
    # Find worker by UUID
    worker = await database.fetch_one(workers.select().where(workers.c.worker_id == worker_id))
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found")

    now = datetime.now(timezone.utc)

    # Revoke all API keys for this worker
    await database.execute(
        worker_api_keys.update()
        .where(worker_api_keys.c.worker_id == worker["id"])
        .where(worker_api_keys.c.revoked_at.is_(None))
        .values(revoked_at=now)
    )

    # Mark worker as disabled
    await database.execute(workers.update().where(workers.c.id == worker["id"]).values(status="disabled"))

    return StatusResponse(status="ok", message=f"Worker {worker_id} has been revoked")


@app.get("/api/health")
@limiter.limit(RATE_LIMIT_WORKER_DEFAULT)
async def health_check(request: Request):
    """
    Health check endpoint for kubernetes probes.

    Returns detailed status of database and storage health.
    Returns 503 if any critical component is unhealthy.
    """
    result = await check_health()
    storage_status = get_storage_status()

    return JSONResponse(
        status_code=result["status_code"],
        content={
            "status": "healthy" if result["healthy"] else "unhealthy",
            "checks": result["checks"],
            "storage": {
                "healthy": storage_status["healthy"],
                "last_check": storage_status["last_check"],
                "error": storage_status["last_error"],
            },
        },
    )


@app.get("/metrics")
@limiter.limit("60/minute")
async def metrics_endpoint(request: Request):
    """
    Prometheus metrics endpoint.

    Returns metrics in Prometheus text format for scraping.

    Authentication behavior is controlled by settings:
    - metrics.enabled: If false, returns 404 (default: true)
    - metrics.auth_required: If true, requires X-Admin-Secret header (default: false)

    Security note: For production deployments, consider either:
    1. Setting metrics.auth_required=true and using X-Admin-Secret header
    2. Network-level isolation (only allow Prometheus server to access /metrics)

    Rate limited to 60/minute to prevent brute-force attacks on authentication.

    Related: Issue #436
    """
    # Check if metrics endpoint is enabled
    metrics_enabled = await get_db_setting("metrics.enabled", True)
    if not metrics_enabled:
        raise HTTPException(status_code=404, detail="Metrics endpoint disabled")

    # Check if authentication is required
    auth_required = await get_db_setting("metrics.auth_required", False)
    if auth_required:
        # Validate X-Admin-Secret header
        admin_secret = request.headers.get("X-Admin-Secret", "")
        if not WORKER_ADMIN_SECRET:
            # No secret configured but auth required - deny access
            security_logger.warning(
                "Metrics auth required but VLOG_WORKER_ADMIN_SECRET not configured",
                extra={"event": "metrics_auth_misconfigured", "path": "/metrics"},
            )
            raise HTTPException(status_code=500, detail="Metrics authentication misconfigured")

        if not admin_secret or not hmac.compare_digest(admin_secret, WORKER_ADMIN_SECRET):
            security_logger.warning(
                "Metrics endpoint auth failed",
                extra={
                    "event": "metrics_auth_failure",
                    "path": "/metrics",
                    "client_ip": get_real_ip(request),
                },
            )
            raise HTTPException(status_code=403, detail="Authentication required for metrics")

    return Response(content=get_metrics(), media_type="text/plain; charset=utf-8")


# =============================================================================
# Re-encode Job Endpoints
# =============================================================================


@app.post("/api/reencode/claim")
@limiter.limit(RATE_LIMIT_WORKER_DEFAULT)
async def claim_reencode_job(
    request: Request,
    worker: dict = Depends(verify_worker_key),
):
    """
    Claim the next available re-encode job.

    Re-encode jobs convert existing HLS/TS videos to CMAF format.
    Only available when no regular transcoding jobs are pending.
    """
    # Find next pending re-encode job (priority order: high > normal > low)
    query = sa.text("""
        SELECT rq.*, v.slug
        FROM reencode_queue rq
        JOIN videos v ON v.id = rq.video_id
        WHERE rq.status = 'pending'
        ORDER BY
            CASE rq.priority
                WHEN 'high' THEN 1
                WHEN 'normal' THEN 2
                WHEN 'low' THEN 3
            END,
            rq.created_at
        LIMIT 1
        FOR UPDATE SKIP LOCKED
    """)

    job = await database.fetch_one(query)
    if not job:
        return {"job": None, "message": "No re-encode jobs available"}

    # Claim the job
    await database.execute(
        reencode_queue.update()
        .where(reencode_queue.c.id == job["id"])
        .values(
            status="in_progress",
            started_at=datetime.now(timezone.utc),
            worker_id=worker["worker_id"],
        )
    )

    return {
        "job": {
            "id": job["id"],
            "video_id": job["video_id"],
            "slug": job["slug"],
            "target_format": job["target_format"],
            "target_codec": job["target_codec"],
            "priority": job["priority"],
            "retry_count": job["retry_count"],
        }
    }


@app.get("/api/reencode/{job_id}/download")
@limiter.limit(RATE_LIMIT_WORKER_DEFAULT)
async def download_reencode_source(
    request: Request,
    job_id: int,
    worker: dict = Depends(verify_worker_key),
):
    """
    Download existing video files for re-encoding as tar.gz.

    Streams the highest quality available (source file or HLS segments).
    """
    import tarfile
    import tempfile

    # Verify worker owns this job
    job = await database.fetch_one(
        reencode_queue.select()
        .where(reencode_queue.c.id == job_id)
        .where(reencode_queue.c.worker_id == worker["worker_id"])
        .where(reencode_queue.c.status == "in_progress")
    )
    if not job:
        raise HTTPException(status_code=403, detail="Not your job or job not found")

    # Get video info
    video = await database.fetch_one(videos.select().where(videos.c.id == job["video_id"]))
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    video_dir = VIDEOS_DIR / video["slug"]
    if not video_dir.exists():
        raise HTTPException(status_code=404, detail="Video directory not found")

    # Find source to package - prefer source file, fall back to highest quality
    source_file = None
    for ext in [".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"]:
        candidate = video_dir / f"source{ext}"
        if candidate.exists():
            source_file = candidate
            break

    # Create tar.gz of source or segments
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        with tarfile.open(tmp_path, "w:gz") as tar:
            if source_file:
                # Package source file
                tar.add(source_file, arcname=source_file.name)
            else:
                # Package highest quality segments
                quality_dirs = ["2160p", "1440p", "1080p", "720p", "480p", "360p"]
                for quality in quality_dirs:
                    quality_dir = video_dir / quality
                    if quality_dir.exists():
                        # Add all files from this quality directory
                        for f in quality_dir.iterdir():
                            tar.add(f, arcname=f"{quality}/{f.name}")
                        break  # Only include highest quality

        return FileResponse(
            tmp_path,
            media_type="application/gzip",
            filename=f"reencode_{job_id}.tar.gz",
            background=BackgroundTask(lambda: tmp_path.unlink(missing_ok=True)),
        )
    except Exception as e:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Failed to package files: {e}")


@app.post("/api/reencode/{job_id}/upload")
@limiter.limit(RATE_LIMIT_WORKER_DEFAULT)
async def upload_reencode_result(
    request: Request,
    job_id: int,
    file: UploadFile = File(...),
    worker: dict = Depends(verify_worker_key),
):
    """
    Upload re-encoded video files as tar.gz.

    Performs atomic swap of old files with new ones.
    """
    import tempfile

    # Verify worker owns this job
    job = await database.fetch_one(
        reencode_queue.select()
        .where(reencode_queue.c.id == job_id)
        .where(reencode_queue.c.worker_id == worker["worker_id"])
        .where(reencode_queue.c.status == "in_progress")
    )
    if not job:
        raise HTTPException(status_code=403, detail="Not your job or job not found")

    # Get video info
    video = await database.fetch_one(videos.select().where(videos.c.id == job["video_id"]))
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    video_dir = VIDEOS_DIR / video["slug"]
    backup_dir = VIDEOS_DIR / f"{video['slug']}_backup_{job_id}"
    temp_dir = VIDEOS_DIR / f"{video['slug']}_new_{job_id}"

    try:
        # Save uploaded file
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            content = await file.read()
            tmp.write(content)

        # Extract to temp directory using secure async extraction
        # This validates file types, prevents path traversal, and handles symlinks
        temp_dir.mkdir(parents=True, exist_ok=True)
        try:
            await extract_tar_async(
                tmp_path,
                temp_dir,
                allowed_extensions=(".m3u8", ".ts", ".m4s", ".mp4", ".mpd", ".jpg", ".png", ".vtt"),
                max_files=MAX_HLS_ARCHIVE_FILES,
                max_size=MAX_HLS_ARCHIVE_SIZE,
                max_single_file=MAX_HLS_SINGLE_FILE_SIZE,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        finally:
            tmp_path.unlink(missing_ok=True)

        # Atomic swap
        if video_dir.exists():
            shutil.move(str(video_dir), str(backup_dir))
        shutil.move(str(temp_dir), str(video_dir))

        # Update video record
        await database.execute(
            videos.update()
            .where(videos.c.id == job["video_id"])
            .values(
                streaming_format=job["target_format"],
                primary_codec=job["target_codec"],
            )
        )

        # Mark job complete
        await database.execute(
            reencode_queue.update()
            .where(reencode_queue.c.id == job_id)
            .values(
                status="completed",
                completed_at=datetime.now(timezone.utc),
            )
        )

        # Clean up backup
        if backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)

        return {"status": "success", "message": "Re-encode upload complete"}

    except Exception as e:
        # Restore backup on failure
        if backup_dir.exists() and not video_dir.exists():
            shutil.move(str(backup_dir), str(video_dir))
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)

        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")


@app.patch("/api/reencode/{job_id}")
@limiter.limit(RATE_LIMIT_WORKER_DEFAULT)
async def update_reencode_job(
    request: Request,
    job_id: int,
    worker: dict = Depends(verify_worker_key),
):
    """Update re-encode job status."""
    data = await request.json()
    status = data.get("status")
    error_message = data.get("error_message")
    retry_count = data.get("retry_count")

    # Verify worker owns this job (or job is being set back to pending for retry)
    job = await database.fetch_one(reencode_queue.select().where(reencode_queue.c.id == job_id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job["worker_id"] != worker["worker_id"] and status != "pending":
        raise HTTPException(status_code=403, detail="Not your job")

    update_values = {}
    if status:
        update_values["status"] = status
    if error_message:
        update_values["error_message"] = error_message
    if retry_count is not None:
        update_values["retry_count"] = retry_count
    if status == "completed":
        update_values["completed_at"] = datetime.now(timezone.utc)
    if status == "pending":
        # Reset for retry
        update_values["worker_id"] = None
        update_values["started_at"] = None

    await database.execute(reencode_queue.update().where(reencode_queue.c.id == job_id).values(**update_values))

    return {"status": "success"}


# =============================================================================
# Job Completion Verification (Issue #461)
# =============================================================================


@app.get("/api/worker/{job_id}/verify-complete")
@limiter.limit(RATE_LIMIT_WORKER_DEFAULT)
async def verify_job_completion(
    request: Request,
    job_id: int,
    worker: dict = Depends(verify_worker_key),
):
    """
    Verify that job completion was properly recorded and files are present.

    Workers call this before cleaning up their local work directory to ensure
    the server has all the files and the completion was recorded in the database.

    This prevents data loss in edge cases where:
    - HTTP 200 was returned but database write failed
    - Files were uploaded but finalization failed
    - Server crashed during completion processing

    Returns:
        - all_files_present: True if all expected files exist on disk
        - video_status: Current status in database
        - job_status: Current job status (completed, processing, etc.)
        - qualities_present: List of quality directories found
        - missing_files: List of any expected but missing files
    """
    # Get the job and associated video
    job = await fetch_one_with_retry(transcoding_jobs.select().where(transcoding_jobs.c.id == job_id))

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Verify this worker owns the job (or owned it when it was completed)
    # Use direct access since worker_id should always be present (may be None)
    if job["worker_id"] != worker["worker_id"]:
        raise HTTPException(status_code=403, detail="Not your job")

    video = await fetch_one_with_retry(videos.select().where(videos.c.id == job["video_id"]))

    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    video_dir = VIDEOS_DIR / video["slug"]
    result = {
        "job_id": job_id,
        "video_id": video["id"],
        "video_slug": video["slug"],
        "video_status": video["status"],
        "job_completed": job.get("completed_at") is not None,
        "all_files_present": False,
        "qualities_present": [],
        "missing_files": [],
    }

    # If video directory doesn't exist, files weren't uploaded
    if not video_dir.exists():
        result["missing_files"].append("video_directory")
        return result

    # Check for master playlist
    master_playlist = video_dir / "master.m3u8"
    if not master_playlist.exists():
        result["missing_files"].append("master.m3u8")

    # Check for DASH manifest (for CMAF videos)
    streaming_format = video.get("streaming_format", "hls_ts")
    if streaming_format == "cmaf":
        dash_manifest = video_dir / "manifest.mpd"
        if not dash_manifest.exists():
            result["missing_files"].append("manifest.mpd")

    # Check for thumbnail
    thumbnail = video_dir / "thumbnail.jpg"
    if not thumbnail.exists():
        result["missing_files"].append("thumbnail.jpg")

    # Get quality directories/files (uses config constant for consistency)
    for quality in QUALITY_NAMES:
        # Check CMAF style (subdirectory with init.mp4 and segments)
        quality_dir = video_dir / quality
        if quality_dir.is_dir():
            init_file = quality_dir / "init.mp4"
            if init_file.exists():
                result["qualities_present"].append(quality)
                continue

        # Check HLS/TS style (quality.m3u8 + segments)
        quality_playlist = video_dir / f"{quality}.m3u8"
        if quality_playlist.exists():
            result["qualities_present"].append(quality)

    # Check against qualities recorded in database
    quality_rows = await fetch_all_with_retry(video_qualities.select().where(video_qualities.c.video_id == video["id"]))
    expected_qualities = [q["quality"] for q in quality_rows]

    for expected in expected_qualities:
        if expected not in result["qualities_present"]:
            result["missing_files"].append(f"quality:{expected}")

    # Determine if all files are present
    result["all_files_present"] = (
        len(result["missing_files"]) == 0
        and len(result["qualities_present"]) > 0
        and video["status"] == "ready"
        and job.get("completed_at") is not None
    )

    logger.debug(
        f"Job {job_id} verification: all_files_present={result['all_files_present']}, "
        f"qualities={result['qualities_present']}, missing={result['missing_files']}"
    )

    return result


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=WORKER_API_PORT)
