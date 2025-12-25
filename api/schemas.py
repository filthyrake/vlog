import math
from datetime import datetime
from typing import Any, List, Optional, Set

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
    captions_url: Optional[str] = None  # WebVTT captions URL
    transcription_status: Optional[str] = None  # pending, processing, completed, failed
    qualities: List[VideoQualityResponse] = []
    tags: List[VideoTagInfo] = []

    @field_validator("description", mode="before")
    @classmethod
    def default_description(cls, v):
        return v if v is not None else ""

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
    tags: List[VideoTagInfo] = []

    @field_validator("description", mode="before")
    @classmethod
    def default_description(cls, v):
        return v if v is not None else ""

    @field_validator("created_at", mode="before")
    @classmethod
    def default_created_at(cls, v):
        from datetime import timezone

        return v if v is not None else datetime.now(timezone.utc)


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
