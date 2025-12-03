"""
Centralized enums for status values used throughout the application.
Using str-based enums for SQLite/database compatibility.
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
