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


class TestIssue207NewMetrics:
    """Tests for Issue #207 - Additional Prometheus Metrics."""

    def test_http_requests_in_progress_exists(self):
        """Test that HTTP_REQUESTS_IN_PROGRESS gauge is defined."""
        from api.metrics import HTTP_REQUESTS_IN_PROGRESS

        assert HTTP_REQUESTS_IN_PROGRESS._name == "vlog_http_requests_in_progress"
        # Check it has the 'api' label
        assert "api" in HTTP_REQUESTS_IN_PROGRESS._labelnames

    def test_videos_watch_time_seconds_exists(self):
        """Test that VIDEOS_WATCH_TIME_SECONDS_TOTAL counter is defined."""
        from api.metrics import VIDEOS_WATCH_TIME_SECONDS_TOTAL

        assert "watch_time" in VIDEOS_WATCH_TIME_SECONDS_TOTAL._name

    def test_worker_jobs_completed_exists(self):
        """Test that WORKER_JOBS_COMPLETED_TOTAL counter is defined."""
        from api.metrics import WORKER_JOBS_COMPLETED_TOTAL

        assert "worker_jobs_completed" in WORKER_JOBS_COMPLETED_TOTAL._name
        # Check it has the 'worker_name' label (not worker_id for low cardinality)
        assert "worker_name" in WORKER_JOBS_COMPLETED_TOTAL._labelnames

    def test_worker_heartbeat_age_exists(self):
        """Test that WORKER_HEARTBEAT_AGE_SECONDS gauge is defined."""
        from api.metrics import WORKER_HEARTBEAT_AGE_SECONDS

        assert "heartbeat_age" in WORKER_HEARTBEAT_AGE_SECONDS._name
        # Check it has the 'worker_name' label (not worker_id for low cardinality)
        assert "worker_name" in WORKER_HEARTBEAT_AGE_SECONDS._labelnames

    def test_storage_videos_bytes_exists(self):
        """Test that STORAGE_VIDEOS_BYTES gauge is defined."""
        from api.metrics import STORAGE_VIDEOS_BYTES

        assert STORAGE_VIDEOS_BYTES._name == "vlog_storage_videos_bytes"


class TestNormalizeEndpoint:
    """Tests for the normalize_endpoint helper function (Issue #207)."""

    def test_normalize_static_path(self):
        """Test that static paths are not modified."""
        from api.metrics import normalize_endpoint

        assert normalize_endpoint("/api/health") == "/api/health"
        assert normalize_endpoint("/metrics") == "/metrics"
        assert normalize_endpoint("/") == "/"

    def test_normalize_video_slug(self):
        """Test that video slugs are normalized to {id}."""
        from api.metrics import normalize_endpoint

        assert normalize_endpoint("/api/videos/my-cool-video") == "/api/videos/{id}"
        assert normalize_endpoint("/api/videos/video-123") == "/api/videos/{id}"

    def test_normalize_worker_job_id(self):
        """Test that worker job IDs are normalized."""
        from api.metrics import normalize_endpoint

        assert normalize_endpoint("/api/worker/123/progress") == "/api/worker/{id}/progress"
        assert normalize_endpoint("/api/worker/456/complete") == "/api/worker/{id}/complete"

    def test_normalize_numeric_ids(self):
        """Test that numeric segments are normalized to {id}."""
        from api.metrics import normalize_endpoint

        assert normalize_endpoint("/api/jobs/123") == "/api/jobs/{id}"
        assert normalize_endpoint("/api/workers/456") == "/api/workers/{id}"

    def test_normalize_empty_path(self):
        """Test that empty path returns root."""
        from api.metrics import normalize_endpoint

        assert normalize_endpoint("") == "/"

    def test_normalize_preserves_query_params(self):
        """Test that paths without dynamic segments are preserved."""
        from api.metrics import normalize_endpoint

        assert normalize_endpoint("/api/videos") == "/api/videos"
        assert normalize_endpoint("/api/workers") == "/api/workers"

    def test_normalize_uuid_patterns(self):
        """Test that UUID patterns are normalized to {id}."""
        from api.metrics import normalize_endpoint

        # Standard UUID format
        assert normalize_endpoint("/api/users/550e8400-e29b-41d4-a716-446655440000") == "/api/users/{id}"
        # Lowercase UUID
        assert normalize_endpoint("/api/users/a1b2c3d4-e5f6-7890-abcd-ef1234567890") == "/api/users/{id}"

    def test_normalize_long_slug_patterns(self):
        """Test that long slug patterns are normalized to {id}."""
        from api.metrics import normalize_endpoint

        # Long slugs with multiple hyphens (likely dynamic content)
        assert normalize_endpoint("/api/docs/driving-impressions-of-the-2017-bmw-m4") == "/api/docs/{id}"

    def test_normalize_caches_results(self):
        """Test that normalize_endpoint uses LRU cache for performance."""
        from api.metrics import normalize_endpoint

        # Call the same path multiple times
        path = "/api/videos/test-slug"
        result1 = normalize_endpoint(path)
        result2 = normalize_endpoint(path)

        # Results should be identical (cached)
        assert result1 == result2

        # Check cache info shows hits
        cache_info = normalize_endpoint.cache_info()
        assert cache_info.hits >= 1


class TestSanitizeLabel:
    """Tests for the sanitize_label helper function (Issue #207 review feedback)."""

    def test_sanitize_label_basic(self):
        """Test that normal labels pass through."""
        from api.metrics import sanitize_label

        assert sanitize_label("worker-1") == "worker-1"
        assert sanitize_label("my_worker") == "my_worker"
        assert sanitize_label("Worker123") == "Worker123"

    def test_sanitize_label_special_chars(self):
        """Test that special characters are replaced with underscores."""
        from api.metrics import sanitize_label

        assert sanitize_label("worker\nname") == "worker_name"
        assert sanitize_label("worker;name") == "worker_name"
        assert sanitize_label("worker@name") == "worker_name"
        assert sanitize_label("worker name") == "worker_name"

    def test_sanitize_label_truncation(self):
        """Test that labels are truncated to max length."""
        from api.metrics import sanitize_label

        long_label = "a" * 100
        result = sanitize_label(long_label)
        assert len(result) == 50  # Default max_len

        result_custom = sanitize_label(long_label, max_len=20)
        assert len(result_custom) == 20

    def test_sanitize_label_empty(self):
        """Test that empty labels return 'unknown'."""
        from api.metrics import sanitize_label

        assert sanitize_label("") == "unknown"
        assert sanitize_label(None) == "unknown"


class TestBackgroundTaskMetrics:
    """Tests for background task health metrics (Issue #207 review feedback)."""

    def test_background_task_metrics_exist(self):
        """Test that background task health metrics are defined."""
        from api.metrics import (
            BACKGROUND_TASK_DURATION_SECONDS,
            BACKGROUND_TASK_ERRORS_TOTAL,
            BACKGROUND_TASK_LAST_SUCCESS,
            STORAGE_RECONCILIATION_STATUS,
        )

        # Counter names don't include _total suffix in _name (Prometheus adds it automatically in output)
        assert "background_task_errors" in BACKGROUND_TASK_ERRORS_TOTAL._name
        assert BACKGROUND_TASK_LAST_SUCCESS._name == "vlog_background_task_last_success_timestamp_seconds"
        assert BACKGROUND_TASK_DURATION_SECONDS._name == "vlog_background_task_duration_seconds"
        assert STORAGE_RECONCILIATION_STATUS._name == "vlog_storage_reconciliation_status"

    def test_background_task_metrics_have_correct_labels(self):
        """Test that background task metrics have the task_name label."""
        from api.metrics import (
            BACKGROUND_TASK_DURATION_SECONDS,
            BACKGROUND_TASK_ERRORS_TOTAL,
            BACKGROUND_TASK_LAST_SUCCESS,
        )

        assert "task_name" in BACKGROUND_TASK_ERRORS_TOTAL._labelnames
        assert "task_name" in BACKGROUND_TASK_LAST_SUCCESS._labelnames
        assert "task_name" in BACKGROUND_TASK_DURATION_SECONDS._labelnames


class TestStorageReconciliationConfig:
    """Tests for configurable storage reconciliation (Issue #207 review feedback)."""

    def test_reconciliation_config_defaults(self):
        """Test that storage reconciliation has sensible defaults."""
        from api.metrics import (
            STORAGE_RECONCILIATION_INTERVAL_SECONDS,
            STORAGE_SCAN_MAX_FILES,
            STORAGE_SCAN_TIMEOUT_SECONDS,
        )

        # Default 6 hours
        assert STORAGE_RECONCILIATION_INTERVAL_SECONDS == 6 * 60 * 60
        # Default 5 million files
        assert STORAGE_SCAN_MAX_FILES == 5_000_000
        # Default 30 minutes timeout
        assert STORAGE_SCAN_TIMEOUT_SECONDS == 1800


class TestHTTPMetricsMiddleware:
    """Tests for the HTTPMetricsMiddleware (Issue #207)."""

    def test_middleware_exists(self):
        """Test that HTTPMetricsMiddleware class exists."""
        from api.common import HTTPMetricsMiddleware

        assert HTTPMetricsMiddleware is not None

    def test_middleware_init(self):
        """Test that middleware can be initialized."""
        from api.common import HTTPMetricsMiddleware

        async def dummy_app(scope, receive, send):
            pass

        middleware = HTTPMetricsMiddleware(dummy_app, api_name="test")
        assert middleware.api_name == "test"
        assert middleware.app == dummy_app

    @pytest.mark.asyncio
    async def test_middleware_records_metrics_on_exception(self):
        """Test that middleware records metrics even when the app raises an exception."""
        from api.common import HTTPMetricsMiddleware
        from api.metrics import HTTP_REQUESTS_IN_PROGRESS

        # Track initial gauge value
        initial_value = HTTP_REQUESTS_IN_PROGRESS.labels(api="test_exception")._value.get()

        async def failing_app(scope, receive, send):
            # Increment gauge happens before this
            raise ValueError("Test exception")

        middleware = HTTPMetricsMiddleware(failing_app, api_name="test_exception")

        scope = {"type": "http", "method": "GET", "path": "/test"}

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            pass

        # The middleware should record metrics even on exception
        with pytest.raises(ValueError, match="Test exception"):
            await middleware(scope, receive, send)

        # Gauge should be back to initial value (decremented in finally block)
        final_value = HTTP_REQUESTS_IN_PROGRESS.labels(api="test_exception")._value.get()
        assert final_value == initial_value


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
