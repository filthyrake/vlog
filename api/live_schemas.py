"""Pydantic schemas for live streaming API.

Defines request/response models for:
- Admin API: Create, update, list streams
- Ingest API: Segment upload status
- Public API: Stream info for viewers
"""

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class LiveStreamStatus(str, Enum):
    """Live stream status values."""

    IDLE = "idle"
    LIVE = "live"
    ENDING = "ending"
    ENDED = "ended"


# =============================================================================
# Admin API Schemas
# =============================================================================


class LiveStreamCreate(BaseModel):
    """Request to create a new live stream."""

    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=5000)
    category_id: Optional[int] = None
    dvr_enabled: bool = Field(default=True)
    dvr_window_seconds: int = Field(default=7200, ge=60, le=86400)  # 1 min to 24 hours
    auto_record_vod: bool = Field(default=True)

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Title cannot be empty or whitespace")
        return v


class LiveStreamUpdate(BaseModel):
    """Request to update a live stream."""

    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=5000)
    category_id: Optional[int] = None
    dvr_enabled: Optional[bool] = None
    dvr_window_seconds: Optional[int] = Field(None, ge=60, le=86400)
    auto_record_vod: Optional[bool] = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("Title cannot be empty or whitespace")
        return v


class LiveStreamCreatedResponse(BaseModel):
    """Response after creating a live stream. Includes the stream key (shown once)."""

    id: int
    title: str
    slug: str
    description: str
    status: LiveStreamStatus
    stream_key: str  # Only shown once at creation
    category_id: Optional[int] = None
    dvr_enabled: bool
    dvr_window_seconds: int
    auto_record_vod: bool
    created_at: datetime


class LiveStreamResponse(BaseModel):
    """Response for a live stream (without stream key)."""

    id: int
    title: str
    slug: str
    description: str
    status: LiveStreamStatus
    qualities: Optional[List[str]] = None
    category_id: Optional[int] = None
    dvr_enabled: bool
    dvr_window_seconds: int
    auto_record_vod: bool
    segment_count: int
    vod_video_id: Optional[int] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    last_segment_at: Optional[datetime] = None


class LiveStreamListResponse(BaseModel):
    """Response for listing live streams."""

    streams: List[LiveStreamResponse]
    total: int


class LiveStreamKeyRegenerateResponse(BaseModel):
    """Response after regenerating a stream key."""

    stream_key: str  # New key, shown once


# =============================================================================
# Ingest API Schemas
# =============================================================================


class IngestStatusResponse(BaseModel):
    """Response for ingest status endpoint."""

    stream_id: int
    slug: str
    status: LiveStreamStatus
    segment_count: int
    qualities: List[str]
    last_segment_at: Optional[datetime] = None


class SegmentUploadResponse(BaseModel):
    """Response after successful segment upload."""

    received: bool = True
    sequence_number: int
    quality: str


# =============================================================================
# Public API Schemas
# =============================================================================


class PublicLiveStreamResponse(BaseModel):
    """Public-facing stream info for viewers."""

    title: str
    slug: str
    description: str
    status: LiveStreamStatus
    qualities: List[str]
    category_id: Optional[int] = None
    started_at: Optional[datetime] = None
    dvr_enabled: bool
    dvr_window_seconds: int


class PublicLiveStreamListResponse(BaseModel):
    """List of live streams for public viewing."""

    streams: List[PublicLiveStreamResponse]
    total: int


# =============================================================================
# Metrics API Schemas (Issue #524)
# =============================================================================


class MetricDataPoint(BaseModel):
    """Single metrics data point."""

    timestamp: datetime
    bitrate_video: Optional[int] = None
    bitrate_audio: Optional[int] = None
    bitrate_total: Optional[int] = None
    segment_push_latency_ms: Optional[int] = None
    segments_received: int = 0
    segments_dropped: int = 0
    interval_seconds: int = 10


class StreamMetricsResponse(BaseModel):
    """Response for stream metrics endpoint."""

    stream_id: int
    current_bitrate: Optional[int] = None
    connection_health: str = "unknown"
    last_metric_at: Optional[datetime] = None
    metrics: List[MetricDataPoint] = []


class StreamMetricsHistoryRequest(BaseModel):
    """Request for historical metrics."""

    start: datetime
    end: datetime


# =============================================================================
# Viewer Tracking Schemas (Issue #524)
# =============================================================================


class ViewerJoinRequest(BaseModel):
    """Request to join as a viewer (optional quality)."""

    quality: Optional[str] = Field(None, max_length=10)


class ViewerJoinResponse(BaseModel):
    """Response after joining a stream (includes session ID for heartbeat)."""

    session_id: str
    joined_at: datetime


class ViewerHeartbeatRequest(BaseModel):
    """Request for viewer heartbeat (keep-alive)."""

    session_id: str = Field(..., max_length=64)
    quality: Optional[str] = Field(None, max_length=10)


class ViewerHeartbeatResponse(BaseModel):
    """Response for heartbeat."""

    success: bool


class ViewerLeaveRequest(BaseModel):
    """Request to leave a stream."""

    session_id: str = Field(..., max_length=64)


class ViewerStatsResponse(BaseModel):
    """Response for viewer statistics."""

    current: int
    peak: int
    total: int
    quality_distribution: dict


class ActiveViewerInfo(BaseModel):
    """Info about an active viewer (for broadcaster dashboard)."""

    session_id_prefix: str  # Only first 8 chars for privacy
    user_id: Optional[str] = None
    joined_at: Optional[datetime] = None
    quality: Optional[str] = None


class ActiveViewersResponse(BaseModel):
    """Response for active viewers list."""

    viewers: List[ActiveViewerInfo]
    total: int


# =============================================================================
# Studio API Schemas (Issue #524)
# =============================================================================


class StudioStreamResponse(BaseModel):
    """Stream info for studio dashboard (includes health metrics)."""

    id: int
    title: str
    slug: str
    description: str
    status: LiveStreamStatus
    qualities: Optional[List[str]] = None
    category_id: Optional[int] = None
    # Health metrics
    current_bitrate: Optional[int] = None
    connection_health: str = "unknown"
    # Viewer counts
    viewer_count_current: int = 0
    viewer_count_peak: int = 0
    viewer_count_total: int = 0
    # Timestamps
    created_at: datetime
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    last_segment_at: Optional[datetime] = None
    last_metric_at: Optional[datetime] = None


class StudioStreamListResponse(BaseModel):
    """List of streams for studio dashboard."""

    streams: List[StudioStreamResponse]
    total: int


class StreamKeyRequest(BaseModel):
    """Request to get stream key (requires password re-entry)."""

    current_password: str = Field(..., min_length=1)


class StreamKeyResponse(BaseModel):
    """Response containing the stream key."""

    stream_key: str


class StudioStreamUpdateRequest(BaseModel):
    """Request to update stream metadata from studio."""

    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, max_length=2000)
    category_id: Optional[int] = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            # Strip HTML tags for security
            import re
            v = re.sub(r"<[^>]+>", "", v).strip()
            if not v:
                raise ValueError("Title cannot be empty or contain only HTML tags")
        return v

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            # Strip potentially dangerous HTML
            import re
            v = re.sub(r"<script[^>]*>.*?</script>", "", v, flags=re.IGNORECASE | re.DOTALL)
            v = re.sub(r"on\w+\s*=", "", v, flags=re.IGNORECASE)
        return v
