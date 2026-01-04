"""
Webhook notification service for VLog events.

Provides webhook delivery for external integrations:
- video.uploaded, video.ready, video.failed, video.deleted, video.restored
- transcription.completed
- worker.registered, worker.offline

Features:
- HMAC-SHA256 payload signing
- Exponential backoff retry
- Background delivery processing
- Delivery history tracking

See: https://github.com/filthyrake/vlog/issues/203
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import httpx

from api.database import database, webhook_deliveries, webhooks
from api.db_retry import fetch_all_with_retry, fetch_one_with_retry
from api.schemas import WEBHOOK_EVENT_TYPES

logger = logging.getLogger(__name__)

# Default settings (used as fallback if database settings unavailable)
DEFAULT_WEBHOOK_SETTINGS = {
    "enabled": True,
    "max_retries": 5,
    "retry_base_delay": 30,  # seconds
    "retry_backoff_multiplier": 2.0,
    "request_timeout": 10,  # seconds
    "max_concurrent_deliveries": 10,
    "delivery_batch_size": 50,
}

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

    deliveries_created = 0
    now = datetime.now(timezone.utc)
    event_data_json = json.dumps(event_data)

    for webhook in active_webhooks:
        # Parse events from JSON
        try:
            subscribed_events = json.loads(webhook["events"])
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"Invalid events JSON for webhook {webhook['id']}")
            continue

        if event_type not in subscribed_events:
            continue

        # Create delivery record
        try:
            await database.execute(
                webhook_deliveries.insert().values(
                    webhook_id=webhook["id"],
                    event_type=event_type,
                    event_data=event_data_json,
                    status="pending",
                    attempt_number=1,
                    created_at=now,
                    next_retry_at=now,
                )
            )

            # Update webhook last_triggered_at and total_deliveries
            await database.execute(
                webhooks.update()
                .where(webhooks.c.id == webhook["id"])
                .values(
                    last_triggered_at=now,
                    total_deliveries=webhook["total_deliveries"] + 1,
                )
            )

            deliveries_created += 1
            logger.debug(f"Queued webhook delivery for {event_type} to {webhook['url']}")
        except Exception as e:
            logger.error(f"Failed to create webhook delivery: {e}")

    if deliveries_created > 0:
        logger.info(f"Queued {deliveries_created} webhook deliveries for event: {event_type}")

    return deliveries_created


async def send_webhook_delivery(delivery_id: int) -> bool:
    """Send a single webhook delivery.

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

    # Build headers
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

    # Add custom headers from webhook config
    if webhook["headers"]:
        try:
            custom_headers = json.loads(webhook["headers"])
            if isinstance(custom_headers, dict):
                headers.update(custom_headers)
        except (json.JSONDecodeError, TypeError):
            pass

    # Send request
    timeout = settings.get("request_timeout", 10)
    start_time = time.time()
    response_status = None
    response_body = None
    error_message = None
    success = False

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
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
        # Mark as delivered
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

        # Update webhook success count
        await database.execute(
            webhooks.update()
            .where(webhooks.c.id == webhook["id"])
            .values(successful_deliveries=webhook["successful_deliveries"] + 1)
        )

        logger.info(f"Webhook delivery {delivery_id} succeeded: {webhook['url']}")
        return True
    else:
        # Check if we should retry
        max_retries = settings.get("max_retries", 5)

        if delivery["attempt_number"] < max_retries:
            # Schedule retry with exponential backoff
            base_delay = settings.get("retry_base_delay", 30)
            multiplier = settings.get("retry_backoff_multiplier", 2.0)
            delay = base_delay * (multiplier ** (delivery["attempt_number"] - 1))
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
                f"retry scheduled in {delay}s"
            )
        else:
            # Mark as permanently failed
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

            # Update webhook failure count
            await database.execute(
                webhooks.update()
                .where(webhooks.c.id == webhook["id"])
                .values(failed_deliveries=webhook["failed_deliveries"] + 1)
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

    settings = await _get_webhook_settings()

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

    # Build headers
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

    if webhook["headers"]:
        try:
            custom_headers = json.loads(webhook["headers"])
            if isinstance(custom_headers, dict):
                headers.update(custom_headers)
        except (json.JSONDecodeError, TypeError):
            pass

    timeout = settings.get("request_timeout", 10)
    start_time = time.time()

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
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


# Background task for webhook delivery processing
_webhook_delivery_task: Optional[asyncio.Task] = None


async def _webhook_delivery_worker():
    """Background task to process pending webhook deliveries."""
    logger.info("Webhook delivery worker started")

    while True:
        try:
            await asyncio.sleep(5)  # Check every 5 seconds
            await process_pending_deliveries()
        except asyncio.CancelledError:
            logger.info("Webhook delivery worker shutting down")
            break
        except Exception as e:
            logger.error(f"Error in webhook delivery worker: {e}")
            await asyncio.sleep(10)  # Back off on error


def start_webhook_delivery_worker() -> asyncio.Task:
    """Start the background webhook delivery worker.

    Returns:
        The asyncio Task for the worker
    """
    global _webhook_delivery_task
    if _webhook_delivery_task is None or _webhook_delivery_task.done():
        _webhook_delivery_task = asyncio.create_task(_webhook_delivery_worker())
    return _webhook_delivery_task


def stop_webhook_delivery_worker():
    """Stop the background webhook delivery worker."""
    global _webhook_delivery_task
    if _webhook_delivery_task and not _webhook_delivery_task.done():
        _webhook_delivery_task.cancel()
