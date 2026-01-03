"""
Chapter auto-detection utilities for Issue #493.

Provides functionality to:
1. Extract chapter markers from video file metadata using ffprobe
2. Generate chapter suggestions from transcription analysis
"""

import asyncio
import html
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from config import SUPPORTED_VIDEO_EXTENSIONS, UPLOADS_DIR

logger = logging.getLogger(__name__)

# Constants for chapter detection
MAX_AUTO_GENERATED_CHAPTERS = 10  # Limit to prevent overwhelming UI
MAX_CHAPTER_TITLE_LENGTH = 255  # Database column limit
DISPLAY_TITLE_LENGTH = 60  # Truncate for display purposes
MIN_TITLE_LENGTH = 3  # Shorter than this is not descriptive
PROCESS_KILL_TIMEOUT = 5.0  # Seconds to wait for process to die after kill

# Pre-compiled regex patterns for performance
_SENTENCE_SPLIT_PATTERN = re.compile(r'(?<=[.!?])\s+')
_FILLER_PATTERNS = [
    re.compile(r'^(so|well|now|okay|alright|um|uh|like)\s+', re.IGNORECASE),
    re.compile(r'^(and|but|or)\s+', re.IGNORECASE),
]
_PUNCTUATION_END_PATTERN = re.compile(r'[.!?,;:]+$')


@dataclass
class InternalDetectedChapter:
    """
    A chapter detected from metadata or transcription.

    This is an internal representation used during detection.
    The API response uses the Pydantic DetectedChapter schema.
    """

    title: str
    start_time: float
    end_time: Optional[float] = None
    source: str = "metadata"


def _sanitize_chapter_title(title: str, chapter_num: int) -> str:
    """
    Sanitize a chapter title from untrusted sources (video metadata).

    - Escapes HTML entities to prevent XSS
    - Truncates to database column limit
    - Provides fallback for empty/invalid titles

    Args:
        title: Raw title from video metadata
        chapter_num: Chapter number for fallback title

    Returns:
        Sanitized title safe for storage and display
    """
    if not title or not title.strip():
        return f"Chapter {chapter_num}"

    # Strip whitespace and escape HTML entities
    sanitized = html.escape(title.strip())

    # Truncate to database limit
    if len(sanitized) > MAX_CHAPTER_TITLE_LENGTH:
        sanitized = sanitized[: MAX_CHAPTER_TITLE_LENGTH - 3] + "..."

    return sanitized


async def extract_chapters_from_metadata(
    video_id: int,
    timeout: float = 30.0,
) -> List[InternalDetectedChapter]:
    """
    Extract chapter markers from video file metadata using ffprobe.

    Supports common chapter formats:
    - Matroska (MKV) chapters
    - MP4/MOV chapters
    - Other container formats with embedded chapter metadata

    Args:
        video_id: Database ID of the video (used to find upload file)
        timeout: Maximum time to wait for ffprobe

    Returns:
        List of InternalDetectedChapter objects, may be empty if no chapters found

    Raises:
        RuntimeError: If ffprobe fails or times out
    """
    # Find the source video file
    source_path = _find_source_video(video_id)
    if source_path is None:
        return []

    # Run ffprobe to extract chapters
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
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
        try:
            # Wait for process to actually terminate with timeout
            await asyncio.wait_for(process.wait(), timeout=PROCESS_KILL_TIMEOUT)
        except asyncio.TimeoutError:
            logger.error(
                "ffprobe process for video %d would not terminate after kill signal",
                video_id,
            )
        raise RuntimeError(f"ffprobe timed out after {timeout}s extracting chapters")

    if process.returncode != 0:
        # ffprobe returns non-zero for files without chapter metadata.
        # This is expected for most files, not an error condition.
        return []

    try:
        ffprobe_output = json.loads(stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return []

    chapters_data = ffprobe_output.get("chapters", [])
    if not chapters_data:
        return []

    detected_chapters = []
    for chapter in chapters_data:
        # ffprobe returns start_time and end_time as strings in seconds
        try:
            start_time = float(chapter.get("start_time", 0))
            end_time_raw = chapter.get("end_time")
            end_time = float(end_time_raw) if end_time_raw is not None else None

            # Validate time values are reasonable
            if start_time < 0 or (end_time is not None and end_time <= start_time):
                continue
        except (ValueError, TypeError):
            # Skip chapters with invalid time values
            continue

        # Get title from tags (common locations)
        tags = chapter.get("tags", {})
        raw_title = (
            tags.get("title")
            or tags.get("TITLE")
            or tags.get("name")
            or tags.get("NAME")
            or ""
        )

        # Sanitize title from untrusted video metadata
        title = _sanitize_chapter_title(raw_title, len(detected_chapters) + 1)

        detected_chapters.append(
            InternalDetectedChapter(
                title=title,
                start_time=start_time,
                end_time=end_time,
                source="metadata",
            )
        )

    return detected_chapters


def _find_source_video(video_id: int) -> Optional[Path]:
    """
    Find the source video file for chapter extraction.

    Searches UPLOADS_DIR for the original upload file by video ID.

    Args:
        video_id: Database ID of the video

    Returns:
        Path to source file, or None if not found
    """
    for ext in SUPPORTED_VIDEO_EXTENSIONS:
        source = UPLOADS_DIR / f"{video_id}{ext}"
        if source.exists() and source.stat().st_size > 0:
            return source

    return None


async def generate_chapters_from_transcription(
    transcript_text: str,
    video_duration: float,
    min_chapter_length: int = 60,
) -> List[InternalDetectedChapter]:
    """
    Generate chapter suggestions from transcription text.

    Algorithm:
    1. Calculate how many chapters fit (video_duration / min_chapter_length)
    2. Cap at MAX_AUTO_GENERATED_CHAPTERS to avoid overwhelming the UI
    3. Divide video into equal time segments
    4. Distribute transcript sentences evenly across segments
    5. Use first sentence of each segment as chapter title (cleaned and truncated)

    This simple time-based approach works because most long-form content
    naturally divides into roughly equal sections.

    Args:
        transcript_text: Full transcript text from Whisper
        video_duration: Total video duration in seconds
        min_chapter_length: Minimum seconds between chapters

    Returns:
        List of InternalDetectedChapter objects
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
    detected_chapters = _analyze_transcript_for_chapters(
        sentences,
        video_duration,
        min_chapter_length,
    )

    return detected_chapters


def _split_into_sentences(text: str) -> List[str]:
    """
    Split text into sentences using punctuation boundaries.

    Splits on periods, exclamation marks, and question marks.
    Does NOT handle abbreviations, decimal numbers, or ellipses.
    This is intentionally simple for transcript text.

    Args:
        text: Text to split

    Returns:
        List of sentence strings
    """
    sentences = _SENTENCE_SPLIT_PATTERN.split(text)
    return [s.strip() for s in sentences if s.strip()]


def _analyze_transcript_for_chapters(
    sentences: List[str],
    video_duration: float,
    min_chapter_length: int,
) -> List[InternalDetectedChapter]:
    """
    Analyze transcript sentences to suggest chapter boundaries.

    Divides the video into equal time segments and assigns
    representative titles from the transcript sentences.

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
    num_chapters = min(max_chapters, MAX_AUTO_GENERATED_CHAPTERS)

    # Distribute sentences across chapters
    sentences_per_chapter = max(1, len(sentences) // num_chapters)

    detected_chapters = []
    for i in range(num_chapters):
        start_time = i * (video_duration / num_chapters)

        # Get sentences for this segment
        start_idx = i * sentences_per_chapter
        end_idx = min((i + 1) * sentences_per_chapter, len(sentences))
        sentences_in_segment = sentences[start_idx:end_idx]

        # Generate title from first sentence (truncated)
        if sentences_in_segment:
            title = _generate_chapter_title(sentences_in_segment[0], i + 1)
        else:
            title = f"Section {i + 1}"

        # Calculate end time (next chapter start or video end)
        if i < num_chapters - 1:
            end_time = (i + 1) * (video_duration / num_chapters)
        else:
            end_time = video_duration

        detected_chapters.append(
            InternalDetectedChapter(
                title=title,
                start_time=round(start_time, 2),
                end_time=round(end_time, 2),
                source="transcription",
            )
        )

    return detected_chapters


def _truncate_at_word_boundary(text: str, max_length: int) -> str:
    """
    Truncate text to max_length, preferring word boundaries.

    Adds '...' if truncated. Breaks at word boundary only if
    it's past the halfway point, to avoid very short titles.

    Args:
        text: Text to truncate
        max_length: Maximum length (not including ellipsis)

    Returns:
        Truncated text with ellipsis if needed
    """
    if len(text) <= max_length:
        return text

    truncated = text[:max_length]
    last_space_position = truncated.rfind(" ")

    # Only break at word if it's not too early
    minimum_break_position = max_length // 2
    if last_space_position > minimum_break_position:
        return truncated[:last_space_position] + "..."

    return truncated + "..."


def _generate_chapter_title(sentence: str, chapter_num: int) -> str:
    """
    Generate a chapter title from a sentence.

    Removes filler words and truncates to create a concise, descriptive title.

    Args:
        sentence: Source sentence for the title
        chapter_num: Chapter number (used as fallback)

    Returns:
        Generated chapter title
    """
    cleaned_sentence = sentence

    # Remove common filler words at the start
    for pattern in _FILLER_PATTERNS:
        cleaned_sentence = pattern.sub("", cleaned_sentence)

    # Truncate to display length
    title = _truncate_at_word_boundary(cleaned_sentence, DISPLAY_TITLE_LENGTH)

    # Clean up punctuation at the end
    title = _PUNCTUATION_END_PATTERN.sub("", title)

    # If title is too short or empty, use fallback
    if len(title.strip()) < MIN_TITLE_LENGTH:
        return f"Section {chapter_num}"

    return title.strip()


def filter_chapters_by_length(
    chapters: List[InternalDetectedChapter],
    min_chapter_length: int,
    video_duration: float,
) -> List[InternalDetectedChapter]:
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
    last_start_time: Optional[float] = None

    for chapter in sorted_chapters:
        # Always include first chapter
        if last_start_time is None:
            filtered.append(chapter)
            last_start_time = chapter.start_time
            continue

        # Skip if too close to previous chapter
        if chapter.start_time - last_start_time < min_chapter_length:
            continue

        # Skip if too close to video end
        if video_duration - chapter.start_time < min_chapter_length:
            continue

        filtered.append(chapter)
        last_start_time = chapter.start_time

    return filtered
