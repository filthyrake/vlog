"""
Error handling utilities for sanitizing error messages.

Prevents internal implementation details from being exposed to API clients
while still logging detailed errors for debugging.
"""

import logging
import re
import warnings
from typing import Optional, Union

from api.enums import ErrorLogging
from config import ERROR_DETAIL_MAX_LENGTH, ERROR_SUMMARY_MAX_LENGTH

logger = logging.getLogger(__name__)

# Patterns that indicate internal details
INTERNAL_PATTERNS = [
    r"/home/\w+/",  # Home directory paths
    r"/mnt/\w+/",  # Mount paths
    r"/tmp/\w+",  # Temp paths
    r"/var/\w+/",  # Var paths
    r"line \d+",  # Line numbers in stack traces
    r'File "[^"]+\.py"',  # Python file paths
    r"ffmpeg:.*\.mp4",  # FFmpeg with file paths
    r"ffprobe:.*\.mp4",  # FFprobe with file paths
    r"Permission denied",  # System errors
    r"No such file or directory",  # System errors with paths
    r"UNIQUE constraint failed",  # SQLite database internals
    r"duplicate key value violates unique constraint",  # PostgreSQL internals
    r"sqlite3?\.",  # SQLite details
    r"asyncpg\.",  # PostgreSQL async driver details
    r"psycopg2?\.",  # PostgreSQL sync driver details
    r"Error: .+\.py:\d+",  # Python error traces
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


def truncate_string(text: Optional[str], max_length: int) -> Optional[str]:
    """
    Truncate a string to a maximum length.

    Generic string truncation utility that can be used for any text,
    not just error messages.

    Args:
        text: The text to truncate (can be None)
        max_length: Maximum length (must be at least 4 for truncation with ellipsis)

    Returns:
        The truncated text with "..." appended if it was truncated, or None if input was None
    """
    if text is None:
        return None
    if not text or len(text) <= max_length:
        return text
    # Ensure we have enough space for ellipsis (...)
    if max_length < 4:
        return text[:max_length]
    return text[:max_length - 3] + "..."


def truncate_error(msg: Optional[str], max_length: int = ERROR_DETAIL_MAX_LENGTH) -> Optional[str]:
    """
    Truncate an error message to a maximum length.

    Args:
        msg: The error message to truncate (can be None)
        max_length: Maximum length (default: ERROR_DETAIL_MAX_LENGTH)

    Returns:
        The truncated message with "..." appended if it was truncated, or None if input was None
    """
    return truncate_string(msg, max_length)



def sanitize_error_message(
    error: Optional[str],
    logging_mode: Union[ErrorLogging, bool] = ErrorLogging.LOG_ORIGINAL,
    context: str = "",
) -> Optional[str]:
    """
    Sanitize an error message for safe display to API clients.

    Args:
        error: The original error message (may contain internal details)
        logging_mode: Whether to log the original message before sanitizing.
            Use ErrorLogging.LOG_ORIGINAL or ErrorLogging.SKIP_LOGGING.
            Boolean values are deprecated but supported for backwards compatibility.
        context: Additional context for logging (e.g., "video_id=123")

    Returns:
        A sanitized, user-friendly error message, or None if input was None
    """
    if error is None:
        return None

    # Handle backwards compatibility with boolean values
    if isinstance(logging_mode, bool):
        warnings.warn(
            "Passing boolean to sanitize_error_message() is deprecated. "
            "Use ErrorLogging.LOG_ORIGINAL or ErrorLogging.SKIP_LOGGING instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        should_log = logging_mode
    elif isinstance(logging_mode, ErrorLogging):
        should_log = logging_mode == ErrorLogging.LOG_ORIGINAL
    else:
        raise TypeError(
            f"logging_mode must be ErrorLogging or bool, got {type(logging_mode).__name__}: {logging_mode!r}"
        )

    # Log the original error for debugging
    if should_log and error:
        log_msg = "Original error"
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

    if any(term in error_lower for term in ["sqlite", "postgres", "asyncpg", "database", "constraint"]):
        return ERROR_MESSAGES["database"]

    if "permission" in error_lower:
        return ERROR_MESSAGES["permission"]

    # Check if the error contains any internal patterns
    for pattern in INTERNAL_PATTERNS:
        if re.search(pattern, error, re.IGNORECASE):
            return ERROR_MESSAGES["general"]

    # If the error message is short and doesn't match patterns, it might be safe
    # But truncate and remove any path-like segments just in case
    if len(error) < ERROR_SUMMARY_MAX_LENGTH and "/" not in error and "\\" not in error:
        return error

    # Default to generic message for anything else
    return ERROR_MESSAGES["general"]


def is_unique_violation(exc: Exception, column: Optional[str] = None) -> bool:
    """
    Check if an exception is a unique constraint violation.

    Supports both SQLite and PostgreSQL error formats:
    - SQLite: "UNIQUE constraint failed: table.column"
    - PostgreSQL: "duplicate key value violates unique constraint"

    Args:
        exc: The exception to check
        column: Optional column name to check for specific constraint

    Returns:
        True if this is a unique constraint violation (optionally on the specified column)
    """
    error_str = str(exc).lower()

    # Check for SQLite-style errors
    is_sqlite_unique = "unique constraint failed" in error_str

    # Check for PostgreSQL-style errors
    is_postgres_unique = "duplicate key value violates unique constraint" in error_str
    is_postgres_unique = is_postgres_unique or "uniqueviolation" in error_str

    if not (is_sqlite_unique or is_postgres_unique):
        return False

    # If a column name is specified, check if it's mentioned in the error
    if column:
        return column.lower() in error_str

    return True


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
    return sanitize_error_message(error, ErrorLogging.SKIP_LOGGING)
