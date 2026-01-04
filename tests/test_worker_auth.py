"""
Unit tests for worker authentication helper functions.

These tests focus on the _get_request_context() function which handles
request context extraction for security logging, including trusted proxy
handling and X-Forwarded-For header processing.

Integration tests for verify_worker_key are in test_worker_api.py.
"""

from unittest.mock import Mock

import pytest

from api.worker_auth import _get_request_context


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
