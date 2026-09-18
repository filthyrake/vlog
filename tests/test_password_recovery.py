"""Bound anonymous recovery work and verify transactional single-use tokens."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from limits.storage import MemoryStorage
from limits.strategies import FixedWindowRateLimiter
from starlette.requests import Request

from api.auth import endpoints, reset_limits, sessions
from api.auth.password import hash_token_fast, verify_password
from api.database import password_reset_tokens, user_sessions, users


@pytest.fixture(autouse=True)
def recovery_enabled(monkeypatch):
    monkeypatch.setattr(endpoints, "PASSWORD_RESET_ENABLED", True)
    limiter = FixedWindowRateLimiter(MemoryStorage())
    monkeypatch.setattr(reset_limits, "_limiter", lambda: limiter)


def request(ip="192.0.2.1"):
    return Request({"type": "http", "headers": [], "client": (ip, 1234)})


def body(token="random-token"):
    return endpoints.ResetPasswordRequest(token=token, new_password="A-new-password-1234")


@pytest.mark.asyncio
@pytest.mark.parametrize("scope,limit,count", [
    ("forgot-global", "100/hour", 100), ("forgot-ip", "5/hour", 5),
    ("forgot-account", "3/hour", 3), ("reset-global", "100/minute", 100),
    ("reset-ip", "20/minute", 20), ("reset-account", "5/hour", 5),
])
async def test_limits_enforced(scope, limit, count):
    for _ in range(count):
        await reset_limits.enforce_reset_limit(scope, "identity", limit)
    with pytest.raises(HTTPException) as exc:
        await reset_limits.enforce_reset_limit(scope, "identity", limit)
    assert exc.value.status_code == 429


@pytest.mark.asyncio
async def test_storage_failure_fails_closed(monkeypatch):
    monkeypatch.setattr(reset_limits, "_limiter", Mock(side_effect=RuntimeError("offline")))
    with pytest.raises(HTTPException) as exc:
        await reset_limits.enforce_reset_limit("forgot-global", "all", "100/hour")
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_unknown_account_is_cheap_and_ip_limited(monkeypatch):
    db = Mock(fetch_one=AsyncMock(return_value=None))
    monkeypatch.setattr(endpoints, "database", db)
    expensive_hash = Mock(side_effect=AssertionError("Unknown users must not invoke Argon2"))
    monkeypatch.setattr(endpoints, "hash_password", expensive_hash)
    for n in range(5):
        await endpoints.forgot_password(request(), endpoints.ForgotPasswordRequest(email=f"missing{n}@example.com"))
    with pytest.raises(HTTPException) as exc:
        await endpoints.forgot_password(request(), endpoints.ForgotPasswordRequest(email="last@example.com"))
    assert exc.value.status_code == 429
    assert db.fetch_one.await_count == 5
    expensive_hash.assert_not_called()


@pytest.mark.asyncio
async def test_account_limit_has_same_response_and_bounds_issuance(monkeypatch):
    db = Mock(fetch_one=AsyncMock(return_value={"id": "user", "password_hash": "present"}), execute=AsyncMock())
    monkeypatch.setattr(endpoints, "database", db)
    responses = [await endpoints.forgot_password(request(f"192.0.2.{i}"),
                 endpoints.ForgotPasswordRequest(email="known@example.com")) for i in range(6)]
    assert all(response == responses[0] for response in responses)
    assert db.execute.await_count == 3
    assert all(len(call.args[0].compile().params["token_hash"]) == 64 for call in db.execute.await_args_list)


@pytest.mark.asyncio
async def test_invalid_token_uses_one_indexed_query_without_hashing(monkeypatch):
    db = Mock(fetch_one=AsyncMock(return_value=None), transaction=Mock(return_value=AsyncMock()))
    monkeypatch.setattr(endpoints, "database", db)
    expensive_hash = Mock(side_effect=AssertionError("Invalid tokens must not invoke Argon2"))
    monkeypatch.setattr(endpoints, "hash_password", expensive_hash)
    with pytest.raises(HTTPException) as exc:
        await endpoints.reset_password(request(), body())
    assert exc.value.status_code == 400
    query = db.fetch_one.await_args.args[0].compile()
    assert "password_reset_tokens.token_hash =" in str(query)
    assert hash_token_fast("random-token") in query.params.values()
    assert db.fetch_one.await_count == 1
    expensive_hash.assert_not_called()


@pytest.fixture
async def recovery_db(test_database, monkeypatch):
    monkeypatch.setattr(endpoints, "database", test_database)
    monkeypatch.setattr(sessions, "database", test_database)
    now = datetime.now(timezone.utc)
    uid, tid, sid = (str(uuid.uuid4()) for _ in range(3))
    await test_database.execute(users.insert().values(
        id=uid, username=uid, email=f"{uid}@example.com", password_hash="original", role="viewer",
        status="active", email_verified=False, failed_login_attempts=0, created_at=now,
    ))
    await test_database.execute(password_reset_tokens.insert().values(
        id=tid, user_id=uid, token_hash=hash_token_fast("random-token"),
        created_at=now, expires_at=now+timedelta(hours=1),
    ))
    await test_database.execute(user_sessions.insert().values(
        id=sid, user_id=uid, token_hash=hash_token_fast(sid), refresh_generation=0,
        created_at=now, expires_at=now+timedelta(hours=1),
    ))
    return test_database, uid, tid, sid


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["expired", "used", "legacy"])
async def test_unusable_tokens_rejected(recovery_db, state):
    db, _, tid, _ = recovery_db
    now = datetime.now(timezone.utc)
    update = {"expired": {"expires_at": now-timedelta(seconds=1)}, "used": {"used_at": now},
              "legacy": {"token_hash": "$argon2id$legacy-link-intentionally-invalidated"}}[state]
    await db.execute(password_reset_tokens.update().where(password_reset_tokens.c.id == tid).values(**update))
    with pytest.raises(HTTPException) as exc:
        await endpoints.reset_password(request(), body())
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_concurrent_reuse_rejected_and_sessions_revoked(recovery_db):
    db, uid, tid, sid = recovery_db
    results = await asyncio.gather(endpoints.reset_password(request(), body()),
                                   endpoints.reset_password(request(), body()), return_exceptions=True)
    assert sum(isinstance(result, dict) for result in results) == 1
    errors = [result for result in results if isinstance(result, HTTPException)]
    assert len(errors) == 1 and errors[0].status_code == 400
    user = await db.fetch_one(users.select().where(users.c.id == uid))
    token = await db.fetch_one(password_reset_tokens.select().where(password_reset_tokens.c.id == tid))
    session = await db.fetch_one(user_sessions.select().where(user_sessions.c.id == sid))
    assert verify_password(body().new_password, user["password_hash"])
    assert token["used_at"] is not None and session["revoked_at"] is not None


@pytest.mark.asyncio
async def test_revocation_failure_rolls_back_password_and_token(recovery_db, monkeypatch):
    db, uid, tid, _ = recovery_db
    monkeypatch.setattr(endpoints, "invalidate_user_sessions", AsyncMock(side_effect=RuntimeError("failure")))
    with pytest.raises(RuntimeError):
        await endpoints.reset_password(request(), body())
    user = await db.fetch_one(users.select().where(users.c.id == uid))
    token = await db.fetch_one(password_reset_tokens.select().where(password_reset_tokens.c.id == tid))
    assert user["password_hash"] == "original"
    assert token["used_at"] is None


@pytest.mark.asyncio
async def test_global_issuance_limit_bounds_rotating_ips_and_accounts(monkeypatch):
    db = Mock(fetch_one=AsyncMock(return_value=None))
    monkeypatch.setattr(endpoints, "database", db)
    for i in range(100):
        await endpoints.forgot_password(request(f"192.0.2.{i}"),
                                        endpoints.ForgotPasswordRequest(email=f"u{i}@example.com"))
    with pytest.raises(HTTPException) as exc:
        await endpoints.forgot_password(request("198.51.100.1"),
                                        endpoints.ForgotPasswordRequest(email="last@example.com"))
    assert exc.value.status_code == 429
    assert db.fetch_one.await_count == 100


@pytest.mark.asyncio
@pytest.mark.parametrize("rotate_ips,count", [(False, 20), (True, 100)])
async def test_reset_request_limits_precede_database_work(monkeypatch, rotate_ips, count):
    db = Mock(fetch_one=AsyncMock(return_value=None), transaction=Mock(return_value=AsyncMock()))
    monkeypatch.setattr(endpoints, "database", db)
    for i in range(count):
        with pytest.raises(HTTPException) as exc:
            await endpoints.reset_password(request(f"192.0.2.{i}" if rotate_ips else "192.0.2.1"), body())
        assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        await endpoints.reset_password(request("198.51.100.1" if rotate_ips else "192.0.2.1"), body())
    assert exc.value.status_code == 429
    assert db.fetch_one.await_count == count
