"""
Centralized enums for status values used throughout the application.
Using str-based enums for database compatibility.
"""

from enum import Enum


class VideoStatus(str, Enum):
    """Status values for video processing."""

    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class TranscriptionStatus(str, Enum):
    """Status values for transcription processing."""

    NONE = "none"
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class QualityStatus(str, Enum):
    """Status values for per-quality transcoding progress."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class TranscodingStep(str, Enum):
    """Processing step names for transcoding jobs."""

    PROBE = "probe"
    THUMBNAIL = "thumbnail"
    TRANSCODE = "transcode"
    MASTER_PLAYLIST = "master_playlist"
    FINALIZE = "finalize"


class DurationFilter(str, Enum):
    """Video duration filter options."""

    SHORT = "short"  # < 5 minutes
    MEDIUM = "medium"  # 5-20 minutes
    LONG = "long"  # > 20 minutes


class SortBy(str, Enum):
    """Sort options for video listing."""

    RELEVANCE = "relevance"  # Default for text searches
    DATE = "date"  # Published date
    DURATION = "duration"  # Video length
    VIEWS = "views"  # View count
    TITLE = "title"  # Alphabetical


class SortOrder(str, Enum):
    """Sort order direction."""

    ASC = "asc"  # Ascending
    DESC = "desc"  # Descending


# ============================================================================
# Parameter enums - Replace boolean traps with self-documenting enums
# See: https://github.com/filthyrake/vlog/issues/443
# ============================================================================


class PlaylistValidation(str, Enum):
    """Controls depth of HLS playlist validation.

    Use this instead of boolean check_segments parameter for clarity.

    Example:
        # Clear intent
        validate_hls_playlist(path, PlaylistValidation.CHECK_SEGMENTS)

        # vs unclear boolean
        validate_hls_playlist(path, True)  # What does True mean?
    """

    CHECK_SEGMENTS = "check_segments"
    """Validate playlist structure AND verify all referenced segments exist."""

    STRUCTURE_ONLY = "structure_only"
    """Only validate playlist structure, skip segment file checks."""


class JobFailureMode(str, Enum):
    """Indicates whether a failed job can be retried.

    Use this instead of boolean 'final' parameter for clarity.

    Example:
        # Clear intent
        mark_job_failed(job_id, error, JobFailureMode.PERMANENT)

        # vs unclear boolean
        mark_job_failed(job_id, error, True)  # What does True mean?
    """

    RETRYABLE = "retryable"
    """Job failed but may be retried (does not set completed_at)."""

    PERMANENT = "permanent"
    """Job permanently failed, no more retries (sets completed_at)."""


class DeleteMode(str, Enum):
    """Controls video deletion behavior.

    Use this instead of boolean 'permanent' parameter for clarity.

    Example:
        # Clear intent
        delete_video(request, video_id, DeleteMode.PERMANENT)

        # vs unclear boolean
        delete_video(request, video_id, True)  # What does True mean?
    """

    SOFT = "soft"
    """Soft delete - archive files and set deleted_at, can be restored."""

    PERMANENT = "permanent"
    """Permanently delete - remove all files and database records, cannot be undone."""


class KeyRevocation(str, Enum):
    """Controls API key revocation when deleting a worker.

    Use this instead of boolean 'revoke_keys' parameter for clarity.

    Example:
        # Clear intent
        delete_worker(request, worker_id, KeyRevocation.REVOKE)

        # vs unclear boolean
        delete_worker(request, worker_id, True)  # What does True mean?
    """

    REVOKE = "revoke"
    """Revoke all API keys when deleting the worker."""

    KEEP = "keep"
    """Keep API keys active (worker can re-register with same keys)."""
