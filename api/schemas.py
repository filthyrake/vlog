import math
from pydantic import BaseModel, Field, validator
from datetime import datetime
from typing import List, Optional, Set

# Maximum position value (24 hours in seconds)
MAX_POSITION_SECONDS = 86400

# Whisper-supported language codes (ISO 639-1)
# Full list from OpenAI Whisper documentation
WHISPER_LANGUAGES: Set[str] = {
    "af", "am", "ar", "as", "az", "ba", "be", "bg", "bn", "bo", "br", "bs",
    "ca", "cs", "cy", "da", "de", "el", "en", "es", "et", "eu", "fa", "fi",
    "fo", "fr", "gl", "gu", "ha", "haw", "he", "hi", "hr", "ht", "hu", "hy",
    "id", "is", "it", "ja", "jw", "ka", "kk", "km", "kn", "ko", "la", "lb",
    "ln", "lo", "lt", "lv", "mg", "mi", "mk", "ml", "mn", "mr", "ms", "mt",
    "my", "ne", "nl", "nn", "no", "oc", "pa", "pl", "ps", "pt", "ro", "ru",
    "sa", "sd", "si", "sk", "sl", "sn", "so", "sq", "sr", "su", "sv", "sw",
    "ta", "te", "tg", "th", "tk", "tl", "tr", "tt", "uk", "ur", "uz", "vi",
    "yi", "yo", "zh", "yue",
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


class VideoResponse(BaseModel):
    id: int
    title: str
    slug: str
    description: str
    category_id: Optional[int]
    category_name: Optional[str] = None
    category_slug: Optional[str] = None
    duration: float
    source_width: int
    source_height: int
    status: str
    error_message: Optional[str]
    created_at: datetime
    published_at: Optional[datetime]
    thumbnail_url: Optional[str] = None
    stream_url: Optional[str] = None
    captions_url: Optional[str] = None  # WebVTT captions URL
    transcription_status: Optional[str] = None  # pending, processing, completed, failed
    qualities: List[VideoQualityResponse] = []


class VideoListResponse(BaseModel):
    id: int
    title: str
    slug: str
    description: str
    category_id: Optional[int]
    category_name: Optional[str] = None
    duration: float
    status: str
    created_at: datetime
    published_at: Optional[datetime]
    thumbnail_url: Optional[str] = None


# Analytics request models
class PlaybackSessionCreate(BaseModel):
    video_id: int
    quality: Optional[str] = None


class PlaybackHeartbeat(BaseModel):
    session_token: str
    position: float
    quality: Optional[str] = None
    playing: bool = True

    @validator('position')
    def validate_position(cls, v):
        if not math.isfinite(v):
            raise ValueError('position must be a finite number')
        if v < 0:
            raise ValueError('position must be non-negative')
        if v > MAX_POSITION_SECONDS:
            raise ValueError(f'position exceeds maximum allowed value ({MAX_POSITION_SECONDS}s)')
        return v


class PlaybackEnd(BaseModel):
    session_token: str
    position: float
    completed: bool = False

    @validator('position')
    def validate_position(cls, v):
        if not math.isfinite(v):
            raise ValueError('position must be a finite number')
        if v < 0:
            raise ValueError('position must be non-negative')
        if v > MAX_POSITION_SECONDS:
            raise ValueError(f'position exceeds maximum allowed value ({MAX_POSITION_SECONDS}s)')
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

    @validator('language')
    def validate_language(cls, v):
        if v is not None:
            v = v.lower().strip()
            if v not in WHISPER_LANGUAGES:
                raise ValueError(f"Invalid language code: '{v}'. Must be a valid ISO 639-1 code supported by Whisper.")
        return v


class TranscriptionUpdate(BaseModel):
    text: str = Field(..., min_length=1, max_length=500000)  # 500KB max transcript
