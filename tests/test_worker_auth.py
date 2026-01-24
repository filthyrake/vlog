"""
Unit tests for worker authentication helper functions.

These tests focus on:
- _get_request_context(): Request context extraction for security logging
- _check_key_expiration_with_grace(): Key expiration status with grace period (Issue #226)

Integration tests for verify_worker_key are in test_worker_api.py.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from api.worker_auth import (
    KeyExpirationStatus,
    _check_key_expiration_with_grace,
    _get_request_context,
)


@pytest.fixture
def mock_request():
    """Create a mock Request object with standard attributes."""
    req = Mock()
    req.client = Mock()
    req.client.host = "127.0.0.1"
    req.headers = {}
    return req


class TestGetRequestContext:
    """Tests for _get_request_context() function."""

    def test_none_request_returns_unknown(self):
        """When request is None, return unknown for all fields."""
        ctx = _get_request_context(None)
        assert ctx["ip_address"] == "unknown"
        assert ctx["direct_ip"] == "unknown"
        assert ctx["forwarded_for"] is None
        assert ctx["user_agent"] == "unknown"

    def test_direct_ip_without_forwarded_header(self, mock_request):
        """Direct IP is used when no X-Forwarded-For header present."""
        ctx = _get_request_context(mock_request)
        assert ctx["ip_address"] == "127.0.0.1"
        assert ctx["direct_ip"] == "127.0.0.1"
        assert ctx["forwarded_for"] is None

    def test_forwarded_for_trusted_proxy(self, mock_request, monkeypatch):
        """X-Forwarded-For is trusted when request comes from trusted proxy."""
        monkeypatch.setattr("api.worker_auth.TRUSTED_PROXIES", {"127.0.0.1"})
        mock_request.headers["x-forwarded-for"] = "10.0.0.1"

        ctx = _get_request_context(mock_request)

        assert ctx["ip_address"] == "10.0.0.1"
        assert ctx["direct_ip"] == "127.0.0.1"
        assert ctx["forwarded_for"] == "10.0.0.1"

    def test_forwarded_for_untrusted_proxy(self, mock_request, monkeypatch):
        """X-Forwarded-For is ignored when request comes from untrusted proxy."""
        monkeypatch.setattr("api.worker_auth.TRUSTED_PROXIES", set())
        mock_request.headers["x-forwarded-for"] = "10.0.0.1"

        ctx = _get_request_context(mock_request)

        # Direct IP is used instead of forwarded-for
        assert ctx["ip_address"] == "127.0.0.1"
        assert ctx["direct_ip"] == "127.0.0.1"
        # Forwarded-for is still captured for logging purposes
        assert ctx["forwarded_for"] == "10.0.0.1"

    def test_multiple_forwarded_ips_uses_first(self, mock_request, monkeypatch):
        """When multiple IPs in X-Forwarded-For, first one is used."""
        monkeypatch.setattr("api.worker_auth.TRUSTED_PROXIES", {"127.0.0.1"})
        mock_request.headers["x-forwarded-for"] = "10.0.0.1, 10.0.0.2, 10.0.0.3"

        ctx = _get_request_context(mock_request)

        assert ctx["ip_address"] == "10.0.0.1"
        assert ctx["forwarded_for"] == "10.0.0.1"

    def test_ipv6_address_in_forwarded_for(self, mock_request, monkeypatch):
        """IPv6 addresses in X-Forwarded-For are handled correctly."""
        monkeypatch.setattr("api.worker_auth.TRUSTED_PROXIES", {"127.0.0.1"})
        mock_request.headers["x-forwarded-for"] = "2001:db8::1"

        ctx = _get_request_context(mock_request)

        assert ctx["ip_address"] == "2001:db8::1"
        assert ctx["forwarded_for"] == "2001:db8::1"

    def test_user_agent_captured(self, mock_request):
        """User-Agent header is captured in context."""
        mock_request.headers["user-agent"] = "vlog-worker/1.0"

        ctx = _get_request_context(mock_request)

        assert ctx["user_agent"] == "vlog-worker/1.0"

    def test_missing_user_agent_defaults_to_unknown(self, mock_request):
        """Missing User-Agent defaults to 'unknown'."""
        ctx = _get_request_context(mock_request)
        assert ctx["user_agent"] == "unknown"

    def test_missing_client_defaults_to_unknown(self):
        """When request.client is None, direct_ip is 'unknown'."""
        mock_request = Mock()
        mock_request.client = None
        mock_request.headers = {}

        ctx = _get_request_context(mock_request)

        assert ctx["direct_ip"] == "unknown"
        assert ctx["ip_address"] == "unknown"


class TestCheckKeyExpirationWithGrace:
    """Tests for _check_key_expiration_with_grace() function (Issue #226)."""

    @pytest.mark.asyncio
    async def test_none_expires_at_is_valid(self):
        """Key with no expiration (None) should be VALID."""
        status, grace_ends = await _check_key_expiration_with_grace(
            expires_at=None,
            grace_period_hours=4,
            warning_days=14,
        )
        assert status == KeyExpirationStatus.VALID
        assert grace_ends is None

    @pytest.mark.asyncio
    async def test_future_expiration_is_valid(self):
        """Key expiring far in the future should be VALID."""
        future = datetime.now(timezone.utc) + timedelta(days=60)
        status, grace_ends = await _check_key_expiration_with_grace(
            expires_at=future,
            grace_period_hours=4,
            warning_days=14,
        )
        assert status == KeyExpirationStatus.VALID
        assert grace_ends is None

    @pytest.mark.asyncio
    async def test_expiring_soon_within_warning_days(self):
        """Key expiring within warning period should be EXPIRING_SOON."""
        # Expires in 7 days, warning period is 14 days
        future = datetime.now(timezone.utc) + timedelta(days=7)
        status, grace_ends = await _check_key_expiration_with_grace(
            expires_at=future,
            grace_period_hours=4,
            warning_days=14,
        )
        assert status == KeyExpirationStatus.EXPIRING_SOON
        assert grace_ends is None

    @pytest.mark.asyncio
    async def test_expired_within_grace_period(self):
        """Key expired but within grace period should be IN_GRACE_PERIOD."""
        # Expired 2 hours ago, grace period is 4 hours
        past = datetime.now(timezone.utc) - timedelta(hours=2)
        status, grace_ends = await _check_key_expiration_with_grace(
            expires_at=past,
            grace_period_hours=4,
            warning_days=14,
        )
        assert status == KeyExpirationStatus.IN_GRACE_PERIOD
        assert grace_ends is not None
        # Grace ends 4 hours after expiration
        expected_grace_end = past + timedelta(hours=4)
        assert abs((grace_ends - expected_grace_end).total_seconds()) < 1

    @pytest.mark.asyncio
    async def test_expired_past_grace_period(self):
        """Key expired past grace period should be EXPIRED."""
        # Expired 10 hours ago, grace period is 4 hours
        past = datetime.now(timezone.utc) - timedelta(hours=10)
        status, grace_ends = await _check_key_expiration_with_grace(
            expires_at=past,
            grace_period_hours=4,
            warning_days=14,
        )
        assert status == KeyExpirationStatus.EXPIRED
        assert grace_ends is None

    @pytest.mark.asyncio
    async def test_exactly_at_expiration_is_in_grace(self):
        """Key at exact expiration time should be IN_GRACE_PERIOD."""
        # Just expired (1 second ago)
        just_expired = datetime.now(timezone.utc) - timedelta(seconds=1)
        status, grace_ends = await _check_key_expiration_with_grace(
            expires_at=just_expired,
            grace_period_hours=4,
            warning_days=14,
        )
        assert status == KeyExpirationStatus.IN_GRACE_PERIOD
        assert grace_ends is not None

    @pytest.mark.asyncio
    async def test_exactly_at_grace_end_is_expired(self):
        """Key at exact grace period end should be EXPIRED."""
        # Expired exactly 4 hours and 1 second ago
        grace_just_ended = datetime.now(timezone.utc) - timedelta(hours=4, seconds=1)
        status, grace_ends = await _check_key_expiration_with_grace(
            expires_at=grace_just_ended,
            grace_period_hours=4,
            warning_days=14,
        )
        assert status == KeyExpirationStatus.EXPIRED
        assert grace_ends is None

    @pytest.mark.asyncio
    async def test_zero_grace_period(self):
        """Key with 0-hour grace period expires immediately."""
        # Expired 1 second ago with no grace
        just_expired = datetime.now(timezone.utc) - timedelta(seconds=1)
        status, grace_ends = await _check_key_expiration_with_grace(
            expires_at=just_expired,
            grace_period_hours=0,
            warning_days=14,
        )
        assert status == KeyExpirationStatus.EXPIRED
        assert grace_ends is None

    @pytest.mark.asyncio
    async def test_zero_warning_days(self):
        """Key with 0 warning days never shows EXPIRING_SOON (unless days_until=0)."""
        # Expires in 2 days - use 2 days to avoid timing edge case where
        # timedelta(days=1).days can round to 0 due to microsecond differences
        future = datetime.now(timezone.utc) + timedelta(days=2)
        status, grace_ends = await _check_key_expiration_with_grace(
            expires_at=future,
            grace_period_hours=4,
            warning_days=0,
        )
        # With 0 warning days, key is VALID until it's on the day of expiration
        assert status == KeyExpirationStatus.VALID
        assert grace_ends is None

    @pytest.mark.asyncio
    async def test_naive_datetime_handled(self):
        """Naive datetime should be converted to UTC."""
        # Create naive datetime (no timezone)
        naive_future = datetime.now() + timedelta(days=60)
        # Should not raise, should treat as UTC
        status, grace_ends = await _check_key_expiration_with_grace(
            expires_at=naive_future,
            grace_period_hours=4,
            warning_days=14,
        )
        assert status == KeyExpirationStatus.VALID
