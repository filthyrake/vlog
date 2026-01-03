"""Pydantic schemas for Worker API endpoints."""

import json
import re
from datetime import datetime
from typing import Annotated, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# UUID4 regex pattern for validation
UUID4_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.IGNORECASE)


def validate_uuid4(value: str) -> str:
    """Validate that a string is a valid UUID4 format."""
    if not UUID4_PATTERN.match(value):
        raise ValueError(f"Invalid UUID4 format: {value}")
    return value.lower()  # Normalize to lowercase


# Type alias for worker_id fields with UUID4 validation
WorkerIdStr = Annotated[str, Field(description="Worker UUID4 identifier")]


# GPU and Hardware Acceleration Capabilities
class GPUInfo(BaseModel):
    """GPU hardware information reported by workers."""

    hwaccel_type: str = Field(default="none", description="Hardware acceleration type: nvidia, intel, or none")
    gpu_name: Optional[str] = Field(default=None, description="GPU device name (e.g., 'NVIDIA GeForce RTX 3090')")
    driver_version: Optional[str] = Field(default=None, description="GPU driver version")
    cuda_version: Optional[str] = Field(default=None, description="CUDA version (NVIDIA only)")
    vaapi_device: Optional[str] = Field(
        default=None, description="VAAPI device path (Intel only, e.g., '/dev/dri/renderD128')"
    )


class WorkerCapabilities(BaseModel):
    """Detailed worker capabilities including GPU and encoding support."""

    model_config = ConfigDict(extra="forbid")  # Reject unknown fields

    hwaccel_enabled: bool = Field(default=False, description="Whether hardware acceleration is available")
    hwaccel_type: str = Field(
        default="none", max_length=20, description="Hardware acceleration type: nvidia, intel, or none"
    )
    gpu_name: Optional[str] = Field(default=None, max_length=200, description="GPU device name")
    code_version: Optional[str] = Field(
        default=None, max_length=64, description="Worker code version (git commit hash or semver tag)"
    )
    supported_codecs: List[str] = Field(
        default=["h264"],
        max_length=10,  # Max 10 codecs in the list
        description="List of supported codecs (h264, hevc, av1)",
    )
    encoders: Dict[str, List[str]] = Field(
        default={"h264": ["libx264"]},
        description="Available encoders by codec (e.g., {'h264': ['h264_nvenc', 'libx264']})",
    )
    max_concurrent_encode_sessions: int = Field(
        default=1, ge=1, le=100, description="Maximum concurrent encoding sessions (NVIDIA consumer GPUs: 3-5)"
    )
    ffmpeg_version: Optional[str] = Field(default=None, max_length=100, description="FFmpeg version string")
    driver_version: Optional[str] = Field(default=None, max_length=100, description="GPU driver version")
    cuda_version: Optional[str] = Field(default=None, max_length=100, description="CUDA version (NVIDIA only)")
    vaapi_device: Optional[str] = Field(default=None, max_length=200, description="VAAPI device path (Intel only)")

    @field_validator("supported_codecs")
    @classmethod
    def validate_codec_list(cls, v):
        """Ensure each codec name is reasonable length."""
        if v:
            for codec in v:
                if len(codec) > 20:
                    raise ValueError(f"Codec name too long: {codec}")
        return v

    @field_validator("encoders")
    @classmethod
    def validate_encoders(cls, v):
        """Ensure encoder names and codec keys are reasonable length."""
        if v:
            for codec, encoder_list in v.items():
                if len(codec) > 20:
                    raise ValueError(f"Codec key too long: {codec}")
                if len(encoder_list) > 20:
                    raise ValueError(f"Too many encoders for codec {codec}")
                for encoder in encoder_list:
                    if len(encoder) > 50:
                        raise ValueError(f"Encoder name too long: {encoder}")
        return v


class WorkerMetadata(BaseModel):
    """Worker metadata for Kubernetes pod info, etc."""

    model_config = ConfigDict(extra="forbid")  # Reject unknown fields

    # Deployment type: how this worker is deployed
    deployment_type: Optional[str] = Field(
        default=None,
        max_length=20,
        pattern="^(kubernetes|systemd|docker|manual)$",
        description="Deployment method: kubernetes, systemd, docker, or manual",
    )

    # Kubernetes pod information
    pod_name: Optional[str] = Field(default=None, max_length=253)
    pod_namespace: Optional[str] = Field(default=None, max_length=253)
    pod_uid: Optional[str] = Field(default=None, max_length=36)
    node_name: Optional[str] = Field(default=None, max_length=253)

    # Container information
    container_name: Optional[str] = Field(default=None, max_length=253)
    container_image: Optional[str] = Field(default=None, max_length=500)

    # Cloud/environment info
    cloud_provider: Optional[str] = Field(default=None, max_length=50)
    region: Optional[str] = Field(default=None, max_length=100)
    availability_zone: Optional[str] = Field(default=None, max_length=100)

    # Custom labels/annotations (limited to prevent abuse)
    labels: Optional[Dict[str, str]] = Field(default=None)

    @field_validator("labels")
    @classmethod
    def validate_labels(cls, v):
        """Ensure labels dict is reasonable size."""
        if v:
            if len(v) > 50:
                raise ValueError("Too many labels (max 50)")
            for key, value in v.items():
                if len(key) > 253:
                    raise ValueError(f"Label key too long: {key}")
                if len(value) > 500:
                    raise ValueError(f"Label value too long for key {key}")
        return v


# Worker registration
class WorkerRegisterRequest(BaseModel):
    worker_name: Optional[str] = Field(default=None, max_length=100)
    worker_type: str = Field(default="remote", pattern="^(local|remote)$")
    capabilities: Optional[WorkerCapabilities] = Field(
        default=None, description="Worker capabilities including GPU and encoding support"
    )
    metadata: Optional[WorkerMetadata] = Field(default=None, description="Worker metadata (e.g., kubernetes pod info)")


class WorkerRegisterResponse(BaseModel):
    worker_id: WorkerIdStr
    api_key: str  # Only returned once at registration
    message: str

    @field_validator("worker_id")
    @classmethod
    def validate_worker_id(cls, v):
        """Ensure worker_id is a valid UUID4."""
        return validate_uuid4(v)


# Heartbeat
class HeartbeatRequest(BaseModel):
    status: str = Field(default="active", pattern="^(active|busy|idle)$")
    code_version: Optional[str] = Field(
        default=None,
        description="Worker's code version (git commit hash). Used for version compatibility checks.",
        max_length=40,
    )
    metadata: Optional[Dict] = Field(
        default=None, description="Optional metadata dict containing 'capabilities' key with WorkerCapabilities"
    )

    @field_validator("metadata")
    @classmethod
    def validate_metadata_dict(cls, v):
        """Validate metadata dict size and contents."""
        if v:
            # Limit total number of keys
            if len(v) > 20:
                raise ValueError("Too many keys in metadata (max 20)")

            # Validate JSON serializability first
            try:
                serialized = json.dumps(v)
            except (TypeError, ValueError):
                raise ValueError("Metadata must be JSON-serializable")

            # Then check serialized size to match endpoint limit (10KB)
            if len(serialized) > 10000:  # 10KB max (10000 bytes)
                raise ValueError("Metadata too large (max 10KB)")

            # Validate capabilities structure if present
            if "capabilities" in v:
                try:
                    WorkerCapabilities(**v["capabilities"])
                except Exception as e:
                    raise ValueError(f"Invalid capabilities in metadata: {e}")
        return v


class HeartbeatResponse(BaseModel):
    status: str
    server_time: datetime
    required_version: Optional[str] = Field(
        default=None,
        description="Required code version. If set and doesn't match worker's version, worker should exit.",
    )
    version_ok: bool = Field(
        default=True,
        description="True if worker's code version matches required version. False means worker should exit.",
    )
    # Issue #458: Return server's view of worker state for stale data detection
    worker_status: Optional[str] = Field(
        default=None,
        description="Server's recorded status for this worker (active, busy, idle, offline, disabled)",
    )
    current_job_id: Optional[int] = Field(
        default=None,
        description="Job ID the server thinks this worker is processing (None if idle)",
    )
    last_heartbeat_recorded: Optional[datetime] = Field(
        default=None,
        description="When the server last recorded a heartbeat for this worker",
    )


# Job claiming
class ClaimJobResponse(BaseModel):
    job_id: Optional[int] = None
    video_id: Optional[int] = None
    video_slug: Optional[str] = None
    video_duration: Optional[float] = None
    source_width: Optional[int] = None
    source_height: Optional[int] = None
    source_filename: Optional[str] = None
    claim_expires_at: Optional[datetime] = None
    existing_qualities: Optional[List[str]] = None  # Qualities already transcoded (skip these)
    message: str


# Progress updates
class QualityProgressUpdate(BaseModel):
    name: str
    status: str = Field(pattern="^(pending|in_progress|uploading|completed|uploaded|failed|skipped)$")
    progress: int = Field(ge=0, le=100)
    # Segment tracking for streaming upload (Issue #478)
    segments_total: Optional[int] = Field(default=None, ge=0, description="Total segments expected")
    segments_completed: Optional[int] = Field(default=None, ge=0, description="Segments uploaded so far")


class ProgressUpdateRequest(BaseModel):
    current_step: Optional[str] = Field(
        default=None, pattern="^(download|probe|thumbnail|transcode|master_playlist|upload|finalize)$"
    )
    progress_percent: int = Field(ge=0, le=100)
    quality_progress: Optional[List[QualityProgressUpdate]] = None
    # Video metadata (updated after probing to prevent data loss if worker crashes)
    duration: Optional[float] = Field(default=None, ge=0, description="Video duration in seconds")
    source_width: Optional[int] = Field(default=None, ge=1, description="Source video width")
    source_height: Optional[int] = Field(default=None, ge=1, description="Source video height")


class ProgressUpdateResponse(BaseModel):
    status: str
    claim_expires_at: datetime


# Job completion
class QualityInfo(BaseModel):
    name: str
    width: int
    height: int
    bitrate: int  # kbps


class CompleteJobRequest(BaseModel):
    qualities: List[QualityInfo]
    duration: Optional[float] = None
    source_width: Optional[int] = None
    source_height: Optional[int] = None
    streaming_format: Optional[str] = None  # "hls_ts" or "cmaf"
    streaming_codec: Optional[str] = None  # "h264", "hevc", "av1"


class CompleteJobResponse(BaseModel):
    status: str
    message: str


# Job failure
class FailJobRequest(BaseModel):
    error_message: str = Field(..., max_length=500)
    retry: bool = True


class FailJobResponse(BaseModel):
    status: str
    will_retry: bool
    attempt_number: int


# Worker listing (for admin/CLI)
class WorkerStatusResponse(BaseModel):
    id: int
    worker_id: WorkerIdStr
    worker_name: Optional[str]
    worker_type: str
    status: str
    registered_at: datetime
    last_heartbeat: Optional[datetime]
    current_job_id: Optional[int]
    current_video_slug: Optional[str] = None
    capabilities: Optional[Dict] = None
    metadata: Optional[Dict] = None

    @field_validator("worker_id")
    @classmethod
    def validate_worker_id(cls, v):
        """Ensure worker_id is a valid UUID4."""
        return validate_uuid4(v)


class WorkerListResponse(BaseModel):
    workers: List[WorkerStatusResponse]
    total_count: int
    active_count: int
    offline_count: int


# Simple status responses
class StatusResponse(BaseModel):
    status: str
    message: Optional[str] = None


# =============================================================================
# Streaming Segment Upload Schemas (Issue #478)
# =============================================================================


class SegmentQuality(str):
    """Valid quality names for segment uploads.

    Using an enum-like validation instead of regex for security (Bruce's recommendation).
    """

    VALID_QUALITIES = frozenset(
        ["2160p", "1440p", "1080p", "720p", "480p", "360p", "original"]
    )

    @classmethod
    def validate(cls, value: str) -> str:
        """Validate quality is one of the allowed values."""
        if value not in cls.VALID_QUALITIES:
            raise ValueError(f"Invalid quality '{value}'. Must be one of: {sorted(cls.VALID_QUALITIES)}")
        return value


class SegmentUploadResponse(BaseModel):
    """Response from segment upload endpoint."""

    status: str
    written: bool
    bytes_written: int
    checksum_verified: bool


class SegmentStatusResponse(BaseModel):
    """Response from segments status endpoint."""

    quality: str
    received_segments: List[str]
    total_size_bytes: int


class SegmentFinalizeRequest(BaseModel):
    """Request to finalize a quality upload."""

    quality: str
    segment_count: int = Field(
        ge=0,
        le=100000,  # Reasonable upper bound (code review fix)
        description="Expected number of segment files (init.mp4 + *.m4s or *.ts)",
    )
    manifest_checksum: Optional[str] = Field(
        default=None,
        description="SHA256 checksum of the manifest file (e.g., 'sha256:abc123...')",
    )

    @field_validator("quality")
    @classmethod
    def validate_quality(cls, v):
        """Ensure quality is valid."""
        return SegmentQuality.validate(v)


class SegmentFinalizeResponse(BaseModel):
    """Response from finalize endpoint."""

    status: str
    complete: bool
    missing_segments: List[str] = Field(default_factory=list)
