import ipaddress
import math
import socket
from datetime import datetime
from enum import Enum
from typing import Any, List, Optional, Set
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

# Maximum position value (24 hours in seconds)
MAX_POSITION_SECONDS = 86400

# Whisper-supported language codes (ISO 639-1)
# Full list from OpenAI Whisper documentation
WHISPER_LANGUAGES: Set[str] = {
    "af",
    "am",
    "ar",
    "as",
    "az",
    "ba",
    "be",
    "bg",
    "bn",
    "bo",
    "br",
    "bs",
    "ca",
    "cs",
    "cy",
    "da",
    "de",
    "el",
    "en",
    "es",
    "et",
    "eu",
    "fa",
    "fi",
    "fo",
    "fr",
    "gl",
    "gu",
    "ha",
    "haw",
    "he",
    "hi",
    "hr",
    "ht",
    "hu",
    "hy",
    "id",
    "is",
    "it",
    "ja",
    "jw",
    "ka",
    "kk",
    "km",
    "kn",
    "ko",
    "la",
    "lb",
    "ln",
    "lo",
    "lt",
    "lv",
    "mg",
    "mi",
    "mk",
    "ml",
    "mn",
    "mr",
    "ms",
    "mt",
    "my",
    "ne",
    "nl",
    "nn",
    "no",
    "oc",
    "pa",
    "pl",
    "ps",
    "pt",
    "ro",
    "ru",
    "sa",
    "sd",
    "si",
    "sk",
    "sl",
    "sn",
    "so",
    "sq",
    "sr",
    "su",
    "sv",
    "sw",
    "ta",
    "te",
    "tg",
    "th",
    "tk",
    "tl",
    "tr",
    "tt",
    "uk",
    "ur",
    "uz",
    "vi",
    "yi",
    "yo",
    "zh",
    "yue",
}


class CategoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)


class CategoryResponse(BaseModel):
    id: int
    name: str
    slug: str
    description: str
    created_at: datetime
    video_count: int = 0


class VideoCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=5000)
    category_id: Optional[int] = None


class VideoQualityResponse(BaseModel):
    quality: str
    width: int
    height: int
    bitrate: int


class VideoTagInfo(BaseModel):
    """Tag info included in video responses."""

    id: int
    name: str
    slug: str


class VideoResponse(BaseModel):
    id: int
    title: str
    slug: str
    description: str = ""
    category_id: Optional[int]
    category_name: Optional[str] = None
    category_slug: Optional[str] = None
    duration: float
    source_width: int
    source_height: int
    status: str
    error_message: Optional[str]
    created_at: Optional[datetime] = None
    published_at: Optional[datetime]
    thumbnail_url: Optional[str] = None
    thumbnail_source: str = "auto"  # auto, selected, custom
    thumbnail_timestamp: Optional[float] = None  # timestamp for selected thumbnails
    stream_url: Optional[str] = None
    dash_url: Optional[str] = None  # DASH manifest URL (CMAF format only)
    streaming_format: str = "hls_ts"  # hls_ts (legacy) or cmaf (modern fMP4)
    primary_codec: str = "h264"  # h264, hevc, or av1
    captions_url: Optional[str] = None  # WebVTT captions URL
    transcription_status: Optional[str] = None  # pending, processing, completed, failed
    qualities: List[VideoQualityResponse] = []
    tags: List[VideoTagInfo] = []
    chapters: List["ChapterInfo"] = []  # Issue #413 Phase 7A
    sprite_sheet_info: Optional["SpriteSheetInfo"] = None  # Issue #413 Phase 7B

    @field_validator("description", mode="before")
    @classmethod
    def default_description(cls, v):
        return v if v is not None else ""

    @field_validator("streaming_format", mode="before")
    @classmethod
    def default_streaming_format(cls, v):
        return v if v is not None else "hls_ts"

    @field_validator("primary_codec", mode="before")
    @classmethod
    def default_primary_codec(cls, v):
        return v if v is not None else "h264"

    @field_validator("created_at", mode="before")
    @classmethod
    def default_created_at(cls, v):
        from datetime import timezone

        return v if v is not None else datetime.now(timezone.utc)


class VideoListResponse(BaseModel):
    id: int
    title: str
    slug: str
    description: str = ""
    category_id: Optional[int]
    category_name: Optional[str] = None
    duration: float
    status: str
    created_at: Optional[datetime] = None
    published_at: Optional[datetime]
    thumbnail_url: Optional[str] = None
    thumbnail_source: str = "auto"  # auto, selected, custom
    thumbnail_timestamp: Optional[float] = None  # timestamp for selected thumbnails
    streaming_format: str = "hls_ts"  # hls_ts (legacy) or cmaf (modern fMP4)
    primary_codec: str = "h264"  # h264, hevc, or av1
    tags: List[VideoTagInfo] = []
    view_count: int = 0  # Total playback sessions (Issue #413 Phase 3)

    @field_validator("description", mode="before")
    @classmethod
    def default_description(cls, v):
        return v if v is not None else ""

    @field_validator("created_at", mode="before")
    @classmethod
    def default_created_at(cls, v):
        from datetime import timezone

        return v if v is not None else datetime.now(timezone.utc)

    @field_validator("streaming_format", mode="before")
    @classmethod
    def default_streaming_format(cls, v):
        return v if v is not None else "hls_ts"

    @field_validator("primary_codec", mode="before")
    @classmethod
    def default_primary_codec(cls, v):
        return v if v is not None else "h264"


# Cursor-based pagination response (Issue #463)
class PaginatedVideoListResponse(BaseModel):
    """
    Paginated video list response with cursor-based navigation.

    Cursor-based pagination is more efficient than offset-based pagination
    for large datasets, as it doesn't require scanning/discarding rows.
    """

    videos: List[VideoListResponse]
    next_cursor: Optional[str] = Field(
        default=None,
        description="Cursor for the next page. None if no more results.",
    )
    has_more: bool = Field(
        default=False,
        description="Whether there are more results after this page.",
    )
    total_count: Optional[int] = Field(
        default=None,
        description="Total count of matching items (optional, expensive for large datasets).",
    )


# Analytics request models
class PlaybackSessionCreate(BaseModel):
    video_id: int
    quality: Optional[str] = None


class PlaybackHeartbeat(BaseModel):
    session_token: str = Field(..., max_length=64)
    position: float
    quality: Optional[str] = None
    playing: bool = True

    @field_validator("position")
    @classmethod
    def validate_position(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("position must be a finite number")
        if v < 0:
            raise ValueError("position must be non-negative")
        if v > MAX_POSITION_SECONDS:
            raise ValueError(f"position exceeds maximum allowed value ({MAX_POSITION_SECONDS}s)")
        return v


class PlaybackEnd(BaseModel):
    session_token: str = Field(..., max_length=64)
    position: float
    completed: bool = False

    @field_validator("position")
    @classmethod
    def validate_position(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("position must be a finite number")
        if v < 0:
            raise ValueError("position must be non-negative")
        if v > MAX_POSITION_SECONDS:
            raise ValueError(f"position exceeds maximum allowed value ({MAX_POSITION_SECONDS}s)")
        return v


# Analytics response models
class PlaybackSessionResponse(BaseModel):
    session_token: str


class AnalyticsOverview(BaseModel):
    total_views: int
    unique_viewers: int
    total_watch_time_hours: float
    completion_rate: float
    avg_watch_duration_seconds: float
    views_today: int
    views_this_week: int
    views_this_month: int


class VideoAnalyticsSummary(BaseModel):
    video_id: int
    title: str
    slug: str
    thumbnail_url: Optional[str]
    total_views: int
    unique_viewers: int
    total_watch_time_seconds: float
    avg_watch_duration_seconds: float
    completion_rate: float
    peak_quality: Optional[str]


class VideoAnalyticsListResponse(BaseModel):
    videos: List[VideoAnalyticsSummary]
    total_count: int


class QualityBreakdown(BaseModel):
    quality: str
    percentage: float


class DailyViews(BaseModel):
    date: str
    views: int


class VideoAnalyticsDetail(BaseModel):
    video_id: int
    title: str
    duration: float
    total_views: int
    unique_viewers: int
    total_watch_time_seconds: float
    avg_watch_duration_seconds: float
    completion_rate: float
    avg_percent_watched: float
    quality_breakdown: List[QualityBreakdown]
    views_over_time: List[DailyViews]


class TrendDataPoint(BaseModel):
    date: str
    views: int
    unique_viewers: int
    watch_time_hours: float


class TrendsResponse(BaseModel):
    period: str
    data: List[TrendDataPoint]


# Transcoding progress models
class QualityProgressResponse(BaseModel):
    name: str
    status: str  # pending, in_progress, completed, failed, skipped
    progress: int = 0


class TranscodingProgressResponse(BaseModel):
    status: str  # pending, processing, ready, failed
    current_step: Optional[str] = None  # probe, thumbnail, transcode, master_playlist, finalize
    progress_percent: int = 0
    qualities: List[QualityProgressResponse] = []
    attempt: int = 1
    max_attempts: int = 3
    started_at: Optional[datetime] = None
    last_error: Optional[str] = None


# Transcription models
class TranscriptionResponse(BaseModel):
    status: str  # pending, processing, completed, failed
    language: Optional[str] = None
    text: Optional[str] = None
    vtt_url: Optional[str] = None
    word_count: Optional[int] = None
    duration_seconds: Optional[float] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None


class TranscriptionTrigger(BaseModel):
    language: Optional[str] = Field(default=None, description="ISO 639-1 language code")

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.lower().strip()
            if v not in WHISPER_LANGUAGES:
                raise ValueError(f"Invalid language code: '{v}'. Must be a valid ISO 639-1 code supported by Whisper.")
        return v


class TranscriptionUpdate(BaseModel):
    text: str = Field(..., min_length=1, max_length=500000)  # 500KB max transcript


class RetranscodeRequest(BaseModel):
    qualities: List[str] = Field(..., min_length=1)
    priority: str = Field(default="normal", pattern="^(high|normal|low)$")

    @field_validator("qualities")
    @classmethod
    def validate_qualities(cls, v: List[str]) -> List[str]:
        valid_qualities = {"all", "original", "2160p", "1440p", "1080p", "720p", "480p", "360p"}
        for q in v:
            if q not in valid_qualities:
                raise ValueError(f"Invalid quality '{q}'. Valid options: {', '.join(sorted(valid_qualities))}")
        return v


class RetranscodeResponse(BaseModel):
    status: str
    video_id: int
    message: str
    qualities_queued: List[str]


class VideoQualityInfo(BaseModel):
    name: str
    width: int
    height: int
    bitrate: int
    status: str  # completed, pending, in_progress, failed


class VideoQualitiesResponse(BaseModel):
    video_id: int
    source_width: int
    source_height: int
    available_qualities: List[str]  # What qualities could be generated based on source
    existing_qualities: List[VideoQualityInfo]  # Current transcoded qualities


# ============ Bulk Operation Models ============


# Maximum videos per bulk operation to prevent abuse
MAX_BULK_VIDEOS = 100


class BulkOperationResult(BaseModel):
    """Result for a single item in a bulk operation."""

    video_id: int
    success: bool
    error: Optional[str] = None


class BulkDeleteRequest(BaseModel):
    """Request to delete multiple videos."""

    video_ids: List[int] = Field(..., min_length=1, max_length=MAX_BULK_VIDEOS)
    permanent: bool = Field(default=False, description="Permanently delete instead of soft-delete")


class BulkDeleteResponse(BaseModel):
    """Response from bulk delete operation."""

    status: str
    deleted: int
    failed: int
    results: List[BulkOperationResult]


class BulkUpdateRequest(BaseModel):
    """Request to update multiple videos with the same values."""

    video_ids: List[int] = Field(..., min_length=1, max_length=MAX_BULK_VIDEOS)
    category_id: Optional[int] = Field(default=None, description="Set category (use 0 or null to remove)")
    published_at: Optional[datetime] = Field(default=None, description="Set published date")
    unpublish: bool = Field(default=False, description="Remove published date (set to null)")


class BulkUpdateResponse(BaseModel):
    """Response from bulk update operation."""

    status: str
    updated: int
    failed: int
    results: List[BulkOperationResult]


class BulkRetranscodeRequest(BaseModel):
    """Request to retranscode multiple videos."""

    video_ids: List[int] = Field(..., min_length=1, max_length=MAX_BULK_VIDEOS)
    qualities: List[str] = Field(default=["all"], min_length=1)
    priority: str = Field(default="normal", pattern="^(high|normal|low)$")

    @field_validator("qualities")
    @classmethod
    def validate_qualities(cls, v: List[str]) -> List[str]:
        valid_qualities = {"all", "original", "2160p", "1440p", "1080p", "720p", "480p", "360p"}
        for q in v:
            if q not in valid_qualities:
                raise ValueError(f"Invalid quality '{q}'. Valid options: {', '.join(sorted(valid_qualities))}")
        return v


class BulkRetranscodeResponse(BaseModel):
    """Response from bulk retranscode operation."""

    status: str
    queued: int
    failed: int
    results: List[BulkOperationResult]


class BulkRestoreRequest(BaseModel):
    """Request to restore multiple soft-deleted videos."""

    video_ids: List[int] = Field(..., min_length=1, max_length=MAX_BULK_VIDEOS)


class BulkRestoreResponse(BaseModel):
    """Response from bulk restore operation."""

    status: str
    restored: int
    failed: int
    results: List[BulkOperationResult]


class VideoExportItem(BaseModel):
    """Single video item for export."""

    id: int
    title: str
    slug: str
    description: str
    category_id: Optional[int]
    category_name: Optional[str]
    duration: float
    source_width: int
    source_height: int
    status: str
    created_at: datetime
    published_at: Optional[datetime]


class VideoExportResponse(BaseModel):
    """Response containing exported video metadata."""

    videos: List[VideoExportItem]
    total_count: int
    exported_at: datetime


# ============ Worker Dashboard Models ============


class WorkerDashboardStatus(BaseModel):
    """Worker status for admin dashboard."""

    id: int
    worker_id: str
    worker_name: Optional[str]
    worker_type: str
    status: str  # active, idle, offline, disabled
    registered_at: datetime
    last_heartbeat: Optional[datetime]
    seconds_since_heartbeat: Optional[int] = None
    current_job_id: Optional[int] = None
    current_video_slug: Optional[str] = None
    current_video_title: Optional[str] = None
    current_step: Optional[str] = None
    current_progress: Optional[int] = None
    # Capabilities summary
    hwaccel_enabled: bool = False
    hwaccel_type: Optional[str] = None
    gpu_name: Optional[str] = None
    # Version tracking (Issue #410)
    code_version: Optional[str] = None
    deployment_type: Optional[str] = None  # kubernetes, systemd, docker, manual
    # Stats
    jobs_completed: int = 0
    jobs_failed: int = 0
    last_job_completed_at: Optional[datetime] = None


class WorkerDashboardResponse(BaseModel):
    """Response for worker dashboard listing."""

    workers: List[WorkerDashboardStatus]
    total_count: int
    active_count: int
    idle_count: int
    offline_count: int
    disabled_count: int


class ActiveJobWithWorker(BaseModel):
    """Active transcoding job with worker details."""

    job_id: int
    video_id: int
    video_slug: str
    video_title: str
    thumbnail_url: Optional[str] = None
    # Worker info
    worker_id: Optional[str] = None
    worker_name: Optional[str] = None
    worker_hwaccel_type: Optional[str] = None
    # Progress info
    status: str
    current_step: Optional[str] = None
    progress_percent: int = 0
    qualities: List[QualityProgressResponse] = []
    # Timing
    started_at: Optional[datetime] = None
    claimed_at: Optional[datetime] = None
    attempt: int = 1
    max_attempts: int = 3


class ActiveJobsResponse(BaseModel):
    """Response for active jobs with worker info."""

    jobs: List[ActiveJobWithWorker]
    total_count: int
    processing_count: int
    pending_count: int


class WorkerJobHistory(BaseModel):
    """Job history entry for a worker."""

    job_id: int
    video_id: int
    video_slug: str
    video_title: str
    status: str  # completed, failed
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    error_message: Optional[str] = None


class WorkerDetailResponse(BaseModel):
    """Detailed worker info including job history."""

    id: int
    worker_id: str
    worker_name: Optional[str]
    worker_type: str
    status: str
    registered_at: datetime
    last_heartbeat: Optional[datetime]
    # Capabilities
    capabilities: Optional[dict] = None
    metadata: Optional[dict] = None
    # Stats
    jobs_completed: int = 0
    jobs_failed: int = 0
    avg_job_duration_seconds: Optional[float] = None
    # Recent jobs
    recent_jobs: List[WorkerJobHistory] = []


# ============ Tag Models ============


class TagCreate(BaseModel):
    """Request to create a new tag."""

    name: str = Field(..., min_length=1, max_length=50)


class TagUpdate(BaseModel):
    """Request to update a tag."""

    name: str = Field(..., min_length=1, max_length=50)


class TagResponse(BaseModel):
    """Response for a single tag."""

    id: int
    name: str
    slug: str
    created_at: datetime
    video_count: int = 0


class TagListResponse(BaseModel):
    """Response for tag listing."""

    tags: List[TagResponse]
    total_count: int


class VideoTagsUpdate(BaseModel):
    """Request to set tags on a video."""

    tag_ids: List[int] = Field(..., max_length=20, description="List of tag IDs (max 20 tags per video)")


# ============ Thumbnail Selection Models ============


class ThumbnailFrame(BaseModel):
    """A single frame option for thumbnail selection."""

    index: int
    timestamp: float
    url: str


class ThumbnailFramesResponse(BaseModel):
    """Response containing generated frame options for thumbnail selection."""

    video_id: int
    frames: List[ThumbnailFrame]


class ThumbnailResponse(BaseModel):
    """Response after thumbnail update operations."""

    status: str
    thumbnail_url: str
    thumbnail_source: str  # auto, selected, custom
    thumbnail_timestamp: Optional[float] = None


class ThumbnailInfoResponse(BaseModel):
    """Current thumbnail information for a video."""

    video_id: int
    thumbnail_url: Optional[str]
    thumbnail_source: str  # auto, selected, custom
    thumbnail_timestamp: Optional[float] = None


# ============ Settings Models ============


class SettingConstraints(BaseModel):
    """Validation constraints for a setting value."""

    min: Optional[float] = None
    max: Optional[float] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    pattern: Optional[str] = None
    enum_values: Optional[List[str]] = None


class SettingResponse(BaseModel):
    """Response for a single setting."""

    key: str
    value: Any
    category: str
    value_type: str  # string, integer, float, boolean, enum, json
    description: Optional[str] = None
    constraints: Optional[SettingConstraints] = None
    updated_at: datetime
    updated_by: Optional[str] = None


class SettingUpdate(BaseModel):
    """Request to update a setting value."""

    value: Any = Field(..., description="New value for the setting")


class SettingCreate(BaseModel):
    """Request to create a new setting."""

    key: str = Field(..., min_length=1, max_length=255, pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
    value: Any = Field(..., description="Initial value")
    category: str = Field(..., min_length=1, max_length=100)
    value_type: str = Field(default="string", pattern="^(string|integer|float|boolean|enum|json)$")
    description: Optional[str] = Field(default=None, max_length=1000)
    constraints: Optional[SettingConstraints] = None


class SettingsByCategoryResponse(BaseModel):
    """Response containing settings grouped by category."""

    categories: dict[str, List[SettingResponse]]


class SettingsCategoryResponse(BaseModel):
    """Response containing settings in a single category."""

    category: str
    settings: List[SettingResponse]


class SettingsExport(BaseModel):
    """Export format for settings (for import/export functionality)."""

    version: str = "1.0"
    exported_at: datetime
    settings: List[SettingResponse]


class SettingsImport(BaseModel):
    """Request to import settings from export format."""

    settings: List[SettingCreate]
    overwrite: bool = Field(default=False, description="Overwrite existing settings")


# ============ Custom Field Models ============

# Valid field types for custom fields
CUSTOM_FIELD_TYPES = {"text", "number", "date", "select", "multi_select", "url"}


class CustomFieldConstraints(BaseModel):
    """Validation constraints for custom field values."""

    min: Optional[float] = Field(default=None, description="Minimum value for number fields")
    max: Optional[float] = Field(default=None, description="Maximum value for number fields")
    min_length: Optional[int] = Field(default=None, description="Minimum length for text/url fields")
    max_length: Optional[int] = Field(default=None, description="Maximum length for text/url fields")
    pattern: Optional[str] = Field(default=None, description="Regex pattern for text/url validation")


class CustomFieldCreate(BaseModel):
    """Request to create a custom field definition."""

    name: str = Field(..., min_length=1, max_length=100, description="Display name for the field")
    field_type: str = Field(..., description="Field type: text, number, date, select, multi_select, url")
    options: Optional[List[str]] = Field(
        default=None, description="Options for select/multi_select fields", max_length=100
    )
    required: bool = Field(default=False, description="Whether the field is required")
    category_id: Optional[int] = Field(default=None, description="Category ID (null for global field)")
    position: int = Field(default=0, ge=0, description="Display order position")
    constraints: Optional[CustomFieldConstraints] = Field(default=None, description="Validation constraints")
    description: Optional[str] = Field(default=None, max_length=500, description="Help text for the field")

    @field_validator("field_type")
    @classmethod
    def validate_field_type(cls, v: str) -> str:
        if v not in CUSTOM_FIELD_TYPES:
            raise ValueError(f"Invalid field_type '{v}'. Valid options: {', '.join(sorted(CUSTOM_FIELD_TYPES))}")
        return v

    @field_validator("options")
    @classmethod
    def validate_options(cls, v: Optional[List[str]], info) -> Optional[List[str]]:
        field_type = info.data.get("field_type")
        if field_type in ("select", "multi_select"):
            if not v or len(v) == 0:
                raise ValueError("Options are required for select/multi_select fields")
            # Validate each option is non-empty and reasonable length
            for opt in v:
                if not opt or not opt.strip():
                    raise ValueError("Options cannot be empty strings")
                if len(opt) > 100:
                    raise ValueError("Each option must be 100 characters or less")
        elif v is not None and len(v) > 0:
            raise ValueError("Options should only be provided for select/multi_select fields")
        return v


class CustomFieldUpdate(BaseModel):
    """Request to update a custom field definition.

    Note: field_type and category_id cannot be changed after creation.
    """

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    options: Optional[List[str]] = Field(default=None, max_length=100)
    required: Optional[bool] = None
    position: Optional[int] = Field(default=None, ge=0)
    constraints: Optional[CustomFieldConstraints] = None
    description: Optional[str] = Field(default=None, max_length=500)

    @field_validator("options")
    @classmethod
    def validate_options(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is not None:
            for opt in v:
                if not opt or not opt.strip():
                    raise ValueError("Options cannot be empty strings")
                if len(opt) > 100:
                    raise ValueError("Each option must be 100 characters or less")
        return v


class CustomFieldResponse(BaseModel):
    """Response for a custom field definition."""

    id: int
    name: str
    slug: str
    field_type: str
    options: Optional[List[str]] = None
    required: bool
    category_id: Optional[int]
    category_name: Optional[str] = None
    position: int
    constraints: Optional[CustomFieldConstraints] = None
    description: Optional[str] = None
    created_at: datetime


class CustomFieldListResponse(BaseModel):
    """Response for listing custom field definitions."""

    fields: List[CustomFieldResponse]
    total_count: int


class VideoCustomFieldValue(BaseModel):
    """A single custom field value for a video."""

    field_id: int
    field_slug: str
    field_name: str
    field_type: str
    value: Any  # Type depends on field_type
    required: bool
    options: Optional[List[str]] = None  # For select/multi_select fields


class VideoCustomFieldsUpdate(BaseModel):
    """Request to update custom field values for a video."""

    values: dict[int, Any] = Field(..., description="Map of field_id to value. Use null to clear a field value.")


class VideoCustomFieldsResponse(BaseModel):
    """Response for a video's custom field values."""

    video_id: int
    fields: List[VideoCustomFieldValue]


class BulkCustomFieldsUpdate(BaseModel):
    """Request to update custom field values for multiple videos."""

    video_ids: List[int] = Field(..., min_length=1, max_length=MAX_BULK_VIDEOS)
    values: dict[int, Any] = Field(..., description="Map of field_id to value. Applied to all specified videos.")


class BulkCustomFieldsResponse(BaseModel):
    """Response from bulk custom fields update operation."""

    status: str
    updated: int
    failed: int
    results: List[BulkOperationResult]


# ============ Playlist Models ============

# Valid visibility options for playlists
PLAYLIST_VISIBILITY_OPTIONS = {"public", "private", "unlisted"}

# Valid playlist types
PLAYLIST_TYPE_OPTIONS = {"playlist", "collection", "series", "course"}


class PlaylistCreate(BaseModel):
    """Request to create a new playlist."""

    title: str = Field(..., min_length=1, max_length=255, description="Playlist title")
    description: Optional[str] = Field(default=None, max_length=5000, description="Playlist description")
    visibility: str = Field(default="public", description="Visibility: public, private, unlisted")
    playlist_type: str = Field(default="playlist", description="Type: playlist, collection, series, course")
    is_featured: bool = Field(default=False, description="Whether to feature this playlist")

    @field_validator("visibility")
    @classmethod
    def validate_visibility(cls, v: str) -> str:
        if v not in PLAYLIST_VISIBILITY_OPTIONS:
            opts = ", ".join(sorted(PLAYLIST_VISIBILITY_OPTIONS))
            raise ValueError(f"Invalid visibility '{v}'. Valid options: {opts}")
        return v

    @field_validator("playlist_type")
    @classmethod
    def validate_playlist_type(cls, v: str) -> str:
        if v not in PLAYLIST_TYPE_OPTIONS:
            raise ValueError(f"Invalid playlist_type '{v}'. Valid options: {', '.join(sorted(PLAYLIST_TYPE_OPTIONS))}")
        return v


class PlaylistUpdate(BaseModel):
    """Request to update a playlist."""

    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=5000)
    visibility: Optional[str] = None
    playlist_type: Optional[str] = None
    is_featured: Optional[bool] = None

    @field_validator("visibility")
    @classmethod
    def validate_visibility(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in PLAYLIST_VISIBILITY_OPTIONS:
            opts = ", ".join(sorted(PLAYLIST_VISIBILITY_OPTIONS))
            raise ValueError(f"Invalid visibility '{v}'. Valid options: {opts}")
        return v

    @field_validator("playlist_type")
    @classmethod
    def validate_playlist_type(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in PLAYLIST_TYPE_OPTIONS:
            raise ValueError(f"Invalid playlist_type '{v}'. Valid options: {', '.join(sorted(PLAYLIST_TYPE_OPTIONS))}")
        return v


class PlaylistVideoInfo(BaseModel):
    """Video info included in playlist responses."""

    id: int
    title: str
    slug: str
    thumbnail_url: Optional[str] = None
    duration: float = 0
    position: int
    status: str = "ready"


class PlaylistResponse(BaseModel):
    """Response for a single playlist."""

    id: int
    title: str
    slug: str
    description: Optional[str] = None
    thumbnail_url: Optional[str] = None
    visibility: str
    playlist_type: str
    is_featured: bool
    video_count: int = 0
    total_duration: float = 0  # Sum of all video durations in seconds
    created_at: datetime
    updated_at: Optional[datetime] = None


class PlaylistDetailResponse(PlaylistResponse):
    """Response for playlist with videos included."""

    videos: List[PlaylistVideoInfo] = []


class PlaylistListResponse(BaseModel):
    """Response for playlist listing."""

    playlists: List[PlaylistResponse]
    total_count: int


class AddVideoToPlaylistRequest(BaseModel):
    """Request to add a video to a playlist."""

    video_id: int = Field(..., description="ID of the video to add")
    position: Optional[int] = Field(default=None, ge=0, description="Position in playlist (append if not specified)")


class ReorderPlaylistRequest(BaseModel):
    """Request to reorder videos in a playlist."""

    video_ids: List[int] = Field(..., min_length=1, description="Video IDs in new order")


# ============ Chapter Models (Issue #413 Phase 7) ============

# Maximum chapters per video (per reviewer feedback)
MAX_CHAPTERS_PER_VIDEO = 50


class ChapterCreate(BaseModel):
    """Request to create a new chapter."""

    title: str = Field(..., min_length=1, max_length=255, description="Chapter title")
    description: Optional[str] = Field(default=None, max_length=1000, description="Optional chapter description")
    start_time: float = Field(..., ge=0, description="Start time in seconds")
    end_time: Optional[float] = Field(default=None, description="Optional end time in seconds")

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time_finite(cls, v: Optional[float]) -> Optional[float]:
        """Ensure time values are finite (not NaN, Infinity, or -Infinity)."""
        if v is not None and not math.isfinite(v):
            raise ValueError("Time value must be a finite number")
        return v

    @field_validator("end_time")
    @classmethod
    def validate_end_time(cls, v: Optional[float], info) -> Optional[float]:
        """Ensure end_time > start_time if provided."""
        if v is not None:
            start_time = info.data.get("start_time")
            if start_time is not None and v <= start_time:
                raise ValueError("end_time must be greater than start_time")
        return v


class ChapterUpdate(BaseModel):
    """Request to update an existing chapter."""

    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=1000)
    start_time: Optional[float] = Field(default=None, ge=0)
    end_time: Optional[float] = None

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_time_finite(cls, v: Optional[float]) -> Optional[float]:
        """Ensure time values are finite (not NaN, Infinity, or -Infinity)."""
        if v is not None and not math.isfinite(v):
            raise ValueError("Time value must be a finite number")
        return v

    @field_validator("end_time")
    @classmethod
    def validate_end_time(cls, v: Optional[float], info) -> Optional[float]:
        """Ensure end_time > start_time if provided."""
        if v is not None:
            start_time = info.data.get("start_time")
            if start_time is not None and v <= start_time:
                raise ValueError("end_time must be greater than start_time")
        return v


class ChapterResponse(BaseModel):
    """Response for a single chapter."""

    id: int
    video_id: int
    title: str
    description: Optional[str] = None
    start_time: float
    end_time: Optional[float] = None
    position: int
    created_at: datetime
    updated_at: Optional[datetime] = None


class ChapterListResponse(BaseModel):
    """Response for listing chapters of a video."""

    chapters: List[ChapterResponse]
    video_id: int
    total_count: int


class ChapterInfo(BaseModel):
    """Simplified chapter info for public video responses."""

    id: int
    title: str
    start_time: float
    end_time: Optional[float] = None


class ReorderChaptersRequest(BaseModel):
    """Request to reorder chapters."""

    chapter_ids: List[int] = Field(
        ...,
        min_length=1,
        max_length=MAX_CHAPTERS_PER_VIDEO,
        description="Chapter IDs in new order",
    )


# Auto-detect chapter source types
class ChapterDetectionSource(str, Enum):
    """Source for auto-detecting chapters."""

    METADATA = "metadata"  # Extract from video file metadata (ffprobe -show_chapters)
    TRANSCRIPTION = "transcription"  # Generate from transcription analysis
    BOTH = "both"  # Try metadata first, fall back to transcription


class AutoDetectChaptersRequest(BaseModel):
    """Request to auto-detect chapters for a video (Issue #493)."""

    source: ChapterDetectionSource = Field(
        default=ChapterDetectionSource.METADATA,
        description="Source for chapter detection: 'metadata', 'transcription', or 'both'",
    )
    min_chapter_length: int = Field(
        default=60,
        ge=10,
        le=600,
        description="Minimum seconds between chapters (to avoid too many short chapters)",
    )
    replace_existing: bool = Field(
        default=False,
        description="Whether to replace existing chapters (if False, fails when chapters exist)",
    )


class DetectedChapter(BaseModel):
    """A chapter detected from auto-detection."""

    title: str
    start_time: float
    end_time: Optional[float] = None
    source: str = Field(description="Where this chapter was detected from: 'metadata' or 'transcription'")


class AutoDetectChaptersResponse(BaseModel):
    """Response from auto-detect chapters endpoint."""

    video_id: int
    chapters_created: int
    source_used: str = Field(description="The source that provided the chapters")
    chapters: List[ChapterResponse]
    message: str


# ============ Sprite Sheet Models (Issue #413 Phase 7B) ============


class SpriteSheetInfo(BaseModel):
    """Sprite sheet info for timeline thumbnail previews."""

    base_url: str = Field(..., description="Base URL for sprite sheets (e.g., /videos/{slug}/sprites/sprite_)")
    count: int = Field(..., ge=0, description="Number of sprite sheets")
    interval: int = Field(..., ge=1, description="Seconds between frames")
    tile_size: int = Field(..., ge=1, description="Grid size (e.g., 10 for 10x10)")
    frame_width: int = Field(..., ge=1, description="Width of each thumbnail frame in pixels")
    frame_height: int = Field(..., ge=1, description="Height of each thumbnail frame in pixels")


class SpriteGenerationRequest(BaseModel):
    """Request to generate sprites for a video."""

    priority: str = Field(default="normal", pattern="^(high|normal|low)$", description="Queue priority")


class SpriteStatusResponse(BaseModel):
    """Response for sprite sheet status."""

    video_id: int
    status: Optional[str] = None  # pending, generating, ready, failed, or None if never requested
    error: Optional[str] = None
    count: int = 0
    interval: Optional[int] = None
    tile_size: Optional[int] = None
    frame_width: Optional[int] = None
    frame_height: Optional[int] = None


class SpriteQueueStatusResponse(BaseModel):
    """Response for sprite queue status overview."""

    pending: int = 0
    processing: int = 0
    completed: int = 0
    failed: int = 0
    total: int = 0


class SpriteQueueJob(BaseModel):
    """A single sprite generation job in the queue."""

    id: int
    video_id: int
    video_slug: str
    video_title: str
    priority: str
    status: str
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None


class SpriteQueueJobsResponse(BaseModel):
    """Response for listing sprite queue jobs."""

    jobs: List[SpriteQueueJob]
    count: int
    total: int


# ============ Webhook Models (Issue #203) ============

# SSRF Protection: Blocked IP ranges for webhook URLs
# These ranges are internal/private networks that should never be webhook targets
BLOCKED_IP_RANGES = [
    ipaddress.ip_network("0.0.0.0/8"),  # Current network
    ipaddress.ip_network("10.0.0.0/8"),  # Private network
    ipaddress.ip_network("127.0.0.0/8"),  # Loopback
    ipaddress.ip_network("169.254.0.0/16"),  # Link-local (AWS metadata)
    ipaddress.ip_network("172.16.0.0/12"),  # Private network
    ipaddress.ip_network("192.168.0.0/16"),  # Private network
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),  # IPv6 unique local
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
]

# Known cloud metadata endpoints that must be blocked
BLOCKED_HOSTNAMES = {
    "metadata.google.internal",
    "metadata.google.internal.",
    "169.254.169.254",  # AWS/GCP/Azure metadata
}

# Minimum secret length for HMAC signing security
MINIMUM_SECRET_LENGTH = 32


def validate_webhook_url(url: str) -> str:
    """
    Validate webhook URL for SSRF protection.

    Blocks:
    - Internal/private IP ranges (10.x, 172.16.x, 192.168.x, 127.x)
    - Cloud metadata endpoints (169.254.169.254)
    - Link-local addresses
    - IPv6 private ranges

    Args:
        url: The webhook URL to validate

    Returns:
        The validated URL

    Raises:
        ValueError: If URL is invalid or targets a blocked address
    """
    # Basic URL format validation
    if not url.startswith(("http://", "https://")):
        raise ValueError("URL must start with http:// or https://")

    try:
        parsed = urlparse(url)
    except Exception:
        raise ValueError("Invalid URL format")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("URL must have a valid hostname")

    # Normalize hostname for comparison
    hostname_lower = hostname.lower()

    # Block known metadata hostnames
    if hostname_lower in BLOCKED_HOSTNAMES or hostname_lower.rstrip(".") in BLOCKED_HOSTNAMES:
        raise ValueError("URL targets a blocked hostname (cloud metadata endpoint)")

    # Try to resolve hostname and check IP
    try:
        # Get all IP addresses for the hostname
        addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for family, _, _, _, sockaddr in addr_info:
            if family == socket.AF_INET:
                ip = ipaddress.ip_address(sockaddr[0])
            elif family == socket.AF_INET6:
                ip = ipaddress.ip_address(sockaddr[0])
            else:
                continue

            # Check against blocked ranges
            for blocked_range in BLOCKED_IP_RANGES:
                if ip in blocked_range:
                    raise ValueError(f"URL targets a blocked IP range ({ip} is in {blocked_range})")

    except socket.gaierror:
        # DNS resolution failed - allow for now, will fail on actual delivery
        # This allows webhooks to be configured before DNS is set up
        pass
    except ValueError as e:
        # Re-raise validation errors
        if "blocked" in str(e).lower():
            raise
        # Other IP parsing errors are ignored
        pass

    return url


class WebhookEventType(str, Enum):
    """Supported webhook event types."""

    VIDEO_UPLOADED = "video.uploaded"
    VIDEO_READY = "video.ready"
    VIDEO_FAILED = "video.failed"
    VIDEO_DELETED = "video.deleted"
    VIDEO_RESTORED = "video.restored"
    TRANSCRIPTION_COMPLETED = "transcription.completed"
    WORKER_REGISTERED = "worker.registered"
    WORKER_OFFLINE = "worker.offline"


# Valid event type strings for validation
WEBHOOK_EVENT_TYPES: Set[str] = {e.value for e in WebhookEventType}


class WebhookCreate(BaseModel):
    """Request to create a new webhook subscription."""

    name: str = Field(..., min_length=1, max_length=100, description="Human-readable webhook name")
    url: str = Field(..., min_length=10, max_length=500, description="Webhook endpoint URL")
    events: List[str] = Field(
        ...,
        min_length=1,
        max_length=20,
        description="List of event types to subscribe to",
    )
    secret: Optional[str] = Field(
        default=None,
        max_length=64,
        description="Secret key for HMAC-SHA256 payload signing",
    )
    active: bool = Field(default=True, description="Whether the webhook is active")
    headers: Optional[dict] = Field(
        default=None,
        description="Custom headers to include in webhook requests",
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        # Use SSRF-protected URL validation
        return validate_webhook_url(v)

    @field_validator("events")
    @classmethod
    def validate_events(cls, v: List[str]) -> List[str]:
        for event in v:
            if event not in WEBHOOK_EVENT_TYPES:
                valid_events = ", ".join(sorted(WEBHOOK_EVENT_TYPES))
                raise ValueError(f"Invalid event type: {event}. Valid types: {valid_events}")
        return v

    @field_validator("secret")
    @classmethod
    def validate_secret(cls, v: Optional[str]) -> Optional[str]:
        # Enforce minimum secret length for security
        if v is not None and len(v) < MINIMUM_SECRET_LENGTH:
            raise ValueError(f"Secret must be at least {MINIMUM_SECRET_LENGTH} characters for security")
        return v


class WebhookUpdate(BaseModel):
    """Request to update an existing webhook."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    url: Optional[str] = Field(default=None, min_length=10, max_length=500)
    events: Optional[List[str]] = Field(default=None, min_length=1, max_length=20)
    secret: Optional[str] = Field(default=None, max_length=64)
    active: Optional[bool] = None
    headers: Optional[dict] = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            # Use SSRF-protected URL validation
            return validate_webhook_url(v)
        return v

    @field_validator("events")
    @classmethod
    def validate_events(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is not None:
            for event in v:
                if event not in WEBHOOK_EVENT_TYPES:
                    valid_events = ", ".join(sorted(WEBHOOK_EVENT_TYPES))
                    raise ValueError(f"Invalid event type: {event}. Valid types: {valid_events}")
        return v

    @field_validator("secret")
    @classmethod
    def validate_secret(cls, v: Optional[str]) -> Optional[str]:
        # Enforce minimum secret length for security
        if v is not None and len(v) < MINIMUM_SECRET_LENGTH:
            raise ValueError(f"Secret must be at least {MINIMUM_SECRET_LENGTH} characters for security")
        return v


class WebhookResponse(BaseModel):
    """Response for a single webhook."""

    id: int
    name: str
    url: str
    events: List[str]
    active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None
    last_triggered_at: Optional[datetime] = None
    total_deliveries: int = 0
    successful_deliveries: int = 0
    failed_deliveries: int = 0
    # Secret is never returned for security
    has_secret: bool = False


class WebhookListResponse(BaseModel):
    """Response for webhook listing."""

    webhooks: List[WebhookResponse]
    total_count: int


class WebhookDeliveryResponse(BaseModel):
    """Response for a single webhook delivery attempt."""

    id: int
    webhook_id: int
    event_type: str
    status: str  # pending, delivered, failed, failed_permanent
    attempt_number: int
    response_status: Optional[int] = None
    error_message: Optional[str] = None
    duration_ms: Optional[int] = None
    created_at: datetime
    next_retry_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None


class WebhookDeliveryDetailResponse(WebhookDeliveryResponse):
    """Detailed response for a webhook delivery including payload data."""

    event_data: Optional[dict] = None
    request_body: Optional[str] = None
    response_body: Optional[str] = None


class WebhookDeliveryListResponse(BaseModel):
    """Response for webhook delivery listing."""

    deliveries: List[WebhookDeliveryResponse]
    count: int
    total: int


class WebhookTestRequest(BaseModel):
    """Request to test a webhook with a sample payload."""

    event_type: str = Field(
        default="video.ready",
        description="Event type to simulate",
    )

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, v: str) -> str:
        if v not in WEBHOOK_EVENT_TYPES:
            valid_events = ", ".join(sorted(WEBHOOK_EVENT_TYPES))
            raise ValueError(f"Invalid event type: {v}. Valid types: {valid_events}")
        return v


class WebhookTestResponse(BaseModel):
    """Response from testing a webhook."""

    success: bool
    status_code: Optional[int] = None
    response_body: Optional[str] = None
    error_message: Optional[str] = None
    duration_ms: int


class WebhookStatsResponse(BaseModel):
    """Overview statistics for all webhooks."""

    total_webhooks: int = 0
    active_webhooks: int = 0
    pending_deliveries: int = 0
    failed_deliveries: int = 0
    total_deliveries_24h: int = 0
    successful_deliveries_24h: int = 0
