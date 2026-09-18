"""Codec metadata from fragmented MP4 initialization segments."""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from worker.hwaccel import extract_codec_string_from_file


@pytest.mark.asyncio
@pytest.mark.parametrize('record, expected', [
    ('0164 001e', 'avc1.64001e,mp4a.40.2'),
    ('0142 c015', 'avc1.42c015,mp4a.40.2'),
])
async def test_h264_init_uses_avcc_when_ffprobe_level_unknown(tmp_path, record, expected):
    path = tmp_path / 'init.mp4'
    path.touch()
    streams = [
        {'codec_name': 'h264', 'codec_tag_string': 'avc1', 'level': -99,
         'extradata': f'\n00000000: {record} ffe1 001a 6764 001e acd9 40a0  .d......gd....@.\n'},
        {'codec_name': 'aac'},
    ]
    with patch('subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps({'streams': streams}))):
        assert await extract_codec_string_from_file(path) == expected


@pytest.mark.asyncio
async def test_unknown_level_without_config_does_not_emit_invalid_codec(tmp_path):
    path = tmp_path / 'init.mp4'
    path.touch()
    streams = [{'codec_name': 'h264', 'level': -99}]
    with patch('subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps({'streams': streams}))):
        assert await extract_codec_string_from_file(path) is None
