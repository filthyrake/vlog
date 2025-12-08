"""Tests for Redis Pub/Sub functionality.

Tests cover:
- Channel name generation
- Publishing progress updates
- Publishing worker status
- Publishing job completion/failure notifications
- Subscribing to channels and patterns
- Listening for messages
- Cleanup and resource management
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.pubsub import (
    Publisher,
    Subscriber,
    channel_name,
    subscribe_to_progress,
    subscribe_to_workers,
)


class TestChannelName:
    """Tests for channel_name helper function."""

    def test_channel_name_without_entity(self):
        """Should create channel name without entity ID."""
        with patch("api.pubsub.REDIS_PUBSUB_PREFIX", "vlog"):
            result = channel_name("workers")

        assert result == "vlog:workers"

    def test_channel_name_with_entity(self):
        """Should create channel name with entity ID."""
        with patch("api.pubsub.REDIS_PUBSUB_PREFIX", "vlog"):
            result = channel_name("progress", "123")

        assert result == "vlog:progress:123"

    def test_channel_name_with_string_entity(self):
        """Should handle string entity IDs."""
        with patch("api.pubsub.REDIS_PUBSUB_PREFIX", "vlog"):
            result = channel_name("workers", "status")

        assert result == "vlog:workers:status"


class TestPublisherProgress:
    """Tests for Publisher.publish_progress method."""

    @pytest.mark.asyncio
    async def test_publish_progress_success(self):
        """Should publish progress to both video-specific and global channels."""
        mock_redis = AsyncMock()

        with patch("api.pubsub.get_redis", return_value=mock_redis):
            result = await Publisher.publish_progress(
                video_id=100,
                job_id=1,
                current_step="transcode",
                progress_percent=50,
            )

        assert result is True
        # Should publish to two channels: video-specific and global
        assert mock_redis.publish.call_count == 2

    @pytest.mark.asyncio
    async def test_publish_progress_includes_all_fields(self):
        """Should include all fields in the message."""
        mock_redis = AsyncMock()
        captured_messages = []

        async def capture_publish(channel, payload):
            captured_messages.append((channel, json.loads(payload)))

        mock_redis.publish = capture_publish

        with patch("api.pubsub.get_redis", return_value=mock_redis):
            await Publisher.publish_progress(
                video_id=100,
                job_id=1,
                current_step="transcode",
                progress_percent=50,
                qualities=[{"quality": "1080p", "progress": 75}],
                status="processing",
                last_error=None,
            )

        # Check the message content
        _, message = captured_messages[0]
        assert message["type"] == "progress"
        assert message["video_id"] == 100
        assert message["job_id"] == 1
        assert message["current_step"] == "transcode"
        assert message["progress_percent"] == 50
        assert message["qualities"] == [{"quality": "1080p", "progress": 75}]
        assert message["status"] == "processing"
        assert "timestamp" in message

    @pytest.mark.asyncio
    async def test_publish_progress_returns_false_without_redis(self):
        """Should return False when Redis is unavailable."""
        with patch("api.pubsub.get_redis", return_value=None):
            result = await Publisher.publish_progress(
                video_id=100,
                job_id=1,
                current_step="transcode",
                progress_percent=50,
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_publish_progress_handles_error(self):
        """Should return False on Redis error."""
        mock_redis = AsyncMock()
        mock_redis.publish = AsyncMock(side_effect=Exception("Connection failed"))

        with patch("api.pubsub.get_redis", return_value=mock_redis):
            result = await Publisher.publish_progress(
                video_id=100,
                job_id=1,
                current_step="transcode",
                progress_percent=50,
            )

        assert result is False


class TestPublisherWorkerStatus:
    """Tests for Publisher.publish_worker_status method."""

    @pytest.mark.asyncio
    async def test_publish_worker_status_success(self):
        """Should publish worker status to workers channel."""
        mock_redis = AsyncMock()

        with patch("api.pubsub.get_redis", return_value=mock_redis):
            result = await Publisher.publish_worker_status(
                worker_id="abc123",
                worker_name="worker-1",
                status="busy",
            )

        assert result is True
        mock_redis.publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_publish_worker_status_includes_all_fields(self):
        """Should include all fields in the message."""
        mock_redis = AsyncMock()
        captured_message = {}

        async def capture_publish(channel, payload):
            captured_message.update(json.loads(payload))

        mock_redis.publish = capture_publish

        with patch("api.pubsub.get_redis", return_value=mock_redis):
            await Publisher.publish_worker_status(
                worker_id="abc123",
                worker_name="worker-1",
                status="busy",
                current_job_id=1,
                current_video_slug="test-video",
                hwaccel_type="nvidia",
                progress_percent=50,
                current_step="transcode",
            )

        assert captured_message["type"] == "worker_status"
        assert captured_message["worker_id"] == "abc123"
        assert captured_message["worker_name"] == "worker-1"
        assert captured_message["status"] == "busy"
        assert captured_message["current_job_id"] == 1
        assert captured_message["current_video_slug"] == "test-video"
        assert captured_message["hwaccel_type"] == "nvidia"
        assert captured_message["progress_percent"] == 50
        assert captured_message["current_step"] == "transcode"
        assert "timestamp" in captured_message

    @pytest.mark.asyncio
    async def test_publish_worker_status_returns_false_without_redis(self):
        """Should return False when Redis is unavailable."""
        with patch("api.pubsub.get_redis", return_value=None):
            result = await Publisher.publish_worker_status(
                worker_id="abc123",
                worker_name="worker-1",
                status="idle",
            )

        assert result is False


class TestPublisherJobCompleted:
    """Tests for Publisher.publish_job_completed method."""

    @pytest.mark.asyncio
    async def test_publish_job_completed_success(self):
        """Should publish to completed and progress channels."""
        mock_redis = AsyncMock()

        with patch("api.pubsub.get_redis", return_value=mock_redis):
            result = await Publisher.publish_job_completed(
                job_id=1,
                video_id=100,
                video_slug="test-video",
                worker_id="abc123",
                worker_name="worker-1",
                qualities=[{"quality": "1080p"}, {"quality": "720p"}],
                duration_seconds=120.5,
            )

        assert result is True
        # Should publish to 3 channels: completed, video-specific progress, global progress
        assert mock_redis.publish.call_count == 3

    @pytest.mark.asyncio
    async def test_publish_job_completed_includes_all_fields(self):
        """Should include all fields in the message."""
        mock_redis = AsyncMock()
        captured_message = {}

        async def capture_publish(channel, payload):
            captured_message.update(json.loads(payload))

        mock_redis.publish = capture_publish

        with patch("api.pubsub.get_redis", return_value=mock_redis):
            await Publisher.publish_job_completed(
                job_id=1,
                video_id=100,
                video_slug="test-video",
                worker_id="abc123",
                worker_name="worker-1",
                qualities=[{"quality": "1080p"}],
                duration_seconds=120.5,
            )

        assert captured_message["type"] == "job_completed"
        assert captured_message["job_id"] == 1
        assert captured_message["video_id"] == 100
        assert captured_message["video_slug"] == "test-video"
        assert captured_message["worker_id"] == "abc123"
        assert captured_message["duration_seconds"] == 120.5


class TestPublisherJobFailed:
    """Tests for Publisher.publish_job_failed method."""

    @pytest.mark.asyncio
    async def test_publish_job_failed_success(self):
        """Should publish to failed and progress channels."""
        mock_redis = AsyncMock()

        with patch("api.pubsub.get_redis", return_value=mock_redis):
            result = await Publisher.publish_job_failed(
                job_id=1,
                video_id=100,
                video_slug="test-video",
                worker_id="abc123",
                worker_name="worker-1",
                error="Transcoding failed",
                will_retry=True,
            )

        assert result is True
        # Should publish to 3 channels
        assert mock_redis.publish.call_count == 3

    @pytest.mark.asyncio
    async def test_publish_job_failed_truncates_long_error(self):
        """Should truncate error to 200 characters."""
        mock_redis = AsyncMock()
        captured_message = {}

        async def capture_publish(channel, payload):
            captured_message.update(json.loads(payload))

        mock_redis.publish = capture_publish
        long_error = "x" * 500

        with patch("api.pubsub.get_redis", return_value=mock_redis):
            await Publisher.publish_job_failed(
                job_id=1,
                video_id=100,
                video_slug="test-video",
                worker_id="abc123",
                worker_name="worker-1",
                error=long_error,
                will_retry=False,
            )

        assert len(captured_message["error"]) == 200

    @pytest.mark.asyncio
    async def test_publish_job_failed_includes_retry_info(self):
        """Should include retry attempt information."""
        mock_redis = AsyncMock()
        captured_message = {}

        async def capture_publish(channel, payload):
            captured_message.update(json.loads(payload))

        mock_redis.publish = capture_publish

        with patch("api.pubsub.get_redis", return_value=mock_redis):
            await Publisher.publish_job_failed(
                job_id=1,
                video_id=100,
                video_slug="test-video",
                worker_id="abc123",
                worker_name="worker-1",
                error="Failed",
                will_retry=True,
                attempt=2,
                max_attempts=3,
            )

        assert captured_message["will_retry"] is True
        assert captured_message["attempt"] == 2
        assert captured_message["max_attempts"] == 3


class TestSubscriber:
    """Tests for Subscriber class."""

    @pytest.mark.asyncio
    async def test_subscribe_to_channels(self):
        """Should subscribe to specified channels."""
        subscriber = Subscriber()
        mock_redis = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)

        with patch("api.pubsub.get_redis", return_value=mock_redis):
            result = await subscriber.subscribe("channel1", "channel2")

        assert result is True
        mock_pubsub.subscribe.assert_called_once_with("channel1", "channel2")
        assert "channel1" in subscriber._subscribed_channels
        assert "channel2" in subscriber._subscribed_channels

    @pytest.mark.asyncio
    async def test_subscribe_returns_false_without_redis(self):
        """Should return False when Redis is unavailable."""
        subscriber = Subscriber()

        with patch("api.pubsub.get_redis", return_value=None):
            result = await subscriber.subscribe("channel1")

        assert result is False

    @pytest.mark.asyncio
    async def test_subscribe_pattern(self):
        """Should subscribe to channel patterns."""
        subscriber = Subscriber()
        mock_redis = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)

        with patch("api.pubsub.get_redis", return_value=mock_redis):
            result = await subscriber.subscribe_pattern("vlog:progress:*")

        assert result is True
        mock_pubsub.psubscribe.assert_called_once_with("vlog:progress:*")
        assert "vlog:progress:*" in subscriber._subscribed_patterns

    @pytest.mark.asyncio
    async def test_subscribe_pattern_returns_false_without_redis(self):
        """Should return False when Redis is unavailable."""
        subscriber = Subscriber()

        with patch("api.pubsub.get_redis", return_value=None):
            result = await subscriber.subscribe_pattern("pattern:*")

        assert result is False

    @pytest.mark.asyncio
    async def test_close_cleans_up(self):
        """Should unsubscribe and close resources."""
        subscriber = Subscriber()
        mock_pubsub = AsyncMock()
        subscriber._pubsub = mock_pubsub
        subscriber._subscribed_channels = {"channel1", "channel2"}
        subscriber._subscribed_patterns = {"pattern:*"}

        await subscriber.close()

        # Check unsubscribe was called with the right channels (order doesn't matter)
        mock_pubsub.unsubscribe.assert_called_once()
        call_args = set(mock_pubsub.unsubscribe.call_args[0])
        assert call_args == {"channel1", "channel2"}
        mock_pubsub.punsubscribe.assert_called_once_with("pattern:*")
        mock_pubsub.close.assert_called_once()
        assert subscriber._pubsub is None
        assert len(subscriber._subscribed_channels) == 0
        assert len(subscriber._subscribed_patterns) == 0

    @pytest.mark.asyncio
    async def test_close_handles_errors(self):
        """Should handle errors during close gracefully."""
        subscriber = Subscriber()
        mock_pubsub = AsyncMock()
        mock_pubsub.unsubscribe = AsyncMock(side_effect=Exception("Failed"))
        subscriber._pubsub = mock_pubsub
        subscriber._subscribed_channels = {"channel1"}

        # Should not raise
        await subscriber.close()

        assert subscriber._pubsub is None

    def test_is_active_when_subscribed(self):
        """Should return True when subscribed to channels."""
        subscriber = Subscriber()
        subscriber._pubsub = MagicMock()
        subscriber._subscribed_channels = {"channel1"}

        assert subscriber.is_active is True

    def test_is_active_when_pattern_subscribed(self):
        """Should return True when subscribed to patterns."""
        subscriber = Subscriber()
        subscriber._pubsub = MagicMock()
        subscriber._subscribed_patterns = {"pattern:*"}

        assert subscriber.is_active is True

    def test_is_active_without_subscriptions(self):
        """Should return False when no subscriptions."""
        subscriber = Subscriber()
        subscriber._pubsub = MagicMock()
        subscriber._subscribed_channels = set()
        subscriber._subscribed_patterns = set()

        assert subscriber.is_active is False

    def test_is_active_without_pubsub(self):
        """Should return False when no pubsub object."""
        subscriber = Subscriber()
        subscriber._pubsub = None

        assert subscriber.is_active is False


class TestSubscriberListen:
    """Tests for Subscriber.listen method."""

    @pytest.mark.asyncio
    async def test_listen_yields_messages(self):
        """Should yield parsed messages from pubsub."""
        subscriber = Subscriber()
        mock_pubsub = AsyncMock()

        async def mock_listen():
            yield {"type": "message", "channel": "test", "data": '{"key": "value"}'}

        mock_pubsub.listen = mock_listen
        subscriber._pubsub = mock_pubsub

        messages = []
        async for msg in subscriber.listen():
            messages.append(msg)

        assert len(messages) == 1
        assert messages[0]["key"] == "value"
        assert messages[0]["channel"] == "test"

    @pytest.mark.asyncio
    async def test_listen_skips_subscription_confirmations(self):
        """Should skip subscribe/unsubscribe confirmation messages."""
        subscriber = Subscriber()
        mock_pubsub = AsyncMock()

        async def mock_listen():
            yield {"type": "subscribe", "channel": "test", "data": 1}
            yield {"type": "message", "channel": "test", "data": '{"key": "value"}'}
            yield {"type": "unsubscribe", "channel": "test", "data": 0}

        mock_pubsub.listen = mock_listen
        subscriber._pubsub = mock_pubsub

        messages = []
        async for msg in subscriber.listen():
            messages.append(msg)

        assert len(messages) == 1
        assert messages[0]["key"] == "value"

    @pytest.mark.asyncio
    async def test_listen_handles_pmessage(self):
        """Should handle pattern match messages."""
        subscriber = Subscriber()
        mock_pubsub = AsyncMock()

        async def mock_listen():
            yield {
                "type": "pmessage",
                "channel": "vlog:progress:123",
                "pattern": "vlog:progress:*",
                "data": '{"video_id": 123}',
            }

        mock_pubsub.listen = mock_listen
        subscriber._pubsub = mock_pubsub

        messages = []
        async for msg in subscriber.listen():
            messages.append(msg)

        assert len(messages) == 1
        assert messages[0]["video_id"] == 123
        assert messages[0]["channel"] == "vlog:progress:123"
        assert messages[0]["pattern"] == "vlog:progress:*"

    @pytest.mark.asyncio
    async def test_listen_skips_invalid_json(self):
        """Should skip messages with invalid JSON."""
        subscriber = Subscriber()
        mock_pubsub = AsyncMock()

        async def mock_listen():
            yield {"type": "message", "channel": "test", "data": "not json"}
            yield {"type": "message", "channel": "test", "data": '{"valid": true}'}

        mock_pubsub.listen = mock_listen
        subscriber._pubsub = mock_pubsub

        messages = []
        async for msg in subscriber.listen():
            messages.append(msg)

        assert len(messages) == 1
        assert messages[0]["valid"] is True

    @pytest.mark.asyncio
    async def test_listen_returns_early_without_pubsub(self):
        """Should return immediately if no pubsub."""
        subscriber = Subscriber()
        subscriber._pubsub = None

        messages = []
        async for msg in subscriber.listen():
            messages.append(msg)

        assert len(messages) == 0


class TestSubscribeToProgress:
    """Tests for subscribe_to_progress helper function."""

    @pytest.mark.asyncio
    async def test_subscribe_to_specific_videos(self):
        """Should subscribe to specific video progress channels."""
        mock_redis = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)

        with patch("api.pubsub.get_redis", return_value=mock_redis):
            with patch("api.pubsub.REDIS_PUBSUB_PREFIX", "vlog"):
                await subscribe_to_progress(video_ids=[1, 2, 3])

        mock_pubsub.subscribe.assert_called_once()
        call_args = mock_pubsub.subscribe.call_args[0]
        assert "vlog:progress:1" in call_args
        assert "vlog:progress:2" in call_args
        assert "vlog:progress:3" in call_args

    @pytest.mark.asyncio
    async def test_subscribe_to_all_progress(self):
        """Should subscribe to global progress channel when no IDs specified."""
        mock_redis = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)

        with patch("api.pubsub.get_redis", return_value=mock_redis):
            with patch("api.pubsub.REDIS_PUBSUB_PREFIX", "vlog"):
                await subscribe_to_progress()

        mock_pubsub.subscribe.assert_called_once_with("vlog:progress:all")


class TestSubscribeToWorkers:
    """Tests for subscribe_to_workers helper function."""

    @pytest.mark.asyncio
    async def test_subscribe_to_workers(self):
        """Should subscribe to worker and job channels."""
        mock_redis = AsyncMock()
        mock_pubsub = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)

        with patch("api.pubsub.get_redis", return_value=mock_redis):
            with patch("api.pubsub.REDIS_PUBSUB_PREFIX", "vlog"):
                await subscribe_to_workers()

        mock_pubsub.subscribe.assert_called_once()
        call_args = mock_pubsub.subscribe.call_args[0]
        assert "vlog:workers:status" in call_args
        assert "vlog:jobs:completed" in call_args
        assert "vlog:jobs:failed" in call_args
        assert "vlog:progress:all" in call_args
