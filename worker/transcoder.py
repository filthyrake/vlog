#!/usr/bin/env python3
"""
Video transcoding worker.
Monitors the database for pending videos and transcodes them to HLS.
"""
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import VIDEOS_DIR, UPLOADS_DIR, QUALITY_PRESETS, HLS_SEGMENT_DURATION
from api.database import database, videos, video_qualities


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

    return {
        "width": int(video_stream.get("width", 0)),
        "height": int(video_stream.get("height", 0)),
        "duration": float(data.get("format", {}).get("duration", 0)),
        "codec": video_stream.get("codec_name", "unknown"),
    }


def get_applicable_qualities(source_height: int) -> list:
    """Get quality presets that are <= source resolution."""
    return [q for q in QUALITY_PRESETS if q["height"] <= source_height]


def get_output_dimensions(segment_path: Path) -> tuple:
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
    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-ss", str(timestamp),
        "-vframes", "1",
        "-vf", "scale=640:-1",
        str(output_path)
    ]
    subprocess.run(cmd, capture_output=True, check=True)


def transcode_to_hls(input_path: Path, output_dir: Path, qualities: list) -> list:
    """
    Transcode video to HLS with multiple quality variants.
    Returns list of generated quality info.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    generated = []
    variant_playlists = []

    for quality in qualities:
        name = quality["name"]
        height = quality["height"]
        bitrate = quality["bitrate"]
        audio_bitrate = quality["audio_bitrate"]

        playlist_name = f"{name}.m3u8"
        segment_pattern = f"{name}_%04d.ts"

        # Calculate width maintaining aspect ratio (must be divisible by 2)
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
            "-f", "hls",
            str(output_dir / playlist_name)
        ]

        print(f"  Transcoding {name}...")
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            print(f"  Warning: Failed to transcode {name}: {result.stderr[:200]}")
            continue

        # Get actual resolution from the transcoded output
        first_segment = output_dir / f"{name}_0000.ts"
        actual_width, actual_height = (0, 0)
        
        if first_segment.exists():
            actual_width, actual_height = get_output_dimensions(first_segment)
        
        # Fallback to 16:9 if we couldn't get dimensions
        if actual_width == 0 or actual_height == 0:
            actual_width = int(height * 16 / 9)
            if actual_width % 2 != 0:
                actual_width += 1
            actual_height = height

        variant_playlists.append({
            "name": name,
            "width": actual_width,
            "height": actual_height,
            "bitrate": int(bitrate.replace("k", "")) * 1000,
            "playlist": playlist_name,
        })

        generated.append({
            "quality": name,
            "width": actual_width,
            "height": actual_height,
            "bitrate": int(bitrate.replace("k", "")),
        })

    # Generate master playlist
    master_content = "#EXTM3U\n#EXT-X-VERSION:3\n\n"
    for variant in variant_playlists:
        bandwidth = variant["bitrate"]
        resolution_width = variant["width"]
        resolution_height = variant["height"]

        master_content += f'#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},RESOLUTION={resolution_width}x{resolution_height}\n'
        master_content += f'{variant["playlist"]}\n'

    (output_dir / "master.m3u8").write_text(master_content)

    return generated


async def process_video(video_id: int, video_slug: str):
    """Process a single video."""
    print(f"Processing video: {video_slug} (id={video_id})")

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
        return

    try:
        # Get video info
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

        # Determine output directory
        output_dir = VIDEOS_DIR / video_slug

        # Generate thumbnail
        print("  Generating thumbnail...")
        thumbnail_time = min(5.0, info["duration"] / 4)
        generate_thumbnail(source_file, output_dir / "thumbnail.jpg", thumbnail_time)

        # Get applicable qualities
        qualities = get_applicable_qualities(info["height"])
        if not qualities:
            # Source is very low res, use lowest preset anyway
            qualities = [QUALITY_PRESETS[-1]]

        print(f"  Transcoding to: {[q['name'] for q in qualities]}")

        # Transcode
        generated = transcode_to_hls(source_file, output_dir, qualities)

        if not generated:
            raise RuntimeError("No quality variants were successfully transcoded")

        # Save quality info to database
        for q in generated:
            await database.execute(
                video_qualities.insert().values(
                    video_id=video_id,
                    quality=q["quality"],
                    width=q["width"],
                    height=q["height"],
                    bitrate=q["bitrate"],
                )
            )

        # Mark as ready
        await database.execute(
            videos.update().where(videos.c.id == video_id).values(
                status="ready",
                published_at=datetime.utcnow(),
            )
        )

        # Clean up source file
        source_file.unlink()
        print(f"  Done! Video is ready.")

    except Exception as e:
        print(f"  Error: {e}")
        await database.execute(
            videos.update().where(videos.c.id == video_id).values(
                status="failed",
                error_message=str(e)[:500],
            )
        )


async def worker_loop():
    """Main worker loop - check for pending videos and process them."""
    await database.connect()
    print("Transcoding worker started. Watching for new videos...")

    try:
        while True:
            # Find pending videos
            query = videos.select().where(videos.c.status == "pending").order_by(videos.c.created_at)
            pending = await database.fetch_all(query)

            for video in pending:
                await process_video(video["id"], video["slug"])

            # Wait before checking again
            await asyncio.sleep(5)

    except KeyboardInterrupt:
        print("\nWorker stopped.")
    finally:
        await database.disconnect()


if __name__ == "__main__":
    asyncio.run(worker_loop())
