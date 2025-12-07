#!/usr/bin/env python3
"""
Remote transcoding worker with HTTP file transfer.

Designed for containerized deployment (Docker/Kubernetes).
Downloads source files from the Worker API, transcodes locally,
and uploads HLS output back to the server.

Run with: python -m worker.remote_transcoder
Requires: VLOG_WORKER_API_KEY environment variable

Environment variables:
    VLOG_WORKER_API_URL: Worker API URL (default: http://localhost:9002)
    VLOG_WORKER_API_KEY: Worker API key (required)
    VLOG_WORKER_HEARTBEAT_INTERVAL: Heartbeat interval in seconds (default: 30)
    VLOG_WORKER_POLL_INTERVAL: Job poll interval in seconds (default: 10)
    VLOG_WORKER_WORK_DIR: Working directory for downloads (default: /tmp/vlog-worker)
    VLOG_HWACCEL_TYPE: Hardware acceleration type (auto, nvidia, intel, none)
    VLOG_HWACCEL_PREFERRED_CODEC: Preferred codec (h264, hevc, av1)
"""

import asyncio
import shutil
import signal
import sys
from typing import List, Optional

from config import (
    QUALITY_PRESETS,
    WORKER_API_KEY,
    WORKER_API_URL,
    WORKER_HEARTBEAT_INTERVAL,
    WORKER_POLL_INTERVAL,
    WORKER_WORK_DIR,
)
from worker.http_client import WorkerAPIClient, WorkerAPIError
from worker.hwaccel import GPUCapabilities, detect_gpu_capabilities, get_worker_capabilities
from worker.transcoder import (
    create_original_quality,
    generate_master_playlist,
    generate_thumbnail,
    get_applicable_qualities,
    get_output_dimensions,
    get_video_info,
    transcode_quality_with_progress,
    validate_hls_playlist,
)

# Global shutdown flag
shutdown_requested = False

# Global GPU capabilities (detected at startup)
GPU_CAPS: Optional[GPUCapabilities] = None

# Error messages
CLAIM_EXPIRED_ERROR = "Claim expired - job may have been reassigned to another worker"


class ClaimExpiredError(Exception):
    """Raised when a job claim has expired and the job may have been reassigned."""

    pass


def signal_handler(sig, frame):
    """Handle shutdown signals gracefully."""
    global shutdown_requested
    print("Shutdown signal received, finishing current job...")
    shutdown_requested = True


async def heartbeat_loop(client: WorkerAPIClient, state: dict):
    """Background task to send periodic heartbeats."""
    while not shutdown_requested:
        # Determine status based on whether we're processing a job
        status = "busy" if state.get("processing_job") else "idle"
        try:
            await client.heartbeat(status=status)
        except WorkerAPIError as e:
            print(f"Heartbeat failed: {e.message}")
        except Exception as e:
            print(f"Heartbeat error: {e}")
        await asyncio.sleep(WORKER_HEARTBEAT_INTERVAL)


async def process_job(client: WorkerAPIClient, job: dict) -> bool:
    """
    Process a claimed transcoding job.

    Args:
        client: Worker API client
        job: Job info from claim response

    Returns:
        True if successful, False otherwise
    """
    job_id = job["job_id"]
    video_id = job["video_id"]
    video_slug = job["video_slug"]
    source_filename = job.get("source_filename", f"{video_id}.mp4")

    print(f"Processing video: {video_slug} (job={job_id})")

    # Create work directories
    work_dir = WORKER_WORK_DIR / str(job_id)
    work_dir.mkdir(parents=True, exist_ok=True)
    output_dir = work_dir / "output"
    output_dir.mkdir(exist_ok=True)

    source_path = work_dir / source_filename

    try:
        # Download source file
        print("  Downloading source file...")
        await client.update_progress(job_id, "download", 0)
        await client.download_source(video_id, source_path)
        await client.update_progress(job_id, "download", 5)

        # Probe video
        print("  Probing video info...")
        await client.update_progress(job_id, "probe", 5)
        info = await get_video_info(source_path)
        duration = info["duration"]
        source_width = info["width"]
        source_height = info["height"]
        print(f"    Source: {source_width}x{source_height}, {duration:.1f}s, codec={info['codec']}")

        # Update video metadata immediately after probing to prevent data loss if worker crashes
        await client.update_progress(
            job_id,
            "probe",
            8,
            duration=duration,
            source_width=source_width,
            source_height=source_height,
        )

        # Generate thumbnail
        print("  Generating thumbnail...")
        await client.update_progress(job_id, "thumbnail", 10)
        thumb_path = output_dir / "thumbnail.jpg"
        thumbnail_time = min(5.0, duration / 4)
        await generate_thumbnail(source_path, thumb_path, thumbnail_time)

        # Determine qualities
        qualities = get_applicable_qualities(source_height)
        if not qualities:
            # Use lowest quality if source is very small
            qualities = [QUALITY_PRESETS[-1]]

        quality_names = [q["name"] for q in qualities]
        print(f"  Transcoding to: original + {quality_names}")
        await client.update_progress(job_id, "transcode", 15)

        successful_qualities: List[dict] = []
        failed_qualities: List[str] = []
        total_qualities = len(qualities) + 1  # +1 for original

        # Initialize quality progress tracking
        quality_progress_list = [{"name": "original", "status": "pending", "progress": 0}]
        for q in qualities:
            quality_progress_list.append({"name": q["name"], "status": "pending", "progress": 0})

        # Create original quality (remux)
        print("    original: Remuxing...")
        quality_progress_list[0] = {"name": "original", "status": "in_progress", "progress": 0}
        await client.update_progress(job_id, "transcode", 15, quality_progress_list)

        success, error, quality_info = await create_original_quality(source_path, output_dir, duration, None)
        if success:
            # Get actual bitrate from quality_info
            bitrate_bps = quality_info.get("bitrate_bps", 0) if quality_info else 0
            successful_qualities.append(
                {
                    "name": "original",
                    "width": source_width,
                    "height": source_height,
                    "bitrate": bitrate_bps // 1000,  # Convert to kbps
                }
            )
            print("    original: Done")

            # Validate HLS playlist before upload (issue #166)
            playlist_path = output_dir / "original.m3u8"
            is_valid, validation_error = validate_hls_playlist(playlist_path)
            if not is_valid:
                print(f"    original: HLS validation failed - {validation_error}")
                quality_progress_list[0] = {"name": "original", "status": "failed", "progress": 0}
                failed_qualities.append("original")
            else:
                # Upload original quality immediately
                print("    original: Uploading...")
                try:
                    await client.upload_quality(video_id, "original", output_dir)
                    quality_progress_list[0] = {"name": "original", "status": "uploaded", "progress": 100}
                    print("    original: Uploaded")

                    # Delete local files to free disk space
                    playlist_file = output_dir / "original.m3u8"
                    if playlist_file.exists():
                        playlist_file.unlink()
                    for segment in output_dir.glob("original_*.ts"):
                        segment.unlink()
                    print("    original: Local files cleaned up")
                except WorkerAPIError as e:
                    quality_progress_list[0] = {"name": "original", "status": "completed", "progress": 100}
                    print(f"    original: Upload failed - {e.message}")
        else:
            quality_progress_list[0] = {"name": "original", "status": "failed", "progress": 0}
            failed_qualities.append("original")
            print(f"    original: Failed - {error}")

        # Transcode other qualities
        for idx, quality in enumerate(qualities):
            if shutdown_requested:
                raise Exception("Shutdown requested")

            quality_name = quality["name"]
            quality_idx = idx + 1  # +1 because original is at index 0

            # Calculate progress for this quality step
            progress_base = 15 + int((idx + 1) / total_qualities * 75)

            print(f"    {quality_name}: Transcoding...")
            quality_progress_list[quality_idx] = {"name": quality_name, "status": "in_progress", "progress": 0}
            await client.update_progress(job_id, "transcode", progress_base, quality_progress_list)

            # Define progress callback that updates the API
            last_update_time = [0.0]  # Use list to allow mutation in closure

            # Use default arguments to capture loop variables by value, not reference
            # (classic Python late binding closure fix)
            async def update_quality_progress(pct: int, qidx: int = quality_idx, qname: str = quality_name):
                import time

                now = time.time()
                # Only update every 5 seconds to avoid flooding the API
                if now - last_update_time[0] >= 5.0:
                    quality_progress_list[qidx] = {
                        "name": qname,
                        "status": "in_progress",
                        "progress": pct,
                    }
                    try:
                        await client.update_progress(job_id, "transcode", progress_base, quality_progress_list)
                        last_update_time[0] = now
                    except WorkerAPIError as e:
                        if e.status_code == 409:
                            # Claim expired - job may have been reassigned
                            print("      Claim expired - aborting job")
                            raise ClaimExpiredError(CLAIM_EXPIRED_ERROR)
                        print(f"      Progress update failed: {e.message}")
                    except Exception as e:
                        print(f"      Progress update failed: {e}")

            success, error = await transcode_quality_with_progress(
                source_path,
                output_dir,
                quality,
                duration,
                update_quality_progress,
                gpu_caps=GPU_CAPS,
            )

            if success:
                # Get actual dimensions from transcoded segment
                first_segment = output_dir / f"{quality_name}_0000.ts"
                if first_segment.exists():
                    actual_width, actual_height = await get_output_dimensions(first_segment)
                else:
                    # Estimate based on aspect ratio
                    actual_height = quality["height"]
                    actual_width = int(actual_height * source_width / source_height)
                    # Round to nearest even number (required for h264)
                    actual_width = actual_width + (actual_width % 2)

                successful_qualities.append(
                    {
                        "name": quality_name,
                        "width": actual_width,
                        "height": actual_height,
                        "bitrate": int(quality["bitrate"].replace("k", "")),
                    }
                )
                print(f"    {quality_name}: Done ({actual_width}x{actual_height})")

                # Validate HLS playlist before upload (issue #166)
                quality_playlist_path = output_dir / f"{quality_name}.m3u8"
                is_valid, validation_error = validate_hls_playlist(quality_playlist_path)
                if not is_valid:
                    print(f"    {quality_name}: HLS validation failed - {validation_error}")
                    quality_progress_list[quality_idx] = {"name": quality_name, "status": "failed", "progress": 0}
                    failed_qualities.append(quality_name)
                    # Remove the quality we just added since validation failed
                    if successful_qualities and successful_qualities[-1]["name"] == quality_name:
                        successful_qualities.pop()
                else:
                    # Upload this quality immediately to free disk space
                    print(f"    {quality_name}: Uploading...")
                    try:
                        await client.upload_quality(video_id, quality_name, output_dir)
                        quality_progress_list[quality_idx] = {
                            "name": quality_name,
                            "status": "uploaded",
                            "progress": 100,
                        }
                        print(f"    {quality_name}: Uploaded")

                        # Delete local files to free disk space
                        playlist_file = output_dir / f"{quality_name}.m3u8"
                        if playlist_file.exists():
                            playlist_file.unlink()
                        for segment in output_dir.glob(f"{quality_name}_*.ts"):
                            segment.unlink()
                        print(f"    {quality_name}: Local files cleaned up")
                    except WorkerAPIError as e:
                        # Upload failed - keep files, mark as completed (not uploaded)
                        quality_progress_list[quality_idx] = {
                            "name": quality_name,
                            "status": "completed",
                            "progress": 100,
                        }
                        print(f"    {quality_name}: Upload failed - {e.message}")
            else:
                quality_progress_list[quality_idx] = {"name": quality_name, "status": "failed", "progress": 0}
                failed_qualities.append(quality_name)
                print(f"    {quality_name}: Failed - {error}")

        # Check if we have any successful qualities
        if not successful_qualities:
            raise Exception(f"All quality variants failed: {', '.join(failed_qualities)}")

        # Generate master playlist
        print("  Generating master playlist...")
        await client.update_progress(job_id, "master_playlist", 95, quality_progress_list)

        # Convert successful_qualities to format expected by generate_master_playlist
        master_qualities = []
        for q in successful_qualities:
            mq = {
                "name": q["name"],
                "width": q["width"],
                "height": q["height"],
                "bitrate": f"{q['bitrate']}k" if q["name"] != "original" else "0k",
            }
            if q["name"] == "original":
                mq["is_original"] = True
                mq["bitrate_bps"] = q["bitrate"] * 1000
            master_qualities.append(mq)

        await generate_master_playlist(output_dir, master_qualities)

        # Validate master playlist before upload (issue #166)
        master_playlist_path = output_dir / "master.m3u8"
        if not master_playlist_path.exists():
            raise Exception("Master playlist was not generated")
        master_content = master_playlist_path.read_text()
        if not master_content.startswith("#EXTM3U"):
            raise Exception("Master playlist is malformed (missing #EXTM3U header)")
        if "#EXT-X-STREAM-INF" not in master_content:
            raise Exception("Master playlist is malformed (no stream variants)")

        # Upload finalize files (master.m3u8 + thumbnail.jpg)
        # Quality files were already uploaded incrementally after each transcode
        print("  Uploading master playlist and thumbnail...")
        await client.update_progress(job_id, "upload", 98, quality_progress_list)
        await client.upload_finalize(video_id, output_dir)
        print("  Finalize files uploaded")

        # Complete job
        print("  Marking job complete...")
        await client.complete_job(
            job_id,
            successful_qualities,
            duration=duration,
            source_width=source_width,
            source_height=source_height,
        )
        print(f"  Done! Video {video_slug} is ready.")

        if failed_qualities:
            print(f"  Note: Some qualities failed: {', '.join(failed_qualities)}")

        return True

    except ClaimExpiredError:
        # Claim expired - job may have been reassigned
        print(f"  {CLAIM_EXPIRED_ERROR}")
        # Don't report failure - the job may already be claimed by another worker
        return False

    except WorkerAPIError as e:
        # Handle claim expiration from API responses specially - don't retry
        if e.status_code == 409:
            print(f"  {CLAIM_EXPIRED_ERROR}")
            # Don't report failure - the job may already be claimed by another worker
            return False

        # Other API errors
        error_msg = f"API error: {e.message}"[:500]
        print(f"  Error: {error_msg}")
        try:
            await client.fail_job(job_id, error_msg, retry=True)
        except Exception as fail_e:
            print(f"  Failed to report error: {fail_e}")
        return False

    except Exception as e:
        error_msg = str(e)[:500]
        print(f"  Error: {error_msg}")
        try:
            await client.fail_job(job_id, error_msg, retry=True)
        except Exception as fail_e:
            print(f"  Failed to report error: {fail_e}")
        return False

    finally:
        # Cleanup work directory
        if work_dir.exists():
            shutil.rmtree(work_dir, ignore_errors=True)


async def worker_loop():
    """Main worker loop."""
    global shutdown_requested, GPU_CAPS

    # Set up signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Validate API key
    if not WORKER_API_KEY:
        print("ERROR: VLOG_WORKER_API_KEY environment variable required")
        print("Register a worker first: curl -X POST http://server:9002/api/worker/register")
        sys.exit(1)

    # Create work directory
    WORKER_WORK_DIR.mkdir(parents=True, exist_ok=True)

    client = WorkerAPIClient(WORKER_API_URL, WORKER_API_KEY)

    print("Remote transcoding worker starting...")
    print(f"  API URL: {WORKER_API_URL}")
    print(f"  Work dir: {WORKER_WORK_DIR}")
    print(f"  Heartbeat interval: {WORKER_HEARTBEAT_INTERVAL}s")
    print(f"  Poll interval: {WORKER_POLL_INTERVAL}s")

    # Detect GPU capabilities
    print("  Detecting GPU capabilities...")
    GPU_CAPS = await detect_gpu_capabilities()
    if GPU_CAPS:
        print(f"  GPU detected: {GPU_CAPS.device_name}")
        print(f"    Type: {GPU_CAPS.hwaccel_type.value}")
        encoders = [e.name for codec_encoders in GPU_CAPS.encoders.values() for e in codec_encoders]
        print(f"    Encoders: {encoders}")
        print(f"    Max sessions: {GPU_CAPS.max_concurrent_sessions}")
    else:
        print("  No GPU acceleration available, using CPU encoding")

    # Get worker capabilities for heartbeat
    worker_caps = await get_worker_capabilities(GPU_CAPS)

    # Verify connection with initial heartbeat (include capabilities)
    try:
        await client.heartbeat(status="idle", metadata={"capabilities": worker_caps})
        print("  Connected to Worker API")
    except WorkerAPIError as e:
        print(f"ERROR: Failed to connect to Worker API: {e.message}")
        sys.exit(1)

    # Create worker state for tracking job status
    worker_state = {"processing_job": None}

    # Start heartbeat background task
    heartbeat_task = asyncio.create_task(heartbeat_loop(client, worker_state))

    jobs_processed = 0
    jobs_failed = 0

    try:
        while not shutdown_requested:
            try:
                # Try to claim a job
                result = await client.claim_job()

                if result.get("job_id"):
                    # Mark that we're processing a job
                    worker_state["processing_job"] = result.get("job_id")

                    success = await process_job(client, result)

                    # Clear the processing state
                    worker_state["processing_job"] = None

                    if success:
                        jobs_processed += 1
                    else:
                        jobs_failed += 1
                else:
                    # No jobs available, wait before polling again
                    await asyncio.sleep(WORKER_POLL_INTERVAL)

            except WorkerAPIError as e:
                print(f"API error in worker loop: {e.message}")
                # Clear processing state on error
                worker_state["processing_job"] = None
                await asyncio.sleep(WORKER_POLL_INTERVAL)
            except Exception as e:
                print(f"Error in worker loop: {e}")
                # Clear processing state on error
                worker_state["processing_job"] = None
                await asyncio.sleep(WORKER_POLL_INTERVAL)

    finally:
        # Cancel heartbeat task
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass

        # Close HTTP client
        await client.close()

        print(f"Worker stopped. Jobs processed: {jobs_processed}, failed: {jobs_failed}")


def main():
    """Entry point for the remote transcoder."""
    asyncio.run(worker_loop())


if __name__ == "__main__":
    main()
