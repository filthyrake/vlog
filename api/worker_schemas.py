"""Pydantic schemas for Worker API endpoints."""

import json
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    worker_id: str
    api_key: str  # Only returned once at registration
    message: str


# Heartbeat
class HeartbeatRequest(BaseModel):
    status: str = Field(default="active", pattern="^(active|busy|idle)$")
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
            
            # Limit serialized size to match endpoint limit (10KB)
            try:
                serialized = json.dumps(v)
                if len(serialized) > 10240:  # 10KB max (10 * 1024 bytes)
                    raise ValueError("Metadata too large (max 10KB)")
            except (TypeError, ValueError) as e:
                # If it's a ValueError from size check, re-raise it
                if "too large" in str(e).lower():
                    raise
                raise ValueError("Metadata must be JSON-serializable")
            
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
    message: str


# Progress updates
class QualityProgressUpdate(BaseModel):
    name: str
    status: str = Field(pattern="^(pending|in_progress|completed|uploaded|failed|skipped)$")
    progress: int = Field(ge=0, le=100)


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
    worker_id: str
    worker_name: Optional[str]
    worker_type: str
    status: str
    registered_at: datetime
    last_heartbeat: Optional[datetime]
    current_job_id: Optional[int]
    current_video_slug: Optional[str] = None
    capabilities: Optional[Dict] = None
    metadata: Optional[Dict] = None


class WorkerListResponse(BaseModel):
    workers: List[WorkerStatusResponse]
    total_count: int
    active_count: int
    offline_count: int


# Simple status responses
class StatusResponse(BaseModel):
    status: str
    message: Optional[str] = None
