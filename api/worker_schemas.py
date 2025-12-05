"""Pydantic schemas for Worker API endpoints."""
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# GPU and Hardware Acceleration Capabilities
class GPUInfo(BaseModel):
    """GPU hardware information reported by workers."""
    hwaccel_type: str = Field(
        default="none",
        description="Hardware acceleration type: nvidia, intel, or none"
    )
    gpu_name: Optional[str] = Field(
        default=None,
        description="GPU device name (e.g., 'NVIDIA GeForce RTX 3090')"
    )
    driver_version: Optional[str] = Field(
        default=None,
        description="GPU driver version"
    )
    cuda_version: Optional[str] = Field(
        default=None,
        description="CUDA version (NVIDIA only)"
    )
    vaapi_device: Optional[str] = Field(
        default=None,
        description="VAAPI device path (Intel only, e.g., '/dev/dri/renderD128')"
    )


class WorkerCapabilities(BaseModel):
    """Detailed worker capabilities including GPU and encoding support."""
    hwaccel_enabled: bool = Field(
        default=False,
        description="Whether hardware acceleration is available"
    )
    hwaccel_type: str = Field(
        default="none",
        description="Hardware acceleration type: nvidia, intel, or none"
    )
    gpu_name: Optional[str] = Field(
        default=None,
        description="GPU device name"
    )
    supported_codecs: List[str] = Field(
        default=["h264"],
        description="List of supported codecs (h264, hevc, av1)"
    )
    encoders: Dict[str, List[str]] = Field(
        default={"h264": ["libx264"]},
        description="Available encoders by codec (e.g., {'h264': ['h264_nvenc', 'libx264']})"
    )
    max_concurrent_encode_sessions: int = Field(
        default=1,
        description="Maximum concurrent encoding sessions (NVIDIA consumer GPUs: 3-5)"
    )
    ffmpeg_version: Optional[str] = Field(
        default=None,
        description="FFmpeg version string"
    )
    driver_version: Optional[str] = Field(
        default=None,
        description="GPU driver version"
    )
    cuda_version: Optional[str] = Field(
        default=None,
        description="CUDA version (NVIDIA only)"
    )
    vaapi_device: Optional[str] = Field(
        default=None,
        description="VAAPI device path (Intel only)"
    )


# Worker registration
class WorkerRegisterRequest(BaseModel):
    worker_name: Optional[str] = Field(default=None, max_length=100)
    worker_type: str = Field(default="remote", pattern="^(local|remote)$")
    capabilities: Optional[Dict] = Field(
        default=None,
        description="Worker capabilities as JSON (e.g., max_resolution, gpu)"
    )
    metadata: Optional[Dict] = Field(
        default=None,
        description="Worker metadata (e.g., kubernetes pod info)"
    )


class WorkerRegisterResponse(BaseModel):
    worker_id: str
    api_key: str  # Only returned once at registration
    message: str


# Heartbeat
class HeartbeatRequest(BaseModel):
    status: str = Field(default="active", pattern="^(active|busy|idle)$")
    metadata: Optional[Dict] = None


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
    status: str = Field(pattern="^(pending|in_progress|completed|failed|skipped)$")
    progress: int = Field(ge=0, le=100)


class ProgressUpdateRequest(BaseModel):
    current_step: Optional[str] = Field(
        default=None,
        pattern="^(download|probe|thumbnail|transcode|master_playlist|upload|finalize)$"
    )
    progress_percent: int = Field(ge=0, le=100)
    quality_progress: Optional[List[QualityProgressUpdate]] = None


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
