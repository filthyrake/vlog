"""Tests for the worker alerting system."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from worker.alerts import (
    AlertMetrics,
    AlertType,
    alert_job_failed,
    alert_max_retries_exceeded,
    alert_stale_job_recovered,
    alert_worker_shutdown,
    alert_worker_startup,
    get_metrics,
    reset_metrics,
    send_alert_fire_and_forget,
    send_webhook_alert,
)


@pytest.fixture(autouse=True)
def reset_alert_metrics():
    """Reset metrics before each test."""
    reset_metrics()
    yield
    reset_metrics()


class TestAlertMetrics:
    """Tests for AlertMetrics class."""

    def test_initial_state(self):
        """Test initial counter values."""
        metrics = AlertMetrics()
        assert metrics.stale_jobs_recovered == 0
        assert metrics.jobs_max_retries_exceeded == 0
        assert metrics.jobs_failed == 0
        assert metrics.alerts_sent == 0
        assert metrics.alerts_rate_limited == 0
        assert metrics.alerts_failed == 0

    def test_increment_stale_recovered(self):
        """Test incrementing stale jobs recovered counter."""
        metrics = AlertMetrics()
        result = metrics.increment_stale_recovered()
        assert result == 1
        assert metrics.stale_jobs_recovered == 1

        result = metrics.increment_stale_recovered()
        assert result == 2

    def test_increment_max_retries(self):
        """Test incrementing max retries counter."""
        metrics = AlertMetrics()
        result = metrics.increment_max_retries()
        assert result == 1
        assert metrics.jobs_max_retries_exceeded == 1

    def test_increment_failed_with_video_id(self):
        """Test incrementing failed counter tracks per-video failures."""
        metrics = AlertMetrics()

        metrics.increment_failed(video_id=123)
        assert metrics.jobs_failed == 1
        assert metrics.get_video_failure_count(123) == 1

        metrics.increment_failed(video_id=123)
        assert metrics.jobs_failed == 2
        assert metrics.get_video_failure_count(123) == 2

        metrics.increment_failed(video_id=456)
        assert metrics.jobs_failed == 3
        assert metrics.get_video_failure_count(456) == 1
        assert metrics.get_video_failure_count(123) == 2

    def test_increment_failed_without_video_id(self):
        """Test incrementing failed counter without video ID."""
        metrics = AlertMetrics()
        metrics.increment_failed()
        assert metrics.jobs_failed == 1
        assert metrics.get_video_failure_count(123) == 0

    def test_can_send_alert_first_time(self):
        """Test that first alert of a type can be sent."""
        metrics = AlertMetrics()
        assert metrics.can_send_alert("test_type") is True

    def test_can_send_alert_rate_limited(self):
        """Test that rapid alerts are rate limited."""
        metrics = AlertMetrics()
        metrics.record_alert_sent("test_type")
        # Immediately after sending, should be rate limited
        assert metrics.can_send_alert("test_type") is False

    def test_to_dict(self):
        """Test metrics serialization."""
        metrics = AlertMetrics()
        metrics.increment_stale_recovered()
        metrics.increment_max_retries()
        metrics.increment_failed(video_id=1)
        metrics.increment_failed(video_id=2)

        result = metrics.to_dict()

        assert result["stale_jobs_recovered"] == 1
        assert result["jobs_max_retries_exceeded"] == 1
        assert result["jobs_failed"] == 2
        assert result["videos_with_failures"] == 2


class TestSendWebhookAlert:
    """Tests for send_webhook_alert function."""

    @pytest.mark.asyncio
    async def test_no_webhook_url_configured(self):
        """Test that no alert is sent when webhook URL is not configured."""
        with patch("worker.alerts.ALERT_WEBHOOK_URL", ""):
            result = await send_webhook_alert(
                AlertType.JOB_STALE_RECOVERED,
                {"test": "data"},
            )
            assert result is False

    @pytest.mark.asyncio
    async def test_successful_webhook_call(self):
        """Test successful webhook call."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("worker.alerts.ALERT_WEBHOOK_URL", "https://example.com/webhook"):
            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.post = AsyncMock(return_value=mock_response)
                mock_client.return_value.__aenter__.return_value = mock_instance

                result = await send_webhook_alert(
                    AlertType.JOB_STALE_RECOVERED,
                    {"video_id": 123},
                    force=True,
                )

                assert result is True
                mock_instance.post.assert_called_once()
                call_args = mock_instance.post.call_args
                assert call_args[0][0] == "https://example.com/webhook"
                payload = call_args[1]["json"]
                assert payload["event"] == "job_stale_recovered"
                assert payload["details"]["video_id"] == 123
                assert "timestamp" in payload
                assert "metrics" in payload

    @pytest.mark.asyncio
    async def test_rate_limiting(self):
        """Test that alerts are rate limited."""
        metrics = get_metrics()
        metrics.record_alert_sent(AlertType.JOB_FAILED.value)

        with patch("worker.alerts.ALERT_WEBHOOK_URL", "https://example.com/webhook"):
            result = await send_webhook_alert(
                AlertType.JOB_FAILED,
                {"test": "data"},
            )
            assert result is False
            assert metrics.alerts_rate_limited == 1

    @pytest.mark.asyncio
    async def test_force_bypasses_rate_limiting(self):
        """Test that force=True bypasses rate limiting."""
        metrics = get_metrics()
        metrics.record_alert_sent(AlertType.JOB_MAX_RETRIES_EXCEEDED.value)

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("worker.alerts.ALERT_WEBHOOK_URL", "https://example.com/webhook"):
            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.post = AsyncMock(return_value=mock_response)
                mock_client.return_value.__aenter__.return_value = mock_instance

                result = await send_webhook_alert(
                    AlertType.JOB_MAX_RETRIES_EXCEEDED,
                    {"test": "data"},
                    force=True,
                )
                assert result is True

    @pytest.mark.asyncio
    async def test_timeout_error(self):
        """Test handling of timeout errors."""
        with patch("worker.alerts.ALERT_WEBHOOK_URL", "https://example.com/webhook"):
            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_instance.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
                mock_client.return_value.__aenter__.return_value = mock_instance

                result = await send_webhook_alert(
                    AlertType.JOB_STALE_RECOVERED,
                    {"test": "data"},
                    force=True,
                )

                assert result is False
                assert get_metrics().alerts_failed == 1

    @pytest.mark.asyncio
    async def test_http_error(self):
        """Test handling of HTTP errors."""
        with patch("worker.alerts.ALERT_WEBHOOK_URL", "https://example.com/webhook"):
            with patch("httpx.AsyncClient") as mock_client:
                mock_instance = AsyncMock()
                mock_response = MagicMock()
                mock_response.status_code = 500
                error = httpx.HTTPStatusError("error", request=MagicMock(), response=mock_response)
                mock_instance.post = AsyncMock(side_effect=error)
                mock_client.return_value.__aenter__.return_value = mock_instance

                result = await send_webhook_alert(
                    AlertType.JOB_STALE_RECOVERED,
                    {"test": "data"},
                    force=True,
                )

                assert result is False
                assert get_metrics().alerts_failed == 1


class TestAlertHelpers:
    """Tests for alert helper functions."""

    @pytest.mark.asyncio
    async def test_alert_stale_job_recovered(self):
        """Test alert_stale_job_recovered function."""
        with patch("worker.alerts.send_webhook_alert", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True

            await alert_stale_job_recovered(
                video_id=123,
                video_slug="test-video",
                attempt_number=2,
                worker_id="worker-123",
            )

            assert get_metrics().stale_jobs_recovered == 1
            mock_send.assert_called_once()
            call_args = mock_send.call_args
            assert call_args[0][0] == AlertType.JOB_STALE_RECOVERED
            details = call_args[0][1]
            assert details["video_id"] == 123
            assert details["video_slug"] == "test-video"
            assert details["attempt_number"] == 2
            assert details["next_attempt"] == 3
            assert details["previous_worker_id"] == "worker-123"

    @pytest.mark.asyncio
    async def test_alert_max_retries_exceeded(self):
        """Test alert_max_retries_exceeded function."""
        with patch("worker.alerts.send_webhook_alert", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True

            await alert_max_retries_exceeded(
                video_id=456,
                video_slug="failed-video",
                max_attempts=3,
                last_error="Some error message",
            )

            assert get_metrics().jobs_max_retries_exceeded == 1
            mock_send.assert_called_once()
            call_args = mock_send.call_args
            assert call_args[0][0] == AlertType.JOB_MAX_RETRIES_EXCEEDED
            details = call_args[0][1]
            assert details["video_id"] == 456
            assert details["video_slug"] == "failed-video"
            assert details["max_attempts"] == 3
            assert details["last_error"] == "Some error message"
            # Should always force send
            assert call_args[1]["force"] is True

    @pytest.mark.asyncio
    async def test_alert_job_failed_first_failure(self):
        """Test that first failure doesn't send alert (pattern detection)."""
        with patch("worker.alerts.send_webhook_alert", new_callable=AsyncMock) as mock_send:
            await alert_job_failed(
                video_id=789,
                video_slug="failing-video",
                attempt_number=1,
                error="First error",
                will_retry=True,
            )

            # First failure - no alert
            mock_send.assert_not_called()
            assert get_metrics().jobs_failed == 1

    @pytest.mark.asyncio
    async def test_alert_job_failed_repeated_failure(self):
        """Test that repeated failures trigger alert."""
        with patch("worker.alerts.send_webhook_alert", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True

            # First failure - no alert
            await alert_job_failed(
                video_id=789,
                video_slug="failing-video",
                attempt_number=1,
                error="First error",
                will_retry=True,
            )
            mock_send.assert_not_called()

            # Second failure - should alert
            await alert_job_failed(
                video_id=789,
                video_slug="failing-video",
                attempt_number=2,
                error="Second error",
                will_retry=True,
            )
            mock_send.assert_called_once()
            details = mock_send.call_args[0][1]
            assert details["video_failure_count"] == 2

    @pytest.mark.asyncio
    async def test_alert_worker_startup(self):
        """Test alert_worker_startup function."""
        with patch("worker.alerts.send_webhook_alert", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True

            await alert_worker_startup(
                worker_id="test-worker-id",
                gpu_info="NVIDIA RTX 4090",
                recovered_jobs=3,
            )

            mock_send.assert_called_once()
            call_args = mock_send.call_args
            assert call_args[0][0] == AlertType.WORKER_STARTUP
            details = call_args[0][1]
            assert details["worker_id"] == "test-worker-id"
            assert details["gpu_info"] == "NVIDIA RTX 4090"
            assert details["recovered_jobs"] == 3
            assert call_args[1]["force"] is True

    @pytest.mark.asyncio
    async def test_alert_worker_shutdown(self):
        """Test alert_worker_shutdown function."""
        with patch("worker.alerts.send_webhook_alert", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True

            await alert_worker_shutdown(
                worker_id="test-worker-id",
                jobs_reset=2,
            )

            mock_send.assert_called_once()
            call_args = mock_send.call_args
            assert call_args[0][0] == AlertType.WORKER_SHUTDOWN
            details = call_args[0][1]
            assert details["worker_id"] == "test-worker-id"
            assert details["jobs_reset"] == 2
            assert "final_metrics" in details
            assert call_args[1]["force"] is True


class TestGlobalMetrics:
    """Tests for global metrics management."""

    def test_get_metrics_creates_instance(self):
        """Test that get_metrics creates a new instance if none exists."""
        reset_metrics()
        metrics1 = get_metrics()
        metrics2 = get_metrics()
        assert metrics1 is metrics2

    def test_reset_metrics(self):
        """Test that reset_metrics clears counters."""
        metrics = get_metrics()
        metrics.increment_stale_recovered()
        assert metrics.stale_jobs_recovered == 1

        reset_metrics()
        new_metrics = get_metrics()
        assert new_metrics.stale_jobs_recovered == 0


class TestFireAndForget:
    """Tests for fire-and-forget alert functionality."""

    @pytest.mark.asyncio
    async def test_fire_and_forget_schedules_task(self):
        """Test that send_alert_fire_and_forget schedules a background task."""
        import asyncio

        called = False

        async def mock_alert():
            nonlocal called
            called = True

        send_alert_fire_and_forget(mock_alert())

        # Give the event loop a chance to execute the task
        await asyncio.sleep(0.1)

        assert called is True

    @pytest.mark.asyncio
    async def test_fire_and_forget_catches_exceptions(self):
        """Test that fire-and-forget catches and logs exceptions."""
        import asyncio

        async def failing_alert():
            raise ValueError("Test error")

        # Should not raise - exceptions are caught
        send_alert_fire_and_forget(failing_alert())

        # Give the event loop a chance to process
        await asyncio.sleep(0.1)

        # Test passes if we get here without exception

    @pytest.mark.asyncio
    async def test_fire_and_forget_with_real_alert(self):
        """Test fire-and-forget with an actual alert function."""
        import asyncio

        with patch("worker.alerts.send_webhook_alert", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = True

            send_alert_fire_and_forget(alert_stale_job_recovered(
                video_id=123,
                video_slug="test-video",
                attempt_number=1,
                worker_id="worker-1",
            ))

            # Give the event loop a chance to process
            await asyncio.sleep(0.1)

            # The alert should have been called
            mock_send.assert_called_once()
