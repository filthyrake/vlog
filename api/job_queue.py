"""
Job queue abstraction for transcoding jobs.

Supports two backends:
- Database polling (default, always works)
- Redis Streams (instant dispatch when available)

Priority queue support with three levels:
- high: Processed first (e.g., urgent re-transcodes)
- normal: Default priority
- low: Processed last (e.g., bulk imports)
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from api.redis_client import get_redis
from config import (
    JOB_QUEUE_MODE,
    REDIS_CONSUMER_BLOCK_MS,
    REDIS_CONSUMER_GROUP,
    REDIS_PENDING_TIMEOUT_MS,
    REDIS_PUBSUB_PREFIX,
    REDIS_STREAM_MAX_LEN,
)

logger = logging.getLogger(__name__)

# Stream names by priority (checked in order: high -> normal -> low)
PRIORITY_STREAMS = {
    "high": f"{REDIS_PUBSUB_PREFIX}:jobs:high",
    "normal": f"{REDIS_PUBSUB_PREFIX}:jobs:normal",
    "low": f"{REDIS_PUBSUB_PREFIX}:jobs:low",
}
STREAM_PRIORITIES = ["high", "normal", "low"]  # Check order

DEAD_LETTER_STREAM = f"{REDIS_PUBSUB_PREFIX}:jobs:dead-letter"


@dataclass
class JobDispatch:
    """Job dispatch message for workers."""

    job_id: int
    video_id: int
    video_slug: str
    source_filename: Optional[str] = None
    source_width: Optional[int] = None
    source_height: Optional[int] = None
    duration: Optional[float] = None
    priority: str = "normal"
    created_at: Optional[datetime] = None
    # Internal: Redis message ID for acknowledgment
    _message_id: Optional[str] = field(default=None, repr=False)
    _stream_name: Optional[str] = field(default=None, repr=False)

    def to_stream_dict(self) -> dict:
        """Convert to Redis stream message format (all string values)."""
        return {
            "job_id": str(self.job_id),
            "video_id": str(self.video_id),
            "video_slug": self.video_slug,
            "source_filename": self.source_filename or "",
            "source_width": str(self.source_width or 0),
            "source_height": str(self.source_height or 0),
            "duration": str(self.duration or 0),
            "priority": self.priority,
            "created_at": (self.created_at or datetime.now(timezone.utc)).isoformat(),
        }

    @classmethod
    def from_stream_dict(cls, data: dict, message_id: str = None, stream_name: str = None) -> "JobDispatch":
        """Create from Redis stream message."""
        created_at = None
        if data.get("created_at"):
            try:
                created_at = datetime.fromisoformat(data["created_at"])
            except (ValueError, TypeError):
                # If parsing fails, leave created_at as None (invalid/missing date is acceptable)
                pass

        job = cls(
            job_id=int(data["job_id"]),
            video_id=int(data["video_id"]),
            video_slug=data["video_slug"],
            source_filename=data.get("source_filename") or None,
            source_width=int(data.get("source_width", 0)) or None,
            source_height=int(data.get("source_height", 0)) or None,
            duration=float(data.get("duration", 0)) or None,
            priority=data.get("priority", "normal"),
            created_at=created_at,
        )
        job._message_id = message_id
        job._stream_name = stream_name
        return job


class JobQueue:
    """Job queue manager supporting database and Redis backends."""

    def __init__(self) -> None:
        self._redis_available: bool = False
        self._consumer_name: Optional[str] = None
        self._initialized: bool = False

    async def initialize(self, consumer_name: str) -> None:
        """
        Initialize the job queue for a worker.

        Args:
            consumer_name: Unique name for this consumer (e.g., worker-abc123)
        """
        self._consumer_name = consumer_name

        if JOB_QUEUE_MODE not in ("redis", "hybrid"):
            logger.info("Job queue mode: database (polling)")
            return

        redis = await get_redis()
        if not redis:
            if JOB_QUEUE_MODE == "redis":
                logger.warning("Redis required but unavailable, jobs will not be claimed")
            else:
                logger.info("Redis unavailable, using database polling fallback")
            return

        try:
            # Create consumer groups for all priority streams
            for priority, stream_name in PRIORITY_STREAMS.items():
                try:
                    await redis.xgroup_create(stream_name, REDIS_CONSUMER_GROUP, id="0", mkstream=True)
                    logger.info(f"Created consumer group for {priority} priority stream")
                except Exception as e:
                    if "BUSYGROUP" not in str(e):
                        raise
                    # Group already exists, that's fine

            self._redis_available = True
            self._initialized = True
            logger.info(f"Job queue initialized with Redis Streams (consumer: {consumer_name})")
        except Exception as e:
            logger.warning(f"Failed to initialize Redis consumer groups: {e}")
            if JOB_QUEUE_MODE == "hybrid":
                logger.info("Falling back to database polling")

    @property
    def is_redis_enabled(self) -> bool:
        """Check if Redis queue is enabled and available."""
        return self._redis_available and JOB_QUEUE_MODE in ("redis", "hybrid")

    async def publish_job(self, job: JobDispatch) -> bool:
        """
        Publish a new job to the queue.

        Args:
            job: Job dispatch information

        Returns:
            True if published to Redis, False if database-only
        """
        if JOB_QUEUE_MODE == "database":
            return False

        redis = await get_redis()
        if not redis:
            return False

        try:
            stream_name = PRIORITY_STREAMS.get(job.priority, PRIORITY_STREAMS["normal"])
            await redis.xadd(
                stream_name,
                job.to_stream_dict(),
                maxlen=REDIS_STREAM_MAX_LEN,
            )
            logger.debug(f"Published job {job.job_id} to {stream_name}")
            return True
        except Exception as e:
            logger.warning(f"Failed to publish job to Redis: {e}")
            return False

    async def claim_job(self) -> Optional[JobDispatch]:
        """
        Claim a job from the Redis queue.

        Checks priority streams in order (high -> normal -> low).
        Also recovers abandoned messages from crashed workers.

        Returns:
            JobDispatch if a job was claimed, None if no jobs available
        """
        if not self.is_redis_enabled or not self._consumer_name:
            return None

        redis = await get_redis()
        if not redis:
            # Redis temporarily unavailable; RedisClient handles recovery via circuit breaker
            return None

        try:
            # First, try to recover abandoned messages from any priority
            recovered = await self._recover_abandoned_messages(redis)
            if recovered:
                return recovered

            # Check each priority stream in order
            for priority in STREAM_PRIORITIES:
                stream_name = PRIORITY_STREAMS[priority]
                job = await self._read_from_stream(redis, stream_name)
                if job:
                    return job

            return None

        except Exception as e:
            # Log warning but don't disable Redis; RedisClient handles recovery
            logger.warning(f"Redis claim failed: {e}")
            return None

    async def _recover_abandoned_messages(self, redis) -> Optional[JobDispatch]:
        """Check for and recover abandoned messages from crashed workers."""
        for priority in STREAM_PRIORITIES:
            stream_name = PRIORITY_STREAMS[priority]
            try:
                # Get pending messages that have been idle too long
                pending = await redis.xpending_range(
                    stream_name,
                    REDIS_CONSUMER_GROUP,
                    min="-",
                    max="+",
                    count=10,
                )

                for msg in pending:
                    idle_time = msg.get("time_since_delivered", 0)
                    if idle_time > REDIS_PENDING_TIMEOUT_MS:
                        # Claim this abandoned message
                        claimed = await redis.xclaim(
                            stream_name,
                            REDIS_CONSUMER_GROUP,
                            self._consumer_name,
                            REDIS_PENDING_TIMEOUT_MS,
                            [msg["message_id"]],
                        )
                        if claimed:
                            message_id, data = claimed[0]
                            logger.info(
                                f"Recovered abandoned job {data.get('job_id')} from {stream_name} (idle {idle_time}ms)"
                            )
                            return JobDispatch.from_stream_dict(data, message_id=message_id, stream_name=stream_name)
            except Exception as e:
                logger.debug(f"Error checking pending messages for {stream_name}: {e}")

        return None

    async def _read_from_stream(self, redis, stream_name: str) -> Optional[JobDispatch]:
        """Read a new message from a specific stream."""
        try:
            messages = await redis.xreadgroup(
                REDIS_CONSUMER_GROUP,
                self._consumer_name,
                {stream_name: ">"},
                count=1,
                block=REDIS_CONSUMER_BLOCK_MS,
            )

            if messages:
                # messages format: [[stream_name, [(message_id, data), ...]]]
                stream, msg_list = messages[0]
                if msg_list:
                    message_id, data = msg_list[0]
                    return JobDispatch.from_stream_dict(data, message_id=message_id, stream_name=stream_name)
        except Exception as e:
            logger.debug(f"Error reading from {stream_name}: {e}")

        return None

    async def acknowledge_job(self, job: JobDispatch) -> bool:
        """
        Acknowledge job completion to Redis.

        Args:
            job: The completed job

        Returns:
            True if acknowledged, False otherwise
        """
        if not job._message_id or not job._stream_name:
            return False

        redis = await get_redis()
        if not redis:
            return False

        try:
            await redis.xack(job._stream_name, REDIS_CONSUMER_GROUP, job._message_id)
            logger.debug(f"Acknowledged job {job.job_id}")
            return True
        except Exception as e:
            logger.warning(f"Failed to acknowledge job {job.job_id}: {e}")
            return False

    async def reject_job(self, job: JobDispatch, error: str) -> bool:
        """
        Reject a job and move it to dead letter stream.

        Args:
            job: The failed job
            error: Error message

        Returns:
            True if moved to DLQ, False otherwise
        """
        if not job._message_id or not job._stream_name:
            return False

        redis = await get_redis()
        if not redis:
            return False

        try:
            # Add to dead letter stream
            dlq_data = job.to_stream_dict()
            dlq_data["error"] = error[:500]
            dlq_data["failed_at"] = datetime.now(timezone.utc).isoformat()
            dlq_data["original_stream"] = job._stream_name

            await redis.xadd(DEAD_LETTER_STREAM, dlq_data, maxlen=1000)

            # Acknowledge original message
            await redis.xack(job._stream_name, REDIS_CONSUMER_GROUP, job._message_id)

            logger.info(f"Job {job.job_id} moved to dead letter queue: {error[:100]}")
            return True
        except Exception as e:
            logger.warning(f"Failed to move job {job.job_id} to DLQ: {e}")
            return False

    async def get_queue_stats(self) -> dict:
        """
        Get queue statistics.

        Returns:
            Dict with stream lengths and pending counts
        """
        redis = await get_redis()
        if not redis:
            return {"available": False}

        stats = {"available": True, "streams": {}}

        try:
            for priority, stream_name in PRIORITY_STREAMS.items():
                try:
                    length = await redis.xlen(stream_name)
                    pending_info = await redis.xpending(stream_name, REDIS_CONSUMER_GROUP)
                    stats["streams"][priority] = {
                        "length": length,
                        "pending": pending_info.get("pending", 0) if pending_info else 0,
                    }
                except Exception:
                    stats["streams"][priority] = {"length": 0, "pending": 0}

            # Dead letter queue
            try:
                dlq_length = await redis.xlen(DEAD_LETTER_STREAM)
                stats["dead_letter_queue"] = dlq_length
            except Exception:
                stats["dead_letter_queue"] = 0

        except Exception as e:
            logger.warning(f"Failed to get queue stats: {e}")

        return stats


# Global job queue instance for API use
_job_queue: Optional[JobQueue] = None
_job_queue_initialized: bool = False
_job_queue_init_lock: asyncio.Lock = asyncio.Lock()


async def get_job_queue() -> JobQueue:
    """Get or create the global job queue instance, initialized for API publishing."""
    global _job_queue, _job_queue_initialized
    async with _job_queue_init_lock:
        if _job_queue is None:
            _job_queue = JobQueue()
        if not _job_queue_initialized:
            # Initialize for API publishing (no consumer operations needed)
            await _job_queue.initialize(consumer_name="api-publisher")
            _job_queue_initialized = True
    return _job_queue
