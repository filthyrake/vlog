"""
Pytest fixtures for VLog tests.
Provides test database, test clients, and sample data.
"""

import asyncio
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator

import pytest
import sqlalchemy as sa
from databases import Database

# Set up test paths BEFORE importing config
_test_temp_dir = tempfile.mkdtemp()
os.environ["VLOG_TEST_MODE"] = "1"

# Import config and override paths for testing
from api.database import (  # noqa: E402
    categories,
    metadata,
    playback_sessions,
    transcriptions,
    video_qualities,
    videos,
)
from api.enums import VideoStatus  # noqa: E402

# Test database path
TEST_DB_PATH = Path(_test_temp_dir) / "test_vlog.db"
TEST_DB_URL = f"sqlite:///{TEST_DB_PATH}"

# Test storage paths
TEST_VIDEOS_DIR = Path(_test_temp_dir) / "videos"
TEST_UPLOADS_DIR = Path(_test_temp_dir) / "uploads"
TEST_ARCHIVE_DIR = Path(_test_temp_dir) / "archive"


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="function")
def test_db_path(tmp_path: Path) -> Path:
    """Create a unique test database path for each test."""
    return tmp_path / "test_vlog.db"


@pytest.fixture(scope="function")
def test_storage(tmp_path: Path) -> dict:
    """Create test storage directories."""
    videos_dir = tmp_path / "videos"
    uploads_dir = tmp_path / "uploads"
    archive_dir = tmp_path / "archive"

    videos_dir.mkdir(parents=True, exist_ok=True)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)

    return {
        "videos": videos_dir,
        "uploads": uploads_dir,
        "archive": archive_dir,
    }


@pytest.fixture(scope="function")
async def test_database(test_db_path: Path) -> AsyncGenerator[Database, None]:
    """Create a fresh test database for each test."""
    db_url = f"sqlite:///{test_db_path}"

    # Create tables using sync engine
    engine = sa.create_engine(db_url)
    metadata.create_all(engine)
    engine.dispose()

    # Connect async database
    database = Database(db_url)
    await database.connect()

    yield database

    await database.disconnect()

    # Clean up
    if test_db_path.exists():
        test_db_path.unlink()


@pytest.fixture(scope="function")
async def sample_category(test_database: Database) -> dict:
    """Create a sample category for testing."""
    now = datetime.now(timezone.utc)
    result = await test_database.execute(
        categories.insert().values(
            name="Test Category",
            slug="test-category",
            description="A test category",
            created_at=now,
        )
    )
    return {
        "id": result,
        "name": "Test Category",
        "slug": "test-category",
        "description": "A test category",
        "created_at": now,
    }


@pytest.fixture(scope="function")
async def sample_video(test_database: Database, sample_category: dict) -> dict:
    """Create a sample video for testing."""
    now = datetime.now(timezone.utc)
    result = await test_database.execute(
        videos.insert().values(
            title="Test Video",
            slug="test-video",
            description="A test video description",
            category_id=sample_category["id"],
            duration=120.5,
            source_width=1920,
            source_height=1080,
            status=VideoStatus.READY,
            created_at=now,
            published_at=now,
        )
    )
    return {
        "id": result,
        "title": "Test Video",
        "slug": "test-video",
        "description": "A test video description",
        "category_id": sample_category["id"],
        "duration": 120.5,
        "source_width": 1920,
        "source_height": 1080,
        "status": VideoStatus.READY,
        "created_at": now,
        "published_at": now,
    }


@pytest.fixture(scope="function")
async def sample_video_with_qualities(test_database: Database, sample_video: dict) -> dict:
    """Create a sample video with quality variants."""
    video_id = sample_video["id"]

    qualities = [
        {"quality": "1080p", "width": 1920, "height": 1080, "bitrate": 5000},
        {"quality": "720p", "width": 1280, "height": 720, "bitrate": 2500},
        {"quality": "480p", "width": 854, "height": 480, "bitrate": 1000},
    ]

    for q in qualities:
        await test_database.execute(
            video_qualities.insert().values(
                video_id=video_id,
                quality=q["quality"],
                width=q["width"],
                height=q["height"],
                bitrate=q["bitrate"],
            )
        )

    sample_video["qualities"] = qualities
    return sample_video


@pytest.fixture(scope="function")
async def sample_pending_video(test_database: Database, sample_category: dict) -> dict:
    """Create a sample pending video for testing."""
    now = datetime.now(timezone.utc)
    result = await test_database.execute(
        videos.insert().values(
            title="Pending Video",
            slug="pending-video",
            description="A video waiting to be processed",
            category_id=sample_category["id"],
            status=VideoStatus.PENDING,
            created_at=now,
        )
    )
    return {
        "id": result,
        "title": "Pending Video",
        "slug": "pending-video",
        "status": VideoStatus.PENDING,
    }


@pytest.fixture(scope="function")
async def sample_deleted_video(test_database: Database, sample_category: dict) -> dict:
    """Create a sample deleted video for testing."""
    now = datetime.now(timezone.utc)
    result = await test_database.execute(
        videos.insert().values(
            title="Deleted Video",
            slug="deleted-video",
            description="A video that has been deleted",
            category_id=sample_category["id"],
            status=VideoStatus.READY,
            created_at=now,
            deleted_at=now,  # Soft-deleted
        )
    )
    return {
        "id": result,
        "title": "Deleted Video",
        "slug": "deleted-video",
        "status": VideoStatus.READY,
        "deleted_at": now,
    }


@pytest.fixture(scope="function")
async def sample_playback_session(test_database: Database, sample_video: dict) -> dict:
    """Create a sample playback session for testing."""
    import uuid

    now = datetime.now(timezone.utc)
    session_token = str(uuid.uuid4())

    result = await test_database.execute(
        playback_sessions.insert().values(
            video_id=sample_video["id"],
            session_token=session_token,
            started_at=now,
            duration_watched=60.0,
            max_position=90.0,
            quality_used="1080p",
        )
    )

    return {
        "id": result,
        "video_id": sample_video["id"],
        "session_token": session_token,
        "started_at": now,
        "duration_watched": 60.0,
        "max_position": 90.0,
    }


# ============================================================================
# Test Client Fixtures (require patching config)
# ============================================================================


@pytest.fixture(scope="function")
def public_client(test_database: Database, test_storage: dict, test_db_path: Path, monkeypatch):
    """
    Create a test client for the public API.
    Patches config to use test paths.
    """
    import importlib
    import sys

    from fastapi.testclient import TestClient

    # Patch config before importing app
    import config

    monkeypatch.setattr(config, "VIDEOS_DIR", test_storage["videos"])
    monkeypatch.setattr(config, "UPLOADS_DIR", test_storage["uploads"])
    monkeypatch.setattr(config, "ARCHIVE_DIR", test_storage["archive"])
    monkeypatch.setattr(config, "DATABASE_PATH", test_db_path)

    # Patch database in api.database module
    import api.database

    monkeypatch.setattr(api.database, "DATABASE_URL", f"sqlite:///{test_db_path}")
    monkeypatch.setattr(api.database, "database", test_database)

    # Force reload the public module to pick up the patched database
    if "api.public" in sys.modules:
        # Re-patch the module's database reference after reload
        importlib.reload(sys.modules["api.public"])

    # Patch the app's database reference directly
    import api.public
    from api.public import app

    monkeypatch.setattr(api.public, "database", test_database)

    # Create test client without lifespan (we manage database ourselves)
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


@pytest.fixture(scope="function")
def admin_client(test_database: Database, test_storage: dict, test_db_path: Path, monkeypatch):
    """
    Create a test client for the admin API.
    Patches config to use test paths.
    """
    import importlib
    import sys

    from fastapi.testclient import TestClient

    # Patch config before importing app
    import config

    monkeypatch.setattr(config, "VIDEOS_DIR", test_storage["videos"])
    monkeypatch.setattr(config, "UPLOADS_DIR", test_storage["uploads"])
    monkeypatch.setattr(config, "ARCHIVE_DIR", test_storage["archive"])
    monkeypatch.setattr(config, "DATABASE_PATH", test_db_path)

    # Patch database in api.database module
    import api.database

    monkeypatch.setattr(api.database, "DATABASE_URL", f"sqlite:///{test_db_path}")
    monkeypatch.setattr(api.database, "database", test_database)
    # Patch create_tables to no-op since test_database fixture already created tables
    monkeypatch.setattr(api.database, "create_tables", lambda: None)

    # Force reload the admin module to pick up the patched database
    if "api.admin" in sys.modules:
        importlib.reload(sys.modules["api.admin"])

    # Patch the app's database reference directly
    import api.admin
    from api.admin import app

    monkeypatch.setattr(api.admin, "database", test_database)
    # Also patch create_tables in admin module (after reload)
    monkeypatch.setattr(api.admin, "create_tables", lambda: None)

    # Create test client without lifespan
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


# Test admin secret for worker API authentication
TEST_WORKER_ADMIN_SECRET = "test-admin-secret-for-worker-api-12345"


@pytest.fixture(scope="function")
def worker_admin_headers():
    """Return headers for worker admin authentication."""
    return {"X-Admin-Secret": TEST_WORKER_ADMIN_SECRET}


@pytest.fixture(scope="function")
def worker_client(test_database: Database, test_storage: dict, test_db_path: Path, monkeypatch):
    """
    Create a test client for the Worker API.
    Patches config to use test paths.
    """
    import importlib
    import sys

    from fastapi.testclient import TestClient

    # Patch config before importing app
    import config

    monkeypatch.setattr(config, "VIDEOS_DIR", test_storage["videos"])
    monkeypatch.setattr(config, "UPLOADS_DIR", test_storage["uploads"])
    monkeypatch.setattr(config, "ARCHIVE_DIR", test_storage["archive"])
    monkeypatch.setattr(config, "DATABASE_PATH", test_db_path)
    # Set the worker admin secret for testing
    monkeypatch.setattr(config, "WORKER_ADMIN_SECRET", TEST_WORKER_ADMIN_SECRET)

    # Patch the database module
    import api.database

    monkeypatch.setattr(api.database, "database", test_database)
    monkeypatch.setattr(api.database, "create_tables", lambda: None)

    # Force reload worker_auth to pick up the patched database
    if "api.worker_auth" in sys.modules:
        importlib.reload(sys.modules["api.worker_auth"])

    # Patch worker_auth's database reference
    import api.worker_auth

    monkeypatch.setattr(api.worker_auth, "database", test_database)

    # Force reload the worker_api module to pick up the patched database
    if "api.worker_api" in sys.modules:
        importlib.reload(sys.modules["api.worker_api"])

    # Patch the app's database reference directly
    import api.worker_api
    from api.worker_api import app

    monkeypatch.setattr(api.worker_api, "database", test_database)
    # Also patch the admin secret after reload
    monkeypatch.setattr(api.worker_api, "WORKER_ADMIN_SECRET", TEST_WORKER_ADMIN_SECRET)

    # Create test client without lifespan
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


@pytest.fixture(scope="function")
def registered_worker(worker_client, worker_admin_headers) -> dict:
    """
    Register a worker and return its credentials.
    """
    response = worker_client.post(
        "/api/worker/register",
        json={"worker_name": "test-worker", "worker_type": "remote"},
        headers=worker_admin_headers,
    )
    assert response.status_code == 200
    return response.json()
