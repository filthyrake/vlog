"""Live stream ingest API for segment push.

This module handles the HTTP segment push from FFmpeg:
- PUT /api/live/ingest/{slug}/{quality}/init.mp4 - Push init segment
- PUT /api/live/ingest/{slug}/{quality}/seg_{seq}.m4s - Push media segment
- GET /api/live/ingest/{slug}/status - Get ingest status

Security:
- All endpoints require Bearer token authentication with stream key
- Path validation prevents directory traversal
- Magic byte validation ensures file format
- Rate limiting prevents DoS attacks
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Set

from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette.responses import Response

from api.common import get_real_ip
from api.database import database, live_stream_segments, live_streams
from api.db_retry import db_execute_with_retry, fetch_one_with_retry
from api.live_auth import verify_stream_key
from api.live_schemas import IngestStatusResponse, SegmentUploadResponse

from config import (
    LIVE_ALLOWED_QUALITIES,
    LIVE_ENABLED,
    LIVE_MAX_SEGMENT_SIZE,
    LIVE_STORAGE_PATH,
    RATE_LIMIT_ENABLED,
    RATE_LIMIT_LIVE_GLOBAL,
    RATE_LIMIT_LIVE_SEGMENT,
    RATE_LIMIT_STORAGE_URL,
)

logger = logging.getLogger(__name__)

# Create router for ingest API
router = APIRouter(prefix="/api/live/ingest", tags=["Live Ingest"])

# Initialize rate limiter for ingest API
# Uses same storage backend as other APIs for consistency
limiter = Limiter(
    key_func=get_real_ip,
    storage_uri=RATE_LIMIT_STORAGE_URL if RATE_LIMIT_ENABLED else None,
    enabled=RATE_LIMIT_ENABLED,
)

# Thread pool for file I/O (prevents blocking event loop)
# 16 workers to handle concurrent segment uploads without blocking
_io_executor = ThreadPoolExecutor(max_workers=16, thread_name_prefix="live_io")

# Track active segment uploads for graceful shutdown
_active_uploads: Set[str] = set()
_accepting_uploads = True

# Magic bytes for segment validation (same as worker_api.py)
TS_MAGIC_BYTE = b"\x47"
FTYP_MAGIC = b"ftyp"
MOOF_MAGIC = b"moof"
STYP_MAGIC = b"styp"
SIDX_MAGIC = b"sidx"
EMSG_MAGIC = b"emsg"

# Regex for segment filename validation
SEGMENT_FILENAME_RE = re.compile(r"^seg_(\d{4,6})\.m4s$")


def validate_segment_magic_bytes(data: bytes, filename: str) -> bool:
    """
    Validate segment file magic bytes.

    Verifies that the file content matches expected format based on extension:
    - .ts files start with MPEG-TS sync byte (0x47)
    - .m4s files start with 'ftyp', 'moof', 'styp', 'sidx', or 'emsg' box
    - .mp4 files start with 'ftyp' box
    """
    if len(data) < 8:
        return False

    if filename.endswith(".ts"):
        return data[0:1] == TS_MAGIC_BYTE

    if filename.endswith(".m4s"):
        box_type = data[4:8]
        return box_type in (FTYP_MAGIC, MOOF_MAGIC, STYP_MAGIC, SIDX_MAGIC, EMSG_MAGIC)

    if filename.endswith(".mp4"):
        box_type = data[4:8]
        return box_type == FTYP_MAGIC

    return False


def validate_quality(quality: str) -> bool:
    """Validate quality name against allowed values."""
    return quality in LIVE_ALLOWED_QUALITIES


def validate_path_containment(path: Path, base_dir: Path) -> bool:
    """
    Validate that a path stays within its base directory.

    This is defense-in-depth against path traversal attacks.
    Even though inputs are validated, this ensures the resolved
    path cannot escape the intended directory.

    Args:
        path: The path to validate (will be resolved)
        base_dir: The base directory the path must stay within

    Returns:
        True if path is safely contained within base_dir
    """
    try:
        # Resolve both paths to their absolute, canonical forms
        resolved_path = path.resolve()
        resolved_base = base_dir.resolve()

        # Check if resolved path starts with the base directory
        return str(resolved_path).startswith(str(resolved_base) + os.sep) or resolved_path == resolved_base
    except (OSError, ValueError):
        # Any error during resolution means the path is suspicious
        return False


def validate_segment_filename(filename: str) -> Optional[int]:
    """
    Validate segment filename and extract sequence number.

    Returns sequence number if valid, None otherwise.
    Only allows: seg_NNNN.m4s format
    """
    match = SEGMENT_FILENAME_RE.match(filename)
    if match:
        return int(match.group(1))
    return None


async def write_segment_atomic(dest_path: Path, data: bytes) -> None:
    """
    Write segment data atomically using tempfile and rename.

    This ensures readers never see partial files.
    """
    loop = asyncio.get_event_loop()

    def _write():
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        # Write to temp file in same directory
        fd, temp_path = tempfile.mkstemp(dir=str(dest_path.parent), suffix=".tmp")
        fd_closed = False
        try:
            os.write(fd, data)
            os.fsync(fd)
            os.close(fd)
            fd_closed = True
            # Atomic rename
            os.rename(temp_path, str(dest_path))
        except Exception:
            if not fd_closed:
                os.close(fd)
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise

    await loop.run_in_executor(_io_executor, _write)


def prepare_stream_update_values(stream: dict, quality: str, now: datetime) -> dict:
    """
    Prepare update values for stream record based on current state.

    This function uses the already-fetched stream data to avoid an extra query.

    Args:
        stream: Stream record dict from verify_stream_key
        quality: Quality being uploaded
        now: Current timestamp

    Returns:
        Dict of values to update on the stream record
    """
    update_values = {
        "last_segment_at": now,
        "segment_count": live_streams.c.segment_count + 1,
    }

    # Update status to live if currently idle or ending
    if stream["status"] in ("idle", "ending"):
        update_values["status"] = "live"
        if stream["status"] == "idle":
            update_values["started_at"] = now

    # Update qualities list if this is a new quality
    current_qualities = []
    if stream["qualities"]:
        try:
            current_qualities = json.loads(stream["qualities"])
        except json.JSONDecodeError:
            pass

    if quality not in current_qualities:
        current_qualities.append(quality)
        current_qualities.sort()
        update_values["qualities"] = json.dumps(current_qualities)

    return update_values


@router.put("/{slug}/{quality}/init.mp4")
@limiter.limit(RATE_LIMIT_LIVE_GLOBAL)  # Global per-IP limit (prevents flood via multiple streams)
@limiter.limit(RATE_LIMIT_LIVE_SEGMENT)  # Additional per-IP limit for segment uploads
async def put_init_segment(
    request: Request,
    slug: str,
    quality: str,
    stream: dict = Depends(verify_stream_key),
) -> Response:
    """
    Push init segment for a quality variant.

    The init segment contains the initialization data needed to decode
    subsequent media segments.
    """
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is disabled")

    if not _accepting_uploads:
        raise HTTPException(status_code=503, detail="Server is shutting down")

    # Validate quality
    if not validate_quality(quality):
        raise HTTPException(status_code=400, detail=f"Invalid quality: {quality}")

    # Verify slug matches authenticated stream
    if stream["slug"] != slug:
        raise HTTPException(status_code=403, detail="Stream key does not match slug")

    # Validate Content-Length header if present
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            length = int(content_length)
        except (ValueError, OverflowError):
            raise HTTPException(status_code=400, detail="Invalid Content-Length header")
        if length > LIVE_MAX_SEGMENT_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"Segment too large. Max: {LIVE_MAX_SEGMENT_SIZE} bytes",
            )

    # Read request body with timeout (50MB at 100KB/s = 500s, with 50% margin = 720s)
    try:
        data = await asyncio.wait_for(request.body(), timeout=720.0)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=408, detail="Upload timeout - slow connection")

    if len(data) > LIVE_MAX_SEGMENT_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Segment too large. Max: {LIVE_MAX_SEGMENT_SIZE} bytes",
        )

    # Validate magic bytes
    if not validate_segment_magic_bytes(data, "init.mp4"):
        raise HTTPException(status_code=400, detail="Invalid init segment format")

    # Validate checksum if provided
    x_content_sha256 = request.headers.get("x-content-sha256")
    if x_content_sha256:
        checksum = x_content_sha256.lower()
        if checksum.startswith("sha256:"):
            checksum = checksum[7:]
        computed = hashlib.sha256(data).hexdigest()
        if checksum != computed:
            raise HTTPException(status_code=400, detail="Checksum mismatch")

    # Write init segment
    dest_path = LIVE_STORAGE_PATH / slug / quality / "init.mp4"

    # Defense-in-depth: verify path stays within live storage
    if not validate_path_containment(dest_path, LIVE_STORAGE_PATH):
        logger.warning(f"Path containment violation for init segment: {dest_path}")
        raise HTTPException(status_code=400, detail="Invalid path")

    upload_key = f"{slug}/{quality}/init"
    _active_uploads.add(upload_key)
    try:
        await write_segment_atomic(dest_path, data)
    finally:
        _active_uploads.discard(upload_key)

    logger.debug(f"Received init segment for {slug}/{quality}")

    return Response(status_code=204)


@router.put("/{slug}/{quality}/{filename}")
@limiter.limit(RATE_LIMIT_LIVE_GLOBAL)  # Global per-IP limit (prevents flood via multiple streams)
@limiter.limit(RATE_LIMIT_LIVE_SEGMENT)  # Additional per-IP limit for segment uploads
async def put_media_segment(
    request: Request,
    slug: str,
    quality: str,
    filename: str,
    stream: dict = Depends(verify_stream_key),
) -> SegmentUploadResponse:
    """
    Push a media segment.

    Segment filenames must match pattern: seg_NNNN.m4s
    where NNNN is a 4-6 digit sequence number.
    """
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is disabled")

    if not _accepting_uploads:
        raise HTTPException(status_code=503, detail="Server is shutting down")

    # Validate quality
    if not validate_quality(quality):
        raise HTTPException(status_code=400, detail=f"Invalid quality: {quality}")

    # Verify slug matches authenticated stream
    if stream["slug"] != slug:
        raise HTTPException(status_code=403, detail="Stream key does not match slug")

    # Validate and extract sequence number
    # Server generates the actual filename for security
    sequence_number = validate_segment_filename(filename)
    if sequence_number is None:
        raise HTTPException(
            status_code=400,
            detail="Invalid segment filename. Must be seg_NNNN.m4s",
        )

    # Generate server-controlled filename
    safe_filename = f"seg_{sequence_number:04d}.m4s"

    # Validate Content-Length header if present
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            length = int(content_length)
        except (ValueError, OverflowError):
            raise HTTPException(status_code=400, detail="Invalid Content-Length header")
        if length > LIVE_MAX_SEGMENT_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"Segment too large. Max: {LIVE_MAX_SEGMENT_SIZE} bytes",
            )

    # Read request body with timeout (50MB at 100KB/s = 500s, with 50% margin = 720s)
    try:
        data = await asyncio.wait_for(request.body(), timeout=720.0)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=408, detail="Upload timeout - slow connection")

    if len(data) > LIVE_MAX_SEGMENT_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Segment too large. Max: {LIVE_MAX_SEGMENT_SIZE} bytes",
        )

    # Validate magic bytes
    if not validate_segment_magic_bytes(data, safe_filename):
        raise HTTPException(status_code=400, detail="Invalid segment format")

    # Validate checksum if provided
    x_content_sha256 = request.headers.get("x-content-sha256")
    if x_content_sha256:
        checksum = x_content_sha256.lower()
        if checksum.startswith("sha256:"):
            checksum = checksum[7:]
        computed = hashlib.sha256(data).hexdigest()
        if checksum != computed:
            raise HTTPException(status_code=400, detail="Checksum mismatch")

    # Parse segment duration from header (optional)
    duration_ms = None
    x_segment_duration = request.headers.get("x-segment-duration-ms")
    if x_segment_duration:
        try:
            duration_ms = int(x_segment_duration)
        except ValueError:
            pass

    now = datetime.now(timezone.utc)
    stream_id = stream["id"]
    dest_path = LIVE_STORAGE_PATH / slug / quality / safe_filename

    # Defense-in-depth: verify path stays within live storage
    if not validate_path_containment(dest_path, LIVE_STORAGE_PATH):
        logger.warning(f"Path containment violation for segment: {dest_path}")
        raise HTTPException(status_code=400, detail="Invalid path")

    upload_key = f"{slug}/{quality}/{sequence_number}"
    _active_uploads.add(upload_key)
    file_written = False

    try:
        # Write segment to disk first
        await write_segment_atomic(dest_path, data)
        file_written = True

        # Batch database operations: insert segment + update stream in sequence
        # This reduces 4 queries to 2 (no separate fetch + update)
        segment_is_duplicate = False
        try:
            await db_execute_with_retry(
                live_stream_segments.insert().values(
                    stream_id=stream_id,
                    quality=quality,
                    filename=safe_filename,
                    sequence_number=sequence_number,
                    duration_ms=duration_ms,
                    size_bytes=len(data),
                    received_at=now,
                )
            )
        except Exception as e:
            # Unique constraint violation = segment already exists (idempotent)
            if "uq_live_segment_stream_quality_seq" in str(e).lower() or "unique" in str(e).lower():
                logger.debug(f"Segment {safe_filename} already exists for {slug}/{quality}")
                segment_is_duplicate = True
            else:
                raise

        # Update stream in single query (combines count, status, qualities, timestamp)
        # Only increment count if segment wasn't a duplicate
        if not segment_is_duplicate:
            update_values = prepare_stream_update_values(stream, quality, now)
            await db_execute_with_retry(
                live_streams.update()
                .where(live_streams.c.id == stream_id)
                .values(**update_values)
            )

    except Exception as e:
        # Clean up orphaned file if DB operations failed after file write
        if file_written:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    _io_executor,
                    lambda: dest_path.unlink(missing_ok=True)
                )
                logger.debug(f"Cleaned up orphaned segment file {dest_path}")
            except Exception as cleanup_error:
                logger.warning(f"Failed to cleanup orphaned file {dest_path}: {cleanup_error}")
        raise
    finally:
        _active_uploads.discard(upload_key)

    logger.debug(f"Received segment {safe_filename} for {slug}/{quality}")

    return SegmentUploadResponse(
        received=True,
        sequence_number=sequence_number,
        quality=quality,
    )


@router.get("/{slug}/status")
@limiter.limit(RATE_LIMIT_LIVE_GLOBAL)  # Global per-IP limit
async def get_ingest_status(
    request: Request,
    slug: str,
    stream: dict = Depends(verify_stream_key),
) -> IngestStatusResponse:
    """
    Get ingest status for a stream.

    Returns the current stream status, segment count, and qualities.
    """
    if not LIVE_ENABLED:
        raise HTTPException(status_code=503, detail="Live streaming is disabled")

    # Verify slug matches authenticated stream
    if stream["slug"] != slug:
        raise HTTPException(status_code=403, detail="Stream key does not match slug")

    # Parse qualities
    qualities = []
    if stream["qualities"]:
        try:
            qualities = json.loads(stream["qualities"])
        except json.JSONDecodeError:
            pass

    return IngestStatusResponse(
        stream_id=stream["id"],
        slug=stream["slug"],
        status=stream["status"],
        segment_count=stream["segment_count"],
        qualities=qualities,
        last_segment_at=stream["last_segment_at"],
    )


def stop_accepting_uploads():
    """Stop accepting new uploads (for graceful shutdown)."""
    global _accepting_uploads
    _accepting_uploads = False
    logger.info("Stopped accepting new live segment uploads")


async def wait_for_active_uploads(timeout: float = 10.0) -> bool:
    """
    Wait for active uploads to complete.

    Returns True if all uploads completed, False if timeout.
    """
    if not _active_uploads:
        return True

    logger.info(f"Waiting for {len(_active_uploads)} active uploads to complete...")

    start_time = asyncio.get_event_loop().time()
    while _active_uploads:
        if asyncio.get_event_loop().time() - start_time > timeout:
            logger.warning(f"Timeout waiting for uploads: {_active_uploads}")
            return False
        await asyncio.sleep(0.1)

    logger.info("All active uploads completed")
    return True


def get_active_upload_count() -> int:
    """Get the number of active uploads."""
    return len(_active_uploads)
