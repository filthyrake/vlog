"""
Alert system for transcoding worker events.

Provides webhook notifications for:
- Stale jobs recovered
- Jobs exceeding max retry attempts
- Repeated failures for specific videos

Includes rate limiting to prevent alert flooding.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

import httpx

from config import (
    ALERT_RATE_LIMIT_SECONDS,
    ALERT_WEBHOOK_TIMEOUT,
    ALERT_WEBHOOK_URL,
)

logger = logging.getLogger(__name__)


class AlertType(str, Enum):
    """Types of alerts that can be sent."""

    JOB_STALE_RECOVERED = "job_stale_recovered"
    JOB_MAX_RETRIES_EXCEEDED = "job_max_retries_exceeded"
    JOB_FAILED = "job_failed"
    WORKER_STARTUP = "worker_startup"
    WORKER_SHUTDOWN = "worker_shutdown"


@dataclass
class AlertMetrics:
    """Tracks metrics for alerting and monitoring."""

    # Counters
    stale_jobs_recovered: int = 0
    jobs_max_retries_exceeded: int = 0
    jobs_failed: int = 0
    alerts_sent: int = 0
    alerts_rate_limited: int = 0
    alerts_failed: int = 0

    # Last alert timestamps by type (for rate limiting)
    last_alert_time: Dict[str, float] = field(default_factory=dict)

    # Track failures by video for pattern detection
    video_failure_counts: Dict[int, int] = field(default_factory=dict)

    def increment_stale_recovered(self) -> int:
        """Increment stale jobs recovered counter."""
        self.stale_jobs_recovered += 1
        return self.stale_jobs_recovered

    def increment_max_retries(self) -> int:
        """Increment max retries exceeded counter."""
        self.jobs_max_retries_exceeded += 1
        return self.jobs_max_retries_exceeded

    def increment_failed(self, video_id: Optional[int] = None) -> int:
        """Increment jobs failed counter and track per-video failures."""
        self.jobs_failed += 1
        if video_id is not None:
            self.video_failure_counts[video_id] = self.video_failure_counts.get(video_id, 0) + 1
        return self.jobs_failed

    def get_video_failure_count(self, video_id: int) -> int:
        """Get failure count for a specific video."""
        return self.video_failure_counts.get(video_id, 0)

    def can_send_alert(self, alert_type: str) -> bool:
        """Check if enough time has passed since the last alert of this type."""
        last_time = self.last_alert_time.get(alert_type, 0)
        return (time.time() - last_time) >= ALERT_RATE_LIMIT_SECONDS

    def record_alert_sent(self, alert_type: str):
        """Record that an alert was sent."""
        self.last_alert_time[alert_type] = time.time()
        self.alerts_sent += 1

    def record_alert_rate_limited(self):
        """Record that an alert was rate limited."""
        self.alerts_rate_limited += 1

    def record_alert_failed(self):
        """Record that an alert failed to send."""
        self.alerts_failed += 1

    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to a dictionary for reporting."""
        return {
            "stale_jobs_recovered": self.stale_jobs_recovered,
            "jobs_max_retries_exceeded": self.jobs_max_retries_exceeded,
            "jobs_failed": self.jobs_failed,
            "alerts_sent": self.alerts_sent,
            "alerts_rate_limited": self.alerts_rate_limited,
            "alerts_failed": self.alerts_failed,
            "videos_with_failures": len(self.video_failure_counts),
        }


# Global metrics instance
_metrics: Optional[AlertMetrics] = None


def get_metrics() -> AlertMetrics:
    """Get or create the global metrics instance."""
    global _metrics
    if _metrics is None:
        _metrics = AlertMetrics()
    return _metrics


def reset_metrics():
    """Reset metrics (for testing)."""
    global _metrics
    _metrics = AlertMetrics()


async def send_webhook_alert(
    alert_type: AlertType,
    details: Dict[str, Any],
    force: bool = False,
) -> bool:
    """
    Send an alert to the configured webhook URL.

    Args:
        alert_type: Type of alert being sent
        details: Additional details about the alert
        force: If True, bypass rate limiting

    Returns:
        True if alert was sent successfully, False otherwise
    """
    if not ALERT_WEBHOOK_URL:
        return False

    metrics = get_metrics()

    # Check rate limiting
    if not force and not metrics.can_send_alert(alert_type.value):
        metrics.record_alert_rate_limited()
        logger.debug(f"Alert {alert_type.value} rate limited")
        return False

    payload = {
        "event": alert_type.value,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "details": details,
        "metrics": metrics.to_dict(),
    }

    try:
        async with httpx.AsyncClient(timeout=ALERT_WEBHOOK_TIMEOUT) as client:
            response = await client.post(
                ALERT_WEBHOOK_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()

        metrics.record_alert_sent(alert_type.value)
        logger.info(f"Alert sent: {alert_type.value}")
        return True

    except httpx.TimeoutException:
        metrics.record_alert_failed()
        logger.warning(f"Alert webhook timed out after {ALERT_WEBHOOK_TIMEOUT}s")
        return False
    except httpx.HTTPStatusError as e:
        metrics.record_alert_failed()
        logger.warning(f"Alert webhook returned error: {e.response.status_code}")
        return False
    except Exception as e:
        metrics.record_alert_failed()
        logger.warning(f"Failed to send alert webhook: {e}")
        return False


async def alert_stale_job_recovered(
    video_id: int,
    video_slug: str,
    attempt_number: int,
    worker_id: Optional[str] = None,
):
    """
    Send alert when a stale job is recovered and reset for retry.

    Args:
        video_id: Database ID of the video
        video_slug: URL slug of the video
        attempt_number: Current attempt number (before increment)
        worker_id: ID of the worker that had the stale job
    """
    metrics = get_metrics()
    metrics.increment_stale_recovered()

    await send_webhook_alert(
        AlertType.JOB_STALE_RECOVERED,
        {
            "video_id": video_id,
            "video_slug": video_slug,
            "attempt_number": attempt_number,
            "next_attempt": attempt_number + 1,
            "previous_worker_id": worker_id,
        },
    )


async def alert_max_retries_exceeded(
    video_id: int,
    video_slug: str,
    max_attempts: int,
    last_error: Optional[str] = None,
):
    """
    Send alert when a job exceeds the maximum retry attempts.

    Args:
        video_id: Database ID of the video
        video_slug: URL slug of the video
        max_attempts: Maximum attempts allowed
        last_error: Last error message from the job
    """
    metrics = get_metrics()
    metrics.increment_max_retries()

    # Always send max retries alerts (they're critical)
    await send_webhook_alert(
        AlertType.JOB_MAX_RETRIES_EXCEEDED,
        {
            "video_id": video_id,
            "video_slug": video_slug,
            "max_attempts": max_attempts,
            "last_error": last_error[:500] if last_error else None,
            "total_max_retries_exceeded": metrics.jobs_max_retries_exceeded,
        },
        force=True,  # Always send these alerts
    )


async def alert_job_failed(
    video_id: int,
    video_slug: str,
    attempt_number: int,
    error: str,
    will_retry: bool,
):
    """
    Send alert when a job fails.

    Only sends alerts after repeated failures for the same video.

    Args:
        video_id: Database ID of the video
        video_slug: URL slug of the video
        attempt_number: Current attempt number
        error: Error message
        will_retry: Whether the job will be retried
    """
    metrics = get_metrics()
    metrics.increment_failed(video_id)
    failure_count = metrics.get_video_failure_count(video_id)

    # Only alert after 2+ failures for the same video (pattern detection)
    if failure_count >= 2:
        await send_webhook_alert(
            AlertType.JOB_FAILED,
            {
                "video_id": video_id,
                "video_slug": video_slug,
                "attempt_number": attempt_number,
                "error": error[:500] if error else None,
                "will_retry": will_retry,
                "video_failure_count": failure_count,
            },
        )


async def alert_worker_startup(
    worker_id: str,
    gpu_info: Optional[str] = None,
    recovered_jobs: int = 0,
):
    """
    Send alert when a worker starts up.

    Args:
        worker_id: ID of the worker
        gpu_info: GPU information if available
        recovered_jobs: Number of interrupted jobs recovered
    """
    await send_webhook_alert(
        AlertType.WORKER_STARTUP,
        {
            "worker_id": worker_id,
            "gpu_info": gpu_info,
            "recovered_jobs": recovered_jobs,
        },
        force=True,
    )


async def alert_worker_shutdown(
    worker_id: str,
    jobs_reset: int = 0,
):
    """
    Send alert when a worker shuts down.

    Args:
        worker_id: ID of the worker
        jobs_reset: Number of jobs reset to pending
    """
    await send_webhook_alert(
        AlertType.WORKER_SHUTDOWN,
        {
            "worker_id": worker_id,
            "jobs_reset": jobs_reset,
            "final_metrics": get_metrics().to_dict(),
        },
        force=True,
    )
