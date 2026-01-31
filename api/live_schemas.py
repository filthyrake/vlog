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
# Studio/Broadcaster Dashboard API Schemas (Issue #524)
# =============================================================================


class StudioStreamResponse(BaseModel):
    """Response for a stream in the studio dashboard (without stream key)."""

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


class StudioStreamListResponse(BaseModel):
    """Response for listing streams in studio dashboard."""

    streams: List[StudioStreamResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class StudioStreamCreate(BaseModel):
    """Request to create a new stream from studio."""

    title: str = Field(..., min_length=1, max_length=255)
    description: str = Field(default="", max_length=5000)
    category_id: Optional[int] = None
    dvr_enabled: bool = Field(default=True)
    dvr_window_seconds: int = Field(default=7200, ge=60, le=86400)
    auto_record_vod: bool = Field(default=True)

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Title cannot be empty or whitespace")
        return v


class StudioStreamUpdate(BaseModel):
    """Request to update a stream from studio."""

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


class StudioStreamCreatedResponse(BaseModel):
    """Response after creating a stream. Includes stream key (shown ONCE)."""

    id: int
    title: str
    slug: str
    description: str
    status: LiveStreamStatus
    stream_key: str  # Only shown once at creation - NEVER retrievable again
    rtmp_url: str
    category_id: Optional[int] = None
    dvr_enabled: bool
    dvr_window_seconds: int
    auto_record_vod: bool
    created_at: datetime
    warning: str = "Save this key now. It will not be shown again."


class StudioStreamKeyResponse(BaseModel):
    """Response after regenerating stream key (shown ONCE)."""

    stream_key: str  # New key - shown once, NEVER retrievable again
    rtmp_url: str
    warning: str = "Save this key now. It will not be shown again."


class StudioMetricsEvent(BaseModel):
    """Real-time metrics event for studio SSE."""

    type: str = "metrics"
    stream_id: int
    stream_slug: str
    status: LiveStreamStatus
    segment_count: int
    qualities: List[str]
    bitrate_kbps: Optional[int] = None
    last_segment_at: Optional[datetime] = None
    timestamp: datetime
