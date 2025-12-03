"""
Error handling utilities for sanitizing error messages.

Prevents internal implementation details from being exposed to API clients
while still logging detailed errors for debugging.
"""
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Patterns that indicate internal details
INTERNAL_PATTERNS = [
    r'/home/\w+/',           # Home directory paths
    r'/mnt/\w+/',            # Mount paths
    r'/tmp/\w+',             # Temp paths
    r'/var/\w+/',            # Var paths
    r'line \d+',             # Line numbers in stack traces
    r'File "[^"]+\.py"',     # Python file paths
    r'ffmpeg:.*\.mp4',       # FFmpeg with file paths
    r'ffprobe:.*\.mp4',      # FFprobe with file paths
    r'Permission denied',    # System errors
    r'No such file or directory',  # System errors with paths
    r'UNIQUE constraint failed',   # Database internals
    r'sqlite3?\.',           # SQLite details
    r'Error: .+\.py:\d+',    # Python error traces
]

# Generic user-friendly messages for common error types
ERROR_MESSAGES = {
    "ffmpeg": "Video processing failed. Please try uploading again.",
    "ffprobe": "Could not read video file. The file may be corrupted or in an unsupported format.",
    "timeout": "Video processing timed out. Please try again with a shorter video.",
    "no_video_stream": "No video stream found. Please upload a valid video file.",
    "duration": "Could not determine video duration. The file may be corrupted.",
    "source_not_found": "Source file not found. Please re-upload the video.",
    "transcode_failed": "Video transcoding failed. Please try uploading again.",
    "database": "A database error occurred. Please try again.",
    "permission": "A file access error occurred. Please contact support.",
    "general": "An error occurred while processing your request. Please try again.",
}


def sanitize_error_message(
    error: Optional[str],
    log_original: bool = True,
    context: str = ""
) -> Optional[str]:
    """
    Sanitize an error message for safe display to API clients.

    Args:
        error: The original error message (may contain internal details)
        log_original: Whether to log the original message before sanitizing
        context: Additional context for logging (e.g., "video_id=123")

    Returns:
        A sanitized, user-friendly error message, or None if input was None
    """
    if error is None:
        return None

    # Log the original error for debugging
    if log_original and error:
        log_msg = f"Original error"
        if context:
            log_msg += f" ({context})"
        log_msg += f": {error}"
        logger.warning(log_msg)

    error_lower = error.lower()

    # Check for specific error types and return friendly messages
    if "ffmpeg" in error_lower or "transcode" in error_lower:
        if "timeout" in error_lower or "timed out" in error_lower:
            return ERROR_MESSAGES["timeout"]
        return ERROR_MESSAGES["transcode_failed"]

    if "ffprobe" in error_lower:
        return ERROR_MESSAGES["ffprobe"]

    if "no video stream" in error_lower:
        return ERROR_MESSAGES["no_video_stream"]

    if "duration" in error_lower:
        return ERROR_MESSAGES["duration"]

    if "source file not found" in error_lower or "not found" in error_lower:
        return ERROR_MESSAGES["source_not_found"]

    if "sqlite" in error_lower or "database" in error_lower or "constraint" in error_lower:
        return ERROR_MESSAGES["database"]

    if "permission" in error_lower:
        return ERROR_MESSAGES["permission"]

    # Check if the error contains any internal patterns
    for pattern in INTERNAL_PATTERNS:
        if re.search(pattern, error, re.IGNORECASE):
            return ERROR_MESSAGES["general"]

    # If the error message is short and doesn't match patterns, it might be safe
    # But truncate and remove any path-like segments just in case
    if len(error) < 100 and "/" not in error and "\\" not in error:
        return error

    # Default to generic message for anything else
    return ERROR_MESSAGES["general"]


def sanitize_progress_error(error: Optional[str]) -> Optional[str]:
    """
    Sanitize error messages specifically for transcoding progress responses.
    These are shown in the admin UI during video processing.

    Args:
        error: The original error from transcoding job

    Returns:
        A sanitized error message suitable for display
    """
    if error is None:
        return None

    # For progress errors, we can be slightly more specific
    error_lower = error.lower()

    if "timeout" in error_lower:
        return "Processing timed out"

    if "all" in error_lower and "failed" in error_lower:
        return "All quality variants failed to process"

    if "retry" in error_lower or "attempt" in error_lower:
        return "Processing failed, retrying..."

    if "max" in error_lower and "exceeded" in error_lower:
        return "Maximum retry attempts exceeded"

    # Fall back to general sanitization
    return sanitize_error_message(error, log_original=False)
