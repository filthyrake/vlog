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
    duration: float
    source_width: int
    source_height: int
    status: str
    error_message: Optional[str]
    created_at: datetime
    published_at: Optional[datetime]
    thumbnail_url: Optional[str] = None
    stream_url: Optional[str] = None
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
