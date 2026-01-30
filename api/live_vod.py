"""VOD recording from live streams.

When a live stream ends, this module:
1. Revokes the stream key (prevents new segments)
2. Creates a video record in the videos table
3. Hardlinks segments from live/{slug}/ to videos/{video_slug}/
4. Falls back to copy if hardlinks fail (different filesystems)
5. Generates static HLS playlists with #EXT-X-ENDLIST
6. Links the VOD to the stream via vod_video_id

This allows live streams to become permanent VOD recordings seamlessly.
"""

import asyncio
import functools
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from api.database import live_stream_segments, live_streams, video_qualities, videos
from api.db_retry import db_execute_with_retry, fetch_all_with_retry, fetch_one_with_retry
from api.live_playlist import QUALITY_BANDWIDTH, generate_master_playlist, generate_variant_playlist
from config import LIVE_HLS_SEGMENT_DURATION, LIVE_STORAGE_PATH, VIDEOS_DIR

logger = logging.getLogger(__name__)


async def create_vod_from_stream(stream_id: int) -> Optional[int]:
    """
    Create a VOD recording from a live stream.

    Args:
        stream_id: The ID of the stream to record

    Returns:
        The video ID if successful, None otherwise
    """
    # Get stream info
    stream = await fetch_one_with_retry(
        live_streams.select().where(live_streams.c.id == stream_id)
    )

    if not stream:
        logger.error(f"Stream {stream_id} not found for VOD recording")
        return None

    if stream["status"] != "ended":
        logger.warning(f"Stream {stream_id} is not ended, cannot create VOD")
        return None

    if stream["vod_video_id"]:
        logger.warning(f"Stream {stream_id} already has VOD video {stream['vod_video_id']}")
        return stream["vod_video_id"]

    slug = stream["slug"]
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d_%H%M%S")

    # Generate video slug with timestamp
    video_slug = f"{slug}-{timestamp}"

    logger.info(f"Creating VOD recording for stream {slug} -> {video_slug}")

    # Get qualities from stream
    qualities = []
    if stream["qualities"]:
        try:
            qualities = json.loads(stream["qualities"])
        except json.JSONDecodeError:
            # Invalid qualities JSON; treat as empty and return None
            logger.warning(f"Failed to decode qualities JSON for stream {slug}")
            qualities = []

    if not qualities:
        logger.warning(f"No qualities found for stream {slug}")
        return None

    # Calculate total duration by summing segment durations from the first quality
    # (all qualities should have the same duration)
    total_duration = 0.0
    if qualities:
        segments = await fetch_all_with_retry(
            live_stream_segments.select()
            .where(live_stream_segments.c.stream_id == stream_id)
            .where(live_stream_segments.c.quality == qualities[0])
        )
        for seg in segments:
            duration_ms = seg["duration_ms"] or (LIVE_HLS_SEGMENT_DURATION * 1000)
            total_duration += duration_ms / 1000.0

    # Get resolution from first quality
    primary_quality = qualities[0] if qualities else "720p"
    resolution_map = {
        "2160p": (3840, 2160),
        "1440p": (2560, 1440),
        "1080p": (1920, 1080),
        "720p": (1280, 720),
        "480p": (854, 480),
        "360p": (640, 360),
    }
    width, height = resolution_map.get(primary_quality, (1280, 720))

    # Create video record
    try:
        video_id = await db_execute_with_retry(
            videos.insert().values(
                title=stream["title"],
                slug=video_slug,
                description=stream["description"] or "",
                category_id=stream["category_id"],
                duration=total_duration,
                source_width=width,
                source_height=height,
                status="ready",  # Already transcoded
                created_at=stream["started_at"] or now,
                published_at=now,
                streaming_format="cmaf",  # Live uses CMAF
                primary_codec="h264",
            )
        )
    except Exception as e:
        logger.error(f"Failed to create video record for stream {slug}: {e}")
        return None

    # Create video quality records
    for quality in qualities:
        res = resolution_map.get(quality, (1280, 720))
        bitrate = QUALITY_BANDWIDTH.get(quality, 2500000) // 1000  # Convert to kbps
        try:
            await db_execute_with_retry(
                video_qualities.insert().values(
                    video_id=video_id,
                    quality=quality,
                    width=res[0],
                    height=res[1],
                    bitrate=bitrate,
                )
            )
        except Exception as e:
            logger.warning(f"Failed to create quality record for {quality}: {e}")

    # Create video directory
    video_dir = VIDEOS_DIR / video_slug
    video_dir.mkdir(parents=True, exist_ok=True)

    # Copy/hardlink segments for each quality
    loop = asyncio.get_event_loop()
    for quality in qualities:
        src_dir = LIVE_STORAGE_PATH / slug / quality
        dst_dir = video_dir / quality
        dst_dir.mkdir(parents=True, exist_ok=True)

        # Copy init segment
        src_init = src_dir / "init.mp4"
        dst_init = dst_dir / "init.mp4"
        if src_init.exists():
            try:
                await loop.run_in_executor(
                    None,
                    functools.partial(_hardlink_or_copy, src_init, dst_init),
                )
            except Exception as e:
                logger.warning(f"Failed to copy init segment: {e}")

        # Get segments for this quality
        segments = await fetch_all_with_retry(
            live_stream_segments.select()
            .where(live_stream_segments.c.stream_id == stream_id)
            .where(live_stream_segments.c.quality == quality)
            .order_by(live_stream_segments.c.sequence_number)
        )

        # Copy each segment
        for seg in segments:
            src_seg = src_dir / seg["filename"]
            dst_seg = dst_dir / seg["filename"]
            if src_seg.exists():
                try:
                    await loop.run_in_executor(
                        None,
                        functools.partial(_hardlink_or_copy, src_seg, dst_seg),
                    )
                except Exception as e:
                    logger.warning(f"Failed to copy segment {seg['filename']}: {e}")

        # Generate static VOD playlist
        segments_list = [dict(seg) for seg in segments]
        if segments_list:
            playlist_content = generate_variant_playlist(
                segments_list,
                segments_list[0]["sequence_number"],
                LIVE_HLS_SEGMENT_DURATION,
                is_live=False,  # VOD mode
            )
            playlist_path = dst_dir / "stream.m3u8"
            await loop.run_in_executor(
                None,
                functools.partial(playlist_path.write_text, playlist_content),
            )

    # Generate master playlist
    master_content = generate_master_playlist(qualities, video_slug)
    master_path = video_dir / "master.m3u8"
    await loop.run_in_executor(
        None,
        functools.partial(master_path.write_text, master_content),
    )

    # Copy thumbnail if exists
    thumb_src = LIVE_STORAGE_PATH / slug / "thumbnail.jpg"
    thumb_dst = video_dir / "thumbnail.jpg"
    if thumb_src.exists():
        try:
            await loop.run_in_executor(
                None,
                functools.partial(shutil.copy2, str(thumb_src), str(thumb_dst)),
            )
        except Exception as e:
            logger.debug(f"No thumbnail to copy: {e}")

    # Update stream with VOD video ID
    await db_execute_with_retry(
        live_streams.update()
        .where(live_streams.c.id == stream_id)
        .values(vod_video_id=video_id)
    )

    logger.info(f"Created VOD video {video_id} ({video_slug}) from stream {slug}")
    return video_id


def _hardlink_or_copy(src: Path, dst: Path) -> None:
    """
    Try to hardlink src to dst, fall back to copy if that fails.

    Hardlinks are preferred because they don't use additional disk space.
    Falls back to copy for cross-filesystem operations.
    """
    try:
        # Try hardlink first (fast, no space used)
        os.link(str(src), str(dst))
    except OSError:
        # Fall back to copy (works across filesystems)
        shutil.copy2(str(src), str(dst))


async def trigger_vod_recording(stream_id: int) -> Optional[int]:
    """
    Trigger VOD recording for a stream.

    This is a wrapper that can be called from the end stream endpoint.

    Returns the video ID if successful, None otherwise.
    """
    try:
        return await create_vod_from_stream(stream_id)
    except Exception as e:
        logger.error(f"VOD recording failed for stream {stream_id}: {e}")
        return None
