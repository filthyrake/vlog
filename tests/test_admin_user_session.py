"""Regress the real user-cookie/legacy-admin-middleware integration."""
import hashlib
import hmac
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import admin


@pytest.fixture
def user_client(monkeypatch):
    monkeypatch.setattr(admin, 'ADMIN_API_SECRET', 'legacy-configured')
    monkeypatch.setattr(admin, 'SESSION_SECRET_KEY', 'session-key-for-tests')
    validator = AsyncMock(return_value={'id': 'test-admin', 'role': 'admin'})
    monkeypatch.setattr(admin, 'validate_user_session', validator)
    app = FastAPI()
    app.add_middleware(admin.AdminAuthMiddleware)
    app.add_api_route("/api/v1/auth/csrf-token", admin.get_csrf_token)

    @app.api_route('/api/videos', methods=['GET', 'POST'])
    async def videos():
        return {'ok': True}

    client = TestClient(app)
    client.cookies.set('vlog_session', 'user-session-token')
    return client, validator


def test_admin_user_session_can_read_and_write_with_csrf(user_client):
    client, _ = user_client
    assert client.get('/api/videos').status_code == 200
    assert client.post('/api/videos').status_code == 403
    csrf = client.get('/api/v1/auth/csrf-token').json()['csrf_token']
    assert csrf == hmac.new(b'session-key-for-tests', b'user-session-token', hashlib.sha256).hexdigest()[:32]
    assert client.post('/api/videos', headers={'X-CSRF-Token': csrf}).status_code == 200


def test_viewer_cannot_use_admin_api(user_client):
    client, validator = user_client
    validator.return_value = {'id': 'viewer', 'role': 'viewer'}
    assert client.get('/api/videos').status_code == 403


def test_expired_user_session_is_rejected(user_client):
    client, validator = user_client
    validator.return_value = None
    assert client.get('/api/videos').status_code == 401
