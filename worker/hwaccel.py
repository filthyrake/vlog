#!/usr/bin/env python3
"""
Hardware acceleration detection and FFmpeg encoder selection.

Supports:
- NVIDIA NVENC (h264_nvenc, hevc_nvenc, av1_nvenc)
- Intel VAAPI (h264_vaapi, hevc_vaapi, av1_vaapi) for Arc/QuickSync
- Software fallback (libx264, libx265, libsvtav1)

Usage:
    from worker.hwaccel import detect_gpu_capabilities, select_encoder, build_transcode_command

    # At worker startup
    gpu_caps = await detect_gpu_capabilities()

    # For each transcode job
    cmd = build_transcode_command(input_path, output_dir, quality, gpu_caps)
"""

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class HWAccelType(Enum):
    """Supported hardware acceleration types."""

    NONE = "none"
    NVIDIA = "nvidia"
    INTEL = "intel"
    AUTO = "auto"


class VideoCodec(Enum):
    """Supported video codecs."""

    H264 = "h264"
    HEVC = "hevc"
    AV1 = "av1"


class StreamingFormat(Enum):
    """Supported streaming output formats."""

    HLS_TS = "hls_ts"  # Legacy: HLS with MPEG-TS segments
    CMAF = "cmaf"  # Modern: CMAF with fMP4 segments (HLS + DASH compatible)


@dataclass
class EncoderInfo:
    """Information about an available encoder."""

    name: str  # e.g., "h264_nvenc"
    codec: VideoCodec  # e.g., VideoCodec.H264
    hwaccel_type: HWAccelType  # e.g., HWAccelType.NVIDIA
    is_hardware: bool  # True for GPU encoders


@dataclass
class GPUCapabilities:
    """Detected GPU capabilities."""

    hwaccel_type: HWAccelType
    device_name: str
    device_path: Optional[str] = None  # e.g., "/dev/dri/renderD128" for VAAPI

    # Available encoders by codec
    encoders: Dict[VideoCodec, List[EncoderInfo]] = field(default_factory=dict)

    # Concurrent session limits (NVIDIA consumer GPUs: 3-5)
    max_concurrent_sessions: int = 5

    # Feature support flags
    supports_av1: bool = False
    supports_hevc: bool = True
    supports_h264: bool = True

    # Driver info
    driver_version: Optional[str] = None
    cuda_version: Optional[str] = None


@dataclass
class EncoderSelection:
    """Selected encoder configuration for a transcode job."""

    encoder: EncoderInfo
    input_args: List[str]  # FFmpeg input arguments (before -i)
    output_args: List[str]  # FFmpeg output arguments (after -i)
    scale_filter: str  # Video scale filter


# NVIDIA consumer GPU session limits
NVIDIA_SESSION_LIMITS = {
    "RTX 4090": 5,
    "RTX 4080": 5,
    "RTX 4070": 5,
    "RTX 3090": 3,
    "RTX 3080": 3,
    "RTX 3070": 3,
    "RTX 3060": 3,
    "RTX 2080": 3,
    "RTX 2070": 3,
    "RTX 2060": 3,
    "GTX 1080": 2,
    "GTX 1070": 2,
    "GTX 1060": 2,
    # Datacenter GPUs - unlimited
    "A100": 999,
    "A10": 999,
    "A30": 999,
    "A40": 999,
    "T4": 999,
    "L4": 999,
    "L40": 999,
    "H100": 999,
}


def _get_nvidia_session_limit(gpu_name: str) -> int:
    """Get concurrent encode session limit for NVIDIA GPU."""
    for model, limit in NVIDIA_SESSION_LIMITS.items():
        if model in gpu_name:
            return limit
    return 3  # Conservative default for unknown GPUs


async def _run_command(cmd: List[str], timeout: float = 10.0) -> Tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, stdout.decode("utf-8", errors="ignore"), stderr.decode("utf-8", errors="ignore")
    except asyncio.TimeoutError:
        return -1, "", "Command timed out"
    except FileNotFoundError:
        return -1, "", f"Command not found: {cmd[0]}"
    except Exception as e:
        return -1, "", str(e)


def _extract_ffmpeg_error(stderr: str) -> str:
    """Extract the first meaningful error line from FFmpeg stderr output."""
    for line in stderr.strip().split("\n"):
        # Skip FFmpeg info lines that start with [
        if line and not line.startswith("["):
            return line.strip()
    return "unknown error"


async def _probe_ffmpeg_encoders() -> Dict[str, bool]:
    """Probe FFmpeg for available encoders."""
    returncode, stdout, _ = await _run_command(["ffmpeg", "-hide_banner", "-encoders"])

    if returncode != 0:
        return {}

    encoders = {}
    for line in stdout.split("\n"):
        line = line.strip()
        # Parse encoder list (format: " V..... encoder_name  Description")
        if line.startswith("V"):
            parts = line.split()
            if len(parts) >= 2:
                encoder_name = parts[1]
                encoders[encoder_name] = True

    return encoders


async def detect_nvidia_gpu() -> Optional[GPUCapabilities]:
    """
    Detect NVIDIA GPU and NVENC capabilities.

    Checks:
    1. nvidia-smi availability
    2. GPU name and driver version
    3. NVENC encoder availability via ffmpeg
    """
    # Check for nvidia-smi
    returncode, stdout, _ = await _run_command(
        ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"]
    )

    if returncode != 0 or not stdout.strip():
        return None

    # Parse GPU name and driver version
    parts = stdout.strip().split(", ")
    device_name = parts[0] if parts else "Unknown NVIDIA GPU"
    driver_version = parts[1] if len(parts) > 1 else None

    # Get CUDA version
    cuda_version = None
    returncode, stdout, _ = await _run_command(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"])
    if returncode == 0:
        # Try to get CUDA version from nvidia-smi header
        returncode2, stdout2, _ = await _run_command(["nvidia-smi"])
        if returncode2 == 0:
            for line in stdout2.split("\n"):
                if "CUDA Version" in line:
                    match = re.search(r"CUDA Version:\s*(\d+\.\d+)", line)
                    if match:
                        cuda_version = match.group(1)
                    break

    # Get session limit based on GPU model
    session_limit = _get_nvidia_session_limit(device_name)

    caps = GPUCapabilities(
        hwaccel_type=HWAccelType.NVIDIA,
        device_name=device_name,
        driver_version=driver_version,
        cuda_version=cuda_version,
        max_concurrent_sessions=session_limit,
    )

    # Probe for available NVENC encoders
    available = await _probe_ffmpeg_encoders()

    nvenc_encoders = {
        "h264_nvenc": VideoCodec.H264,
        "hevc_nvenc": VideoCodec.HEVC,
        "av1_nvenc": VideoCodec.AV1,
    }

    for encoder_name, codec in nvenc_encoders.items():
        if encoder_name in available:
            # Verify encoder actually works with a quick test
            if await _test_nvenc_encoder(encoder_name):
                info = EncoderInfo(
                    name=encoder_name,
                    codec=codec,
                    hwaccel_type=HWAccelType.NVIDIA,
                    is_hardware=True,
                )
                if codec not in caps.encoders:
                    caps.encoders[codec] = []
                caps.encoders[codec].append(info)

    caps.supports_h264 = VideoCodec.H264 in caps.encoders
    caps.supports_hevc = VideoCodec.HEVC in caps.encoders
    caps.supports_av1 = VideoCodec.AV1 in caps.encoders

    # Only return if we have at least one working encoder
    if not caps.encoders:
        print("  NVIDIA GPU detected but no NVENC encoders available in FFmpeg")
        return None

    return caps


async def detect_intel_vaapi() -> Optional[GPUCapabilities]:
    """
    Detect Intel GPU with VAAPI support (QuickSync, Arc).

    Checks:
    1. /dev/dri/renderD* device presence
    2. vainfo output for supported profiles
    3. FFmpeg VAAPI encoder availability
    """
    # Find VAAPI render device
    dri_path = Path("/dev/dri")
    if not dri_path.exists():
        return None

    render_devices = sorted(dri_path.glob("renderD*"))
    if not render_devices:
        return None

    device_path = str(render_devices[0])  # Usually renderD128

    # Check vainfo for device capabilities
    device_name = "Intel GPU"
    driver_version = None

    returncode, stdout, stderr = await _run_command(["vainfo", "--display", "drm", "--device", device_path])

    if returncode != 0:
        # Try without explicit device
        returncode, stdout, stderr = await _run_command(["vainfo"])

    if returncode == 0:
        output = stdout + stderr  # vainfo outputs to both
        for line in output.split("\n"):
            if "Driver version" in line or "driver version" in line.lower():
                # Parse driver info
                driver_version = line.split(":")[-1].strip() if ":" in line else line.strip()
                # Try to identify GPU type
                if "iHD" in line or "Intel" in line:
                    if "Arc" in line or "DG2" in line or "Battlemage" in line:
                        device_name = "Intel Arc GPU"
                    else:
                        device_name = "Intel QuickSync"
                break

    caps = GPUCapabilities(
        hwaccel_type=HWAccelType.INTEL,
        device_name=device_name,
        device_path=device_path,
        driver_version=driver_version,
        max_concurrent_sessions=10,  # Intel generally has higher limits
    )

    # Probe for available VAAPI encoders
    available = await _probe_ffmpeg_encoders()

    vaapi_encoders = {
        "h264_vaapi": VideoCodec.H264,
        "hevc_vaapi": VideoCodec.HEVC,
        "av1_vaapi": VideoCodec.AV1,
    }

    for encoder_name, codec in vaapi_encoders.items():
        if encoder_name in available:
            # Verify encoder actually works with a quick test
            if await _test_vaapi_encoder(encoder_name, device_path):
                info = EncoderInfo(
                    name=encoder_name,
                    codec=codec,
                    hwaccel_type=HWAccelType.INTEL,
                    is_hardware=True,
                )
                if codec not in caps.encoders:
                    caps.encoders[codec] = []
                caps.encoders[codec].append(info)

    caps.supports_h264 = VideoCodec.H264 in caps.encoders
    caps.supports_hevc = VideoCodec.HEVC in caps.encoders
    caps.supports_av1 = VideoCodec.AV1 in caps.encoders

    if not caps.encoders:
        print("  Intel GPU detected but no working VAAPI encoders found")
        return None

    return caps


async def _test_vaapi_encoder(encoder_name: str, device_path: str) -> bool:
    """Test if a VAAPI encoder actually works (not just listed)."""
    # Quick encode test with null output
    # Use 256x256 to avoid minimum resolution issues (NVENC requires 144x144)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-vaapi_device",
        device_path,
        "-f",
        "lavfi",
        "-i",
        "color=black:s=256x256:d=0.1",
        "-vf",
        "format=nv12,hwupload",
        "-c:v",
        encoder_name,
        "-f",
        "null",
        "-",
    ]

    returncode, _, stderr = await _run_command(cmd, timeout=15.0)
    if returncode != 0:
        logger.warning(
            f"VAAPI encoder {encoder_name} test failed: {_extract_ffmpeg_error(stderr)}"
        )
        return False
    return True


async def _test_nvenc_encoder(encoder_name: str) -> bool:
    """Test if an NVENC encoder actually works (not just listed)."""
    # Quick encode test with null output
    # Note: NVENC requires minimum 144x144 resolution, use 256x256 to be safe
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-hwaccel",
        "cuda",
        "-f",
        "lavfi",
        "-i",
        "color=black:s=256x256:d=0.1",
        "-c:v",
        encoder_name,
        "-f",
        "null",
        "-",
    ]

    returncode, _, stderr = await _run_command(cmd, timeout=15.0)
    if returncode != 0:
        logger.warning(
            f"NVENC encoder {encoder_name} test failed: {_extract_ffmpeg_error(stderr)}"
        )
        return False
    return True


async def detect_gpu_capabilities() -> Optional[GPUCapabilities]:
    """
    Auto-detect GPU capabilities.

    Checks VLOG_HWACCEL_TYPE environment variable:
    - "auto": Try NVIDIA first, then Intel
    - "nvidia": Only check for NVIDIA
    - "intel": Only check for Intel
    - "none": Disable hardware acceleration

    Returns:
        GPUCapabilities if hardware acceleration available, None otherwise.
    """
    hwaccel_type = os.getenv("VLOG_HWACCEL_TYPE", "auto").lower()

    if hwaccel_type == "none":
        return None

    if hwaccel_type == "nvidia":
        return await detect_nvidia_gpu()

    if hwaccel_type == "intel":
        return await detect_intel_vaapi()

    # Auto-detect: try NVIDIA first, then Intel
    caps = await detect_nvidia_gpu()
    if caps:
        return caps

    return await detect_intel_vaapi()


def _get_preferred_codec() -> VideoCodec:
    """Get preferred codec from environment."""
    codec_str = os.getenv("VLOG_HWACCEL_PREFERRED_CODEC", "h264").lower()
    if codec_str == "hevc" or codec_str == "h265":
        return VideoCodec.HEVC
    elif codec_str == "av1":
        return VideoCodec.AV1
    return VideoCodec.H264


def select_encoder(
    gpu_caps: Optional[GPUCapabilities],
    target_height: int,
    preferred_codec: Optional[VideoCodec] = None,
) -> EncoderSelection:
    """
    Select the best encoder for the given resolution and preferences.

    Args:
        gpu_caps: Detected GPU capabilities (None for CPU-only)
        target_height: Target resolution height (e.g., 1080, 2160)
        preferred_codec: Override codec preference

    Returns:
        EncoderSelection with FFmpeg arguments for the selected encoder.
    """
    if preferred_codec is None:
        preferred_codec = _get_preferred_codec()

    # Try hardware encoder first
    if gpu_caps and gpu_caps.encoders:
        selection = _select_hardware_encoder(gpu_caps, preferred_codec, target_height)
        if selection:
            return selection

    # Fall back to software
    return _select_software_encoder(preferred_codec, target_height)


def _select_hardware_encoder(
    gpu_caps: GPUCapabilities,
    codec: VideoCodec,
    target_height: int,
) -> Optional[EncoderSelection]:
    """Select hardware encoder based on GPU capabilities."""
    # Get encoders for preferred codec
    encoders = gpu_caps.encoders.get(codec, [])

    # Fall back to H.264 if preferred codec not available
    if not encoders and codec != VideoCodec.H264:
        encoders = gpu_caps.encoders.get(VideoCodec.H264, [])
        codec = VideoCodec.H264

    if not encoders:
        return None

    encoder = encoders[0]  # Use first available

    if gpu_caps.hwaccel_type == HWAccelType.NVIDIA:
        return _build_nvenc_selection(encoder, target_height)
    elif gpu_caps.hwaccel_type == HWAccelType.INTEL:
        return _build_vaapi_selection(encoder, target_height, gpu_caps.device_path)

    return None


def _build_nvenc_selection(encoder: EncoderInfo, target_height: int) -> EncoderSelection:
    """Build FFmpeg arguments for NVENC encoding."""
    # Input arguments for CUDA hardware decoding
    # Note: We don't use -hwaccel_output_format cuda because scale_npp/scale_cuda
    # require FFmpeg compiled with --enable-libnpp or --enable-cuda-llvm respectively,
    # which most distro FFmpeg builds (including RPM Fusion) don't have.
    # Frames are decoded with CUDA, copied to CPU for scaling, then the NVENC encoder
    # uploads them back to GPU for encoding. This is still much faster than CPU encoding.
    input_args = [
        "-hwaccel",
        "cuda",
    ]

    # Output arguments
    output_args = [
        "-c:v",
        encoder.name,
        "-preset",
        "p4",  # NVENC presets: p1 (fastest) to p7 (slowest/best quality)
        "-tune",
        "hq",
        "-rc",
        "vbr",  # Variable bitrate
        "-rc-lookahead",
        "32",
        "-bf",
        "3",  # B-frames for better compression
    ]

    # Add HEVC-specific options
    if encoder.codec == VideoCodec.HEVC:
        output_args.extend(["-tag:v", "hvc1"])  # Apple compatibility

    # Use standard CPU scale filter since scale_npp/scale_cuda aren't available
    # in most distro FFmpeg builds. The NVENC encoder handles GPU upload.
    scale_filter = f"scale=-2:{target_height}"

    return EncoderSelection(
        encoder=encoder,
        input_args=input_args,
        output_args=output_args,
        scale_filter=scale_filter,
    )


def _build_vaapi_selection(
    encoder: EncoderInfo,
    target_height: int,
    device_path: Optional[str],
) -> EncoderSelection:
    """Build FFmpeg arguments for VAAPI encoding."""
    device = device_path or "/dev/dri/renderD128"

    # Input arguments - specify VAAPI device
    input_args = [
        "-vaapi_device",
        device,
    ]

    # Output arguments
    output_args = [
        "-c:v",
        encoder.name,
    ]

    # Codec-specific quality settings
    if encoder.codec == VideoCodec.H264:
        output_args.extend(
            [
                "-qp",
                "23",  # Quality parameter
                "-profile:v",
                "high",
            ]
        )
    elif encoder.codec == VideoCodec.HEVC:
        output_args.extend(
            [
                "-qp",
                "25",
                "-profile:v",
                "main",
                "-tag:v",
                "hvc1",
            ]
        )
    elif encoder.codec == VideoCodec.AV1:
        output_args.extend(
            [
                "-qp",
                "30",
            ]
        )

    # VAAPI requires format conversion and hwupload
    # The scale happens on the GPU
    scale_filter = f"format=nv12,hwupload,scale_vaapi=-2:{target_height}"

    return EncoderSelection(
        encoder=encoder,
        input_args=input_args,
        output_args=output_args,
        scale_filter=scale_filter,
    )


def _select_software_encoder(codec: VideoCodec, target_height: int) -> EncoderSelection:
    """Select software encoder as fallback."""
    encoder_map = {
        VideoCodec.H264: ("libx264", ["-preset", "medium", "-crf", "23"]),
        VideoCodec.HEVC: ("libx265", ["-preset", "medium", "-crf", "28", "-tag:v", "hvc1"]),
        VideoCodec.AV1: ("libsvtav1", ["-preset", "6", "-crf", "30"]),
    }

    # Default to H.264 if codec not in map
    encoder_name, codec_args = encoder_map.get(codec, encoder_map[VideoCodec.H264])

    encoder = EncoderInfo(
        name=encoder_name,
        codec=codec,
        hwaccel_type=HWAccelType.NONE,
        is_hardware=False,
    )

    output_args = ["-c:v", encoder_name] + codec_args

    # Standard CPU scale filter
    scale_filter = f"scale=-2:{target_height}"

    return EncoderSelection(
        encoder=encoder,
        input_args=[],
        output_args=output_args,
        scale_filter=scale_filter,
    )


def build_transcode_command(
    input_path: Path,
    output_dir: Path,
    quality: dict,
    selection: EncoderSelection,
    hls_segment_duration: int = 6,
) -> List[str]:
    """
    Build complete FFmpeg command for HLS transcoding.

    Args:
        input_path: Source video file
        output_dir: Output directory for HLS files
        quality: Quality preset dict with name, height, bitrate, audio_bitrate
        selection: EncoderSelection from select_encoder()
        hls_segment_duration: HLS segment length in seconds

    Returns:
        Complete FFmpeg command as list of arguments.
    """
    name = quality["name"]
    bitrate = quality["bitrate"]
    audio_bitrate = quality["audio_bitrate"]

    playlist_name = f"{name}.m3u8"
    segment_pattern = f"{name}_%04d.ts"

    cmd = ["ffmpeg", "-y"]

    # Hardware decoding input arguments (before -i)
    cmd.extend(selection.input_args)

    # Input file
    cmd.extend(["-i", str(input_path)])

    # Video encoding arguments
    cmd.extend(selection.output_args)

    # Bitrate control
    cmd.extend(
        [
            "-b:v",
            bitrate,
            "-maxrate",
            bitrate,
            "-bufsize",
            f"{int(bitrate.replace('k', '')) * 2}k",
        ]
    )

    # Video filter (scaling)
    cmd.extend(["-vf", selection.scale_filter])

    # Audio encoding
    cmd.extend(
        [
            "-c:a",
            "aac",
            "-b:a",
            audio_bitrate,
            "-ac",
            "2",
        ]
    )

    # HLS output
    cmd.extend(
        [
            "-hls_time",
            str(hls_segment_duration),
            "-hls_list_size",
            "0",
            "-hls_segment_filename",
            str(output_dir / segment_pattern),
            "-progress",
            "pipe:1",
            "-f",
            "hls",
            str(output_dir / playlist_name),
        ]
    )

    return cmd


def build_cmaf_transcode_command(
    input_path: Path,
    output_dir: Path,
    quality: dict,
    selection: EncoderSelection,
    segment_duration: int = 6,
) -> List[str]:
    """
    Build FFmpeg command for CMAF transcoding (fMP4 segments).

    CMAF (Common Media Application Format) uses fragmented MP4 segments
    that are compatible with both HLS and DASH streaming protocols.

    Output structure:
        {output_dir}/{quality_name}/
            ├── stream.m3u8    # HLS variant playlist
            ├── init.mp4       # CMAF initialization segment
            └── seg_*.m4s      # CMAF media segments

    Args:
        input_path: Source video file
        output_dir: Output directory (quality subdir created automatically)
        quality: Quality preset dict with name, height, bitrate, audio_bitrate
        selection: EncoderSelection from select_encoder()
        segment_duration: Segment length in seconds

    Returns:
        Complete FFmpeg command as list of arguments.
    """
    name = quality["name"]
    bitrate = quality["bitrate"]
    audio_bitrate = quality["audio_bitrate"]

    # Create quality subdirectory for CMAF output
    quality_dir = output_dir / name
    playlist_name = "stream.m3u8"
    init_segment = "init.mp4"
    segment_pattern = str(quality_dir / "seg_%04d.m4s")

    cmd = ["ffmpeg", "-y"]

    # Hardware decoding input arguments (before -i)
    cmd.extend(selection.input_args)

    # Input file
    cmd.extend(["-i", str(input_path)])

    # Video encoding arguments
    cmd.extend(selection.output_args)

    # Bitrate control
    cmd.extend(
        [
            "-b:v",
            bitrate,
            "-maxrate",
            bitrate,
            "-bufsize",
            f"{int(bitrate.replace('k', '')) * 2}k",
        ]
    )

    # Video filter (scaling)
    cmd.extend(["-vf", selection.scale_filter])

    # Audio encoding
    cmd.extend(
        [
            "-c:a",
            "aac",
            "-b:a",
            audio_bitrate,
            "-ac",
            "2",
        ]
    )

    # CMAF/fMP4 HLS output
    # -hls_segment_type fmp4: Use fragmented MP4 instead of MPEG-TS
    # -hls_fmp4_init_filename: Name of the initialization segment
    # -movflags +cmaf: Enable CMAF compatibility flags
    cmd.extend(
        [
            "-hls_time",
            str(segment_duration),
            "-hls_list_size",
            "0",
            "-hls_segment_type",
            "fmp4",
            "-hls_fmp4_init_filename",
            init_segment,
            "-hls_segment_filename",
            segment_pattern,
            "-movflags",
            "+cmaf+faststart",
            "-progress",
            "pipe:1",
            "-f",
            "hls",
            str(quality_dir / playlist_name),
        ]
    )

    return cmd


def get_codec_string(codec: VideoCodec, level: str = "L120") -> str:
    """
    Get codec string for HLS/DASH manifest.

    Args:
        codec: Video codec
        level: Codec level (default L120 for HD)

    Returns:
        Codec string for EXT-X-STREAM-INF CODECS attribute.
    """
    if codec == VideoCodec.HEVC:
        # hvc1.1.6.L120.90 - Main profile, level 4.0 (1080p)
        return f"hvc1.1.6.{level}.90,mp4a.40.2"
    elif codec == VideoCodec.AV1:
        # av01.0.08M.08 - Main profile, level 4.0, 8-bit
        return "av01.0.08M.08,mp4a.40.2"
    else:
        # avc1.640028 - H.264 High profile, level 4.0
        return "avc1.640028,mp4a.40.2"


async def get_worker_capabilities(gpu_caps: Optional[GPUCapabilities] = None) -> dict:
    """
    Get worker capabilities for registration/heartbeat.

    Returns dict suitable for storing in workers.capabilities JSON column.
    """
    # If no caps provided, detect them
    if gpu_caps is None:
        gpu_caps = await detect_gpu_capabilities()

    caps = {
        "hwaccel_enabled": gpu_caps is not None,
        "hwaccel_type": gpu_caps.hwaccel_type.value if gpu_caps else "none",
        "gpu_name": gpu_caps.device_name if gpu_caps else None,
        "supported_codecs": ["h264"],  # Always have software H.264
        "encoders": {
            "h264": ["libx264"],
        },
        "max_concurrent_encode_sessions": 1,  # CPU default
    }

    # Get FFmpeg version
    returncode, stdout, _ = await _run_command(["ffmpeg", "-version"])
    if returncode == 0:
        version_line = stdout.split("\n")[0]
        match = re.search(r"ffmpeg version\s+(\S+)", version_line)
        if match:
            caps["ffmpeg_version"] = match.group(1)

    if gpu_caps:
        caps["max_concurrent_encode_sessions"] = gpu_caps.max_concurrent_sessions

        if gpu_caps.driver_version:
            caps["driver_version"] = gpu_caps.driver_version
        if gpu_caps.cuda_version:
            caps["cuda_version"] = gpu_caps.cuda_version
        if gpu_caps.device_path:
            caps["vaapi_device"] = gpu_caps.device_path

        # List all supported codecs and encoders
        codecs = set(["h264"])  # Always have CPU h264
        encoders = {"h264": ["libx264"]}

        for codec, encoder_list in gpu_caps.encoders.items():
            codecs.add(codec.value)
            if codec.value not in encoders:
                encoders[codec.value] = []
            for e in encoder_list:
                encoders[codec.value].insert(0, e.name)  # GPU encoder first

        caps["supported_codecs"] = sorted(codecs)
        caps["encoders"] = encoders

    return caps


def get_recommended_parallel_sessions(gpu_caps: Optional[GPUCapabilities] = None) -> int:
    """
    Get recommended number of parallel quality encode sessions.

    Uses config settings:
    - PARALLEL_QUALITIES_AUTO: If true, auto-detect based on GPU
    - PARALLEL_QUALITIES: Manual override value

    Args:
        gpu_caps: Detected GPU capabilities (None for CPU-only)

    Returns:
        Number of qualities to encode in parallel (minimum 1)
    """
    # Import here to avoid circular imports
    from config import PARALLEL_QUALITIES, PARALLEL_QUALITIES_AUTO

    # If auto-detection is disabled, use the configured value
    if not PARALLEL_QUALITIES_AUTO:
        return max(1, PARALLEL_QUALITIES)

    # If no GPU, default to configured value (likely 1)
    if gpu_caps is None:
        return max(1, PARALLEL_QUALITIES)

    # Auto-detect: reserve 1 session for headroom, cap at 3 for safety
    # This ensures we don't hit GPU session limits or memory issues
    # When auto is enabled, PARALLEL_QUALITIES is ignored - GPU capabilities determine parallelism
    max_sessions = gpu_caps.max_concurrent_sessions
    recommended = min(3, max(1, max_sessions - 1))

    return recommended


# For testing: print capabilities when run directly
if __name__ == "__main__":
    import json

    async def main():
        print("Detecting GPU capabilities...")
        caps = await detect_gpu_capabilities()

        if caps:
            print(f"\nGPU Detected: {caps.device_name}")
            print(f"Type: {caps.hwaccel_type.value}")
            print(f"Driver: {caps.driver_version}")
            if caps.cuda_version:
                print(f"CUDA: {caps.cuda_version}")
            if caps.device_path:
                print(f"Device: {caps.device_path}")
            print(f"Max concurrent sessions: {caps.max_concurrent_sessions}")
            print("\nAvailable encoders:")
            for codec, encoders in caps.encoders.items():
                print(f"  {codec.value}: {[e.name for e in encoders]}")
        else:
            print("\nNo GPU acceleration available, will use CPU encoding")

        print("\n--- Worker capabilities JSON ---")
        worker_caps = await get_worker_capabilities(caps)
        print(json.dumps(worker_caps, indent=2))

    asyncio.run(main())
