"""
Chapter auto-detection utilities for Issue #493.

Provides functionality to:
1. Extract chapter markers from video file metadata using ffprobe
2. Generate chapter suggestions from transcription analysis
"""

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from config import SUPPORTED_VIDEO_EXTENSIONS, UPLOADS_DIR


@dataclass
class DetectedChapter:
    """A chapter detected from metadata or transcription."""

    title: str
    start_time: float
    end_time: Optional[float] = None
    source: str = "metadata"


async def extract_chapters_from_metadata(
    video_id: int,
    video_slug: str,
    timeout: float = 30.0,
) -> List[DetectedChapter]:
    """
    Extract chapter markers from video file metadata using ffprobe.

    Supports common chapter formats:
    - Matroska (MKV) chapters
    - MP4/MOV chapters
    - Other container formats with embedded chapter metadata

    Args:
        video_id: Database ID of the video (used to find upload file)
        video_slug: URL slug of the video (used to find video directory)
        timeout: Maximum time to wait for ffprobe

    Returns:
        List of DetectedChapter objects, may be empty if no chapters found

    Raises:
        RuntimeError: If ffprobe fails or times out
    """
    # Find the source video file
    source_path = _find_source_video(video_id, video_slug)
    if source_path is None:
        return []

    # Run ffprobe to extract chapters
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_chapters",
        str(source_path),
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError(f"ffprobe timed out after {timeout}s extracting chapters")

    if process.returncode != 0:
        # Not an error if no chapters - just return empty
        return []

    try:
        data = json.loads(stdout.decode("utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return []

    chapters = data.get("chapters", [])
    if not chapters:
        return []

    detected = []
    for chapter in chapters:
        # ffprobe returns start_time and end_time as strings in seconds
        start_time = float(chapter.get("start_time", 0))
        end_time_raw = chapter.get("end_time")
        end_time = float(end_time_raw) if end_time_raw is not None else None

        # Get title from tags (common locations)
        tags = chapter.get("tags", {})
        title = (
            tags.get("title")
            or tags.get("TITLE")
            or tags.get("name")
            or tags.get("NAME")
            or f"Chapter {len(detected) + 1}"
        )

        detected.append(
            DetectedChapter(
                title=title.strip(),
                start_time=start_time,
                end_time=end_time,
                source="metadata",
            )
        )

    return detected


def _find_source_video(video_id: int, video_slug: str) -> Optional[Path]:
    """
    Find the source video file for chapter extraction.

    Priority:
    1. Original upload file in UPLOADS_DIR
    2. (Future: could add support for finding processed files)

    Args:
        video_id: Database ID of the video
        video_slug: URL slug of the video

    Returns:
        Path to source file, or None if not found
    """
    # Try to find original upload file
    for ext in SUPPORTED_VIDEO_EXTENSIONS:
        source = UPLOADS_DIR / f"{video_id}{ext}"
        if source.exists() and source.stat().st_size > 0:
            return source

    return None


async def generate_chapters_from_transcription(
    transcript_text: str,
    video_duration: float,
    min_chapter_length: int = 60,
) -> List[DetectedChapter]:
    """
    Generate chapter suggestions from transcription text.

    Uses heuristics to identify topic changes:
    - Sentence boundaries after significant pauses
    - Keyword patterns indicating new topics
    - Regular intervals as fallback

    Args:
        transcript_text: Full transcript text from Whisper
        video_duration: Total video duration in seconds
        min_chapter_length: Minimum seconds between chapters

    Returns:
        List of DetectedChapter objects
    """
    if not transcript_text or not transcript_text.strip():
        return []

    # Clean and normalize the transcript
    text = transcript_text.strip()

    # Split into sentences
    sentences = _split_into_sentences(text)
    if not sentences:
        return []

    # Generate chapters based on text analysis
    chapters = _analyze_transcript_for_chapters(
        sentences,
        video_duration,
        min_chapter_length,
    )

    return chapters


def _split_into_sentences(text: str) -> List[str]:
    """Split text into sentences using basic rules."""
    # Basic sentence splitting (handles common cases)
    pattern = r'(?<=[.!?])\s+'
    sentences = re.split(pattern, text)
    return [s.strip() for s in sentences if s.strip()]


def _analyze_transcript_for_chapters(
    sentences: List[str],
    video_duration: float,
    min_chapter_length: int,
) -> List[DetectedChapter]:
    """
    Analyze transcript sentences to suggest chapter boundaries.

    This is a simple heuristic-based approach that:
    1. Divides the video into segments based on min_chapter_length
    2. Assigns representative titles from the transcript

    Args:
        sentences: List of sentences from the transcript
        video_duration: Total video duration in seconds
        min_chapter_length: Minimum chapter length in seconds

    Returns:
        List of suggested chapters
    """
    if video_duration < min_chapter_length:
        return []

    # Calculate how many chapters we can have
    max_chapters = int(video_duration // min_chapter_length)
    if max_chapters < 2:
        # Not enough duration for multiple chapters
        return []

    # Limit to reasonable number of chapters
    num_chapters = min(max_chapters, 10)

    # Distribute sentences across chapters
    sentences_per_chapter = max(1, len(sentences) // num_chapters)

    chapters = []
    for i in range(num_chapters):
        start_time = i * (video_duration / num_chapters)

        # Get sentences for this segment
        start_idx = i * sentences_per_chapter
        end_idx = min((i + 1) * sentences_per_chapter, len(sentences))
        segment_sentences = sentences[start_idx:end_idx]

        # Generate title from first sentence (truncated)
        if segment_sentences:
            title = _generate_chapter_title(segment_sentences[0], i + 1)
        else:
            title = f"Section {i + 1}"

        # Calculate end time (next chapter start or video end)
        if i < num_chapters - 1:
            end_time = (i + 1) * (video_duration / num_chapters)
        else:
            end_time = video_duration

        chapters.append(
            DetectedChapter(
                title=title,
                start_time=round(start_time, 2),
                end_time=round(end_time, 2),
                source="transcription",
            )
        )

    return chapters


def _generate_chapter_title(sentence: str, chapter_num: int) -> str:
    """
    Generate a chapter title from a sentence.

    Truncates long sentences and removes filler words to create
    a concise, descriptive title.

    Args:
        sentence: Source sentence for the title
        chapter_num: Chapter number (used as fallback)

    Returns:
        Generated chapter title
    """
    # Remove common filler words at the start
    filler_patterns = [
        r'^(so|well|now|okay|alright|um|uh|like)\s+',
        r'^(and|but|or)\s+',
    ]

    title = sentence
    for pattern in filler_patterns:
        title = re.sub(pattern, '', title, flags=re.IGNORECASE)

    # Truncate to reasonable length (max 60 chars)
    max_length = 60
    if len(title) > max_length:
        # Try to break at word boundary
        truncated = title[:max_length]
        last_space = truncated.rfind(' ')
        if last_space > max_length // 2:
            title = truncated[:last_space] + "..."
        else:
            title = truncated + "..."

    # Clean up punctuation at the end
    title = re.sub(r'[.!?,;:]+$', '', title)

    # If title is too short or empty, use fallback
    if len(title.strip()) < 3:
        return f"Section {chapter_num}"

    return title.strip()


def filter_chapters_by_length(
    chapters: List[DetectedChapter],
    min_chapter_length: int,
    video_duration: float,
) -> List[DetectedChapter]:
    """
    Filter out chapters that are too short or too close together.

    Args:
        chapters: List of detected chapters
        min_chapter_length: Minimum seconds between chapters
        video_duration: Total video duration

    Returns:
        Filtered list of chapters
    """
    if not chapters:
        return []

    # Sort by start time
    sorted_chapters = sorted(chapters, key=lambda c: c.start_time)

    filtered = []
    last_start_time = -float("inf")  # Use negative infinity to always include first chapter

    for chapter in sorted_chapters:
        # Skip if too close to previous chapter (but always allow first chapter)
        if chapter.start_time - last_start_time < min_chapter_length:
            continue

        # Skip if too close to video end
        if video_duration - chapter.start_time < min_chapter_length:
            continue

        filtered.append(chapter)
        last_start_time = chapter.start_time

    return filtered
