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
from typing import List, Optional

from config import VIDEOS_DIR

from .http_client import WorkerAPIClient
from .hwaccel import (
    VideoCodec,
    build_cmaf_transcode_command,
    detect_gpu_capabilities,
    select_encoder,
)
from .transcoder import (
    calculate_ffmpeg_timeout,
    generate_dash_manifest,
    generate_master_playlist_cmaf,
    get_applicable_qualities,
    get_video_info,
    run_ffmpeg_with_progress,
)

logger = logging.getLogger(__name__)


# Map string codec names to VideoCodec enum
CODEC_MAP = {
    "h264": VideoCodec.H264,
    "hevc": VideoCodec.HEVC,
    "av1": VideoCodec.AV1,
}


class ReencodeWorker:
    """Worker for processing re-encode queue jobs."""

    def __init__(
        self,
        client: WorkerAPIClient,
        work_dir: Path,
        videos_dir: Path,
        poll_interval: int = 30,
        max_retries: int = 3,
    ):
        """
        Initialize the re-encode worker.

        Args:
            client: API client for communicating with the server
            work_dir: Directory for temporary work files
            videos_dir: Directory where video outputs are stored
            poll_interval: Seconds between queue polls when idle
            max_retries: Maximum retry attempts for failed jobs
        """
        self.client = client
        self.work_dir = Path(work_dir)
        self.videos_dir = Path(videos_dir)
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
                "/api/reencode/claim",
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
        1. Finding the existing video output directory
        2. Locating a source to re-encode from (source file or existing segments)
        3. Re-transcoding to CMAF with the target codec
        4. Generating new HLS and DASH manifests
        5. Atomically swapping the old output with new
        6. Updating the video record via API
        """
        job_id = job["id"]
        video_id = job["video_id"]
        target_format = job.get("target_format", "cmaf")
        target_codec_str = job.get("target_codec", "hevc")
        target_codec = CODEC_MAP.get(target_codec_str, VideoCodec.HEVC)

        logger.info(
            "Processing re-encode job %d for video %d -> %s/%s",
            job_id,
            video_id,
            target_format,
            target_codec_str,
        )

        self._current_job = job
        job_work_dir = self.work_dir / f"reencode_{job_id}"

        try:
            # Create work directory
            job_work_dir.mkdir(parents=True, exist_ok=True)

            # Get video info to find current output location
            video_info = await self._get_video_info(video_id)
            if not video_info:
                raise ValueError(f"Video {video_id} not found")

            slug = video_info.get("slug")
            if not slug:
                raise ValueError(f"Video {video_id} has no slug")

            # Find the existing video directory
            video_dir = self.videos_dir / slug
            if not video_dir.exists():
                raise ValueError(f"Video directory not found: {video_dir}")

            # Find source to re-encode from
            source_path = await self._find_source(video_dir, video_info)
            if not source_path:
                raise ValueError(f"No source found for video {video_id}")

            # Get video info for quality selection
            probe_info = await get_video_info(source_path)
            source_height = probe_info.get("height", 1080)
            duration = probe_info.get("duration", 0)

            # Determine qualities to encode
            qualities = get_applicable_qualities(source_height)

            # Create output directory for new encoding
            new_output_dir = job_work_dir / "output"
            new_output_dir.mkdir(parents=True, exist_ok=True)

            # Re-encode each quality
            completed_qualities = []
            for quality in qualities:
                try:
                    await self._encode_quality(
                        source_path=source_path,
                        output_dir=new_output_dir,
                        quality=quality,
                        target_codec=target_codec,
                        duration=duration,
                    )
                    completed_qualities.append(quality)
                    logger.info(
                        "Job %d: Completed quality %s", job_id, quality["name"]
                    )
                except Exception as e:
                    logger.error(
                        "Job %d: Failed to encode quality %s: %s",
                        job_id,
                        quality["name"],
                        e,
                    )
                    # Continue with other qualities

            if not completed_qualities:
                raise ValueError("No qualities were successfully encoded")

            # Generate manifests
            await generate_master_playlist_cmaf(
                new_output_dir, completed_qualities, target_codec
            )
            await generate_dash_manifest(
                new_output_dir,
                completed_qualities,
                segment_duration=6,
                codec=target_codec,
            )

            # Copy thumbnail if it exists
            old_thumbnail = video_dir / "thumbnail.jpg"
            if old_thumbnail.exists():
                shutil.copy2(old_thumbnail, new_output_dir / "thumbnail.jpg")

            # Atomic swap: backup old, move new, cleanup
            backup_dir = video_dir.parent / f"{slug}_backup_{job_id}"
            try:
                # Move old to backup
                shutil.move(str(video_dir), str(backup_dir))
                # Move new to final location
                shutil.move(str(new_output_dir), str(video_dir))
                # Remove backup on success
                shutil.rmtree(backup_dir, ignore_errors=True)
            except Exception as e:
                # Try to restore backup if swap failed
                if backup_dir.exists() and not video_dir.exists():
                    shutil.move(str(backup_dir), str(video_dir))
                raise ValueError(f"Failed to swap directories: {e}")

            # Update video record in database via API
            await self._update_video_format(
                video_id, target_format, target_codec_str
            )

            # Mark job as completed
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

    async def _find_source(
        self, video_dir: Path, video_info: dict
    ) -> Optional[Path]:
        """
        Find a source file to re-encode from.

        Priority:
        1. Original source file (if archived)
        2. Highest quality existing segment (reconstruct from HLS)
        """
        # Check for archived source file
        source_extensions = [".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"]
        for ext in source_extensions:
            source_path = video_dir / f"source{ext}"
            if source_path.exists():
                logger.info("Found archived source: %s", source_path)
                return source_path

        # Check for original quality directory
        original_dir = video_dir / "original"
        if original_dir.exists():
            # Look for init.mp4 or first segment
            init_file = original_dir / "init.mp4"
            if init_file.exists():
                logger.info("Found original quality init: %s", init_file)
                # For CMAF, we could potentially use the segments directly
                # but for now, find a segment to probe
                for seg in sorted(original_dir.glob("seg_*.m4s")):
                    return seg

        # Fall back to highest quality available
        quality_dirs = ["2160p", "1440p", "1080p", "720p", "480p", "360p"]
        for quality in quality_dirs:
            quality_dir = video_dir / quality
            if quality_dir.exists():
                # Check for TS segments (legacy HLS)
                ts_files = sorted(quality_dir.glob("*.ts"))
                if ts_files:
                    # Create a concat file to use as source
                    concat_file = video_dir / f"{quality}_concat.txt"
                    with open(concat_file, "w") as f:
                        for ts in ts_files:
                            f.write(f"file '{ts}'\n")
                    logger.info(
                        "Using concatenated TS segments from %s", quality
                    )
                    return concat_file

                # Check for fMP4 segments (CMAF)
                m4s_files = sorted(quality_dir.glob("seg_*.m4s"))
                if m4s_files:
                    init_file = quality_dir / "init.mp4"
                    if init_file.exists():
                        # For fMP4, create a virtual concat source
                        concat_file = video_dir / f"{quality}_concat.txt"
                        with open(concat_file, "w") as f:
                            f.write(f"file '{init_file}'\n")
                            for seg in m4s_files:
                                f.write(f"file '{seg}'\n")
                        logger.info(
                            "Using concatenated fMP4 segments from %s", quality
                        )
                        return concat_file

        logger.warning("No source found for re-encoding")
        return None

    async def _encode_quality(
        self,
        source_path: Path,
        output_dir: Path,
        quality: dict,
        target_codec: VideoCodec,
        duration: float,
    ) -> None:
        """Encode a single quality level to CMAF format."""
        target_height = quality.get("height", 1080)

        # Detect GPU capabilities and select encoder
        gpu_caps = await detect_gpu_capabilities()
        selection = select_encoder(gpu_caps, target_height, target_codec)

        # Handle concat files specially - need to modify how we build the command
        if source_path.suffix == ".txt":
            # For concat files, we need to build the command manually
            cmd = self._build_concat_cmaf_command(
                concat_file=source_path,
                output_dir=output_dir,
                quality=quality,
                selection=selection,
                segment_duration=6,
            )
        else:
            # Standard input file
            cmd = build_cmaf_transcode_command(
                input_path=source_path,
                output_dir=output_dir,
                quality=quality,
                selection=selection,
                segment_duration=6,
            )

        # Calculate timeout based on duration
        timeout = calculate_ffmpeg_timeout(duration, target_height)

        # Run FFmpeg
        await run_ffmpeg_with_progress(
            cmd,
            duration,
            progress_callback=None,  # No callback for re-encode worker
            timeout=timeout,
        )

    def _build_concat_cmaf_command(
        self,
        concat_file: Path,
        output_dir: Path,
        quality: dict,
        selection,
        segment_duration: int = 6,
    ) -> List[str]:
        """
        Build FFmpeg command for CMAF transcoding from a concat file.

        Similar to build_cmaf_transcode_command but handles concat input.
        """
        name = quality["name"]
        bitrate = quality["bitrate"]
        audio_bitrate = quality["audio_bitrate"]

        # Create quality subdirectory for CMAF output
        quality_dir = output_dir / name
        quality_dir.mkdir(parents=True, exist_ok=True)
        playlist_path = quality_dir / "stream.m3u8"
        segment_pattern = str(quality_dir / "seg_%04d.m4s")

        cmd = ["ffmpeg", "-y"]

        # Concat input
        cmd.extend(["-f", "concat", "-safe", "0", "-i", str(concat_file)])

        # Video encoding arguments from selection
        cmd.extend(selection.output_args)

        # Bitrate control
        bitrate_kbps = int(bitrate.replace("k", "").replace("K", ""))
        cmd.extend([
            "-b:v", bitrate,
            "-maxrate", bitrate,
            "-bufsize", f"{bitrate_kbps * 2}k",
        ])

        # Audio encoding
        cmd.extend(["-c:a", "aac", "-b:a", audio_bitrate, "-ac", "2"])

        # CMAF/fMP4 HLS output
        cmd.extend([
            "-f", "hls",
            "-hls_time", str(segment_duration),
            "-hls_playlist_type", "vod",
            "-hls_segment_type", "fmp4",
            "-hls_fmp4_init_filename", "init.mp4",
            "-hls_segment_filename", segment_pattern,
            "-movflags", "+frag_keyframe+empty_moov+default_base_moof",
            str(playlist_path),
        ])

        return cmd

    async def _get_video_info(self, video_id: int) -> Optional[dict]:
        """Get video information from the server."""
        try:
            result = await self.client._request(
                "GET",
                f"/api/videos/{video_id}",
                timeout=10.0,
            )
            return result
        except Exception as e:
            logger.error("Failed to get video info for %d: %s", video_id, e)
            return None

    async def _update_video_format(
        self, video_id: int, streaming_format: str, primary_codec: str
    ) -> None:
        """Update the video's streaming format in the database."""
        try:
            # This would need an API endpoint to update video format
            # For now, log the intended update
            logger.info(
                "Video %d should be updated to format=%s, codec=%s",
                video_id,
                streaming_format,
                primary_codec,
            )
            # TODO: Add API endpoint to update video streaming_format and primary_codec
        except Exception as e:
            logger.error("Failed to update video format for %d: %s", video_id, e)

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
                f"/api/reencode/{job_id}",
                json=data,
                timeout=10.0,
            )
        except Exception as e:
            logger.error("Failed to update job %d status: %s", job_id, e)


async def run_reencode_worker(
    api_url: str,
    api_key: str,
    work_dir: str,
    videos_dir: Optional[str] = None,
    poll_interval: int = 30,
) -> None:
    """
    Run the re-encode worker.

    Args:
        api_url: Base URL of the API server
        api_key: Worker API key for authentication
        work_dir: Directory for temporary work files
        videos_dir: Directory where video outputs are stored (default: VIDEOS_DIR)
        poll_interval: Seconds between queue polls
    """
    client = WorkerAPIClient(api_url, api_key)
    worker = ReencodeWorker(
        client=client,
        work_dir=Path(work_dir),
        videos_dir=Path(videos_dir) if videos_dir else Path(VIDEOS_DIR),
        poll_interval=poll_interval,
    )

    try:
        await worker.start()
    finally:
        worker.stop()
        await client.close()
