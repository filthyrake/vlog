import time
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import download_tokens, studio_vod
from api.auth.middleware import require_auth


def test_download_signature_binds_user_path_and_expiry(monkeypatch):
    monkeypatch.setattr(download_tokens, "SESSION_SECRET_KEY", "test-key" * 8)
    expiry = int(time.time()) + 300
    token = download_tokens.sign_download("video", "original.mp4", "alice", expiry)
    assert download_tokens.verify_download("video", "original.mp4", "alice", expiry, token)
    for slug, filename, user, exp, signature in [
        ("other", "original.mp4", "alice", expiry, token),
        ("video", "original.mov", "alice", expiry, token),
        ("video", "original.mp4", "bob", expiry, token),
        ("video", "original.mp4", "alice", expiry + 1, token),
        ("video", "original.mp4", "alice", int(time.time()) - 1, token),
        ("video", "original.mp4", "alice", expiry, "é" * 64),
    ]:
        assert not download_tokens.verify_download(slug, filename, user, exp, signature)


def test_signed_download_requires_current_user_and_access(monkeypatch, tmp_path):
    monkeypatch.setattr(studio_vod, "LIVE_ENABLED", True)
    monkeypatch.setattr(studio_vod, "VIDEOS_DIR", tmp_path)
    monkeypatch.setattr(studio_vod.limiter, "enabled", False)
    monkeypatch.setattr(download_tokens, "SESSION_SECRET_KEY", "test-key" * 8)
    access = AsyncMock(return_value={"slug": "video"})
    monkeypatch.setattr(studio_vod, "verify_vod_access", access)
    (tmp_path / "video").mkdir()
    (tmp_path / "video" / "original.mp4").write_bytes(b"test source")
    app = FastAPI()
    app.include_router(studio_vod.router)
    user = {"id": "alice"}
    app.dependency_overrides[require_auth] = lambda: user
    client = TestClient(app)
    link = client.get("/api/v1/studio/vods/video/download").json()["download_url"]
    response = client.get(link)
    assert response.status_code == 200
    assert response.content == b"test source"
    assert response.headers["cache-control"] == "private, no-store"
    access.assert_awaited()
    user["id"] = "bob"
    assert client.get(link).status_code == 403
    user["id"] = "alice"
    from fastapi import HTTPException
    access.side_effect = HTTPException(status_code=404, detail="VOD not found")
    assert client.get(link).status_code == 404


@pytest.mark.parametrize("mount_type", ["public", "admin"])
def test_public_static_mount_does_not_serve_source_files(tmp_path, mount_type):
    from api.public import StreamingStaticFiles
    from api.source_files import PlaybackStaticFiles
    files_class = StreamingStaticFiles if mount_type == "public" else PlaybackStaticFiles
    (tmp_path / "original.mp4").write_bytes(b"private source")
    (tmp_path / "original.m3u8").write_text("#EXTM3U")
    app = FastAPI()
    app.mount("/videos", files_class(directory=tmp_path))
    client = TestClient(app)
    assert client.get("/videos/original.mp4").status_code == 404
    assert client.get("/videos/original.m3u8").status_code == 200
