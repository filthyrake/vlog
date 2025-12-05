"""
Worker API - Separate FastAPI service for distributed transcoding workers.

Provides endpoints for:
- Worker registration and heartbeat
- Job claiming with distributed locking
- Source file download and HLS upload
- Progress reporting and job completion

Run with: uvicorn api.worker_api:app --host 0.0.0.0 --port 9002
"""
import json
import secrets
import tarfile
import tempfile
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import sqlalchemy as sa
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from api.database import (
    configure_sqlite_pragmas,
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
    UPLOADS_DIR,
    VIDEOS_DIR,
    WORKER_API_PORT,
    WORKER_CLAIM_DURATION_MINUTES,
    WORKER_OFFLINE_THRESHOLD_MINUTES,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage database connection lifecycle."""
    await database.connect()
    await configure_sqlite_pragmas()
    yield
    await database.disconnect()


app = FastAPI(
    title="VLog Worker API",
    description="API for distributed transcoding workers",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS - allow all origins since this is internal
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# Worker Registration
# =============================================================================


@app.post("/api/worker/register", response_model=WorkerRegisterResponse)
async def register_worker(data: WorkerRegisterRequest):
    """
    Register a new transcoding worker and generate API key.

    The API key is only returned once at registration - store it securely.
    """
    worker_id = str(uuid.uuid4())
    api_key = secrets.token_urlsafe(32)  # 256-bit key
    key_hash = hash_api_key(api_key)
    key_prefix = get_key_prefix(api_key)
    now = datetime.now(timezone.utc)

    worker_name = data.worker_name or f"worker-{worker_id[:8]}"

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
                    capabilities=json.dumps(data.capabilities) if data.capabilities else None,
                    metadata=json.dumps(data.metadata) if data.metadata else None,
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
async def worker_heartbeat(
    data: HeartbeatRequest,
    worker: dict = Depends(verify_worker_key),
):
    """Update worker heartbeat timestamp."""
    now = datetime.now(timezone.utc)

    update_values = {
        "last_heartbeat": now,
        "status": data.status,
    }
    if data.metadata:
        update_values["metadata"] = json.dumps(data.metadata)

    await database.execute(
        workers.update()
        .where(workers.c.id == worker["id"])
        .values(**update_values)
    )

    return HeartbeatResponse(status="ok", server_time=now)


# =============================================================================
# Job Claiming
# =============================================================================


@app.post("/api/worker/claim", response_model=ClaimJobResponse)
async def claim_job(worker: dict = Depends(verify_worker_key)):
    """
    Atomically claim the next available transcoding job.

    Uses database transaction for distributed safety.
    Claims expire after WORKER_CLAIM_DURATION_MINUTES (extended on progress updates).
    """
    now = datetime.now(timezone.utc)
    claim_duration = timedelta(minutes=WORKER_CLAIM_DURATION_MINUTES)
    expires_at = now + claim_duration

    # Store job data in mutable container for the transaction
    claim_result = {"job": None}

    async def do_claim_transaction():
        """Execute the claim transaction - wrapped with retry logic."""
        async with database.transaction():
            # Find oldest unclaimed pending job, or job with expired claim
            job = await database.fetch_one(
                sa.text("""
                    SELECT tj.id, tj.video_id, v.slug, v.duration, v.source_width, v.source_height
                    FROM transcoding_jobs tj
                    JOIN videos v ON tj.video_id = v.id
                    WHERE v.status = 'pending'
                      AND v.deleted_at IS NULL
                      AND (tj.claimed_at IS NULL OR tj.claim_expires_at < :now)
                      AND tj.completed_at IS NULL
                    ORDER BY v.created_at ASC
                    LIMIT 1
                """).bindparams(now=now)
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
            await database.execute(
                videos.update()
                .where(videos.c.id == job["video_id"])
                .values(status="processing")
            )

            # Update worker's current job
            await database.execute(
                workers.update()
                .where(workers.c.id == worker["id"])
                .values(current_job_id=job["id"])
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
    for ext in [".mp4", ".mkv", ".webm", ".mov", ".avi"]:
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
async def update_progress(
    job_id: int,
    data: ProgressUpdateRequest,
    worker: dict = Depends(verify_worker_key),
):
    """Update job progress and extend claim."""
    # Verify worker owns this job
    job = await database.fetch_one(
        transcoding_jobs.select().where(transcoding_jobs.c.id == job_id)
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["worker_id"] != worker["worker_id"]:
        raise HTTPException(status_code=403, detail="Not your job")

    # Extend claim on progress update
    now = datetime.now(timezone.utc)
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
            if result == 0:
                await database.execute(
                    quality_progress.insert().values(
                        job_id=job_id,
                        quality=qp.name,
                        status=qp.status,
                        progress_percent=qp.progress,
                    )
                )

    return ProgressUpdateResponse(status="ok", claim_expires_at=new_expiry)


# =============================================================================
# Job Completion
# =============================================================================


@app.post("/api/worker/{job_id}/complete", response_model=CompleteJobResponse)
async def complete_job(
    job_id: int,
    data: CompleteJobRequest,
    worker: dict = Depends(verify_worker_key),
):
    """Mark job as complete after HLS files uploaded."""
    job = await database.fetch_one(
        transcoding_jobs.select().where(transcoding_jobs.c.id == job_id)
    )
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
            await database.execute(
                videos.update()
                .where(videos.c.id == job["video_id"])
                .values(**video_updates)
            )

            # Clear worker's current job
            await database.execute(
                workers.update()
                .where(workers.c.id == worker["id"])
                .values(current_job_id=None)
            )

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
async def fail_job(
    job_id: int,
    data: FailJobRequest,
    worker: dict = Depends(verify_worker_key),
):
    """Report job failure."""
    job = await database.fetch_one(
        transcoding_jobs.select().where(transcoding_jobs.c.id == job_id)
    )
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
                await database.execute(
                    videos.update()
                    .where(videos.c.id == job["video_id"])
                    .values(status="pending")
                )
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
            await database.execute(
                workers.update()
                .where(workers.c.id == worker["id"])
                .values(current_job_id=None)
            )

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
async def download_source(
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
    for ext in [".mp4", ".mkv", ".webm", ".mov", ".avi"]:
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


@app.post("/api/worker/upload/{video_id}", response_model=StatusResponse)
async def upload_hls(
    video_id: int,
    file: UploadFile = File(...),
    worker: dict = Depends(verify_worker_key),
):
    """
    Upload HLS output files (tar.gz archive of video directory).

    Worker packages: master.m3u8, quality playlists, .ts segments, thumbnail.jpg
    """
    job = await database.fetch_one(
        transcoding_jobs.select()
        .where(transcoding_jobs.c.video_id == video_id)
        .where(transcoding_jobs.c.worker_id == worker["worker_id"])
    )
    if not job:
        raise HTTPException(status_code=403, detail="Not your job or job not found")

    video = await database.fetch_one(
        videos.select().where(videos.c.id == video_id)
    )
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    output_dir = VIDEOS_DIR / video["slug"]
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save uploaded tar.gz to temp file
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        with tarfile.open(tmp_path, "r:gz") as tar:
            # Security: validate and extract files safely (CVE-2007-4559 mitigation)
            # We extract each member individually after validating the resolved path
            output_dir_resolved = output_dir.resolve()

            for member in tar.getmembers():
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
    finally:
        tmp_path.unlink()

    return StatusResponse(status="ok", message="HLS files uploaded successfully")


# =============================================================================
# Worker Management (for admin/CLI)
# =============================================================================


@app.get("/api/workers", response_model=WorkerListResponse)
async def list_workers():
    """List all registered workers with their status."""
    now = datetime.now(timezone.utc)
    offline_threshold = now - timedelta(minutes=WORKER_OFFLINE_THRESHOLD_MINUTES)

    # Get all workers
    rows = await database.fetch_all(
        workers.select().order_by(workers.c.last_heartbeat.desc())
    )

    worker_list = []
    active_count = 0
    offline_count = 0

    for row in rows:
        # Determine status
        status = row["status"]
        if status == "active" and row["last_heartbeat"]:
            # Handle both timezone-aware and naive datetimes from SQLite
            last_hb = row["last_heartbeat"]
            if last_hb.tzinfo is None:
                last_hb = last_hb.replace(tzinfo=timezone.utc)
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
async def revoke_worker(worker_id: str):
    """Revoke a worker's API keys (admin endpoint, no auth required for now)."""
    # Find worker by UUID
    worker = await database.fetch_one(
        workers.select().where(workers.c.worker_id == worker_id)
    )
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
    await database.execute(
        workers.update()
        .where(workers.c.id == worker["id"])
        .values(status="disabled")
    )

    return StatusResponse(status="ok", message=f"Worker {worker_id} has been revoked")


@app.get("/api/health")
async def health_check():
    """Health check endpoint for kubernetes probes."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=WORKER_API_PORT)
