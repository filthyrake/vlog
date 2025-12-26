"""
Redis Pub/Sub for real-time updates.

Provides publish methods for:
- Transcoding progress updates
- Worker status changes
- Job completion/failure notifications

Channels:
- vlog:progress:{video_id} - Per-video progress updates
- vlog:progress:all - All progress (for dashboard)
- vlog:workers:status - Worker status changes
- vlog:jobs:completed - Job completion notifications
- vlog:jobs:failed - Job failure notifications
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional, Set

from api.redis_client import get_redis
from config import REDIS_PUBSUB_PREFIX

logger = logging.getLogger(__name__)


def channel_name(channel_type: str, entity_id: Optional[str] = None) -> str:
    """
    Generate consistent channel name.

    Args:
        channel_type: Type of channel (e.g., "progress", "workers", "jobs")
        entity_id: Optional entity identifier

    Returns:
        Full channel name (e.g., "vlog:progress:123")
    """
    if entity_id:
        return f"{REDIS_PUBSUB_PREFIX}:{channel_type}:{entity_id}"
    return f"{REDIS_PUBSUB_PREFIX}:{channel_type}"


class Publisher:
    """Publish updates to Redis Pub/Sub channels."""

    @staticmethod
    async def publish_progress(
        video_id: int,
        job_id: int,
        current_step: str,
        progress_percent: int,
        qualities: Optional[List[Dict]] = None,
        status: Optional[str] = None,
        last_error: Optional[str] = None,
    ) -> bool:
        """
        Publish transcoding progress update.

        Args:
            video_id: Video being transcoded
            job_id: Transcoding job ID
            current_step: Current step (probe, thumbnail, transcode, etc.)
            progress_percent: Overall progress 0-100
            qualities: List of per-quality progress dicts
            status: Video status (processing, ready, failed)
            last_error: Error message if failed

        Returns:
            True if published successfully
        """
        redis = await get_redis()
        if not redis:
            return False

        message = {
            "type": "progress",
            "video_id": video_id,
            "job_id": job_id,
            "current_step": current_step,
            "progress_percent": progress_percent,
            "qualities": qualities or [],
            "status": status,
            "last_error": last_error,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        try:
            payload = json.dumps(message)
            # Publish to video-specific channel
            await redis.publish(channel_name("progress", str(video_id)), payload)
            # Also publish to global channel for dashboard
            await redis.publish(channel_name("progress", "all"), payload)
            return True
        except Exception as e:
            logger.warning(f"Failed to publish progress: {e}")
            return False

    @staticmethod
    async def publish_worker_status(
        worker_id: str,
        worker_name: str,
        status: str,
        current_job_id: Optional[int] = None,
        current_video_slug: Optional[str] = None,
        hwaccel_type: Optional[str] = None,
        progress_percent: Optional[int] = None,
        current_step: Optional[str] = None,
    ) -> bool:
        """
        Publish worker status change.

        Args:
            worker_id: Worker UUID
            worker_name: Human-readable worker name
            status: Worker status (active, busy, idle, offline)
            current_job_id: Job being processed (if any)
            current_video_slug: Video slug being processed
            hwaccel_type: Hardware acceleration type (nvidia, intel, none)
            progress_percent: Current job progress
            current_step: Current transcoding step

        Returns:
            True if published successfully
        """
        redis = await get_redis()
        if not redis:
            return False

        message = {
            "type": "worker_status",
            "worker_id": worker_id,
            "worker_name": worker_name,
            "status": status,
            "current_job_id": current_job_id,
            "current_video_slug": current_video_slug,
            "hwaccel_type": hwaccel_type,
            "progress_percent": progress_percent,
            "current_step": current_step,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        try:
            await redis.publish(channel_name("workers", "status"), json.dumps(message))
            return True
        except Exception as e:
            logger.warning(f"Failed to publish worker status: {e}")
            return False

    @staticmethod
    async def publish_job_completed(
        job_id: int,
        video_id: int,
        video_slug: str,
        worker_id: str,
        worker_name: str,
        qualities: List[Dict],
        duration_seconds: Optional[float] = None,
    ) -> bool:
        """
        Publish job completion notification.

        Args:
            job_id: Completed job ID
            video_id: Video that was transcoded
            video_slug: Video slug
            worker_id: Worker that completed the job
            worker_name: Worker name
            qualities: List of completed quality dicts
            duration_seconds: Total transcoding duration

        Returns:
            True if published successfully
        """
        redis = await get_redis()
        if not redis:
            return False

        message = {
            "type": "job_completed",
            "job_id": job_id,
            "video_id": video_id,
            "video_slug": video_slug,
            "worker_id": worker_id,
            "worker_name": worker_name,
            "qualities": qualities,
            "duration_seconds": duration_seconds,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        try:
            payload = json.dumps(message)
            await redis.publish(channel_name("jobs", "completed"), payload)
            # Also notify on progress channel that video is ready
            await redis.publish(channel_name("progress", str(video_id)), payload)
            await redis.publish(channel_name("progress", "all"), payload)
            return True
        except Exception as e:
            logger.warning(f"Failed to publish job completion: {e}")
            return False

    @staticmethod
    async def publish_job_failed(
        job_id: int,
        video_id: int,
        video_slug: str,
        worker_id: str,
        worker_name: str,
        error: str,
        will_retry: bool,
        attempt: int = 1,
        max_attempts: int = 3,
    ) -> bool:
        """
        Publish job failure notification.

        Args:
            job_id: Failed job ID
            video_id: Video that failed
            video_slug: Video slug
            worker_id: Worker that reported failure
            worker_name: Worker name
            error: Error message
            will_retry: Whether job will be retried
            attempt: Current attempt number
            max_attempts: Maximum retry attempts

        Returns:
            True if published successfully
        """
        redis = await get_redis()
        if not redis:
            return False

        message = {
            "type": "job_failed",
            "job_id": job_id,
            "video_id": video_id,
            "video_slug": video_slug,
            "worker_id": worker_id,
            "worker_name": worker_name,
            "error": error[:200],
            "will_retry": will_retry,
            "attempt": attempt,
            "max_attempts": max_attempts,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        try:
            payload = json.dumps(message)
            await redis.publish(channel_name("jobs", "failed"), payload)
            await redis.publish(channel_name("progress", str(video_id)), payload)
            await redis.publish(channel_name("progress", "all"), payload)
            return True
        except Exception as e:
            logger.warning(f"Failed to publish job failure: {e}")
            return False


class Subscriber:
    """Subscribe to Redis Pub/Sub channels for SSE streaming."""

    def __init__(self) -> None:
        self._pubsub = None
        self._subscribed_channels: Set[str] = set()
        self._subscribed_patterns: Set[str] = set()

    async def subscribe(self, *channels: str) -> bool:
        """
        Subscribe to one or more channels.

        Args:
            *channels: Channel names to subscribe to

        Returns:
            True if subscribed successfully
        """
        redis = await get_redis()
        if not redis:
            return False

        try:
            if not self._pubsub:
                self._pubsub = redis.pubsub()

            await self._pubsub.subscribe(*channels)
            self._subscribed_channels.update(channels)
            logger.debug(f"Subscribed to channels: {channels}")
            return True
        except Exception as e:
            logger.warning(f"Failed to subscribe to channels: {e}")
            return False

    async def subscribe_pattern(self, *patterns: str) -> bool:
        """
        Subscribe to channels matching patterns.

        Args:
            *patterns: Glob-style patterns (e.g., "vlog:progress:*")

        Returns:
            True if subscribed successfully
        """
        redis = await get_redis()
        if not redis:
            return False

        try:
            if not self._pubsub:
                self._pubsub = redis.pubsub()

            await self._pubsub.psubscribe(*patterns)
            self._subscribed_patterns.update(patterns)
            logger.debug(f"Subscribed to patterns: {patterns}")
            return True
        except Exception as e:
            logger.warning(f"Failed to subscribe to patterns: {e}")
            return False

    async def listen(self) -> AsyncIterator[Dict[str, Any]]:
        """
        Async generator yielding messages from subscribed channels.

        Yields:
            Parsed message dicts with type, channel, and data

        Raises:
            Exception: On connection errors (caller should handle reconnection)
        """
        if not self._pubsub:
            return

        try:
            async for message in self._pubsub.listen():
                msg_type = message.get("type", "")

                # Skip subscription confirmations
                if msg_type in ("subscribe", "psubscribe", "unsubscribe", "punsubscribe"):
                    continue

                if msg_type in ("message", "pmessage"):
                    try:
                        data = json.loads(message.get("data", "{}"))
                        yield {
                            "channel": message.get("channel", ""),
                            "pattern": message.get("pattern"),
                            **data,
                        }
                    except json.JSONDecodeError:
                        logger.debug(f"Invalid JSON in pub/sub message: {message}")
                        continue
        except Exception as e:
            logger.warning(f"Pub/Sub listen error: {e}")
            raise

    async def close(self) -> None:
        """Close the subscription and clean up."""
        if self._pubsub:
            try:
                if self._subscribed_channels:
                    await self._pubsub.unsubscribe(*self._subscribed_channels)
                if self._subscribed_patterns:
                    await self._pubsub.punsubscribe(*self._subscribed_patterns)
                await self._pubsub.close()
            except Exception as e:
                logger.debug(f"Error closing pub/sub: {e}")
            finally:
                self._pubsub = None
                self._subscribed_channels.clear()
                self._subscribed_patterns.clear()

    @property
    def is_active(self) -> bool:
        """Check if subscription is active."""
        return self._pubsub is not None and (bool(self._subscribed_channels) or bool(self._subscribed_patterns))


async def subscribe_to_progress(video_ids: Optional[List[int]] = None) -> Subscriber:
    """
    Create a subscriber for progress updates.

    Args:
        video_ids: Specific video IDs to monitor, or None for all

    Returns:
        Configured Subscriber instance
    """
    subscriber = Subscriber()

    if video_ids:
        channels = [channel_name("progress", str(vid)) for vid in video_ids]
        await subscriber.subscribe(*channels)
    else:
        # Subscribe to all progress updates
        await subscriber.subscribe(channel_name("progress", "all"))

    return subscriber


async def subscribe_to_workers() -> Subscriber:
    """
    Create a subscriber for worker status updates.

    Returns:
        Configured Subscriber instance
    """
    subscriber = Subscriber()
    await subscriber.subscribe(
        channel_name("workers", "status"),
        channel_name("jobs", "completed"),
        channel_name("jobs", "failed"),
        channel_name("progress", "all"),
    )
    return subscriber


async def subscribe_to_worker_commands(worker_id: str) -> Subscriber:
    """
    Create a subscriber for worker-specific management commands.

    Subscribes to both worker-specific and broadcast command channels.

    Args:
        worker_id: The worker's UUID

    Returns:
        Configured Subscriber instance
    """
    subscriber = Subscriber()
    await subscriber.subscribe(
        channel_name("worker", f"{worker_id}:commands"),  # Worker-specific commands
        channel_name("workers", "commands"),  # Broadcast commands to all workers
    )
    return subscriber


async def publish_worker_command(
    worker_id: str,
    command: str,
    params: Optional[Dict] = None,
    request_id: Optional[str] = None,
) -> bool:
    """
    Publish a management command to a worker.

    Args:
        worker_id: Target worker UUID or "all" for broadcast
        command: Command type (restart, stop, update, get_logs, get_metrics)
        params: Optional command parameters
        request_id: Optional request ID for response correlation

    Returns:
        True if published successfully
    """
    redis = await get_redis()
    if not redis:
        return False

    message = {
        "type": "command",
        "command": command,
        "params": params or {},
        "request_id": request_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        payload = json.dumps(message)
        if worker_id == "all":
            # Broadcast to all workers
            await redis.publish(channel_name("workers", "commands"), payload)
        else:
            # Target specific worker
            await redis.publish(channel_name("worker", f"{worker_id}:commands"), payload)
        logger.info(f"Published {command} command to worker {worker_id}")
        return True
    except Exception as e:
        logger.warning(f"Failed to publish worker command: {e}")
        return False


async def request_worker_response(
    worker_id: str,
    command: str,
    params: Optional[Dict] = None,
    timeout_seconds: float = 10.0,
) -> Optional[Dict]:
    """
    Send a command to a worker and wait for a response.

    This is used for commands that return data (get_logs, get_metrics).

    Args:
        worker_id: Target worker UUID
        command: Command type (get_logs, get_metrics)
        params: Optional command parameters
        timeout_seconds: How long to wait for response

    Returns:
        Response dict from worker, or None if timeout/error
    """
    import uuid

    redis = await get_redis()
    if not redis:
        return None

    # Generate unique request ID
    request_id = str(uuid.uuid4())[:8]

    # Subscribe to response channel before sending command
    response_channel = f"{REDIS_PUBSUB_PREFIX}:worker:{worker_id}:response:{request_id}"
    pubsub = None

    async def _wait_for_response(ps) -> Optional[Dict]:
        """Inner coroutine to wait for response message."""
        async for message in ps.listen():
            msg_type = message.get("type", "")
            if msg_type in ("subscribe", "unsubscribe"):
                continue

            if msg_type == "message":
                try:
                    return json.loads(message.get("data", "{}"))
                except json.JSONDecodeError:
                    continue
        return None

    try:
        pubsub = redis.pubsub()
        await pubsub.subscribe(response_channel)

        # Send the command
        success = await publish_worker_command(worker_id, command, params, request_id)
        if not success:
            return None

        # Wait for response with proper timeout using asyncio.wait_for
        try:
            result = await asyncio.wait_for(
                _wait_for_response(pubsub),
                timeout=timeout_seconds
            )
            return result
        except asyncio.TimeoutError:
            logger.warning(f"Timeout waiting for worker {worker_id} response after {timeout_seconds}s")
            return None

    except Exception as e:
        logger.warning(f"Error in request_worker_response: {e}")
        return None
    finally:
        # Always clean up the pubsub connection
        if pubsub:
            try:
                await pubsub.close()
            except Exception:
                pass  # Ignore cleanup errors
