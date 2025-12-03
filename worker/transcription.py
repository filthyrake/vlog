#!/usr/bin/env python3
"""
Video transcription worker using faster-whisper.
Monitors the database for videos needing transcription and generates WebVTT captions.
"""
import asyncio
import sys
import time
from pathlib import Path
from datetime import datetime
from typing import Optional, List

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import (
    VIDEOS_DIR,
    WHISPER_MODEL,
    TRANSCRIPTION_ENABLED,
    TRANSCRIPTION_LANGUAGE,
    TRANSCRIPTION_COMPUTE_TYPE,
)
from api.database import database, videos, transcriptions


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
            print(f"Model loaded successfully")
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
            segment_list.append({
                "start": segment.start,
                "end": segment.end,
                "text": segment.text,
            })
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
            status="pending",
        )
    )

    query = transcriptions.select().where(transcriptions.c.id == result)
    return dict(await database.fetch_one(query))


async def update_transcription_status(transcription_id: int, status: str, **kwargs):
    """Update transcription status and optional fields."""
    values = {"status": status, **kwargs}
    await database.execute(
        transcriptions.update()
        .where(transcriptions.c.id == transcription_id)
        .values(**values)
    )


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


async def find_audio_source(video_slug: str) -> Optional[Path]:
    """
    Find the best audio source for transcription.
    Prefers the highest quality HLS file or original upload.
    """
    video_dir = VIDEOS_DIR / video_slug

    if not video_dir.exists():
        return None

    # Try to find the highest quality m3u8 playlist and use its first segment
    # Or use a full concatenated version if available
    # For simplicity, we'll look for the highest quality TS segment pattern

    # Find all quality playlists
    playlists = list(video_dir.glob("*p.m3u8"))
    if playlists:
        # Sort by quality (parse the number from filename like "1080p.m3u8")
        def get_quality(p):
            try:
                return int(p.stem.replace("p", ""))
            except ValueError:
                return 0

        playlists.sort(key=get_quality, reverse=True)

        # Use the highest quality playlist
        best_playlist = playlists[0]

        # For faster-whisper, we can point directly to the m3u8 playlist
        # or extract audio from a few segments. Let's use the first approach
        # since ffmpeg (used internally) can handle HLS playlists
        return best_playlist

    return None


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

    try:
        # Update status to processing
        await update_transcription_status(
            transcription_id,
            "processing",
            started_at=datetime.utcnow(),
        )

        # Find audio source
        audio_source = await find_audio_source(slug)
        if not audio_source:
            raise RuntimeError(f"No audio source found for video {slug}")

        print(f"  Using audio source: {audio_source}")

        # Run transcription (this is CPU-intensive and blocking)
        # We run it in the default executor to not block the event loop
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            worker.transcribe,
            audio_source,
            None  # language
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
            "completed",
            completed_at=datetime.utcnow(),
            duration_seconds=duration,
            transcript_text=result["text"],
            language=result["language"],
            vtt_path=str(vtt_path),
            word_count=word_count,
        )

        print(f"  Completed in {duration:.1f}s ({word_count} words, language: {result['language']})")

    except Exception as e:
        error_msg = str(e)[:500]
        print(f"  Error: {error_msg}")
        await update_transcription_status(
            transcription_id,
            "failed",
            error_message=error_msg,
        )


async def worker_loop():
    """Main transcription worker loop."""
    if not TRANSCRIPTION_ENABLED:
        print("Transcription is disabled in config. Exiting.")
        return

    await database.connect()
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
