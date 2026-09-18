"""A failed encoder must not be reported as a completed rendition."""

from unittest.mock import AsyncMock

import pytest

from worker import remote_transcoder
from worker.hwaccel import VideoCodec


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", [".mp4", ".txt"])
async def test_failed_reencode_raises_and_creates_quality_directory(tmp_path, monkeypatch, suffix):
    run = AsyncMock(return_value=(False, "encoder rejected parameters"))
    monkeypatch.setattr(remote_transcoder, "run_ffmpeg_with_progress", run)
    output = tmp_path / "output"
    with pytest.raises(RuntimeError, match="encoder rejected parameters"):
        await remote_transcoder.reencode_quality(
            tmp_path / ("source" + suffix), output,
            {"name": "360p", "height": 360, "bitrate": "800k", "audio_bitrate": "96k"},
            VideoCodec.AV1, 1.0, None,
        )
    assert (output / "360p").is_dir()
    command = run.call_args.args[0]
    assert "-b:v" not in command  # SVT-AV1 capped CRF rejects a target bitrate.
    assert command[command.index("-maxrate") + 1] == "800k"
    assert command[command.index("-vf") + 1] == "scale=-2:360"
