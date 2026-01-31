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


class QualityPreset(str, Enum):
    """Quality preset for stream transcoding (Issue #530 - Phase 2E)."""

    AUTO = "auto"  # Let the system choose based on input
    LOW = "low"  # 480p only
    MEDIUM = "medium"  # Up to 720p
    HIGH = "high"  # Up to 1080p
    SOURCE = "source"  # Pass through original quality


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
    # Additional controls (Phase 2E)
    stream_delay_seconds: int = 0
    quality_preset: QualityPreset = QualityPreset.AUTO
    scheduled_at: Optional[datetime] = None


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
    # Additional controls (Phase 2E)
    stream_delay_seconds: int = Field(default=0, ge=0, le=900)  # Max 15 minutes
    quality_preset: QualityPreset = QualityPreset.AUTO
    scheduled_at: Optional[datetime] = None

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
    # Additional controls (Phase 2E)
    stream_delay_seconds: Optional[int] = Field(None, ge=0, le=900)  # Max 15 minutes
    quality_preset: Optional[QualityPreset] = None
    scheduled_at: Optional[datetime] = None

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
    # Additional controls (Phase 2E)
    stream_delay_seconds: int = 0
    quality_preset: QualityPreset = QualityPreset.AUTO
    scheduled_at: Optional[datetime] = None


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


# =============================================================================
# Studio VOD Management API Schemas (Issue #530)
# =============================================================================


class StudioVODResponse(BaseModel):
    """Response for a VOD in the studio dashboard."""

    id: int
    title: str
    slug: str
    description: str
    status: str  # pending, processing, ready, failed
    duration: float
    source_width: int
    source_height: int
    category_id: Optional[int] = None
    thumbnail_url: Optional[str] = None
    created_at: datetime
    published_at: Optional[datetime] = None
    # Link back to the source stream
    stream_id: Optional[int] = None
    stream_slug: Optional[str] = None
    stream_title: Optional[str] = None


class StudioVODListResponse(BaseModel):
    """Response for listing VODs in studio dashboard."""

    vods: List[StudioVODResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class StudioVODUpdate(BaseModel):
    """Request to update VOD metadata from studio."""

    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=5000)
    category_id: Optional[int] = None

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("Title cannot be empty or whitespace")
        return v


class StudioVODAnalyticsResponse(BaseModel):
    """Analytics summary for a VOD."""

    vod_id: int
    total_views: int
    unique_viewers: int
    total_watch_time_seconds: float
    average_watch_time_seconds: float
    completion_rate: float  # Percentage of viewers who watched >= 90%
    peak_concurrent_viewers: Optional[int] = None
    view_history: List[dict] = []  # Daily view counts


class StudioVODDownloadResponse(BaseModel):
    """Response with download URL for a VOD."""

    download_url: str
    filename: str
    expires_at: datetime


# =============================================================================
# Studio Chat API Schemas (Issue #530)
# =============================================================================


class ChatMessageResponse(BaseModel):
    """Response for a single chat message."""

    id: int
    stream_id: int
    user_id: Optional[str] = None
    username: Optional[str] = None
    content: str
    stream_offset_ms: Optional[int] = None
    created_at: datetime
    deleted_at: Optional[datetime] = None
    deleted_by_username: Optional[str] = None


class ChatMessageListResponse(BaseModel):
    """Response for listing chat messages."""

    messages: List[ChatMessageResponse]
    total: int
    has_more: bool
    before_id: Optional[int] = None  # For pagination


class ChatMessageSend(BaseModel):
    """Request to send a chat message."""

    content: str = Field(..., min_length=1, max_length=500)

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Message cannot be empty or whitespace")
        return v


class ChatSettingsResponse(BaseModel):
    """Chat settings for a stream."""

    stream_id: int
    chat_enabled: bool
    chat_slow_mode_seconds: int
    chat_subscriber_only: bool
    chat_follower_only: bool
    chat_follower_min_minutes: int
    chat_emote_only: bool
    chat_links_allowed: bool


class ChatSettingsUpdate(BaseModel):
    """Request to update chat settings."""

    chat_enabled: Optional[bool] = None
    chat_slow_mode_seconds: Optional[int] = Field(None, ge=0, le=120)
    chat_subscriber_only: Optional[bool] = None
    chat_follower_only: Optional[bool] = None
    chat_follower_min_minutes: Optional[int] = Field(None, ge=0, le=1440)  # Max 24 hours
    chat_emote_only: Optional[bool] = None
    chat_links_allowed: Optional[bool] = None


class StreamModeratorResponse(BaseModel):
    """Response for a stream moderator."""

    id: int
    stream_id: int
    user_id: str
    username: str
    permissions: List[str]
    granted_by_id: Optional[str] = None
    granted_by_username: Optional[str] = None
    granted_at: datetime


class StreamModeratorListResponse(BaseModel):
    """Response for listing stream moderators."""

    moderators: List[StreamModeratorResponse]
    total: int


class StreamModeratorAdd(BaseModel):
    """Request to add a stream moderator."""

    user_id: str = Field(..., min_length=1)
    permissions: List[str] = Field(
        default=["delete_message", "timeout"],
        description="Permissions: delete_message, timeout, ban",
    )

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, v: List[str]) -> List[str]:
        valid_perms = {"delete_message", "timeout", "ban"}
        for perm in v:
            if perm not in valid_perms:
                raise ValueError(f"Invalid permission: {perm}. Valid: {valid_perms}")
        return list(set(v))  # Deduplicate


class StreamModeratorUpdate(BaseModel):
    """Request to update moderator permissions."""

    permissions: List[str] = Field(
        ..., description="Permissions: delete_message, timeout, ban"
    )

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, v: List[str]) -> List[str]:
        valid_perms = {"delete_message", "timeout", "ban"}
        for perm in v:
            if perm not in valid_perms:
                raise ValueError(f"Invalid permission: {perm}. Valid: {valid_perms}")
        return list(set(v))


# =============================================================================
# WebSocket Protocol Schemas (Issue #530)
# =============================================================================


class WSMessageType(str, Enum):
    """WebSocket message types."""

    # Client → Server
    MESSAGE = "message"
    DELETE = "delete"
    PING = "ping"

    # Server → Client
    CHAT_MESSAGE = "chat_message"
    MESSAGE_DELETED = "message_deleted"
    USER_TIMEOUT = "user_timeout"
    USER_BAN = "user_ban"
    USER_UNBAN = "user_unban"
    SETTINGS_UPDATED = "settings_updated"
    ERROR = "error"
    PONG = "pong"
    CONNECTED = "connected"


class WSClientMessage(BaseModel):
    """Message from client to server via WebSocket."""

    type: WSMessageType
    content: Optional[str] = Field(None, max_length=500)  # For message type
    message_id: Optional[int] = None  # For delete type


class WSServerMessage(BaseModel):
    """Message from server to client via WebSocket."""

    type: WSMessageType
    # Chat message fields
    id: Optional[int] = None
    user_id: Optional[str] = None
    username: Optional[str] = None
    content: Optional[str] = None
    timestamp: Optional[datetime] = None
    # Moderation fields
    message_id: Optional[int] = None
    deleted_by: Optional[str] = None
    target_user_id: Optional[str] = None
    target_username: Optional[str] = None
    duration_seconds: Optional[int] = None
    reason: Optional[str] = None
    # Settings fields
    settings: Optional[ChatSettingsResponse] = None
    # Error fields
    code: Optional[str] = None
    error: Optional[str] = None
    retry_after: Optional[int] = None


class WSConnectedMessage(BaseModel):
    """Connection confirmation message."""

    type: WSMessageType = WSMessageType.CONNECTED
    user_id: str
    username: str
    is_moderator: bool
    is_owner: bool
    settings: ChatSettingsResponse


# =============================================================================
# Stream Moderation Schemas (Issue #530 - Phase 2C)
# =============================================================================


class BanType(str, Enum):
    """Types of user bans."""

    TIMEOUT = "timeout"
    PERMANENT = "permanent"


class FilterAction(str, Enum):
    """Actions for word filter matches."""

    DELETE = "delete"
    TIMEOUT = "timeout"
    WARN = "warn"


class StreamBanResponse(BaseModel):
    """Response for a stream ban."""

    id: int
    stream_id: int
    user_id: str
    username: Optional[str] = None
    ban_type: BanType
    duration_seconds: Optional[int] = None
    reason: Optional[str] = None
    banned_by_id: Optional[str] = None
    banned_by_username: Optional[str] = None
    created_at: datetime
    expires_at: Optional[datetime] = None
    unbanned_at: Optional[datetime] = None
    is_active: bool  # Computed: unbanned_at is None AND (expires_at is None OR expires_at > now)


class StreamBanListResponse(BaseModel):
    """Response for listing stream bans."""

    bans: List[StreamBanResponse]
    total: int
    has_more: bool


class StreamBanCreate(BaseModel):
    """Request to ban or timeout a user."""

    user_id: str = Field(..., min_length=1)
    ban_type: BanType = BanType.TIMEOUT
    duration_seconds: Optional[int] = Field(None, ge=1, le=2592000)  # Max 30 days
    reason: Optional[str] = Field(None, max_length=500)

    @field_validator("duration_seconds")
    @classmethod
    def validate_duration(cls, v, info):
        # duration_seconds required for timeouts
        if info.data.get("ban_type") == BanType.TIMEOUT and v is None:
            raise ValueError("duration_seconds required for timeout")
        return v


class WordFilterResponse(BaseModel):
    """Response for a word filter."""

    id: int
    stream_id: int
    pattern: str
    is_regex: bool
    action: FilterAction
    timeout_seconds: Optional[int] = None
    created_at: datetime
    created_by_id: Optional[str] = None
    created_by_username: Optional[str] = None


class WordFilterListResponse(BaseModel):
    """Response for listing word filters."""

    filters: List[WordFilterResponse]
    total: int


class WordFilterCreate(BaseModel):
    """Request to create a word filter."""

    pattern: str = Field(..., min_length=1, max_length=100)  # Max 100 chars (ReDoS protection)
    is_regex: bool = False
    action: FilterAction = FilterAction.DELETE
    timeout_seconds: Optional[int] = Field(None, ge=1, le=86400)  # Max 24 hours

    @field_validator("pattern")
    @classmethod
    def validate_pattern(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Pattern cannot be empty")
        return v

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, v, info):
        # timeout_seconds required if action is timeout
        if info.data.get("action") == FilterAction.TIMEOUT and v is None:
            raise ValueError("timeout_seconds required for timeout action")
        return v


class WordFilterUpdate(BaseModel):
    """Request to update a word filter."""

    pattern: Optional[str] = Field(None, min_length=1, max_length=100)
    is_regex: Optional[bool] = None
    action: Optional[FilterAction] = None
    timeout_seconds: Optional[int] = Field(None, ge=1, le=86400)


class ModerationLogResponse(BaseModel):
    """Response for a moderation log entry."""

    id: int
    stream_id: int
    moderator_id: Optional[str] = None
    moderator_username: Optional[str] = None
    action: str
    target_user_id: Optional[str] = None
    target_username: Optional[str] = None
    target_message_id: Optional[int] = None
    details: Optional[dict] = None
    created_at: datetime


class ModerationLogListResponse(BaseModel):
    """Response for listing moderation logs."""

    logs: List[ModerationLogResponse]
    total: int
    has_more: bool


# =============================================================================
# Stream Analytics Schemas (Issue #530 - Phase 2D)
# =============================================================================


class ViewerCountResponse(BaseModel):
    """Response for a single viewer count data point."""

    recorded_at: datetime
    viewer_count: int


class StreamAnalyticsSummaryResponse(BaseModel):
    """Response for stream analytics summary."""

    stream_id: int
    # Viewer metrics
    peak_viewers: int
    average_viewers: float
    total_unique_viewers: int
    # Chat metrics
    total_chat_messages: int
    # Watch time metrics
    total_watch_minutes: float
    average_watch_time_seconds: float
    # Stream info
    stream_duration_seconds: int
    computed_at: Optional[datetime] = None


class ViewerHistoryResponse(BaseModel):
    """Response for viewer count history."""

    stream_id: int
    data_points: List[ViewerCountResponse]
    total_points: int


class StreamAnalyticsResponse(BaseModel):
    """Combined analytics response for a stream."""

    summary: StreamAnalyticsSummaryResponse
    viewer_history: List[ViewerCountResponse]
