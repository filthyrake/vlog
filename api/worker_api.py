"""
Worker API - Separate FastAPI service for distributed transcoding workers.

Provides endpoints for:
- Worker registration and heartbeat
- Job claiming with distributed locking
- Source file download and HLS upload
- Progress reporting and job completion

Run with: uvicorn api.worker_api:app --host 0.0.0.0 --port 9002
"""

import asyncio
import hmac
import json
import logging
import secrets
import tarfile
import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import sqlalchemy as sa
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

from api.common import check_health, ensure_utc, get_real_ip, get_storage_status, rate_limit_exceeded_handler
from api.database import (
    configure_database,
    database,
    quality_progress,
    transcoding_jobs,
    video_qualities,
    videos,
    worker_api_keys,
    workers,
)
from api.db_retry import DatabaseLockedError, execute_with_retry
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
    RATE_LIMIT_ENABLED,
    RATE_LIMIT_STORAGE_URL,
    RATE_LIMIT_WORKER_DEFAULT,
    RATE_LIMIT_WORKER_PROGRESS,
    RATE_LIMIT_WORKER_REGISTER,
    STALE_JOB_CHECK_INTERVAL,
    SUPPORTED_VIDEO_EXTENSIONS,
    UPLOADS_DIR,
    VIDEOS_DIR,
    WORKER_ADMIN_SECRET,
    WORKER_API_PORT,
    WORKER_CLAIM_DURATION_MINUTES,
    WORKER_OFFLINE_THRESHOLD_MINUTES,
)

logger = logging.getLogger(__name__)

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
    """
    global _api_start_time
    now = datetime.now(timezone.utc)

    # Skip stale check during startup grace period
    # This allows workers to send heartbeats after API recovers from downtime
    if _api_start_time:
        time_since_startup = (now - _api_start_time).total_seconds()
        if time_since_startup < STALE_CHECK_STARTUP_GRACE_PERIOD:
            logger.debug(
                f"Skipping stale check during startup grace period "
                f"({time_since_startup:.0f}s < {STALE_CHECK_STARTUP_GRACE_PERIOD}s)"
            )
            return 0

    offline_threshold = now - timedelta(minutes=WORKER_OFFLINE_THRESHOLD_MINUTES)

    # Find workers that are not already offline but haven't sent heartbeat
    stale_workers = await database.fetch_all(
        workers.select().where(workers.c.status != "offline").where(workers.c.last_heartbeat < offline_threshold)
    )

    processed_count = 0
    for worker in stale_workers:
        worker_name = worker["worker_name"] or worker["worker_id"][:8]
        worker_last_hb = worker["last_heartbeat"]

        # ATOMIC CONDITIONAL UPDATE: Only mark offline if last_heartbeat is STILL old
        # This prevents race condition where heartbeat arrives between our fetch and update
        result = await database.execute(
            workers.update()
            .where(workers.c.id == worker["id"])
            .where(workers.c.last_heartbeat < offline_threshold)  # Re-check condition
            .where(workers.c.status != "offline")  # Don't update if already offline
            .values(status="offline", current_job_id=None)
        )

        if result == 0:
            # Worker was updated (heartbeat received) between fetch and update
            logger.info(
                f"Worker '{worker_name}' recovered (heartbeat received since stale check started)"
            )
            continue

        processed_count += 1
        logger.warning(f"Worker '{worker_name}' went offline (no heartbeat since {worker_last_hb})")

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
                f"Releasing stale job {job['id']} from offline worker '{worker_name}' "
                f"(claim expired: {claim_expires})"
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
        f"Worker API started - database connected. "
        f"Stale check grace period: {STALE_CHECK_STARTUP_GRACE_PERIOD}s"
    )

    # Start background task for stale job detection
    stale_job_task = asyncio.create_task(check_stale_jobs())

    yield

    # Signal background task to stop
    _shutdown_event.set()
    try:
        await asyncio.wait_for(stale_job_task, timeout=5.0)
    except asyncio.TimeoutError:
        logger.warning("Stale job checker did not stop in time, cancelling...")
        stale_job_task.cancel()
        try:
            await stale_job_task
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

# CORS - allow all origins since workers use API key auth (not cookies)
# Note: allow_credentials must be False with wildcard origins per CORS spec
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    key_hash = hash_api_key(api_key)
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

            # Create API key record
            await database.execute(
                worker_api_keys.insert().values(
                    worker_id=result["worker_db_id"],
                    key_hash=key_hash,
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
    """
    now = datetime.now(timezone.utc)
    worker_name = worker["worker_name"] or worker["worker_id"][:8]
    was_offline = worker["status"] == "offline"

    update_values = {
        "last_heartbeat": now,
        "status": data.status,
    }

    # Validate and serialize metadata with size limit
    if data.metadata:
        metadata_json = json.dumps(data.metadata)
        if len(metadata_json) > 10000:  # 10KB limit
            raise HTTPException(status_code=400, detail="Metadata JSON too large (max 10KB)")
        update_values["metadata"] = metadata_json

    await database.execute(workers.update().where(workers.c.id == worker["id"]).values(**update_values))

    # Log recovery from offline status for debugging
    if was_offline:
        logger.info(
            f"Worker '{worker_name}' recovered from offline status "
            f"(was offline since last_heartbeat={worker['last_heartbeat']})"
        )

    return HeartbeatResponse(status="ok", server_time=now)


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
async def claim_job(request: Request, worker: dict = Depends(verify_worker_key)):
    """
    Atomically claim the next available transcoding job.

    Uses database transaction for distributed safety.
    Claims expire after WORKER_CLAIM_DURATION_MINUTES (extended on progress updates).

    GPU workers have priority over CPU workers. If a CPU worker requests a job
    but idle GPU workers are available, the CPU worker will be told to wait.
    """
    now = datetime.now(timezone.utc)
    claim_duration = timedelta(minutes=WORKER_CLAIM_DURATION_MINUTES)
    expires_at = now + claim_duration

    # Check worker priority - GPU workers get priority over CPU workers
    requesting_worker_has_gpu = worker_has_gpu(worker)

    if not requesting_worker_has_gpu:
        # This is a CPU worker - check if any GPU workers are idle
        idle_gpu_workers = await database.fetch_all(
            workers.select().where(workers.c.status == "idle").where(workers.c.id != worker["id"])  # Exclude self
        )

        # Check if any idle workers have GPU
        for idle_worker in idle_gpu_workers:
            if worker_has_gpu(dict(idle_worker)):
                # GPU worker is available, CPU worker should wait
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

            # Find oldest unclaimed pending job with row locking
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

            if is_postgresql:
                job = await database.fetch_one(
                    sa.text("""
                        SELECT tj.id, tj.video_id, v.slug, v.duration, v.source_width, v.source_height
                        FROM transcoding_jobs tj
                        JOIN videos v ON tj.video_id = v.id
                        WHERE v.status = 'pending'
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
                        SELECT tj.id, tj.video_id, v.slug, v.duration, v.source_width, v.source_height
                        FROM transcoding_jobs tj
                        JOIN videos v ON tj.video_id = v.id
                        WHERE v.status = 'pending'
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

            # Update job with claim
            await database.execute(
                transcoding_jobs.update()
                .where(transcoding_jobs.c.id == job["id"])
                .values(
                    worker_id=worker["worker_id"],
                    claimed_at=now,
                    claim_expires_at=expires_at,
                    started_at=now,
                    current_step="claimed",
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

    # Find source filename
    source_filename = None
    for ext in SUPPORTED_VIDEO_EXTENSIONS:
        candidate = UPLOADS_DIR / f"{job['video_id']}{ext}"
        if candidate.exists():
            source_filename = candidate.name
            break

    return ClaimJobResponse(
        job_id=job["id"],
        video_id=job["video_id"],
        video_slug=job["slug"],
        video_duration=job["duration"],
        source_width=job["source_width"],
        source_height=job["source_height"],
        source_filename=source_filename,
        claim_expires_at=expires_at,
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
    if data.quality_progress:
        for qp in data.quality_progress:
            # Try to update existing record
            result = await database.execute(
                quality_progress.update()
                .where(quality_progress.c.job_id == job_id)
                .where(quality_progress.c.quality == qp.name)
                .values(status=qp.status, progress_percent=qp.progress)
            )
            # If no record exists, create one
            # PostgreSQL with databases library returns None for UPDATE when no rows affected
            # SQLite returns 0
            if result is None or result == 0 or (isinstance(result, str) and result.startswith("UPDATE 0")):
                logger.info(f"Job {job_id}: Inserting new record for {qp.name}")
                await database.execute(
                    quality_progress.insert().values(
                        job_id=job_id,
                        quality=qp.name,
                        status=qp.status,
                        progress_percent=qp.progress,
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
    """Mark job as complete after HLS files uploaded."""
    job = await database.fetch_one(transcoding_jobs.select().where(transcoding_jobs.c.id == job_id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["worker_id"] != worker["worker_id"]:
        raise HTTPException(status_code=403, detail="Not your job")

    now = datetime.now(timezone.utc)

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
            video_updates = {"status": "ready", "published_at": now}
            if data.duration is not None:
                video_updates["duration"] = data.duration
            if data.source_width is not None:
                video_updates["source_width"] = data.source_width
            if data.source_height is not None:
                video_updates["source_height"] = data.source_height

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
        raise HTTPException(
            status_code=503,
            detail="Database temporarily unavailable, please retry",
        ) from e

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

    will_retry = data.retry and job["attempt_number"] < job["max_attempts"]
    now = datetime.now(timezone.utc)

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
        with tarfile.open(tmp_path, "r:gz") as tar:
            output_dir_resolved = output_dir.resolve()
            extracted_count = 0
            extracted_size = 0

            for member in tar.getmembers():
                extracted_count += 1
                if extracted_count > MAX_HLS_ARCHIVE_FILES:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Archive contains too many files (limit: {MAX_HLS_ARCHIVE_FILES})",
                    )

                if member.isfile() and member.size > MAX_HLS_SINGLE_FILE_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail=f"File too large: {member.name} ({member.size} bytes)",
                    )

                extracted_size += member.size
                if extracted_size > MAX_HLS_ARCHIVE_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Archive too large (limit: {MAX_HLS_ARCHIVE_SIZE} bytes)",
                    )

                if member.issym() or member.islnk():
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid archive: symlinks not allowed ({member.name})",
                    )

                if not (member.isfile() or member.isdir()):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid archive: unsupported file type ({member.name})",
                    )

                # Validate file belongs to this quality
                if member.isfile():
                    valid_extensions = (".m3u8", ".ts", ".jpg", ".vtt")
                    if not member.name.endswith(valid_extensions):
                        raise HTTPException(
                            status_code=400,
                            detail=f"Invalid file type: {member.name}",
                        )

                member_path = output_dir / member.name
                try:
                    if member_path.exists():
                        dest_resolved = member_path.resolve()
                    else:
                        dest_resolved = member_path.parent.resolve() / member_path.name
                except (ValueError, OSError) as e:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid path {member.name}: {e}",
                    )

                try:
                    dest_resolved.relative_to(output_dir_resolved)
                except ValueError:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Path traversal detected ({member.name})",
                    )

                tar.extract(member, output_dir)
                # Reset permissions to safe defaults (issue #164)
                extracted_path = output_dir / member.name
                if extracted_path.is_file():
                    extracted_path.chmod(0o644)  # rw-r--r-- for files
                elif extracted_path.is_dir():
                    extracted_path.chmod(0o755)  # rwxr-xr-x for directories
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
    Upload final files after all qualities: master.m3u8 and thumbnail.jpg.

    Called after all quality uploads complete.
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
        with tarfile.open(tmp_path, "r:gz") as tar:
            output_dir_resolved = output_dir.resolve()

            for member in tar.getmembers():
                if member.issym() or member.islnk():
                    raise HTTPException(status_code=400, detail="Symlinks not allowed")

                if member.isfile():
                    # Only allow master.m3u8 and thumbnail.jpg
                    if member.name not in ("master.m3u8", "thumbnail.jpg"):
                        raise HTTPException(
                            status_code=400,
                            detail=f"Unexpected file in finalize: {member.name}",
                        )

                member_path = output_dir / member.name
                try:
                    if member_path.exists():
                        dest_resolved = member_path.resolve()
                    else:
                        dest_resolved = member_path.parent.resolve() / member_path.name
                except (ValueError, OSError) as e:
                    raise HTTPException(status_code=400, detail=f"Invalid path: {e}")

                try:
                    dest_resolved.relative_to(output_dir_resolved)
                except ValueError:
                    raise HTTPException(status_code=400, detail="Path traversal detected")

                tar.extract(member, output_dir)
                # Reset permissions to safe defaults (issue #164)
                extracted_path = output_dir / member.name
                if extracted_path.is_file():
                    extracted_path.chmod(0o644)  # rw-r--r-- for files
                elif extracted_path.is_dir():
                    extracted_path.chmod(0o755)  # rwxr-xr-x for directories
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
        with tarfile.open(tmp_path, "r:gz") as tar:
            # Security: validate and extract files safely (CVE-2007-4559 mitigation)
            # We extract each member individually after validating the resolved path
            output_dir_resolved = output_dir.resolve()

            # Track extraction counts and sizes to prevent tar bomb attacks
            extracted_count = 0
            extracted_size = 0

            for member in tar.getmembers():
                # Check file count limit
                extracted_count += 1
                if extracted_count > MAX_HLS_ARCHIVE_FILES:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Archive contains too many files (limit: {MAX_HLS_ARCHIVE_FILES})",
                    )

                # Check individual file size (for regular files)
                if member.isfile() and member.size > MAX_HLS_SINGLE_FILE_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail=f"File too large: {member.name} ({member.size} bytes, limit: {MAX_HLS_SINGLE_FILE_SIZE})",
                    )

                # Track cumulative extracted size
                extracted_size += member.size
                if extracted_size > MAX_HLS_ARCHIVE_SIZE:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Archive too large (limit: {MAX_HLS_ARCHIVE_SIZE} bytes)",
                    )

                # Reject symlinks and hardlinks (could point outside target)
                if member.issym() or member.islnk():
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid archive: symlinks not allowed ({member.name})",
                    )

                # Reject device files, fifos, etc.
                if not (member.isfile() or member.isdir()):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid archive: unsupported file type ({member.name})",
                    )

                # Only allow expected file extensions for regular files
                if member.isfile() and not (
                    member.name.endswith(".m3u8")
                    or member.name.endswith(".ts")
                    or member.name.endswith(".jpg")
                    or member.name.endswith(".vtt")
                ):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid archive: unexpected file type {member.name}",
                    )

                # Compute the resolved destination path
                # This catches path traversal via "..", absolute paths, etc.
                member_path = output_dir / member.name
                try:
                    # For existing paths, resolve() works directly
                    # For new paths, we need to resolve the parent and append the name
                    if member_path.exists():
                        dest_resolved = member_path.resolve()
                    else:
                        # Resolve parent (must exist after mkdir) and join with name
                        dest_resolved = member_path.parent.resolve() / member_path.name
                except (ValueError, OSError) as e:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid archive: cannot resolve path {member.name}: {e}",
                    )

                # Verify the destination is within the output directory
                try:
                    dest_resolved.relative_to(output_dir_resolved)
                except ValueError:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid archive: path traversal detected ({member.name})",
                    )

                # Safe to extract this member
                tar.extract(member, output_dir)
                # Reset permissions to safe defaults (issue #164)
                extracted_path = output_dir / member.name
                if extracted_path.is_file():
                    extracted_path.chmod(0o644)  # rw-r--r-- for files
                elif extracted_path.is_dir():
                    extracted_path.chmod(0o755)  # rwxr-xr-x for directories
    finally:
        tmp_path.unlink()

    return StatusResponse(status="ok", message="HLS files uploaded successfully")


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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=WORKER_API_PORT)
