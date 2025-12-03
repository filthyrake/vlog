from pydantic import BaseModel
from datetime import datetime
from typing import List, Optional


class CategoryCreate(BaseModel):
    name: str
    description: str = ""


class CategoryResponse(BaseModel):
    id: int
    name: str
    slug: str
    description: str
    created_at: datetime
    video_count: int = 0


class VideoCreate(BaseModel):
    title: str
    description: str = ""
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


class PlaybackEnd(BaseModel):
    session_token: str
    position: float
    completed: bool = False


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
    language: Optional[str] = None  # Optional language hint


class TranscriptionUpdate(BaseModel):
    text: str  # Manually corrected transcript text
