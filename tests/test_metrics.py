"""Tests for Prometheus metrics functionality."""

import os

import pytest

os.environ["VLOG_TEST_MODE"] = "1"


class TestMetricsModule:
    """Tests for the metrics module."""

    def test_get_metrics_returns_bytes(self):
        """Test that get_metrics returns bytes in Prometheus format."""
        from api.metrics import get_metrics

        metrics = get_metrics()

        assert isinstance(metrics, bytes)
        assert len(metrics) > 0

    def test_get_metrics_contains_expected_metrics(self):
        """Test that generated metrics contain expected metric names."""
        from api.metrics import get_metrics

        metrics = get_metrics()

        # Check for key metric names in the output
        assert b"vlog_http_requests_total" in metrics
        assert b"vlog_transcoding_jobs_total" in metrics
        assert b"vlog_workers_total" in metrics
        assert b"vlog_videos_total" in metrics

    def test_init_app_info_sets_version(self):
        """Test that init_app_info sets the application info metric."""
        from api.metrics import get_metrics, init_app_info

        init_app_info(version="1.2.3")
        metrics = get_metrics()

        assert b"vlog_info" in metrics
        assert b'version="1.2.3"' in metrics

    def test_init_app_info_default_version(self):
        """Test that init_app_info uses default version when not specified."""
        from api.metrics import get_metrics, init_app_info

        init_app_info()
        metrics = get_metrics()

        assert b"vlog_info" in metrics
        assert b'version="0.1.0"' in metrics

    def test_metrics_valid_prometheus_format(self):
        """Test that metrics output is valid Prometheus exposition format."""
        from api.metrics import get_metrics

        metrics = get_metrics().decode("utf-8")

        # Prometheus format has lines starting with # for comments/HELP/TYPE
        # and metric lines in format: metric_name{labels} value
        lines = metrics.strip().split("\n")

        for line in lines:
            if not line:
                continue
            # Lines should either be comments (# ...) or metric data
            assert line.startswith("#") or " " in line or "{" in line, f"Invalid line: {line}"


class TestMetricDefinitions:
    """Tests for metric definitions."""

    def test_counter_metrics_exist(self):
        """Test that Counter metrics are properly defined."""
        from api.metrics import (
            HTTP_REQUESTS_TOTAL,
            TRANSCODING_JOBS_TOTAL,
            VIDEO_UPLOADS_TOTAL,
            WORKER_HEARTBEAT_TOTAL,
        )

        # Counters - prometheus-client adds _total suffix automatically in output
        # but internal _name doesn't include it
        assert "http_requests" in HTTP_REQUESTS_TOTAL._name
        assert "transcoding_jobs" in TRANSCODING_JOBS_TOTAL._name
        assert "video_uploads" in VIDEO_UPLOADS_TOTAL._name
        assert "worker_heartbeat" in WORKER_HEARTBEAT_TOTAL._name

    def test_gauge_metrics_exist(self):
        """Test that Gauge metrics are properly defined."""
        from api.metrics import (
            TRANSCODING_JOBS_ACTIVE,
            TRANSCODING_QUEUE_SIZE,
            VIDEOS_TOTAL,
            WORKERS_TOTAL,
        )

        assert VIDEOS_TOTAL._name == "vlog_videos_total"
        assert TRANSCODING_JOBS_ACTIVE._name == "vlog_transcoding_jobs_active"
        assert TRANSCODING_QUEUE_SIZE._name == "vlog_transcoding_queue_size"
        assert WORKERS_TOTAL._name == "vlog_workers_total"

    def test_histogram_metrics_exist(self):
        """Test that Histogram metrics are properly defined."""
        from api.metrics import (
            DB_QUERY_DURATION_SECONDS,
            HTTP_REQUEST_DURATION_SECONDS,
            TRANSCODING_JOB_DURATION_SECONDS,
        )

        assert HTTP_REQUEST_DURATION_SECONDS._name == "vlog_http_request_duration_seconds"
        assert TRANSCODING_JOB_DURATION_SECONDS._name == "vlog_transcoding_job_duration_seconds"
        assert DB_QUERY_DURATION_SECONDS._name == "vlog_db_query_duration_seconds"


class TestAuditLogRotationConfig:
    """Tests for audit log rotation configuration."""

    def test_audit_log_max_bytes_default(self):
        """Test that audit log max bytes has correct default (10MB)."""
        from config import AUDIT_LOG_MAX_BYTES

        assert AUDIT_LOG_MAX_BYTES == 10 * 1024 * 1024  # 10MB

    def test_audit_log_backup_count_default(self):
        """Test that audit log backup count has correct default (5)."""
        from config import AUDIT_LOG_BACKUP_COUNT

        assert AUDIT_LOG_BACKUP_COUNT == 5

    def test_audit_log_max_bytes_is_integer(self):
        """Test that audit log max bytes is an integer."""
        from config import AUDIT_LOG_MAX_BYTES

        assert isinstance(AUDIT_LOG_MAX_BYTES, int)
        assert AUDIT_LOG_MAX_BYTES >= 1024  # Minimum validation

    def test_audit_log_backup_count_is_integer(self):
        """Test that audit log backup count is an integer."""
        from config import AUDIT_LOG_BACKUP_COUNT

        assert isinstance(AUDIT_LOG_BACKUP_COUNT, int)
        assert AUDIT_LOG_BACKUP_COUNT >= 0  # Minimum validation


class TestMetricsEndpointAuth:
    """Tests for metrics endpoint authentication (Issue #436)."""

    def test_metrics_settings_defined(self):
        """Test that metrics settings are defined in KNOWN_SETTINGS."""
        from api.settings_service import KNOWN_SETTINGS

        setting_keys = [s[0] for s in KNOWN_SETTINGS]
        assert "metrics.enabled" in setting_keys
        assert "metrics.auth_required" in setting_keys

    def test_metrics_settings_env_mappings(self):
        """Test that metrics settings have environment variable mappings."""
        from api.settings_service import SETTING_TO_ENV_MAP

        assert "metrics.enabled" in SETTING_TO_ENV_MAP
        assert SETTING_TO_ENV_MAP["metrics.enabled"] == "VLOG_METRICS_ENABLED"
        assert "metrics.auth_required" in SETTING_TO_ENV_MAP
        assert SETTING_TO_ENV_MAP["metrics.auth_required"] == "VLOG_METRICS_AUTH_REQUIRED"

    def test_metrics_enabled_setting_is_boolean(self):
        """Test that metrics.enabled setting is defined as boolean type."""
        from api.settings_service import KNOWN_SETTINGS

        for setting in KNOWN_SETTINGS:
            if setting[0] == "metrics.enabled":
                assert setting[2] == "boolean"
                assert setting[1] == "metrics"  # category
                break
        else:
            pytest.fail("metrics.enabled setting not found")

    def test_metrics_auth_required_setting_is_boolean(self):
        """Test that metrics.auth_required setting is defined as boolean type."""
        from api.settings_service import KNOWN_SETTINGS

        for setting in KNOWN_SETTINGS:
            if setting[0] == "metrics.auth_required":
                assert setting[2] == "boolean"
                assert setting[1] == "metrics"  # category
                break
        else:
            pytest.fail("metrics.auth_required setting not found")


class TestMetricsEndpointIntegration:
    """Integration tests for metrics endpoint authentication (Issue #436).

    Uses the admin_client fixture from conftest.py which properly sets up
    test database and storage paths.
    """

    def test_metrics_endpoint_returns_200_when_enabled_no_auth(self, admin_client, monkeypatch):
        """Metrics endpoint should return 200 when enabled and no auth required (default)."""
        from unittest.mock import AsyncMock

        # Mock settings to return defaults
        mock_get = AsyncMock(side_effect=lambda k, d=None: {"metrics.enabled": True, "metrics.auth_required": False}.get(k, d))
        monkeypatch.setattr("api.admin.get_db_setting", mock_get)

        response = admin_client.get("/metrics")
        assert response.status_code == 200
        assert b"vlog_" in response.content  # Check for Prometheus metrics

    def test_metrics_endpoint_returns_404_when_disabled(self, admin_client, monkeypatch):
        """Metrics endpoint should return 404 when metrics.enabled=false."""
        from unittest.mock import AsyncMock

        # Mock settings to disable metrics
        mock_get = AsyncMock(side_effect=lambda k, d=None: {"metrics.enabled": False, "metrics.auth_required": False}.get(k, d))
        monkeypatch.setattr("api.admin.get_db_setting", mock_get)

        response = admin_client.get("/metrics")
        assert response.status_code == 404
        assert "disabled" in response.json()["detail"].lower()

    def test_metrics_endpoint_returns_403_when_auth_required_no_header(self, admin_client, monkeypatch):
        """Metrics endpoint should return 403 when auth required but no header provided."""
        from unittest.mock import AsyncMock

        import api.admin

        # Set admin secret so auth can work (patch at module level where it's imported)
        monkeypatch.setattr(api.admin, "ADMIN_API_SECRET", "test-secret-123")

        # Mock settings to require auth
        mock_get = AsyncMock(side_effect=lambda k, d=None: {"metrics.enabled": True, "metrics.auth_required": True}.get(k, d))
        monkeypatch.setattr("api.admin.get_db_setting", mock_get)

        response = admin_client.get("/metrics")
        assert response.status_code == 403
        assert "Authentication required" in response.json()["detail"]

    def test_metrics_endpoint_returns_403_when_auth_required_wrong_secret(self, admin_client, monkeypatch):
        """Metrics endpoint should return 403 when auth required but wrong secret provided."""
        from unittest.mock import AsyncMock

        import api.admin

        # Set admin secret (patch at module level where it's imported)
        monkeypatch.setattr(api.admin, "ADMIN_API_SECRET", "correct-secret-123")

        # Mock settings to require auth
        mock_get = AsyncMock(side_effect=lambda k, d=None: {"metrics.enabled": True, "metrics.auth_required": True}.get(k, d))
        monkeypatch.setattr("api.admin.get_db_setting", mock_get)

        response = admin_client.get("/metrics", headers={"X-Admin-Secret": "wrong-secret"})
        assert response.status_code == 403
        assert "Authentication required" in response.json()["detail"]

    def test_metrics_endpoint_returns_200_when_auth_required_correct_secret(self, admin_client, monkeypatch):
        """Metrics endpoint should return 200 when auth required and correct secret provided."""
        from unittest.mock import AsyncMock

        import api.admin

        test_secret = "correct-secret-123"
        # Patch at module level where it's imported
        monkeypatch.setattr(api.admin, "ADMIN_API_SECRET", test_secret)

        # Mock settings to require auth
        mock_get = AsyncMock(side_effect=lambda k, d=None: {"metrics.enabled": True, "metrics.auth_required": True}.get(k, d))
        monkeypatch.setattr("api.admin.get_db_setting", mock_get)

        response = admin_client.get("/metrics", headers={"X-Admin-Secret": test_secret})
        assert response.status_code == 200
        assert b"vlog_" in response.content

    def test_metrics_endpoint_returns_500_when_auth_required_no_secret_configured(self, admin_client, monkeypatch):
        """Metrics endpoint should return 500 when auth required but ADMIN_API_SECRET not set."""
        from unittest.mock import AsyncMock

        import api.admin

        # Ensure no secret is configured (patch at module level where it's imported)
        monkeypatch.setattr(api.admin, "ADMIN_API_SECRET", None)

        # Mock settings to require auth
        mock_get = AsyncMock(side_effect=lambda k, d=None: {"metrics.enabled": True, "metrics.auth_required": True}.get(k, d))
        monkeypatch.setattr("api.admin.get_db_setting", mock_get)

        response = admin_client.get("/metrics")
        assert response.status_code == 500
        assert "misconfigured" in response.json()["detail"].lower()
