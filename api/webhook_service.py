"""
Webhook notification service for VLog events.

Provides webhook delivery for external integrations:
- video.uploaded, video.ready, video.failed, video.deleted, video.restored
- transcription.completed
- worker.registered, worker.offline

Features:
- HMAC-SHA256 payload signing
- Exponential backoff retry with jitter
- Background delivery processing
- Delivery history tracking
- SSRF protection (via schema validation)
- Header injection protection
- Circuit breaker for failing webhooks
- Crash recovery for in-flight deliveries
- Connection pooling via shared HTTP client

See: https://github.com/filthyrake/vlog/issues/203
"""

import asyncio
import hashlib
import hmac
import json
import logging
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

import httpx

from api.database import database, webhook_deliveries, webhooks
from api.db_retry import fetch_all_with_retry, fetch_one_with_retry
from api.schemas import WEBHOOK_EVENT_TYPES

logger = logging.getLogger(__name__)

# Protected headers that cannot be overridden by custom headers (security)
PROTECTED_HEADERS: Set[str] = {
    "content-type",
    "content-length",
    "host",
    "user-agent",
    "x-vlog-event",
    "x-vlog-delivery-id",
    "x-vlog-timestamp",
    "x-vlog-signature",
    "x-vlog-test",
    "authorization",
    "cookie",
    "transfer-encoding",
    "connection",
}

# Default settings (used as fallback if database settings unavailable)
DEFAULT_WEBHOOK_SETTINGS = {
    "enabled": True,
    "max_retries": 5,
    "retry_base_delay": 30,  # seconds
    "retry_backoff_multiplier": 2.0,
    "retry_jitter_factor": 0.25,  # Add up to 25% random jitter
    "request_timeout": 10,  # seconds
    "max_concurrent_deliveries": 10,
    "delivery_batch_size": 50,
    "circuit_breaker_threshold": 5,  # Consecutive failures before opening circuit
    "circuit_breaker_reset_time": 300,  # Seconds before trying again
    "delivery_retention_days": 30,  # Days to keep delivery history
}

# Shared HTTP client for connection pooling
_http_client: Optional[httpx.AsyncClient] = None
_http_client_lock = asyncio.Lock()

# Circuit breaker state: {webhook_id: {"failures": count, "circuit_open_until": datetime}}
_circuit_breaker_state: Dict[int, Dict[str, Any]] = {}


async def _get_http_client() -> httpx.AsyncClient:
    """Get or create shared HTTP client for connection pooling."""
    global _http_client
    async with _http_client_lock:
        if _http_client is None or _http_client.is_closed:
            settings = await _get_webhook_settings()
            timeout = settings.get("request_timeout", 10)
            _http_client = httpx.AsyncClient(
                timeout=timeout,
                limits=httpx.Limits(
                    max_keepalive_connections=20,
                    max_connections=50,
                    keepalive_expiry=30,
                ),
                follow_redirects=False,  # Don't follow redirects (SSRF protection)
            )
        return _http_client


async def _close_http_client() -> None:
    """Close the shared HTTP client."""
    global _http_client
    async with _http_client_lock:
        if _http_client is not None and not _http_client.is_closed:
            await _http_client.aclose()
            _http_client = None


def _is_circuit_open(webhook_id: int) -> bool:
    """Check if circuit breaker is open for a webhook."""
    state = _circuit_breaker_state.get(webhook_id)
    if state is None:
        return False

    circuit_open_until = state.get("circuit_open_until")
    if circuit_open_until is None:
        return False

    return datetime.now(timezone.utc) < circuit_open_until


def _record_circuit_failure(webhook_id: int) -> None:
    """Record a failure for circuit breaker."""
    if webhook_id not in _circuit_breaker_state:
        _circuit_breaker_state[webhook_id] = {"failures": 0, "circuit_open_until": None}

    state = _circuit_breaker_state[webhook_id]
    state["failures"] = state.get("failures", 0) + 1

    threshold = DEFAULT_WEBHOOK_SETTINGS["circuit_breaker_threshold"]
    if state["failures"] >= threshold:
        reset_time = DEFAULT_WEBHOOK_SETTINGS["circuit_breaker_reset_time"]
        state["circuit_open_until"] = datetime.now(timezone.utc) + timedelta(seconds=reset_time)
        logger.warning(f"Circuit breaker opened for webhook {webhook_id} after {state['failures']} failures")


def _record_circuit_success(webhook_id: int) -> None:
    """Record a success, resetting circuit breaker."""
    if webhook_id in _circuit_breaker_state:
        _circuit_breaker_state[webhook_id] = {"failures": 0, "circuit_open_until": None}


def _filter_custom_headers(custom_headers: Dict[str, str]) -> Dict[str, str]:
    """Filter out protected headers from custom headers (header injection protection)."""
    return {k: v for k, v in custom_headers.items() if k.lower() not in PROTECTED_HEADERS}

# Cached settings
_cached_webhook_settings: Dict[str, Any] = {}
_cached_settings_time: float = 0
_SETTINGS_CACHE_TTL = 60  # Refresh settings every 60 seconds


async def _get_webhook_settings() -> Dict[str, Any]:
    """Get webhook settings from database with caching.

    Returns:
        Dict with webhook configuration settings
    """
    global _cached_webhook_settings, _cached_settings_time

    now = time.time()
    if _cached_webhook_settings and (now - _cached_settings_time) < _SETTINGS_CACHE_TTL:
        return _cached_webhook_settings

    try:
        from api.settings_service import get_settings_service

        service = get_settings_service()

        _cached_webhook_settings = {
            "enabled": await service.get("webhooks.enabled", DEFAULT_WEBHOOK_SETTINGS["enabled"]),
            "max_retries": await service.get("webhooks.max_retries", DEFAULT_WEBHOOK_SETTINGS["max_retries"]),
            "retry_base_delay": await service.get(
                "webhooks.retry_base_delay", DEFAULT_WEBHOOK_SETTINGS["retry_base_delay"]
            ),
            "retry_backoff_multiplier": await service.get(
                "webhooks.retry_backoff_multiplier", DEFAULT_WEBHOOK_SETTINGS["retry_backoff_multiplier"]
            ),
            "request_timeout": await service.get(
                "webhooks.request_timeout", DEFAULT_WEBHOOK_SETTINGS["request_timeout"]
            ),
            "max_concurrent_deliveries": await service.get(
                "webhooks.max_concurrent_deliveries", DEFAULT_WEBHOOK_SETTINGS["max_concurrent_deliveries"]
            ),
            "delivery_batch_size": await service.get(
                "webhooks.delivery_batch_size", DEFAULT_WEBHOOK_SETTINGS["delivery_batch_size"]
            ),
        }
        _cached_settings_time = now
    except Exception as e:
        logger.debug(f"Failed to get webhook settings from DB, using defaults: {e}")
        _cached_webhook_settings = DEFAULT_WEBHOOK_SETTINGS.copy()
        _cached_settings_time = now

    return _cached_webhook_settings


def reset_webhook_settings_cache() -> None:
    """Reset the cached webhook settings. Useful for testing."""
    global _cached_webhook_settings, _cached_settings_time
    _cached_webhook_settings = {}
    _cached_settings_time = 0


def generate_signature(payload: str, secret: str) -> str:
    """Generate HMAC-SHA256 signature for webhook payload.

    Args:
        payload: JSON-encoded webhook payload
        secret: Webhook secret key

    Returns:
        Signature in format "sha256=<hex_digest>"
    """
    signature = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"sha256={signature}"


def verify_signature(payload: str, signature: str, secret: str) -> bool:
    """Verify webhook payload signature.

    Args:
        payload: JSON-encoded webhook payload
        signature: Signature from X-VLog-Signature header
        secret: Webhook secret key

    Returns:
        True if signature is valid, False otherwise
    """
    expected = generate_signature(payload, secret)
    return hmac.compare_digest(expected, signature)


async def trigger_webhook_event(event_type: str, event_data: Dict[str, Any]) -> int:
    """Trigger a webhook event for all subscribed webhooks.

    Creates delivery records for each active webhook subscribed to this event.
    Deliveries are processed asynchronously by the background task.

    Uses batch operations and atomic updates to fix N+1 and race conditions.

    Args:
        event_type: Event type (e.g., "video.ready")
        event_data: Event payload data

    Returns:
        Number of deliveries queued
    """
    settings = await _get_webhook_settings()
    if not settings.get("enabled", True):
        logger.debug(f"Webhooks disabled, skipping event: {event_type}")
        return 0

    if event_type not in WEBHOOK_EVENT_TYPES:
        logger.warning(f"Invalid webhook event type: {event_type}")
        return 0

    # Find all active webhooks
    query = webhooks.select().where(webhooks.c.active == True)  # noqa: E712
    active_webhooks = await fetch_all_with_retry(query)

    if not active_webhooks:
        logger.debug(f"No active webhooks for event: {event_type}")
        return 0

    now = datetime.now(timezone.utc)
    event_data_json = json.dumps(event_data)

    # Collect webhooks that are subscribed to this event
    subscribed_webhook_ids: List[int] = []
    delivery_values: List[Dict[str, Any]] = []

    for webhook in active_webhooks:
        # Skip webhooks with open circuit breakers
        if _is_circuit_open(webhook["id"]):
            logger.debug(f"Circuit breaker open for webhook {webhook['id']}, skipping")
            continue

        # Parse events from JSON
        try:
            subscribed_events = json.loads(webhook["events"])
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"Invalid events JSON for webhook {webhook['id']}")
            continue

        if event_type not in subscribed_events:
            continue

        subscribed_webhook_ids.append(webhook["id"])
        delivery_values.append(
            {
                "webhook_id": webhook["id"],
                "event_type": event_type,
                "event_data": event_data_json,
                "status": "pending",
                "attempt_number": 1,
                "created_at": now,
                "next_retry_at": now,
            }
        )

    if not delivery_values:
        logger.debug(f"No webhooks subscribed to event: {event_type}")
        return 0

    # Batch insert all deliveries in a single transaction
    try:
        async with database.transaction():
            # Batch insert all delivery records
            await database.execute_many(webhook_deliveries.insert(), delivery_values)

            # Atomic update of webhook statistics (avoids race condition)
            # Uses SQL expression instead of read-then-write
            await database.execute(
                webhooks.update()
                .where(webhooks.c.id.in_(subscribed_webhook_ids))
                .values(
                    last_triggered_at=now,
                    total_deliveries=webhooks.c.total_deliveries + 1,
                )
            )

        deliveries_created = len(delivery_values)
        logger.info(f"Queued {deliveries_created} webhook deliveries for event: {event_type}")
        return deliveries_created

    except Exception as e:
        logger.error(f"Failed to create webhook deliveries: {e}")
        return 0


async def send_webhook_delivery(delivery_id: int) -> bool:
    """Send a single webhook delivery.

    Uses shared HTTP client for connection pooling.
    Includes header injection protection and circuit breaker integration.
    Uses atomic SQL updates to prevent race conditions.

    Args:
        delivery_id: ID of the delivery record

    Returns:
        True if delivery succeeded, False otherwise
    """
    settings = await _get_webhook_settings()

    # Fetch delivery and webhook
    delivery = await fetch_one_with_retry(webhook_deliveries.select().where(webhook_deliveries.c.id == delivery_id))

    if not delivery:
        logger.warning(f"Webhook delivery {delivery_id} not found")
        return False

    webhook = await fetch_one_with_retry(webhooks.select().where(webhooks.c.id == delivery["webhook_id"]))

    if not webhook:
        logger.warning(f"Webhook {delivery['webhook_id']} not found for delivery {delivery_id}")
        # Mark as permanently failed
        await database.execute(
            webhook_deliveries.update()
            .where(webhook_deliveries.c.id == delivery_id)
            .values(status="failed_permanent", error_message="Webhook not found")
        )
        return False

    if not webhook["active"]:
        logger.debug(f"Webhook {webhook['id']} is inactive, skipping delivery {delivery_id}")
        await database.execute(
            webhook_deliveries.update()
            .where(webhook_deliveries.c.id == delivery_id)
            .values(status="failed_permanent", error_message="Webhook is inactive")
        )
        return False

    # Check circuit breaker
    if _is_circuit_open(webhook["id"]):
        logger.debug(f"Circuit breaker open for webhook {webhook['id']}, skipping delivery {delivery_id}")
        # Schedule retry after circuit breaker reset
        reset_time = DEFAULT_WEBHOOK_SETTINGS["circuit_breaker_reset_time"]
        next_retry = datetime.now(timezone.utc) + timedelta(seconds=reset_time)
        await database.execute(
            webhook_deliveries.update()
            .where(webhook_deliveries.c.id == delivery_id)
            .values(
                error_message="Circuit breaker open",
                next_retry_at=next_retry,
            )
        )
        return False

    # Build request payload
    try:
        event_data = json.loads(delivery["event_data"])
    except (json.JSONDecodeError, TypeError):
        event_data = {}

    request_body = {
        "id": str(delivery["id"]),
        "event": delivery["event_type"],
        "timestamp": delivery["created_at"].isoformat(),
        "attempt": delivery["attempt_number"],
        "data": event_data,
    }

    request_json = json.dumps(request_body)

    # Build headers (these are protected and cannot be overridden)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "VLog-Webhook/1.0",
        "X-VLog-Event": delivery["event_type"],
        "X-VLog-Delivery-Id": str(delivery["id"]),
        "X-VLog-Timestamp": delivery["created_at"].isoformat(),
    }

    # Add signature if secret is configured
    if webhook["secret"]:
        signature = generate_signature(request_json, webhook["secret"])
        headers["X-VLog-Signature"] = signature

    # Add custom headers from webhook config (with header injection protection)
    if webhook["headers"]:
        try:
            custom_headers = json.loads(webhook["headers"])
            if isinstance(custom_headers, dict):
                # Filter out protected headers to prevent header injection
                safe_headers = _filter_custom_headers(custom_headers)
                headers.update(safe_headers)
        except (json.JSONDecodeError, TypeError):
            pass

    # Send request using shared HTTP client
    start_time = time.time()
    response_status = None
    response_body = None
    error_message = None
    success = False

    try:
        client = await _get_http_client()
        response = await client.post(
            webhook["url"],
            content=request_json,
            headers=headers,
        )

        response_status = response.status_code
        response_body = response.text[:2000] if response.text else None  # Truncate long responses

        if 200 <= response.status_code < 300:
            success = True
        else:
            error_message = f"HTTP {response.status_code}"

    except httpx.TimeoutException:
        timeout = settings.get("request_timeout", 10)
        error_message = f"Request timeout ({timeout}s)"
        logger.warning(f"Webhook delivery {delivery_id} timeout: {webhook['url']}")
    except httpx.ConnectError as e:
        error_message = f"Connection error: {str(e)[:200]}"
        logger.warning(f"Webhook delivery {delivery_id} connection error: {e}")
    except httpx.HTTPError as e:
        error_message = f"HTTP error: {str(e)[:200]}"
        logger.warning(f"Webhook delivery {delivery_id} HTTP error: {e}")
    except Exception as e:
        error_message = f"Unexpected error: {str(e)[:200]}"
        logger.error(f"Webhook delivery {delivery_id} unexpected error: {e}")

    duration_ms = int((time.time() - start_time) * 1000)
    now = datetime.now(timezone.utc)

    if success:
        # Mark as delivered and update success count atomically
        async with database.transaction():
            await database.execute(
                webhook_deliveries.update()
                .where(webhook_deliveries.c.id == delivery_id)
                .values(
                    status="delivered",
                    response_status=response_status,
                    response_body=response_body,
                    request_body=request_json,
                    duration_ms=duration_ms,
                    delivered_at=now,
                )
            )

            # Atomic update of webhook success count (prevents race condition)
            await database.execute(
                webhooks.update()
                .where(webhooks.c.id == webhook["id"])
                .values(successful_deliveries=webhooks.c.successful_deliveries + 1)
            )

        # Record success for circuit breaker
        _record_circuit_success(webhook["id"])

        logger.info(f"Webhook delivery {delivery_id} succeeded: {webhook['url']}")
        return True
    else:
        # Record failure for circuit breaker
        _record_circuit_failure(webhook["id"])

        # Check if we should retry
        max_retries = settings.get("max_retries", 5)

        if delivery["attempt_number"] < max_retries:
            # Schedule retry with exponential backoff + jitter
            base_delay = settings.get("retry_base_delay", 30)
            multiplier = settings.get("retry_backoff_multiplier", 2.0)
            jitter_factor = settings.get("retry_jitter_factor", 0.25)

            # Calculate base delay with exponential backoff
            delay = base_delay * (multiplier ** (delivery["attempt_number"] - 1))

            # Add random jitter to prevent thundering herd
            jitter = delay * jitter_factor * random.random()
            delay = delay + jitter

            next_retry = now + timedelta(seconds=delay)

            await database.execute(
                webhook_deliveries.update()
                .where(webhook_deliveries.c.id == delivery_id)
                .values(
                    attempt_number=delivery["attempt_number"] + 1,
                    response_status=response_status,
                    response_body=response_body,
                    request_body=request_json,
                    error_message=error_message,
                    duration_ms=duration_ms,
                    next_retry_at=next_retry,
                )
            )

            logger.info(
                f"Webhook delivery {delivery_id} failed (attempt {delivery['attempt_number']}), "
                f"retry scheduled in {delay:.1f}s"
            )
        else:
            # Mark as permanently failed and update failure count atomically
            async with database.transaction():
                await database.execute(
                    webhook_deliveries.update()
                    .where(webhook_deliveries.c.id == delivery_id)
                    .values(
                        status="failed_permanent",
                        response_status=response_status,
                        response_body=response_body,
                        request_body=request_json,
                        error_message=error_message,
                        duration_ms=duration_ms,
                    )
                )

                # Atomic update of webhook failure count (prevents race condition)
                await database.execute(
                    webhooks.update()
                    .where(webhooks.c.id == webhook["id"])
                    .values(failed_deliveries=webhooks.c.failed_deliveries + 1)
                )

            logger.warning(
                f"Webhook delivery {delivery_id} permanently failed after {max_retries} attempts: {webhook['url']}"
            )

        return False


async def process_pending_deliveries() -> int:
    """Process pending webhook deliveries.

    Fetches pending deliveries that are due for retry and sends them.

    Returns:
        Number of deliveries processed
    """
    settings = await _get_webhook_settings()

    if not settings.get("enabled", True):
        return 0

    batch_size = settings.get("delivery_batch_size", 50)
    max_concurrent = settings.get("max_concurrent_deliveries", 10)
    now = datetime.now(timezone.utc)

    # Fetch pending deliveries due for processing
    query = (
        webhook_deliveries.select()
        .where(webhook_deliveries.c.status == "pending")
        .where(webhook_deliveries.c.next_retry_at <= now)
        .order_by(webhook_deliveries.c.next_retry_at.asc())
        .limit(batch_size)
    )

    pending = await fetch_all_with_retry(query)

    if not pending:
        return 0

    # Process in batches with concurrency limit
    semaphore = asyncio.Semaphore(max_concurrent)

    async def process_with_semaphore(delivery_id: int) -> bool:
        async with semaphore:
            try:
                return await send_webhook_delivery(delivery_id)
            except Exception as e:
                logger.error(f"Error processing webhook delivery {delivery_id}: {e}")
                return False

    # Process all deliveries concurrently (up to max_concurrent)
    tasks = [process_with_semaphore(d["id"]) for d in pending]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    processed = sum(1 for r in results if not isinstance(r, Exception))
    logger.debug(f"Processed {processed}/{len(pending)} pending webhook deliveries")

    return processed


async def test_webhook(webhook_id: int, event_type: str = "video.ready") -> Dict[str, Any]:
    """Send a test webhook delivery.

    Uses shared HTTP client for connection pooling.
    Includes header injection protection.

    Args:
        webhook_id: ID of the webhook to test
        event_type: Event type to simulate

    Returns:
        Dict with test results (success, status_code, response_body, error_message, duration_ms)
    """
    webhook = await fetch_one_with_retry(webhooks.select().where(webhooks.c.id == webhook_id))

    if not webhook:
        return {
            "success": False,
            "error_message": "Webhook not found",
            "duration_ms": 0,
        }

    # Build test payload
    test_data = {
        "test": True,
        "video_id": 0,
        "video_slug": "test-video",
        "title": "Test Video",
        "message": "This is a test webhook delivery",
    }

    request_body = {
        "id": "test",
        "event": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "attempt": 1,
        "data": test_data,
    }

    request_json = json.dumps(request_body)

    # Build headers (protected headers)
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "VLog-Webhook/1.0",
        "X-VLog-Event": event_type,
        "X-VLog-Delivery-Id": "test",
        "X-VLog-Timestamp": datetime.now(timezone.utc).isoformat(),
        "X-VLog-Test": "true",
    }

    if webhook["secret"]:
        signature = generate_signature(request_json, webhook["secret"])
        headers["X-VLog-Signature"] = signature

    # Add custom headers with header injection protection
    if webhook["headers"]:
        try:
            custom_headers = json.loads(webhook["headers"])
            if isinstance(custom_headers, dict):
                safe_headers = _filter_custom_headers(custom_headers)
                headers.update(safe_headers)
        except (json.JSONDecodeError, TypeError):
            pass

    start_time = time.time()

    try:
        client = await _get_http_client()
        response = await client.post(
            webhook["url"],
            content=request_json,
            headers=headers,
        )

        duration_ms = int((time.time() - start_time) * 1000)

        return {
            "success": 200 <= response.status_code < 300,
            "status_code": response.status_code,
            "response_body": response.text[:2000] if response.text else None,
            "error_message": None if 200 <= response.status_code < 300 else f"HTTP {response.status_code}",
            "duration_ms": duration_ms,
        }

    except httpx.TimeoutException:
        settings = await _get_webhook_settings()
        timeout = settings.get("request_timeout", 10)
        return {
            "success": False,
            "error_message": f"Request timeout ({timeout}s)",
            "duration_ms": int((time.time() - start_time) * 1000),
        }
    except httpx.ConnectError as e:
        return {
            "success": False,
            "error_message": f"Connection error: {str(e)[:200]}",
            "duration_ms": int((time.time() - start_time) * 1000),
        }
    except Exception as e:
        return {
            "success": False,
            "error_message": f"Error: {str(e)[:200]}",
            "duration_ms": int((time.time() - start_time) * 1000),
        }


async def recover_in_flight_deliveries() -> int:
    """Recover deliveries that were in-flight when the system crashed.

    Called on startup to reset any deliveries that got stuck due to crashes.
    Resets deliveries that have been pending for more than an hour back to
    their original state for retry.

    Returns:
        Number of deliveries recovered
    """
    now = datetime.now(timezone.utc)
    stale_threshold = now - timedelta(hours=1)

    try:
        # Find deliveries that are pending but have a very old next_retry_at
        # This indicates they may have been in-flight during a crash
        result = await database.execute(
            webhook_deliveries.update()
            .where(webhook_deliveries.c.status == "pending")
            .where(webhook_deliveries.c.next_retry_at < stale_threshold)
            .values(
                next_retry_at=now,
                error_message="Recovered after system restart",
            )
            .returning(webhook_deliveries.c.id)
        )

        recovered = len(result.fetchall()) if result else 0

        if recovered > 0:
            logger.info(f"Recovered {recovered} stale webhook deliveries after restart")

        return recovered

    except Exception as e:
        logger.error(f"Failed to recover in-flight deliveries: {e}")
        return 0


async def cleanup_old_deliveries() -> int:
    """Clean up old delivery records to prevent table bloat.

    Deletes delivered and permanently failed deliveries older than
    the configured retention period.

    Returns:
        Number of deliveries deleted
    """
    settings = await _get_webhook_settings()
    retention_days = settings.get("delivery_retention_days", 30)
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

    try:
        result = await database.execute(
            webhook_deliveries.delete()
            .where(webhook_deliveries.c.status.in_(["delivered", "failed_permanent"]))
            .where(webhook_deliveries.c.created_at < cutoff)
        )

        deleted = result if isinstance(result, int) else 0

        if deleted > 0:
            logger.info(f"Cleaned up {deleted} old webhook deliveries (older than {retention_days} days)")

        return deleted

    except Exception as e:
        logger.error(f"Failed to cleanup old deliveries: {e}")
        return 0


# Background task for webhook delivery processing
_webhook_delivery_task: Optional[asyncio.Task] = None
_worker_last_heartbeat: Optional[datetime] = None
_worker_healthy: bool = False

# Cleanup interval (run cleanup every 6 hours)
_CLEANUP_INTERVAL_SECONDS = 6 * 60 * 60


async def _webhook_delivery_worker():
    """Background task to process pending webhook deliveries.

    Includes health monitoring heartbeat and periodic cleanup.
    """
    global _worker_last_heartbeat, _worker_healthy

    logger.info("Webhook delivery worker started")
    _worker_healthy = True

    # Track time since last cleanup
    last_cleanup = time.time()
    cleanup_interval = _CLEANUP_INTERVAL_SECONDS

    while True:
        try:
            # Update heartbeat for health monitoring
            _worker_last_heartbeat = datetime.now(timezone.utc)

            await asyncio.sleep(5)  # Check every 5 seconds

            # Process pending deliveries
            await process_pending_deliveries()

            # Periodic cleanup of old deliveries
            if time.time() - last_cleanup > cleanup_interval:
                await cleanup_old_deliveries()
                last_cleanup = time.time()

        except asyncio.CancelledError:
            logger.info("Webhook delivery worker received shutdown signal")
            _worker_healthy = False
            break
        except Exception as e:
            logger.error(f"Error in webhook delivery worker: {e}")
            await asyncio.sleep(10)  # Back off on error

    logger.info("Webhook delivery worker stopped")


def is_worker_healthy() -> bool:
    """Check if the webhook delivery worker is healthy.

    Returns:
        True if worker is running and responsive
    """
    global _worker_last_heartbeat, _worker_healthy

    if not _worker_healthy:
        return False

    if _webhook_delivery_task is None or _webhook_delivery_task.done():
        return False

    if _worker_last_heartbeat is None:
        return False

    # Worker is unhealthy if no heartbeat in last 30 seconds
    heartbeat_age = datetime.now(timezone.utc) - _worker_last_heartbeat
    return heartbeat_age.total_seconds() < 30


def get_worker_status() -> Dict[str, Any]:
    """Get detailed worker status information.

    Returns:
        Dict with worker health and status details
    """
    global _worker_last_heartbeat, _worker_healthy

    task_running = _webhook_delivery_task is not None and not _webhook_delivery_task.done()

    status = {
        "healthy": is_worker_healthy(),
        "running": task_running,
        "last_heartbeat": _worker_last_heartbeat.isoformat() if _worker_last_heartbeat else None,
        "circuit_breakers_open": sum(1 for state in _circuit_breaker_state.values() if state.get("circuit_open_until")),
    }

    if _worker_last_heartbeat:
        age = datetime.now(timezone.utc) - _worker_last_heartbeat
        status["heartbeat_age_seconds"] = int(age.total_seconds())

    return status


async def start_webhook_delivery_worker() -> asyncio.Task:
    """Start the background webhook delivery worker.

    Also performs crash recovery on startup.

    Returns:
        The asyncio Task for the worker
    """
    global _webhook_delivery_task

    # Recover any in-flight deliveries from previous runs
    await recover_in_flight_deliveries()

    if _webhook_delivery_task is None or _webhook_delivery_task.done():
        _webhook_delivery_task = asyncio.create_task(_webhook_delivery_worker())

    return _webhook_delivery_task


async def stop_webhook_delivery_worker(timeout: float = 10.0) -> None:
    """Stop the background webhook delivery worker gracefully.

    Waits for in-flight requests to complete before shutting down.

    Args:
        timeout: Maximum seconds to wait for graceful shutdown
    """
    global _webhook_delivery_task, _worker_healthy

    _worker_healthy = False

    if _webhook_delivery_task and not _webhook_delivery_task.done():
        logger.info(f"Stopping webhook delivery worker (timeout: {timeout}s)")
        _webhook_delivery_task.cancel()

        try:
            await asyncio.wait_for(
                asyncio.shield(_webhook_delivery_task),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(f"Webhook worker did not stop within {timeout}s timeout")
        except asyncio.CancelledError:
            pass

    # Close the shared HTTP client
    await _close_http_client()
    logger.info("Webhook delivery worker stopped and HTTP client closed")
