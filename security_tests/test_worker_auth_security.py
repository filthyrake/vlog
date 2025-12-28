import sys
from unittest.mock import MagicMock

sys.modules["api.database"] = MagicMock()
sys.modules["api.database"].database = MagicMock()
sys.modules["api.database"].worker_api_keys = MagicMock()
sys.modules["api.database"].workers = MagicMock()


import pytest
from unittest.mock import AsyncMock, Mock, patch
from fastapi import HTTPException

from api.worker_auth import (
    hash_api_key,
    _get_request_context,
    verify_worker_key,
)

def test_hash_api_key_deterministic():
    key = "secret-key"
    assert hash_api_key(key) == hash_api_key(key)


def test_hash_api_key_uniqueness():
    assert hash_api_key("key1") != hash_api_key("key2")


def test_hash_api_key_not_reversible():
    key = "super-secret"
    hashed = hash_api_key(key)
    assert key not in hashed
    
@pytest.fixture
def mock_request():
    req = Mock()
    req.client = Mock()
    req.client.host = "127.0.0.1"
    req.headers = {}
    return req

def test_request_context_none():
    ctx = _get_request_context(None)
    assert ctx["ip_address"] == "unknown"

@patch("api.worker_auth.TRUSTED_PROXIES", {"127.0.0.1"})
def test_request_context_forwarded_for_trusted_proxy(mock_request):
    mock_request.headers["x-forwarded-for"] = "10.0.0.1"
    ctx = _get_request_context(mock_request)
    assert ctx["ip_address"] == "10.0.0.1"

@patch("api.worker_auth.TRUSTED_PROXIES", set())
def test_request_context_forwarded_for_untrusted_proxy(mock_request):
    mock_request.headers["x-forwarded-for"] = "10.0.0.1"
    ctx = _get_request_context(mock_request)
    assert ctx["ip_address"] == "127.0.0.1"

@patch("api.worker_auth.TRUSTED_PROXIES", {"127.0.0.1"})
def test_request_context_multiple_forwarded_ips(mock_request):
    mock_request.headers["x-forwarded-for"] = "10.0.0.1, 10.0.0.2"
    ctx = _get_request_context(mock_request)
    assert ctx["forwarded_for"] == "10.0.0.1"

@patch("api.worker_auth.TRUSTED_PROXIES", {"127.0.0.1"})
def test_request_context_ipv6(mock_request):
    mock_request.headers["x-forwarded-for"] = "2001:db8::1"
    ctx = _get_request_context(mock_request)
    assert ctx["ip_address"] == "2001:db8::1"

@pytest.fixture
def mock_database():
    with patch("api.worker_auth.database") as db:
        db.fetch_one = AsyncMock()
        db.execute = AsyncMock()
        yield db

@pytest.mark.asyncio
async def test_verify_worker_key_missing_key(mock_request):
    with pytest.raises(HTTPException) as exc:
        await verify_worker_key(mock_request, None)

    assert exc.value.status_code == 401

@pytest.mark.asyncio
async def test_verify_worker_key_invalid_key(mock_request, mock_database):
    mock_database.fetch_one.return_value = None

    with pytest.raises(HTTPException) as exc:
        await verify_worker_key(mock_request, "invalid-key")

    assert exc.value.status_code == 401

@pytest.mark.asyncio
async def test_verify_worker_key_hash_mismatch(mock_request, mock_database):
    mock_database.fetch_one.side_effect = [
        {"key_hash": "wrong-hash", "expires_at": None, "worker_id": "w1", "id": 1},
    ]

    with pytest.raises(HTTPException) as exc:
        await verify_worker_key(mock_request, "real-key")

    assert exc.value.status_code == 401

from datetime import datetime, timedelta, timezone

@pytest.mark.asyncio
async def test_verify_worker_key_expired(mock_request, mock_database):
    expired_time = datetime.now(timezone.utc) - timedelta(days=1)

    mock_database.fetch_one.side_effect = [
        {
            "key_hash": hash_api_key("key"),
            "expires_at": expired_time,
            "worker_id": "w1",
            "id": 1,
        }
    ]

    with pytest.raises(HTTPException) as exc:
        await verify_worker_key(mock_request, "key")

    assert exc.value.status_code == 401

@pytest.mark.asyncio
async def test_verify_worker_key_disabled_worker(mock_request, mock_database):
    mock_database.fetch_one.side_effect = [
        {
            "key_hash": hash_api_key("key"),
            "expires_at": None,
            "worker_id": "w1",
            "id": 1,
        },
        {
            "status": "disabled",
            "worker_id": "w1",
            "worker_name": "Worker One",
        },
    ]

    with pytest.raises(HTTPException) as exc:
        await verify_worker_key(mock_request, "key")

    assert exc.value.status_code == 403

@pytest.mark.asyncio
async def test_verify_worker_key_success(mock_request, mock_database):
    mock_database.fetch_one.side_effect = [
        {
            "key_hash": hash_api_key("key"),
            "expires_at": None,
            "worker_id": "w1",
            "id": 1,
        },
        {
            "status": "active",
            "worker_id": "w1",
            "worker_name": "Worker One",
        },
    ]

    result = await verify_worker_key(mock_request, "key")
    assert result["worker_id"] == "w1"


