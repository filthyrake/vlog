"""Tests for Redis Streams job queue.

Tests cover:
- JobDispatch dataclass serialization/deserialization
- Priority queue ordering (high > normal > low)
- Job publishing to streams
- Job claiming and acknowledgment
- Dead letter queue for failed jobs
- Recovery of abandoned messages
- Queue statistics
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from api.job_queue import (
    DEAD_LETTER_STREAM,
    PRIORITY_STREAMS,
    STREAM_PRIORITIES,
    JobDispatch,
    JobQueue,
    get_job_queue,
)


class TestJobDispatch:
    """Tests for JobDispatch dataclass."""

    def test_to_stream_dict_basic(self):
        """Should convert to stream dict with string values."""
        job = JobDispatch(
            job_id=1,
            video_id=100,
            video_slug="test-video",
            priority="normal",
        )

        result = job.to_stream_dict()

        assert result["job_id"] == "1"
        assert result["video_id"] == "100"
        assert result["video_slug"] == "test-video"
        assert result["priority"] == "normal"
        assert result["source_filename"] == ""
        assert result["source_width"] == "0"
        assert result["source_height"] == "0"
        assert result["duration"] == "0"
        assert "created_at" in result

    def test_to_stream_dict_with_all_fields(self):
        """Should include all optional fields when provided."""
        now = datetime.now(timezone.utc)
        job = JobDispatch(
            job_id=1,
            video_id=100,
            video_slug="test-video",
            source_filename="video.mp4",
            source_width=1920,
            source_height=1080,
            duration=120.5,
            priority="high",
            created_at=now,
        )

        result = job.to_stream_dict()

        assert result["source_filename"] == "video.mp4"
        assert result["source_width"] == "1920"
        assert result["source_height"] == "1080"
        assert result["duration"] == "120.5"
        assert result["priority"] == "high"
        assert result["created_at"] == now.isoformat()

    def test_from_stream_dict_basic(self):
        """Should create JobDispatch from stream dict."""
        data = {
            "job_id": "1",
            "video_id": "100",
            "video_slug": "test-video",
            "priority": "normal",
        }

        job = JobDispatch.from_stream_dict(data)

        assert job.job_id == 1
        assert job.video_id == 100
        assert job.video_slug == "test-video"
        assert job.priority == "normal"

    def test_from_stream_dict_with_message_id(self):
        """Should store message_id and stream_name for acknowledgment."""
        data = {
            "job_id": "1",
            "video_id": "100",
            "video_slug": "test-video",
        }

        job = JobDispatch.from_stream_dict(
            data, message_id="1234-0", stream_name="vlog:jobs:normal"
        )

        assert job._message_id == "1234-0"
        assert job._stream_name == "vlog:jobs:normal"

    def test_from_stream_dict_with_all_fields(self):
        """Should parse all optional fields."""
        now = datetime.now(timezone.utc)
        data = {
            "job_id": "1",
            "video_id": "100",
            "video_slug": "test-video",
            "source_filename": "video.mp4",
            "source_width": "1920",
            "source_height": "1080",
            "duration": "120.5",
            "priority": "high",
            "created_at": now.isoformat(),
        }

        job = JobDispatch.from_stream_dict(data)

        assert job.source_filename == "video.mp4"
        assert job.source_width == 1920
        assert job.source_height == 1080
        assert job.duration == 120.5
        assert job.priority == "high"
        assert job.created_at == now

    def test_from_stream_dict_handles_invalid_created_at(self):
        """Should handle invalid created_at gracefully."""
        data = {
            "job_id": "1",
            "video_id": "100",
            "video_slug": "test-video",
            "created_at": "invalid-date",
        }

        job = JobDispatch.from_stream_dict(data)

        assert job.created_at is None

    def test_from_stream_dict_handles_zero_values(self):
        """Should convert zero values to None for optional fields."""
        data = {
            "job_id": "1",
            "video_id": "100",
            "video_slug": "test-video",
            "source_width": "0",
            "source_height": "0",
            "duration": "0",
        }

        job = JobDispatch.from_stream_dict(data)

        assert job.source_width is None
        assert job.source_height is None
        assert job.duration is None


class TestJobQueueInitialization:
    """Tests for JobQueue initialization."""

    @pytest.mark.asyncio
    async def test_initialize_in_database_mode(self):
        """Should skip Redis initialization in database mode."""
        queue = JobQueue()

        with patch("api.job_queue.JOB_QUEUE_MODE", "database"):
            await queue.initialize("test-consumer")

        assert queue._redis_available is False
        assert queue._consumer_name == "test-consumer"

    @pytest.mark.asyncio
    async def test_initialize_creates_consumer_groups(self):
        """Should create consumer groups for all priority streams."""
        queue = JobQueue()
        mock_redis = AsyncMock()
        mock_redis.xgroup_create = AsyncMock()

        with patch("api.job_queue.JOB_QUEUE_MODE", "redis"):
            with patch("api.job_queue.get_redis", return_value=mock_redis):
                await queue.initialize("test-consumer")

        # Should create groups for high, normal, and low priority streams
        assert mock_redis.xgroup_create.call_count == 3
        assert queue._redis_available is True
        assert queue._initialized is True

    @pytest.mark.asyncio
    async def test_initialize_handles_existing_consumer_group(self):
        """Should ignore BUSYGROUP error for existing groups."""
        queue = JobQueue()
        mock_redis = AsyncMock()
        mock_redis.xgroup_create = AsyncMock(
            side_effect=Exception("BUSYGROUP Consumer Group name already exists")
        )

        with patch("api.job_queue.JOB_QUEUE_MODE", "redis"):
            with patch("api.job_queue.get_redis", return_value=mock_redis):
                await queue.initialize("test-consumer")

        # Should not raise, and should mark as available
        assert queue._redis_available is True

    @pytest.mark.asyncio
    async def test_initialize_handles_redis_unavailable_in_hybrid_mode(self):
        """Should fall back gracefully when Redis unavailable in hybrid mode."""
        queue = JobQueue()

        with patch("api.job_queue.JOB_QUEUE_MODE", "hybrid"):
            with patch("api.job_queue.get_redis", return_value=None):
                await queue.initialize("test-consumer")

        assert queue._redis_available is False

    @pytest.mark.asyncio
    async def test_initialize_warns_in_redis_only_mode(self):
        """Should warn when Redis unavailable in redis-only mode."""
        queue = JobQueue()

        with patch("api.job_queue.JOB_QUEUE_MODE", "redis"):
            with patch("api.job_queue.get_redis", return_value=None):
                await queue.initialize("test-consumer")

        assert queue._redis_available is False


class TestJobQueuePublish:
    """Tests for job publishing."""

    @pytest.mark.asyncio
    async def test_publish_job_to_correct_priority_stream(self):
        """Should publish job to the correct priority stream."""
        queue = JobQueue()
        queue._redis_available = True
        mock_redis = AsyncMock()

        job = JobDispatch(
            job_id=1,
            video_id=100,
            video_slug="test-video",
            priority="high",
        )

        with patch("api.job_queue.JOB_QUEUE_MODE", "redis"):
            with patch("api.job_queue.get_redis", return_value=mock_redis):
                result = await queue.publish_job(job)

        assert result is True
        mock_redis.xadd.assert_called_once()
        # Check it was published to the high priority stream
        call_args = mock_redis.xadd.call_args
        assert "high" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_publish_job_returns_false_in_database_mode(self):
        """Should return False when in database-only mode."""
        queue = JobQueue()

        job = JobDispatch(job_id=1, video_id=100, video_slug="test-video")

        with patch("api.job_queue.JOB_QUEUE_MODE", "database"):
            result = await queue.publish_job(job)

        assert result is False

    @pytest.mark.asyncio
    async def test_publish_job_handles_redis_error(self):
        """Should return False on Redis error."""
        queue = JobQueue()
        queue._redis_available = True
        mock_redis = AsyncMock()
        mock_redis.xadd = AsyncMock(side_effect=Exception("Connection failed"))

        job = JobDispatch(job_id=1, video_id=100, video_slug="test-video")

        with patch("api.job_queue.JOB_QUEUE_MODE", "redis"):
            with patch("api.job_queue.get_redis", return_value=mock_redis):
                result = await queue.publish_job(job)

        assert result is False

    @pytest.mark.asyncio
    async def test_publish_job_uses_normal_priority_for_unknown(self):
        """Should default to normal priority stream for unknown priority."""
        queue = JobQueue()
        queue._redis_available = True
        mock_redis = AsyncMock()

        job = JobDispatch(
            job_id=1,
            video_id=100,
            video_slug="test-video",
            priority="unknown",
        )

        with patch("api.job_queue.JOB_QUEUE_MODE", "redis"):
            with patch("api.job_queue.get_redis", return_value=mock_redis):
                await queue.publish_job(job)

        call_args = mock_redis.xadd.call_args
        assert "normal" in call_args[0][0]


class TestJobQueueClaim:
    """Tests for job claiming."""

    @pytest.mark.asyncio
    async def test_claim_job_returns_none_when_not_enabled(self):
        """Should return None when Redis is not enabled."""
        queue = JobQueue()
        queue._redis_available = False

        result = await queue.claim_job()

        assert result is None

    @pytest.mark.asyncio
    async def test_claim_job_returns_none_without_consumer_name(self):
        """Should return None when consumer name not set."""
        queue = JobQueue()
        queue._redis_available = True
        queue._consumer_name = None

        with patch("api.job_queue.JOB_QUEUE_MODE", "redis"):
            result = await queue.claim_job()

        assert result is None

    @pytest.mark.asyncio
    async def test_claim_job_checks_priority_order(self):
        """Should check streams in priority order: high -> normal -> low."""
        queue = JobQueue()
        queue._redis_available = True
        queue._consumer_name = "test-consumer"
        mock_redis = AsyncMock()
        mock_redis.xpending_range = AsyncMock(return_value=[])
        mock_redis.xreadgroup = AsyncMock(return_value=None)

        with patch("api.job_queue.JOB_QUEUE_MODE", "redis"):
            with patch("api.job_queue.get_redis", return_value=mock_redis):
                await queue.claim_job()

        # Should have checked all three streams in order
        assert mock_redis.xreadgroup.call_count == 3
        calls = mock_redis.xreadgroup.call_args_list
        streams_checked = [list(call[0][2].keys())[0] for call in calls]
        assert "high" in streams_checked[0]
        assert "normal" in streams_checked[1]
        assert "low" in streams_checked[2]

    @pytest.mark.asyncio
    async def test_claim_job_returns_job_from_stream(self):
        """Should return JobDispatch when job is available."""
        queue = JobQueue()
        queue._redis_available = True
        queue._consumer_name = "test-consumer"
        mock_redis = AsyncMock()
        mock_redis.xpending_range = AsyncMock(return_value=[])
        mock_redis.xreadgroup = AsyncMock(
            return_value=[
                [
                    "vlog:jobs:normal",
                    [
                        (
                            "1234-0",
                            {
                                "job_id": "1",
                                "video_id": "100",
                                "video_slug": "test-video",
                            },
                        )
                    ],
                ]
            ]
        )

        with patch("api.job_queue.JOB_QUEUE_MODE", "redis"):
            with patch("api.job_queue.get_redis", return_value=mock_redis):
                result = await queue.claim_job()

        assert result is not None
        assert result.job_id == 1
        assert result.video_id == 100
        assert result._message_id == "1234-0"


class TestJobQueueRecoverAbandoned:
    """Tests for recovering abandoned messages."""

    @pytest.mark.asyncio
    async def test_recover_abandoned_claims_idle_message(self):
        """Should claim messages that have been idle too long."""
        queue = JobQueue()
        queue._redis_available = True
        queue._consumer_name = "test-consumer"
        mock_redis = AsyncMock()
        mock_redis.xpending_range = AsyncMock(
            return_value=[
                {
                    "message_id": "1234-0",
                    "time_since_delivered": 999999,  # Very old
                }
            ]
        )
        mock_redis.xclaim = AsyncMock(
            return_value=[
                (
                    "1234-0",
                    {
                        "job_id": "1",
                        "video_id": "100",
                        "video_slug": "test-video",
                    },
                )
            ]
        )

        result = await queue._recover_abandoned_messages(mock_redis)

        assert result is not None
        assert result.job_id == 1
        mock_redis.xclaim.assert_called_once()

    @pytest.mark.asyncio
    async def test_recover_abandoned_ignores_recent_messages(self):
        """Should not claim messages that haven't been idle long enough."""
        queue = JobQueue()
        queue._redis_available = True
        queue._consumer_name = "test-consumer"
        mock_redis = AsyncMock()
        mock_redis.xpending_range = AsyncMock(
            return_value=[
                {
                    "message_id": "1234-0",
                    "time_since_delivered": 100,  # Recent
                }
            ]
        )

        with patch("api.job_queue.REDIS_PENDING_TIMEOUT_MS", 60000):
            result = await queue._recover_abandoned_messages(mock_redis)

        assert result is None
        mock_redis.xclaim.assert_not_called()


class TestJobQueueAcknowledge:
    """Tests for job acknowledgment."""

    @pytest.mark.asyncio
    async def test_acknowledge_job_success(self):
        """Should acknowledge job to Redis."""
        queue = JobQueue()
        mock_redis = AsyncMock()

        job = JobDispatch(job_id=1, video_id=100, video_slug="test-video")
        job._message_id = "1234-0"
        job._stream_name = "vlog:jobs:normal"

        with patch("api.job_queue.get_redis", return_value=mock_redis):
            result = await queue.acknowledge_job(job)

        assert result is True
        mock_redis.xack.assert_called_once()

    @pytest.mark.asyncio
    async def test_acknowledge_job_without_message_id(self):
        """Should return False if job has no message ID."""
        queue = JobQueue()

        job = JobDispatch(job_id=1, video_id=100, video_slug="test-video")
        # No _message_id set

        result = await queue.acknowledge_job(job)

        assert result is False

    @pytest.mark.asyncio
    async def test_acknowledge_job_handles_error(self):
        """Should return False on Redis error."""
        queue = JobQueue()
        mock_redis = AsyncMock()
        mock_redis.xack = AsyncMock(side_effect=Exception("Failed"))

        job = JobDispatch(job_id=1, video_id=100, video_slug="test-video")
        job._message_id = "1234-0"
        job._stream_name = "vlog:jobs:normal"

        with patch("api.job_queue.get_redis", return_value=mock_redis):
            result = await queue.acknowledge_job(job)

        assert result is False


class TestJobQueueReject:
    """Tests for job rejection and dead letter queue."""

    @pytest.mark.asyncio
    async def test_reject_job_moves_to_dlq(self):
        """Should move failed job to dead letter queue."""
        queue = JobQueue()
        mock_redis = AsyncMock()

        job = JobDispatch(job_id=1, video_id=100, video_slug="test-video")
        job._message_id = "1234-0"
        job._stream_name = "vlog:jobs:normal"

        with patch("api.job_queue.get_redis", return_value=mock_redis):
            result = await queue.reject_job(job, "Transcoding failed")

        assert result is True
        # Should add to DLQ
        mock_redis.xadd.assert_called_once()
        call_args = mock_redis.xadd.call_args
        assert DEAD_LETTER_STREAM in str(call_args)
        # Should acknowledge original message
        mock_redis.xack.assert_called_once()

    @pytest.mark.asyncio
    async def test_reject_job_truncates_long_error(self):
        """Should truncate error message to 500 characters."""
        queue = JobQueue()
        mock_redis = AsyncMock()

        job = JobDispatch(job_id=1, video_id=100, video_slug="test-video")
        job._message_id = "1234-0"
        job._stream_name = "vlog:jobs:normal"
        long_error = "x" * 1000

        with patch("api.job_queue.get_redis", return_value=mock_redis):
            await queue.reject_job(job, long_error)

        call_args = mock_redis.xadd.call_args
        dlq_data = call_args[0][1]
        assert len(dlq_data["error"]) == 500

    @pytest.mark.asyncio
    async def test_reject_job_without_message_id(self):
        """Should return False if job has no message ID."""
        queue = JobQueue()

        job = JobDispatch(job_id=1, video_id=100, video_slug="test-video")

        result = await queue.reject_job(job, "Error")

        assert result is False


class TestJobQueueStats:
    """Tests for queue statistics."""

    @pytest.mark.asyncio
    async def test_get_queue_stats_returns_stream_info(self):
        """Should return statistics for all streams."""
        queue = JobQueue()
        mock_redis = AsyncMock()
        mock_redis.xlen = AsyncMock(return_value=5)
        mock_redis.xpending = AsyncMock(return_value={"pending": 2})

        with patch("api.job_queue.get_redis", return_value=mock_redis):
            stats = await queue.get_queue_stats()

        assert stats["available"] is True
        assert "streams" in stats
        assert "high" in stats["streams"]
        assert "normal" in stats["streams"]
        assert "low" in stats["streams"]
        assert stats["streams"]["high"]["length"] == 5
        assert stats["streams"]["high"]["pending"] == 2

    @pytest.mark.asyncio
    async def test_get_queue_stats_without_redis(self):
        """Should return unavailable status when Redis is down."""
        queue = JobQueue()

        with patch("api.job_queue.get_redis", return_value=None):
            stats = await queue.get_queue_stats()

        assert stats["available"] is False

    @pytest.mark.asyncio
    async def test_get_queue_stats_includes_dlq(self):
        """Should include dead letter queue length."""
        queue = JobQueue()
        mock_redis = AsyncMock()
        mock_redis.xlen = AsyncMock(return_value=10)
        mock_redis.xpending = AsyncMock(return_value={"pending": 0})

        with patch("api.job_queue.get_redis", return_value=mock_redis):
            stats = await queue.get_queue_stats()

        assert "dead_letter_queue" in stats
        assert stats["dead_letter_queue"] == 10


class TestIsRedisEnabled:
    """Tests for is_redis_enabled property."""

    def test_is_redis_enabled_when_available_and_mode_redis(self):
        """Should return True when Redis available and mode is redis."""
        queue = JobQueue()
        queue._redis_available = True

        with patch("api.job_queue.JOB_QUEUE_MODE", "redis"):
            assert queue.is_redis_enabled is True

    def test_is_redis_enabled_when_available_and_mode_hybrid(self):
        """Should return True when Redis available and mode is hybrid."""
        queue = JobQueue()
        queue._redis_available = True

        with patch("api.job_queue.JOB_QUEUE_MODE", "hybrid"):
            assert queue.is_redis_enabled is True

    def test_is_redis_enabled_when_mode_database(self):
        """Should return False when mode is database."""
        queue = JobQueue()
        queue._redis_available = True

        with patch("api.job_queue.JOB_QUEUE_MODE", "database"):
            assert queue.is_redis_enabled is False

    def test_is_redis_enabled_when_not_available(self):
        """Should return False when Redis not available."""
        queue = JobQueue()
        queue._redis_available = False

        with patch("api.job_queue.JOB_QUEUE_MODE", "redis"):
            assert queue.is_redis_enabled is False


class TestGlobalJobQueue:
    """Tests for global job queue instance."""

    @pytest.mark.asyncio
    async def test_get_job_queue_returns_singleton(self):
        """get_job_queue should return the same instance."""
        # Reset global state
        import api.job_queue

        api.job_queue._job_queue = None
        api.job_queue._job_queue_initialized = False

        with patch("api.job_queue.JOB_QUEUE_MODE", "database"):
            queue1 = await get_job_queue()
            queue2 = await get_job_queue()

        assert queue1 is queue2

        # Clean up
        api.job_queue._job_queue = None
        api.job_queue._job_queue_initialized = False


class TestPriorityConstants:
    """Tests for priority stream constants."""

    def test_priority_streams_defined(self):
        """Should have streams for all priority levels."""
        assert "high" in PRIORITY_STREAMS
        assert "normal" in PRIORITY_STREAMS
        assert "low" in PRIORITY_STREAMS

    def test_stream_priorities_order(self):
        """Should check high priority first."""
        assert STREAM_PRIORITIES[0] == "high"
        assert STREAM_PRIORITIES[1] == "normal"
        assert STREAM_PRIORITIES[2] == "low"

    def test_dead_letter_stream_defined(self):
        """Should have a dead letter stream."""
        assert "dead-letter" in DEAD_LETTER_STREAM
