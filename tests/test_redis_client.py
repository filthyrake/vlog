"""Tests for Redis client with connection pooling and circuit breaker.

Tests cover:
- Circuit breaker behavior (opens after 3 consecutive failures)
- Exponential backoff on reconnection
- Graceful fallback when Redis unavailable
- Health check caching
- Singleton pattern with reset capability
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError


class TestRedisClientCircuitBreaker:
    """Tests for circuit breaker behavior."""

    @pytest.fixture
    def redis_client_instance(self):
        """Create a fresh RedisClient instance for testing."""
        from api.redis_client import RedisClient

        client = RedisClient()
        client._client = MagicMock()
        client._healthy = True
        return client

    @pytest.fixture
    def mock_redis_url(self):
        """Mock REDIS_URL to be non-empty for is_available tests."""
        with patch("api.redis_client.REDIS_URL", "redis://localhost:6379"):
            yield

    def test_circuit_opens_after_three_failures(self, redis_client_instance):
        """Circuit breaker should open after 3 consecutive failures."""
        client = redis_client_instance

        # Record 3 failures
        client._record_failure()
        assert client._circuit_open is False
        assert client._consecutive_failures == 1

        client._record_failure()
        assert client._circuit_open is False
        assert client._consecutive_failures == 2

        client._record_failure()
        assert client._circuit_open is True
        assert client._consecutive_failures == 3
        assert client._circuit_open_until is not None

    def test_circuit_breaker_exponential_backoff(self, redis_client_instance):
        """Circuit breaker backoff should increase exponentially."""
        client = redis_client_instance

        # First 3 failures open the circuit with 30s backoff
        for _ in range(3):
            client._record_failure()

        first_backoff = client._circuit_open_until
        assert first_backoff is not None

        # 4th failure should double the backoff to 60s
        client._record_failure()
        second_backoff = client._circuit_open_until
        assert second_backoff > first_backoff

        # 5th failure should double again to 120s
        client._record_failure()
        third_backoff = client._circuit_open_until
        assert third_backoff > second_backoff

    def test_circuit_breaker_backoff_capped_at_300s(self, redis_client_instance):
        """Circuit breaker backoff should be capped at ~300 seconds (with jitter)."""
        client = redis_client_instance

        # Record many failures to hit the cap
        for _ in range(10):
            client._record_failure()

        # Calculate expected backoff (base capped at 300, with ±20% jitter = max 360s)
        now = datetime.now(timezone.utc)
        # Max backoff is 300s base + 20% jitter = 360s, plus timing tolerance
        max_expected = now + timedelta(seconds=360 + 1)
        # Min backoff is 30s (enforced minimum in _record_failure)
        min_expected = now + timedelta(seconds=30 - 1)
        assert client._circuit_open_until <= max_expected
        assert client._circuit_open_until >= min_expected

    def test_success_resets_circuit_breaker(self, redis_client_instance):
        """Successful operation should reset circuit breaker."""
        client = redis_client_instance

        # Open the circuit
        for _ in range(3):
            client._record_failure()
        assert client._circuit_open is True
        assert client._consecutive_failures == 3

        # Record success
        client._record_success()
        assert client._circuit_open is False
        assert client._consecutive_failures == 0
        assert client._circuit_open_until is None
        assert client._healthy is True

    def test_is_available_respects_circuit_breaker(self, redis_client_instance, mock_redis_url):
        """is_available should return False when circuit is open."""
        client = redis_client_instance

        # Initially available
        assert client.is_available is True

        # Open the circuit
        for _ in range(3):
            client._record_failure()

        assert client.is_available is False

    def test_circuit_breaker_closes_after_timeout(self, redis_client_instance, mock_redis_url):
        """Circuit should close after the timeout period (but client remains unhealthy until success)."""
        client = redis_client_instance

        # Open the circuit
        for _ in range(3):
            client._record_failure()
        assert client._circuit_open is True
        assert client._healthy is False

        # Set the timeout to the past
        client._circuit_open_until = datetime.now(timezone.utc) - timedelta(seconds=1)

        # Calling is_available should close the circuit
        # But it will still return False because _healthy is False (no successful operation yet)
        assert client.is_available is False
        # The circuit itself should be closed (allowing retries)
        assert client._circuit_open is False

        # After a successful operation, client becomes available
        client._record_success()
        assert client.is_available is True
        assert client._healthy is True


class TestRedisClientExecuteWithFallback:
    """Tests for execute_with_fallback method."""

    @pytest.fixture
    def redis_client_instance(self):
        """Create a fresh RedisClient instance for testing."""
        from api.redis_client import RedisClient

        client = RedisClient()
        client._client = AsyncMock()
        client._healthy = True
        return client

    @pytest.fixture
    def mock_redis_url(self):
        """Mock REDIS_URL to be non-empty for is_available tests."""
        with patch("api.redis_client.REDIS_URL", "redis://localhost:6379"):
            yield

    @pytest.mark.asyncio
    async def test_executes_redis_function_when_available(
        self, redis_client_instance, mock_redis_url
    ):
        """Should execute Redis function when Redis is available."""
        client = redis_client_instance

        async def redis_fn(redis_client, *args, **kwargs):
            return "redis_result"

        async def fallback_fn(*args, **kwargs):
            return "fallback_result"

        result = await client.execute_with_fallback(redis_fn, fallback_fn)
        assert result == "redis_result"
        assert client._consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_uses_fallback_when_redis_unavailable(
        self, redis_client_instance
    ):
        """Should use fallback when Redis is not available."""
        client = redis_client_instance
        client._healthy = False

        async def redis_fn(redis_client, *args, **kwargs):
            return "redis_result"

        async def fallback_fn(*args, **kwargs):
            return "fallback_result"

        result = await client.execute_with_fallback(redis_fn, fallback_fn)
        assert result == "fallback_result"

    @pytest.mark.asyncio
    async def test_uses_fallback_on_redis_error(self, redis_client_instance, mock_redis_url):
        """Should use fallback when Redis operation raises an error."""
        client = redis_client_instance

        async def redis_fn(redis_client, *args, **kwargs):
            raise RedisError("Connection failed")

        async def fallback_fn(*args, **kwargs):
            return "fallback_result"

        result = await client.execute_with_fallback(redis_fn, fallback_fn)
        assert result == "fallback_result"
        assert client._consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_passes_args_to_functions(self, redis_client_instance, mock_redis_url):
        """Should pass args and kwargs to both functions."""
        client = redis_client_instance

        received_args = {}

        async def redis_fn(redis_client, arg1, kwarg1=None):
            received_args["arg1"] = arg1
            received_args["kwarg1"] = kwarg1
            return "result"

        async def fallback_fn(arg1, kwarg1=None):
            pass

        await client.execute_with_fallback(
            redis_fn, fallback_fn, "test_arg", kwarg1="test_kwarg"
        )

        assert received_args["arg1"] == "test_arg"
        assert received_args["kwarg1"] == "test_kwarg"


class TestRedisClientHealthCheck:
    """Tests for health check functionality."""

    @pytest.fixture
    def redis_client_instance(self):
        """Create a fresh RedisClient instance for testing."""
        from api.redis_client import RedisClient

        client = RedisClient()
        client._client = AsyncMock()
        client._healthy = True
        return client

    @pytest.mark.asyncio
    async def test_health_check_success(self, redis_client_instance):
        """Health check should return True on successful ping."""
        client = redis_client_instance
        client._client.ping = AsyncMock(return_value=True)
        client._last_health_check = None

        result = await client.health_check()

        assert result is True
        client._client.ping.assert_called_once()
        assert client._healthy is True
        assert client._last_health_check is not None

    @pytest.mark.asyncio
    async def test_health_check_failure(self, redis_client_instance):
        """Health check should return False and record failure on error."""
        client = redis_client_instance
        client._client.ping = AsyncMock(side_effect=RedisConnectionError("Failed"))
        client._last_health_check = None

        result = await client.health_check()

        assert result is False
        assert client._consecutive_failures == 1
        assert client._healthy is False

    @pytest.mark.asyncio
    async def test_health_check_skips_if_recent(self, redis_client_instance):
        """Health check should skip if checked recently."""
        client = redis_client_instance
        client._client.ping = AsyncMock(return_value=True)
        # Set last check to now
        client._last_health_check = datetime.now(timezone.utc)

        result = await client.health_check()

        # Should return cached healthy state without calling ping
        assert result is True
        client._client.ping.assert_not_called()

    @pytest.mark.asyncio
    async def test_health_check_returns_false_without_client(
        self, redis_client_instance
    ):
        """Health check should return False if no client."""
        client = redis_client_instance
        client._client = None

        result = await client.health_check()

        assert result is False


class TestRedisClientSingleton:
    """Tests for singleton pattern."""

    @pytest.mark.asyncio
    async def test_get_instance_returns_same_instance(self):
        """get_instance should return the same instance."""
        from api.redis_client import RedisClient

        # Reset first to ensure clean state
        await RedisClient.reset_instance()

        with patch.object(RedisClient, "_initialize", new_callable=AsyncMock):
            instance1 = await RedisClient.get_instance()
            instance2 = await RedisClient.get_instance()

        assert instance1 is instance2

        # Clean up
        await RedisClient.reset_instance()

    @pytest.mark.asyncio
    async def test_reset_instance_clears_singleton(self):
        """reset_instance should clear the singleton."""
        from api.redis_client import RedisClient

        with patch.object(RedisClient, "_initialize", new_callable=AsyncMock):
            instance1 = await RedisClient.get_instance()

        await RedisClient.reset_instance()

        with patch.object(RedisClient, "_initialize", new_callable=AsyncMock):
            instance2 = await RedisClient.get_instance()

        assert instance1 is not instance2

        # Clean up
        await RedisClient.reset_instance()


class TestRedisClientInitialization:
    """Tests for client initialization."""

    @pytest.mark.asyncio
    async def test_initialization_without_redis_url(self):
        """Should not create pool when REDIS_URL is not configured."""
        from api.redis_client import RedisClient

        await RedisClient.reset_instance()

        with patch("api.redis_client.REDIS_URL", ""):
            instance = RedisClient()
            await instance._initialize()

        assert instance._pool is None
        assert instance._client is None
        assert instance._healthy is False

        await RedisClient.reset_instance()

    @pytest.mark.asyncio
    async def test_initialization_handles_connection_failure(self):
        """Should handle connection failure during initialization."""
        from api.redis_client import RedisClient

        await RedisClient.reset_instance()

        with patch("api.redis_client.REDIS_URL", "redis://localhost:6379"):
            with patch(
                "api.redis_client.ConnectionPool.from_url",
                side_effect=RedisConnectionError("Connection refused"),
            ):
                instance = RedisClient()
                await instance._initialize()

        assert instance._healthy is False

        await RedisClient.reset_instance()


class TestConvenienceFunctions:
    """Tests for module-level convenience functions."""

    @pytest.mark.asyncio
    async def test_get_redis_returns_client(self):
        """get_redis should return Redis client when available."""
        from api.redis_client import RedisClient, get_redis

        mock_client = AsyncMock()

        await RedisClient.reset_instance()

        with patch("api.redis_client.REDIS_URL", "redis://localhost:6379"):
            with patch.object(RedisClient, "_initialize", new_callable=AsyncMock):
                instance = await RedisClient.get_instance()
                instance._client = mock_client
                instance._healthy = True

                result = await get_redis()

        assert result is mock_client

        await RedisClient.reset_instance()

    @pytest.mark.asyncio
    async def test_get_redis_returns_none_when_unavailable(self):
        """get_redis should return None when Redis is unavailable."""
        from api.redis_client import RedisClient, get_redis

        await RedisClient.reset_instance()

        with patch.object(RedisClient, "_initialize", new_callable=AsyncMock):
            instance = await RedisClient.get_instance()
            instance._healthy = False

            result = await get_redis()

        assert result is None

        await RedisClient.reset_instance()

    @pytest.mark.asyncio
    async def test_is_redis_available(self):
        """is_redis_available should return correct availability status."""
        from api.redis_client import RedisClient, is_redis_available

        await RedisClient.reset_instance()

        with patch("api.redis_client.REDIS_URL", "redis://localhost:6379"):
            with patch.object(RedisClient, "_initialize", new_callable=AsyncMock):
                instance = await RedisClient.get_instance()
                instance._client = AsyncMock()
                instance._healthy = True

                result = await is_redis_available()

        assert result is True

        await RedisClient.reset_instance()


class TestRedisClientClose:
    """Tests for client close functionality."""

    @pytest.mark.asyncio
    async def test_close_cleans_up_resources(self):
        """close should clean up client and pool."""
        from api.redis_client import RedisClient

        client = RedisClient()
        mock_redis = AsyncMock()
        mock_pool = AsyncMock()
        client._client = mock_redis
        client._pool = mock_pool
        client._healthy = True

        await client.close()

        mock_redis.close.assert_called_once()
        mock_pool.disconnect.assert_called_once()
        assert client._client is None
        assert client._pool is None
        assert client._healthy is False

    @pytest.mark.asyncio
    async def test_close_handles_errors_gracefully(self):
        """close should handle errors without raising."""
        from api.redis_client import RedisClient

        client = RedisClient()
        mock_redis = AsyncMock()
        mock_redis.close = AsyncMock(side_effect=Exception("Close failed"))
        mock_pool = AsyncMock()
        mock_pool.disconnect = AsyncMock(side_effect=Exception("Disconnect failed"))
        client._client = mock_redis
        client._pool = mock_pool

        # Should not raise
        await client.close()

        assert client._client is None
        assert client._pool is None


class TestRedisClientIsConfigured:
    """Tests for is_configured property."""

    def test_is_configured_with_url(self):
        """is_configured should return True when REDIS_URL is set."""
        from api.redis_client import RedisClient

        with patch("api.redis_client.REDIS_URL", "redis://localhost:6379"):
            client = RedisClient()
            # Need to check the property directly with the mock in place
            assert client.is_configured is True

    def test_is_configured_without_url(self):
        """is_configured should return False when REDIS_URL is empty."""
        from api.redis_client import RedisClient

        with patch("api.redis_client.REDIS_URL", ""):
            client = RedisClient()
            assert client.is_configured is False
