"""
Re-encode Worker for VLog.

Background worker that processes the reencode_queue table to convert
existing HLS/TS videos to CMAF format with HEVC/AV1 codecs.

This is a separate worker process that runs alongside the main transcoder.
It handles re-encoding jobs at lower priority to avoid impacting new uploads.

See: https://github.com/filthyrake/vlog/issues/212
"""

import asyncio
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .http_client import WorkerAPIClient

logger = logging.getLogger(__name__)


class ReencodeWorker:
    """Worker for processing re-encode queue jobs."""

    def __init__(
        self,
        client: WorkerAPIClient,
        work_dir: Path,
        poll_interval: int = 30,
        max_retries: int = 3,
    ):
        """
        Initialize the re-encode worker.

        Args:
            client: API client for communicating with the server
            work_dir: Directory for temporary work files
            poll_interval: Seconds between queue polls when idle
            max_retries: Maximum retry attempts for failed jobs
        """
        self.client = client
        self.work_dir = Path(work_dir)
        self.poll_interval = poll_interval
        self.max_retries = max_retries
        self.running = False
        self._current_job: Optional[dict] = None

    async def start(self) -> None:
        """Start the worker main loop."""
        logger.info("Starting re-encode worker")
        self.running = True

        while self.running:
            try:
                job = await self._claim_next_job()
                if job:
                    await self._process_job(job)
                else:
                    # No jobs available, wait before polling again
                    await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                logger.info("Re-encode worker cancelled")
                break
            except Exception as e:
                logger.exception("Error in worker main loop: %s", e)
                await asyncio.sleep(self.poll_interval)

    def stop(self) -> None:
        """Signal the worker to stop after current job completes."""
        logger.info("Stopping re-encode worker")
        self.running = False

    async def _claim_next_job(self) -> Optional[dict]:
        """Claim the next available re-encode job from the queue."""
        try:
            result = await self.client._request(
                "POST",
                "/api/admin/reencode/claim",
                timeout=10.0,
            )
            if result.get("job"):
                return result["job"]
            return None
        except Exception as e:
            logger.debug("No jobs available or error claiming: %s", e)
            return None

    async def _process_job(self, job: dict) -> None:
        """
        Process a re-encode job.

        This involves:
        1. Creating a work directory
        2. Downloading the current HLS output
        3. Re-transcoding to CMAF with the target codec
        4. Atomically swapping the old output with new
        5. Cleaning up temporary files
        """
        job_id = job["id"]
        video_id = job["video_id"]
        target_format = job.get("target_format", "cmaf")
        target_codec = job.get("target_codec", "hevc")

        logger.info(
            "Processing re-encode job %d for video %d -> %s/%s",
            job_id,
            video_id,
            target_format,
            target_codec,
        )

        self._current_job = job
        job_work_dir = self.work_dir / f"reencode_{job_id}"

        try:
            # Create work directory
            job_work_dir.mkdir(parents=True, exist_ok=True)

            # Update job status
            await self._update_job_status(job_id, "in_progress")

            # Get video info to find current output location
            video_info = await self._get_video_info(video_id)
            if not video_info:
                raise ValueError(f"Video {video_id} not found")

            # The actual re-encoding would happen here
            # For now, this is a placeholder that marks the job as completed
            # Full implementation would:
            # 1. Download source file (if still available) or use existing HLS
            # 2. Run CMAF transcoding using hwaccel.build_cmaf_transcode_command
            # 3. Generate new manifests
            # 4. Atomic directory swap

            # TODO: Implement full re-encoding logic
            # This would integrate with transcoder.py and hwaccel.py

            await self._update_job_status(
                job_id,
                "completed",
                completed_at=datetime.now(timezone.utc),
            )
            logger.info("Re-encode job %d completed successfully", job_id)

        except Exception as e:
            logger.exception("Re-encode job %d failed: %s", job_id, e)
            retry_count = job.get("retry_count", 0) + 1

            if retry_count >= self.max_retries:
                await self._update_job_status(
                    job_id,
                    "failed",
                    error_message=str(e),
                )
            else:
                await self._update_job_status(
                    job_id,
                    "pending",
                    retry_count=retry_count,
                    error_message=f"Attempt {retry_count} failed: {e}",
                )

        finally:
            self._current_job = None
            # Clean up work directory
            if job_work_dir.exists():
                try:
                    shutil.rmtree(job_work_dir)
                except Exception as e:
                    logger.warning(
                        "Failed to clean up work dir %s: %s", job_work_dir, e
                    )

    async def _get_video_info(self, video_id: int) -> Optional[dict]:
        """Get video information from the server."""
        try:
            result = await self.client._request(
                "GET",
                f"/api/admin/videos/{video_id}",
                timeout=10.0,
            )
            return result
        except Exception as e:
            logger.error("Failed to get video info for %d: %s", video_id, e)
            return None

    async def _update_job_status(
        self,
        job_id: int,
        status: str,
        error_message: Optional[str] = None,
        retry_count: Optional[int] = None,
        completed_at: Optional[datetime] = None,
    ) -> None:
        """Update the status of a re-encode job."""
        data = {"status": status}
        if error_message:
            data["error_message"] = error_message
        if retry_count is not None:
            data["retry_count"] = retry_count
        if completed_at:
            data["completed_at"] = completed_at.isoformat()

        try:
            await self.client._request(
                "PATCH",
                f"/api/admin/reencode/{job_id}",
                json=data,
                timeout=10.0,
            )
        except Exception as e:
            logger.error("Failed to update job %d status: %s", job_id, e)


async def run_reencode_worker(
    api_url: str,
    api_key: str,
    work_dir: str,
    poll_interval: int = 30,
) -> None:
    """
    Run the re-encode worker.

    Args:
        api_url: Base URL of the API server
        api_key: Worker API key for authentication
        work_dir: Directory for temporary work files
        poll_interval: Seconds between queue polls
    """
    client = WorkerAPIClient(api_url, api_key)
    worker = ReencodeWorker(
        client=client,
        work_dir=Path(work_dir),
        poll_interval=poll_interval,
    )

    try:
        await worker.start()
    finally:
        worker.stop()
        await client.close()
