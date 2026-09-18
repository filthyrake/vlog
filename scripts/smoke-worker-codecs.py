"""Exercise the remote worker's real CMAF re-encode path for every CPU codec.

Run inside the built worker image with this script mounted read-only; no database,
network, or production storage is needed. All media is disposable temporary data.
"""

import asyncio
import json
import subprocess
import tempfile
from pathlib import Path

from worker.hwaccel import VideoCodec
from worker.remote_transcoder import reencode_quality


async def main():
    with tempfile.TemporaryDirectory(prefix="vlog-codecs-") as work:
        root = Path(work)
        source = root / "source.mp4"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
             "-s", "128x128", "-r", "10", "-i", "pipe:0", "-c:v", "libx264",
             "-pix_fmt", "yuv420p", str(source)],
            input=bytes((40, 120, 200)) * 128 * 128 * 10,
            check=True, timeout=60,
        )
        for codec in (VideoCodec.H264, VideoCodec.HEVC, VideoCodec.AV1):
            output = root / codec.value
            (output / "test").mkdir(parents=True)
            await reencode_quality(
                source, output,
                {"name": "test", "height": 128, "bitrate": "250k", "audio_bitrate": "64k"},
                codec, duration=1.0, gpu_caps=None,
            )
            playlist = output / "test/stream.m3u8"
            if not playlist.is_file() or not list((output / "test").glob("seg_*.m4s")):
                raise RuntimeError(f"Missing CMAF output for {codec.value}")
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                 "stream=codec_name", "-of", "json", str(playlist)],
                capture_output=True, text=True, check=True, timeout=30,
            )
            actual = json.loads(probe.stdout)["streams"][0]["codec_name"]
            if actual != codec.value:
                raise RuntimeError(f"Expected {codec.value}, got {actual}")
            subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(playlist), "-f", "null", "-"],
                check=True, timeout=60,
            )
            print(f"{codec.value}: CMAF re-encode and decode passed", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
