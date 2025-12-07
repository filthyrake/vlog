"""
Pytest fixtures for VLog tests.
Provides test database, test clients, and sample data.

Uses PostgreSQL for testing to match production database.
"""

import asyncio
import os
import re
import tempfile
import uuid
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
    video_qualities,
    videos,
)
from api.enums import VideoStatus  # noqa: E402

# PostgreSQL test database configuration
# Uses environment variable or falls back to local development defaults
TEST_PG_HOST = os.environ.get("VLOG_TEST_PG_HOST", "localhost")
TEST_PG_PORT = os.environ.get("VLOG_TEST_PG_PORT", "5432")
TEST_PG_USER = os.environ.get("VLOG_TEST_PG_USER", "vlog")
TEST_PG_PASSWORD = os.environ.get("VLOG_TEST_PG_PASSWORD", "vlog_password")
TEST_PG_ADMIN_DB = os.environ.get("VLOG_TEST_PG_ADMIN_DB", "postgres")

# Base URL for connecting to PostgreSQL (used to create/drop test databases)
TEST_PG_ADMIN_URL = f"postgresql://{TEST_PG_USER}:{TEST_PG_PASSWORD}@{TEST_PG_HOST}:{TEST_PG_PORT}/{TEST_PG_ADMIN_DB}"

# Test storage paths
TEST_VIDEOS_DIR = Path(_test_temp_dir) / "videos"
TEST_UPLOADS_DIR = Path(_test_temp_dir) / "uploads"
TEST_ARCHIVE_DIR = Path(_test_temp_dir) / "archive"


def _validate_db_name(db_name: str) -> None:
    """Validate database name contains only safe characters to prevent SQL injection."""
    if not re.match(r"^[a-zA-Z0-9_]+$", db_name):
        raise ValueError(f"Invalid database name: {db_name}")


def _generate_test_db_name() -> str:
    """Generate a unique test database name."""
    # Use a UUID suffix to ensure uniqueness across parallel test runs
    suffix = uuid.uuid4().hex[:8]
    return f"vlog_test_{suffix}"


def _create_test_database(db_name: str) -> str:
    """Create a test database and return its URL."""
    _validate_db_name(db_name)
    admin_engine = sa.create_engine(TEST_PG_ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        # Drop if exists (shouldn't happen but just in case)
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        conn.execute(sa.text(f'CREATE DATABASE "{db_name}"'))
    admin_engine.dispose()
    return f"postgresql://{TEST_PG_USER}:{TEST_PG_PASSWORD}@{TEST_PG_HOST}:{TEST_PG_PORT}/{db_name}"


def _drop_test_database(db_name: str) -> None:
    """Drop a test database."""
    _validate_db_name(db_name)
    admin_engine = sa.create_engine(TEST_PG_ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        # Try to terminate connections (may fail if not superuser, which is ok)
        try:
            conn.execute(
                sa.text(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = :db_name AND pid <> pg_backend_pid()
                    """
                ),
                {"db_name": db_name},
            )
        except Exception:
            # Not a superuser, can't terminate connections - that's ok
            pass
        # Drop the database (may need to retry if connections still exist)
        try:
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{db_name}"'))
        except Exception:
            # Database may still have connections, ignore cleanup failure
            pass
    admin_engine.dispose()


def _create_tables(db_url: str) -> None:
    """Create all tables in the test database."""
    engine = sa.create_engine(db_url)
    metadata.create_all(engine)
    engine.dispose()


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="function")
def test_db_name() -> str:
    """Generate a unique test database name for each test."""
    return _generate_test_db_name()


@pytest.fixture(scope="function")
def test_db_url(test_db_name: str) -> str:
    """Create a test database and return its URL. Cleans up after test."""
    db_url = _create_test_database(test_db_name)
    _create_tables(db_url)
    yield db_url
    _drop_test_database(test_db_name)


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
async def test_database(test_db_url: str) -> AsyncGenerator[Database, None]:
    """Create a fresh test database for each test."""
    # Connect async database
    database = Database(test_db_url)
    await database.connect()

    yield database

    await database.disconnect()


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
def public_client(test_storage: dict, test_db_url: str, monkeypatch):
    """
    Create a test client for the public API.
    Patches config to use test paths.
    The app manages its own database connection through its lifespan.
    """
    import importlib
    import sys

    from fastapi.testclient import TestClient

    # Patch config before importing app
    import config

    monkeypatch.setattr(config, "VIDEOS_DIR", test_storage["videos"])
    monkeypatch.setattr(config, "UPLOADS_DIR", test_storage["uploads"])
    monkeypatch.setattr(config, "ARCHIVE_DIR", test_storage["archive"])
    monkeypatch.setattr(config, "DATABASE_URL", test_db_url)

    # Reload api.database to create a new Database instance with the test URL
    if "api.database" in sys.modules:
        importlib.reload(sys.modules["api.database"])

    # Force reload the public module to pick up the new database
    if "api.public" in sys.modules:
        importlib.reload(sys.modules["api.public"])

    from api.public import app

    # Create test client with lifespan so the app manages its own database
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


@pytest.fixture(scope="function")
def admin_client(test_storage: dict, test_db_url: str, monkeypatch):
    """
    Create a test client for the admin API.
    Patches config to use test paths.
    The app manages its own database connection through its lifespan.
    """
    import importlib
    import sys

    from fastapi.testclient import TestClient

    # Patch config before importing app
    import config

    monkeypatch.setattr(config, "VIDEOS_DIR", test_storage["videos"])
    monkeypatch.setattr(config, "UPLOADS_DIR", test_storage["uploads"])
    monkeypatch.setattr(config, "ARCHIVE_DIR", test_storage["archive"])
    monkeypatch.setattr(config, "DATABASE_URL", test_db_url)

    # Reload api.database to create a new Database instance with the test URL
    if "api.database" in sys.modules:
        importlib.reload(sys.modules["api.database"])

    # Force reload the admin module to pick up the new database
    if "api.admin" in sys.modules:
        importlib.reload(sys.modules["api.admin"])

    from api.admin import app

    # Create test client with lifespan so the app manages its own database
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


# Test admin secret for worker API authentication
TEST_WORKER_ADMIN_SECRET = "test-admin-secret-for-worker-api-12345"


@pytest.fixture(scope="function")
def worker_admin_headers():
    """Return headers for worker admin authentication."""
    return {"X-Admin-Secret": TEST_WORKER_ADMIN_SECRET}


@pytest.fixture(scope="function")
def worker_client(test_storage: dict, test_db_url: str, monkeypatch):
    """
    Create a test client for the Worker API.
    Patches config to use test paths.
    The app manages its own database connection through its lifespan.
    """
    import importlib
    import sys

    from fastapi.testclient import TestClient

    # Patch config before importing app
    import config

    monkeypatch.setattr(config, "VIDEOS_DIR", test_storage["videos"])
    monkeypatch.setattr(config, "UPLOADS_DIR", test_storage["uploads"])
    monkeypatch.setattr(config, "ARCHIVE_DIR", test_storage["archive"])
    monkeypatch.setattr(config, "DATABASE_URL", test_db_url)
    # Set the worker admin secret for testing
    monkeypatch.setattr(config, "WORKER_ADMIN_SECRET", TEST_WORKER_ADMIN_SECRET)

    # Reload api.database to create a new Database instance with the test URL
    if "api.database" in sys.modules:
        importlib.reload(sys.modules["api.database"])

    # Force reload common to pick up new database and storage paths (needed for health checks)
    if "api.common" in sys.modules:
        importlib.reload(sys.modules["api.common"])

    # Force reload worker_auth to pick up the new database
    if "api.worker_auth" in sys.modules:
        importlib.reload(sys.modules["api.worker_auth"])

    # Force reload the worker_api module to pick up the new database
    if "api.worker_api" in sys.modules:
        importlib.reload(sys.modules["api.worker_api"])

    from api.worker_api import app

    # Create test client with lifespan so the app manages its own database
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
