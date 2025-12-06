#!/usr/bin/env python3
"""
Video transcription worker using faster-whisper.
Monitors the database for videos needing transcription and generates WebVTT captions.
"""

import asyncio
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from api.database import configure_sqlite_pragmas, database, transcriptions
from api.enums import TranscriptionStatus
from config import (
    AUDIO_EXTRACTION_TIMEOUT,
    SUPPORTED_VIDEO_EXTENSIONS,
    TRANSCRIPTION_COMPUTE_TYPE,
    TRANSCRIPTION_ENABLED,
    TRANSCRIPTION_LANGUAGE,
    TRANSCRIPTION_TIMEOUT,
    UPLOADS_DIR,
    VIDEOS_DIR,
    WHISPER_MODEL,
)


def format_timestamp(seconds: float) -> str:
    """Format seconds as WebVTT timestamp (HH:MM:SS.mmm)."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


def generate_webvtt(segments: List[dict]) -> str:
    """Convert Whisper segments to WebVTT format."""
    vtt = "WEBVTT\n\n"

    for i, segment in enumerate(segments):
        start = format_timestamp(segment["start"])
        end = format_timestamp(segment["end"])
        text = segment["text"].strip()

        vtt += f"{i + 1}\n"
        vtt += f"{start} --> {end}\n"
        vtt += f"{text}\n\n"

    return vtt


class TranscriptionWorker:
    def __init__(self):
        self.model = None
        self.model_loaded = False

    def load_model(self):
        """Load the Whisper model (lazy loading to save memory)."""
        if self.model_loaded:
            return

        print(f"Loading Whisper model: {WHISPER_MODEL}...")
        try:
            from faster_whisper import WhisperModel

            # Use CPU by default, GPU if available
            self.model = WhisperModel(
                WHISPER_MODEL,
                device="cpu",
                compute_type=TRANSCRIPTION_COMPUTE_TYPE,
            )
            self.model_loaded = True
            print("Model loaded successfully")
        except Exception as e:
            print(f"Failed to load model: {e}")
            raise

    def transcribe(self, audio_path: Path, language: Optional[str] = None) -> dict:
        """
        Transcribe audio/video file using Whisper.
        Returns dict with text, language, and segments.
        """
        if not self.model_loaded:
            self.load_model()

        print(f"  Transcribing: {audio_path.name}")

        # Use specified language or auto-detect
        lang = language or TRANSCRIPTION_LANGUAGE

        segments, info = self.model.transcribe(
            str(audio_path),
            language=lang,
            task="transcribe",
            beam_size=5,
            vad_filter=True,  # Filter out non-speech
        )

        # Collect all segments
        segment_list = []
        full_text_parts = []

        for segment in segments:
            segment_list.append(
                {
                    "start": segment.start,
                    "end": segment.end,
                    "text": segment.text,
                }
            )
            full_text_parts.append(segment.text.strip())

        full_text = " ".join(full_text_parts)

        return {
            "text": full_text,
            "language": info.language,
            "segments": segment_list,
        }


async def get_or_create_transcription(video_id: int) -> dict:
    """Get existing transcription or create a new pending one."""
    query = transcriptions.select().where(transcriptions.c.video_id == video_id)
    row = await database.fetch_one(query)

    if row:
        return dict(row)

    # Create new transcription record
    result = await database.execute(
        transcriptions.insert().values(
            video_id=video_id,
            status=TranscriptionStatus.PENDING,
        )
    )

    query = transcriptions.select().where(transcriptions.c.id == result)
    return dict(await database.fetch_one(query))


async def update_transcription_status(transcription_id: int, status: str, **kwargs):
    """Update transcription status and optional fields."""
    values = {"status": status, **kwargs}
    await database.execute(transcriptions.update().where(transcriptions.c.id == transcription_id).values(**values))


async def get_videos_needing_transcription() -> List[dict]:
    """
    Find videos that need transcription:
    - Video status is 'ready'
    - No transcription record exists OR transcription status is 'pending'
    """
    # Videos with pending transcription
    query = """
        SELECT v.id, v.slug, v.title, t.id as transcription_id, t.status as transcription_status
        FROM videos v
        LEFT JOIN transcriptions t ON v.id = t.video_id
        WHERE v.status = 'ready'
        AND (t.id IS NULL OR t.status = 'pending')
        ORDER BY v.published_at DESC
    """
    import sqlalchemy as sa

    rows = await database.fetch_all(sa.text(query))
    return [dict(row) for row in rows]


def _extract_quality(filename_stem: str) -> int:
    """
    Extract quality number from filename like '1080p' or '720p'.
    Returns 0 if no valid quality found.

    Examples:
        '1080p' -> 1080
        '720p' -> 720
        'master' -> 0
        'backup' -> 0 (ends with 'p' but not a quality)
    """
    try:
        # Only parse if ends with 'p' and the rest is all digits
        if filename_stem.endswith("p") and len(filename_stem) > 1:
            quality_str = filename_stem[:-1]
            if quality_str.isdigit():
                return int(quality_str)
        return 0
    except (ValueError, AttributeError):
        return 0


def find_audio_source(video_id: int, video_slug: str) -> Path:
    """
    Find the best audio source for transcription.

    Priority:
    1. Original upload file (best quality, most reliable)
    2. Highest quality HLS playlist (fallback)

    Args:
        video_id: Database ID of the video
        video_slug: URL slug of the video

    Returns:
        Path to audio source file

    Raises:
        ValueError: If no audio source found
    """
    # Try 1: Find original upload file (preferred)
    # The transcoder saves uploads as {video_id}{extension}
    for ext in SUPPORTED_VIDEO_EXTENSIONS:
        source = UPLOADS_DIR / f"{video_id}{ext}"
        if source.exists() and source.stat().st_size > 0:
            return source

    # Try 2: Fall back to highest quality HLS playlist
    video_dir = VIDEOS_DIR / video_slug

    if not video_dir.exists():
        raise ValueError(f"Video directory not found: {video_dir}")

    # Find all quality playlists (e.g., "1080p.m3u8", "720p.m3u8")
    playlists = sorted(
        video_dir.glob("*p.m3u8"),
        key=lambda p: _extract_quality(p.stem),
        reverse=True,  # Highest quality first
    )

    if not playlists:
        raise ValueError(
            f"No audio source found for video {video_slug} (ID: {video_id}). "
            f"Upload file missing and no HLS playlists available."
        )

    # Validate that the best playlist is readable
    best_playlist = playlists[0]
    if not best_playlist.exists():
        raise ValueError(f"Best quality playlist disappeared: {best_playlist}")
    if best_playlist.stat().st_size == 0:
        raise ValueError(f"Best quality playlist is empty: {best_playlist}")

    return best_playlist


def extract_audio_to_wav(source_path: Path, output_path: Path) -> None:
    """
    Extract audio from video/HLS source to WAV file using ffmpeg.
    This provides more reliable input for Whisper than streaming from HLS.

    Args:
        source_path: Source video or HLS playlist
        output_path: Output WAV file path

    Raises:
        RuntimeError: If extraction fails
    """
    # Validate paths exist/are writable
    if not source_path.exists():
        raise RuntimeError(f"Source file does not exist: {source_path}")

    # Convert paths to strings (subprocess accepts str or Path)
    cmd = [
        "ffmpeg",
        "-i",
        str(source_path),
        "-vn",  # No video
        "-acodec",
        "pcm_s16le",  # PCM 16-bit WAV
        "-ar",
        "16000",  # 16kHz sample rate (Whisper's preferred rate)
        "-ac",
        "1",  # Mono
        "-y",  # Overwrite output file
        str(output_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=AUDIO_EXTRACTION_TIMEOUT)

        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg audio extraction failed: {result.stderr}")
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Audio extraction timed out after {AUDIO_EXTRACTION_TIMEOUT} seconds")
    except FileNotFoundError:
        raise RuntimeError("ffmpeg not found - required for audio extraction")


async def process_transcription(video: dict, worker: TranscriptionWorker):
    """Process transcription for a single video."""
    video_id = video["id"]
    slug = video["slug"]
    title = video["title"]

    print(f"Processing transcription for: {title} ({slug})")

    # Get or create transcription record
    transcription = await get_or_create_transcription(video_id)
    transcription_id = transcription["id"]

    start_time = time.time()
    temp_wav = None

    try:
        # Update status to processing
        await update_transcription_status(
            transcription_id,
            TranscriptionStatus.PROCESSING,
            started_at=datetime.now(timezone.utc),
        )

        # Find audio source
        audio_source = find_audio_source(video_id, slug)
        print(f"  Using audio source: {audio_source}")

        # Extract audio to temporary WAV file for reliable processing
        # This avoids potential issues with streaming HLS or complex video formats
        # Use mkstemp for explicit control over file creation and cleanup
        fd, temp_wav_path = tempfile.mkstemp(suffix=".wav", prefix="vlog_transcribe_")
        temp_wav = Path(temp_wav_path)  # Assign before close
        os.close(fd)  # Close file descriptor, we'll use the path

        print("  Extracting audio to WAV...")
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, extract_audio_to_wav, audio_source, temp_wav)

        if not temp_wav.exists() or temp_wav.stat().st_size == 0:
            raise RuntimeError("Audio extraction produced empty file")

        # Run transcription with timeout (this is CPU-intensive and blocking)
        # We run it in the default executor to not block the event loop
        print("  Running Whisper transcription...")
        result = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                worker.transcribe,
                temp_wav,
                None,  # language
            ),
            timeout=TRANSCRIPTION_TIMEOUT,
        )

        # Generate WebVTT
        vtt_content = generate_webvtt(result["segments"])

        # Save WebVTT file
        vtt_path = VIDEOS_DIR / slug / "captions.vtt"
        vtt_path.write_text(vtt_content, encoding="utf-8")
        print(f"  Saved captions to: {vtt_path}")

        # Calculate stats
        duration = time.time() - start_time
        word_count = len(result["text"].split())

        # Update database
        await update_transcription_status(
            transcription_id,
            TranscriptionStatus.COMPLETED,
            completed_at=datetime.now(timezone.utc),
            duration_seconds=duration,
            transcript_text=result["text"],
            language=result["language"],
            vtt_path=str(vtt_path),
            word_count=word_count,
        )

        print(f"  Completed in {duration:.1f}s ({word_count} words, language: {result['language']})")

    except asyncio.TimeoutError:
        error_msg = f"Transcription timed out after {TRANSCRIPTION_TIMEOUT} seconds"
        print(f"  Error: {error_msg}")
        await update_transcription_status(
            transcription_id,
            TranscriptionStatus.FAILED,
            error_message=error_msg,
        )
    except Exception as e:
        error_msg = str(e)[:500]
        print(f"  Error: {error_msg}")
        await update_transcription_status(
            transcription_id,
            TranscriptionStatus.FAILED,
            error_message=error_msg,
        )
    finally:
        # Clean up temporary WAV file
        if temp_wav and temp_wav.exists():
            try:
                temp_wav.unlink()
            except Exception as e:
                print(f"  Warning: Failed to delete temp file {temp_wav}: {e}")


async def worker_loop():
    """Main transcription worker loop."""
    if not TRANSCRIPTION_ENABLED:
        print("Transcription is disabled in config. Exiting.")
        return

    await database.connect()
    await configure_sqlite_pragmas()
    print("Transcription worker started")
    print(f"Model: {WHISPER_MODEL}, Compute type: {TRANSCRIPTION_COMPUTE_TYPE}")
    print("Watching for videos needing transcription...")

    worker = TranscriptionWorker()

    try:
        while True:
            # Find videos needing transcription
            videos_to_process = await get_videos_needing_transcription()

            if videos_to_process:
                print(f"Found {len(videos_to_process)} video(s) needing transcription")

                for video in videos_to_process:
                    await process_transcription(video, worker)

            # Wait before checking again
            await asyncio.sleep(30)

    except KeyboardInterrupt:
        print("\nTranscription worker stopped.")
    finally:
        await database.disconnect()


if __name__ == "__main__":
    asyncio.run(worker_loop())
