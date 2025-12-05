"""Tests for hardware acceleration detection and encoder selection."""
from pathlib import Path
from unittest.mock import patch

import pytest

from worker.hwaccel import (
    EncoderInfo,
    EncoderSelection,
    GPUCapabilities,
    HWAccelType,
    VideoCodec,
    _get_nvidia_session_limit,
    _test_nvenc_encoder,
    _test_vaapi_encoder,
    build_transcode_command,
    detect_gpu_capabilities,
    detect_nvidia_gpu,
    get_worker_capabilities,
    select_encoder,
)


class TestNvidiaSessionLimits:
    """Tests for NVIDIA GPU session limit detection."""

    def test_rtx_4090_limit(self):
        assert _get_nvidia_session_limit("NVIDIA GeForce RTX 4090") == 5

    def test_rtx_3090_limit(self):
        assert _get_nvidia_session_limit("NVIDIA GeForce RTX 3090") == 3

    def test_a100_unlimited(self):
        assert _get_nvidia_session_limit("NVIDIA A100-SXM4-40GB") == 999

    def test_t4_unlimited(self):
        assert _get_nvidia_session_limit("Tesla T4") == 999

    def test_unknown_gpu_default(self):
        assert _get_nvidia_session_limit("Unknown GPU Model") == 3


class TestEncoderSelection:
    """Tests for encoder selection logic."""

    def test_select_nvenc_h264(self):
        """Test NVENC H.264 encoder selection."""
        caps = GPUCapabilities(
            hwaccel_type=HWAccelType.NVIDIA,
            device_name="NVIDIA GeForce RTX 3090",
            encoders={
                VideoCodec.H264: [
                    EncoderInfo(
                        name="h264_nvenc",
                        codec=VideoCodec.H264,
                        hwaccel_type=HWAccelType.NVIDIA,
                        is_hardware=True,
                    )
                ]
            },
        )

        selection = select_encoder(caps, 1080, VideoCodec.H264)

        assert selection.encoder.name == "h264_nvenc"
        assert selection.encoder.is_hardware is True
        assert "-hwaccel" in selection.input_args
        assert "cuda" in selection.input_args
        assert "scale_cuda" in selection.scale_filter

    def test_select_nvenc_hevc(self):
        """Test NVENC HEVC encoder selection."""
        caps = GPUCapabilities(
            hwaccel_type=HWAccelType.NVIDIA,
            device_name="NVIDIA GeForce RTX 3090",
            encoders={
                VideoCodec.H264: [
                    EncoderInfo(
                        name="h264_nvenc",
                        codec=VideoCodec.H264,
                        hwaccel_type=HWAccelType.NVIDIA,
                        is_hardware=True,
                    )
                ],
                VideoCodec.HEVC: [
                    EncoderInfo(
                        name="hevc_nvenc",
                        codec=VideoCodec.HEVC,
                        hwaccel_type=HWAccelType.NVIDIA,
                        is_hardware=True,
                    )
                ],
            },
        )

        selection = select_encoder(caps, 1080, VideoCodec.HEVC)

        assert selection.encoder.name == "hevc_nvenc"
        assert "-tag:v" in selection.output_args
        assert "hvc1" in selection.output_args

    def test_select_vaapi_h264(self):
        """Test VAAPI H.264 encoder selection."""
        caps = GPUCapabilities(
            hwaccel_type=HWAccelType.INTEL,
            device_name="Intel Arc A770",
            device_path="/dev/dri/renderD128",
            encoders={
                VideoCodec.H264: [
                    EncoderInfo(
                        name="h264_vaapi",
                        codec=VideoCodec.H264,
                        hwaccel_type=HWAccelType.INTEL,
                        is_hardware=True,
                    )
                ]
            },
        )

        selection = select_encoder(caps, 1080, VideoCodec.H264)

        assert selection.encoder.name == "h264_vaapi"
        assert "-vaapi_device" in selection.input_args
        assert "hwupload" in selection.scale_filter
        assert "scale_vaapi" in selection.scale_filter

    def test_fallback_to_h264_when_hevc_unavailable(self):
        """Test fallback to H.264 when HEVC not available."""
        caps = GPUCapabilities(
            hwaccel_type=HWAccelType.NVIDIA,
            device_name="NVIDIA GeForce GTX 1060",
            encoders={
                VideoCodec.H264: [
                    EncoderInfo(
                        name="h264_nvenc",
                        codec=VideoCodec.H264,
                        hwaccel_type=HWAccelType.NVIDIA,
                        is_hardware=True,
                    )
                ]
                # No HEVC encoder
            },
        )

        selection = select_encoder(caps, 1080, VideoCodec.HEVC)

        # Should fall back to H.264
        assert selection.encoder.name == "h264_nvenc"

    def test_fallback_to_cpu_when_no_gpu(self):
        """Test fallback to CPU when no GPU available."""
        selection = select_encoder(None, 1080, VideoCodec.H264)

        assert selection.encoder.name == "libx264"
        assert selection.encoder.is_hardware is False
        assert selection.input_args == []
        assert "scale=-2:1080" == selection.scale_filter

    def test_cpu_hevc_selection(self):
        """Test CPU HEVC encoder selection."""
        selection = select_encoder(None, 1080, VideoCodec.HEVC)

        assert selection.encoder.name == "libx265"
        assert "-tag:v" in selection.output_args


class TestBuildTranscodeCommand:
    """Tests for FFmpeg command generation."""

    def test_build_nvenc_command(self):
        """Test NVENC command generation."""
        selection = EncoderSelection(
            encoder=EncoderInfo(
                name="h264_nvenc",
                codec=VideoCodec.H264,
                hwaccel_type=HWAccelType.NVIDIA,
                is_hardware=True,
            ),
            input_args=["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"],
            output_args=["-c:v", "h264_nvenc", "-preset", "p4"],
            scale_filter="scale_cuda=-2:1080",
        )

        quality = {"name": "1080p", "height": 1080, "bitrate": "5000k", "audio_bitrate": "128k"}
        cmd = build_transcode_command(
            Path("/tmp/input.mp4"),
            Path("/tmp/output"),
            quality,
            selection,
        )

        assert cmd[0] == "ffmpeg"
        assert "-hwaccel" in cmd
        assert "cuda" in cmd
        assert "h264_nvenc" in cmd
        assert "-b:v" in cmd
        assert "5000k" in cmd
        assert "-hls_time" in cmd

    def test_build_vaapi_command(self):
        """Test VAAPI command generation."""
        selection = EncoderSelection(
            encoder=EncoderInfo(
                name="h264_vaapi",
                codec=VideoCodec.H264,
                hwaccel_type=HWAccelType.INTEL,
                is_hardware=True,
            ),
            input_args=["-vaapi_device", "/dev/dri/renderD128"],
            output_args=["-c:v", "h264_vaapi", "-qp", "23"],
            scale_filter="format=nv12,hwupload,scale_vaapi=-2:720",
        )

        quality = {"name": "720p", "height": 720, "bitrate": "2500k", "audio_bitrate": "128k"}
        cmd = build_transcode_command(
            Path("/tmp/input.mp4"),
            Path("/tmp/output"),
            quality,
            selection,
        )

        assert "-vaapi_device" in cmd
        assert "/dev/dri/renderD128" in cmd
        assert "h264_vaapi" in cmd
        assert "hwupload" in " ".join(cmd)

    def test_build_cpu_command(self):
        """Test CPU command generation."""
        selection = EncoderSelection(
            encoder=EncoderInfo(
                name="libx264",
                codec=VideoCodec.H264,
                hwaccel_type=HWAccelType.NONE,
                is_hardware=False,
            ),
            input_args=[],
            output_args=["-c:v", "libx264", "-preset", "medium", "-crf", "23"],
            scale_filter="scale=-2:480",
        )

        quality = {"name": "480p", "height": 480, "bitrate": "1000k", "audio_bitrate": "96k"}
        cmd = build_transcode_command(
            Path("/tmp/input.mp4"),
            Path("/tmp/output"),
            quality,
            selection,
        )

        assert "libx264" in cmd
        assert "-hwaccel" not in cmd
        assert "-vaapi_device" not in cmd


class TestGPUDetection:
    """Tests for GPU detection functions."""

    @pytest.mark.asyncio
    async def test_nvenc_encoder_validation_success(self):
        """Test NVENC encoder validation when encoder works."""
        with patch("worker.hwaccel._run_command") as mock_run:
            # Mock successful encode test
            mock_run.return_value = (0, "", "")

            result = await _test_nvenc_encoder("h264_nvenc")

            assert result is True
            # Verify correct command was called
            args = mock_run.call_args[0][0]
            assert "ffmpeg" in args
            assert "-hwaccel" in args
            assert "cuda" in args
            assert "h264_nvenc" in args
            assert "-f" in args and "null" in args

    @pytest.mark.asyncio
    async def test_nvenc_encoder_validation_failure(self):
        """Test NVENC encoder validation when encoder fails."""
        with patch("worker.hwaccel._run_command") as mock_run:
            # Mock failed encode test
            mock_run.return_value = (1, "", "NVENC encoder initialization failed")

            result = await _test_nvenc_encoder("h264_nvenc")

            assert result is False

    @pytest.mark.asyncio
    async def test_vaapi_encoder_validation_success(self):
        """Test VAAPI encoder validation when encoder works."""
        with patch("worker.hwaccel._run_command") as mock_run:
            # Mock successful encode test
            mock_run.return_value = (0, "", "")

            result = await _test_vaapi_encoder("h264_vaapi", "/dev/dri/renderD128")

            assert result is True
            # Verify correct command was called
            args = mock_run.call_args[0][0]
            assert "ffmpeg" in args
            assert "-vaapi_device" in args
            assert "/dev/dri/renderD128" in args
            assert "h264_vaapi" in args

    @pytest.mark.asyncio
    async def test_vaapi_encoder_validation_failure(self):
        """Test VAAPI encoder validation when encoder fails."""
        with patch("worker.hwaccel._run_command") as mock_run:
            # Mock failed encode test
            mock_run.return_value = (1, "", "VAAPI encoder initialization failed")

            result = await _test_vaapi_encoder("h264_vaapi", "/dev/dri/renderD128")

            assert result is False

    @pytest.mark.asyncio
    async def test_detect_nvidia_gpu_available(self):
        """Test NVIDIA GPU detection when nvidia-smi is available."""
        with patch("worker.hwaccel._run_command") as mock_run:
            # Mock nvidia-smi responses
            async def mock_command(cmd, timeout=10.0):
                if "nvidia-smi" in cmd[0]:
                    if "--query-gpu=name,driver_version" in cmd:
                        return 0, "NVIDIA GeForce RTX 3090, 535.154.05\n", ""
                    else:
                        return 0, "CUDA Version: 12.2\n", ""
                elif "ffmpeg" in cmd[0]:
                    if "-encoders" in cmd:
                        return 0, " V..... h264_nvenc\n V..... hevc_nvenc\n", ""
                    else:
                        # Mock successful encoder tests
                        return 0, "", ""
                return -1, "", "Not found"

            mock_run.side_effect = mock_command

            caps = await detect_nvidia_gpu()

            assert caps is not None
            assert caps.hwaccel_type == HWAccelType.NVIDIA
            assert "RTX 3090" in caps.device_name
            assert caps.max_concurrent_sessions == 3  # RTX 3090 limit

    @pytest.mark.asyncio
    async def test_detect_nvidia_gpu_encoder_test_failure(self):
        """Test NVIDIA GPU detection when encoders are listed but don't work."""
        with patch("worker.hwaccel._run_command") as mock_run:
            # Mock nvidia-smi responses
            async def mock_command(cmd, timeout=10.0):
                if "nvidia-smi" in cmd[0]:
                    if "--query-gpu=name,driver_version" in cmd:
                        return 0, "NVIDIA GeForce RTX 3090, 535.154.05\n", ""
                    else:
                        return 0, "CUDA Version: 12.2\n", ""
                elif "ffmpeg" in cmd[0]:
                    if "-encoders" in cmd:
                        # Encoders are listed
                        return 0, " V..... h264_nvenc\n V..... hevc_nvenc\n", ""
                    else:
                        # But encoder tests fail (e.g., missing CUDA libraries)
                        return 1, "", "NVENC encoder initialization failed"
                return -1, "", "Not found"

            mock_run.side_effect = mock_command

            caps = await detect_nvidia_gpu()

            # Should return None since no working encoders
            assert caps is None

    @pytest.mark.asyncio
    async def test_detect_nvidia_gpu_not_available(self):
        """Test NVIDIA detection when nvidia-smi fails."""
        with patch("worker.hwaccel._run_command") as mock_run:
            mock_run.return_value = (-1, "", "Command not found")

            caps = await detect_nvidia_gpu()

            assert caps is None

    @pytest.mark.asyncio
    async def test_detect_gpu_capabilities_none(self):
        """Test GPU detection with HWACCEL_TYPE=none."""
        with patch.dict("os.environ", {"VLOG_HWACCEL_TYPE": "none"}):
            caps = await detect_gpu_capabilities()
            assert caps is None


class TestWorkerCapabilities:
    """Tests for worker capability reporting."""

    @pytest.mark.asyncio
    async def test_get_capabilities_with_nvidia_gpu(self):
        """Test capability reporting with NVIDIA GPU."""
        caps = GPUCapabilities(
            hwaccel_type=HWAccelType.NVIDIA,
            device_name="NVIDIA GeForce RTX 4090",
            driver_version="535.154.05",
            cuda_version="12.2",
            max_concurrent_sessions=5,
            encoders={
                VideoCodec.H264: [
                    EncoderInfo(
                        name="h264_nvenc",
                        codec=VideoCodec.H264,
                        hwaccel_type=HWAccelType.NVIDIA,
                        is_hardware=True,
                    )
                ],
                VideoCodec.HEVC: [
                    EncoderInfo(
                        name="hevc_nvenc",
                        codec=VideoCodec.HEVC,
                        hwaccel_type=HWAccelType.NVIDIA,
                        is_hardware=True,
                    )
                ],
            },
        )

        with patch("worker.hwaccel._run_command") as mock_run:
            mock_run.return_value = (0, "ffmpeg version 6.1\n", "")

            worker_caps = await get_worker_capabilities(caps)

            assert worker_caps["hwaccel_enabled"] is True
            assert worker_caps["hwaccel_type"] == "nvidia"
            assert worker_caps["gpu_name"] == "NVIDIA GeForce RTX 4090"
            assert worker_caps["max_concurrent_encode_sessions"] == 5
            assert "h264" in worker_caps["supported_codecs"]
            assert "hevc" in worker_caps["supported_codecs"]
            assert "h264_nvenc" in worker_caps["encoders"]["h264"]

    @pytest.mark.asyncio
    async def test_get_capabilities_cpu_only(self):
        """Test capability reporting without GPU."""
        with patch("worker.hwaccel._run_command") as mock_run:
            mock_run.return_value = (0, "ffmpeg version 6.1\n", "")

            worker_caps = await get_worker_capabilities(None)

            assert worker_caps["hwaccel_enabled"] is False
            assert worker_caps["hwaccel_type"] == "none"
            assert worker_caps["gpu_name"] is None
            assert worker_caps["max_concurrent_encode_sessions"] == 1
            assert "h264" in worker_caps["supported_codecs"]
            assert "libx264" in worker_caps["encoders"]["h264"]
