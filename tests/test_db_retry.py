"""Tests for database retry functionality.

Tests cover both SQLite and PostgreSQL error patterns.
"""

import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from api.db_retry import (
    DatabaseLockedError,
    DatabaseRetryableError,
    DatabaseTimeoutError,
    execute_with_retry,
    fetch_one_with_retry,
    fetch_val_with_retry,
    is_database_locked_error,
    is_retryable_database_error,
    with_db_retry,
)


class TestIsRetryableDatabaseError:
    """Tests for is_retryable_database_error function (and its alias is_database_locked_error)."""

    # SQLite error patterns
    def test_sqlite_database_is_locked_message(self):
        """Should detect SQLite 'database is locked' message."""
        exc = sqlite3.OperationalError("database is locked")
        assert is_retryable_database_error(exc) is True
        assert is_database_locked_error(exc) is True  # Test alias

    def test_sqlite_database_table_is_locked_message(self):
        """Should detect SQLite 'database table is locked' message."""
        exc = sqlite3.OperationalError("database table is locked")
        assert is_retryable_database_error(exc) is True

    def test_sqlite_busy_message(self):
        """Should detect SQLite 'SQLITE_BUSY' message."""
        exc = Exception("SQLITE_BUSY: some other text")
        assert is_retryable_database_error(exc) is True

    def test_sqlite_locked_message(self):
        """Should detect SQLite 'SQLITE_LOCKED' message."""
        exc = Exception("Error: SQLITE_LOCKED")
        assert is_retryable_database_error(exc) is True

    # PostgreSQL error patterns
    def test_postgres_deadlock_detected(self):
        """Should detect PostgreSQL deadlock error."""
        exc = Exception("deadlock detected")
        assert is_retryable_database_error(exc) is True

    def test_postgres_serialization_failure(self):
        """Should detect PostgreSQL serialization failure."""
        exc = Exception("could not serialize access due to concurrent update")
        assert is_retryable_database_error(exc) is True

    def test_postgres_lock_timeout(self):
        """Should detect PostgreSQL lock timeout error."""
        exc = Exception("canceling statement due to lock timeout")
        assert is_retryable_database_error(exc) is True

    def test_postgres_could_not_obtain_lock(self):
        """Should detect PostgreSQL lock contention error."""
        exc = Exception("could not obtain lock on relation")
        assert is_retryable_database_error(exc) is True

    def test_postgres_connection_error(self):
        """Should detect PostgreSQL connection errors."""
        exc = Exception("server closed the connection unexpectedly")
        assert is_retryable_database_error(exc) is True

    # General tests
    def test_case_insensitive(self):
        """Should be case insensitive."""
        exc = Exception("DATABASE IS LOCKED")
        assert is_retryable_database_error(exc) is True

        exc2 = Exception("DEADLOCK DETECTED")
        assert is_retryable_database_error(exc2) is True

    def test_non_retryable_error(self):
        """Should return False for non-retryable errors."""
        exc = Exception("syntax error at or near")
        assert is_retryable_database_error(exc) is False

    def test_other_sqlite_error(self):
        """Should return False for other SQLite errors."""
        exc = sqlite3.OperationalError("no such table: users")
        assert is_retryable_database_error(exc) is False

    def test_backwards_compatible_alias(self):
        """DatabaseLockedError should be an alias for DatabaseRetryableError."""
        assert DatabaseLockedError is DatabaseRetryableError


class TestExecuteWithRetry:
    """Tests for execute_with_retry function."""

    @pytest.mark.asyncio
    async def test_success_on_first_try(self):
        """Should return result immediately on success."""
        mock_func = AsyncMock(return_value="success")

        result = await execute_with_retry(mock_func)

        assert result == "success"
        assert mock_func.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_database_locked_then_succeed(self):
        """Should retry on database locked error and return result on success."""
        mock_func = AsyncMock(
            side_effect=[
                sqlite3.OperationalError("database is locked"),
                sqlite3.OperationalError("database is locked"),
                "success",
            ]
        )

        with patch("api.db_retry.asyncio.sleep", new_callable=AsyncMock):
            result = await execute_with_retry(
                mock_func, max_retries=3, base_delay=0.01
            )

        assert result == "success"
        assert mock_func.call_count == 3

    @pytest.mark.asyncio
    async def test_exhaust_retries_raises_database_locked_error(self):
        """Should raise DatabaseLockedError after exhausting retries."""
        mock_func = AsyncMock(
            side_effect=sqlite3.OperationalError("database is locked")
        )

        with patch("api.db_retry.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(DatabaseLockedError) as exc_info:
                await execute_with_retry(
                    mock_func, max_retries=2, base_delay=0.01
                )

        assert "3 attempts" in str(exc_info.value)  # max_retries + 1
        assert mock_func.call_count == 3  # Initial + 2 retries

    @pytest.mark.asyncio
    async def test_non_locking_error_raised_immediately(self):
        """Should re-raise non-locking errors immediately without retry."""
        mock_func = AsyncMock(
            side_effect=ValueError("some other error")
        )

        with pytest.raises(ValueError) as exc_info:
            await execute_with_retry(mock_func, max_retries=5)

        assert "some other error" in str(exc_info.value)
        assert mock_func.call_count == 1  # No retries

    @pytest.mark.asyncio
    async def test_sqlite_operational_error_non_locking(self):
        """Should re-raise non-locking SQLite errors immediately."""
        mock_func = AsyncMock(
            side_effect=sqlite3.OperationalError("no such table: test")
        )

        with pytest.raises(sqlite3.OperationalError) as exc_info:
            await execute_with_retry(mock_func, max_retries=5)

        assert "no such table" in str(exc_info.value)
        assert mock_func.call_count == 1

    @pytest.mark.asyncio
    async def test_exponential_backoff(self):
        """Should use exponential backoff between retries."""
        mock_func = AsyncMock(
            side_effect=[
                sqlite3.OperationalError("database is locked"),
                sqlite3.OperationalError("database is locked"),
                "success",
            ]
        )
        sleep_mock = AsyncMock()

        with patch("api.db_retry.asyncio.sleep", sleep_mock):
            with patch("random.random", return_value=0.5):
                await execute_with_retry(
                    mock_func, max_retries=3, base_delay=0.1, max_delay=2.0
                )

        # First retry: base_delay * 2^0 = 0.1
        # Second retry: base_delay * 2^1 = 0.2
        assert sleep_mock.call_count == 2
        # With jitter at 0.5, jitter factor is 0 (2*0.5 - 1 = 0)
        assert sleep_mock.call_args_list[0][0][0] == pytest.approx(0.1, rel=0.01)
        assert sleep_mock.call_args_list[1][0][0] == pytest.approx(0.2, rel=0.01)

    @pytest.mark.asyncio
    async def test_max_delay_cap(self):
        """Should cap delay at max_delay."""
        mock_func = AsyncMock(
            side_effect=[
                sqlite3.OperationalError("database is locked"),
                sqlite3.OperationalError("database is locked"),
                sqlite3.OperationalError("database is locked"),
                sqlite3.OperationalError("database is locked"),
                "success",
            ]
        )
        sleep_mock = AsyncMock()

        with patch("api.db_retry.asyncio.sleep", sleep_mock):
            with patch("random.random", return_value=0.5):
                await execute_with_retry(
                    mock_func, max_retries=5, base_delay=1.0, max_delay=2.0
                )

        # Delays: 1.0, 2.0, 2.0 (capped), 2.0 (capped)
        assert sleep_mock.call_count == 4
        # All delays after the second should be capped at max_delay
        assert sleep_mock.call_args_list[2][0][0] == pytest.approx(2.0, rel=0.01)
        assert sleep_mock.call_args_list[3][0][0] == pytest.approx(2.0, rel=0.01)

    @pytest.mark.asyncio
    async def test_passes_args_and_kwargs(self):
        """Should pass arguments to the wrapped function."""
        mock_func = AsyncMock(return_value="result")

        result = await execute_with_retry(
            mock_func, "arg1", "arg2", kwarg1="value1"
        )

        assert result == "result"
        mock_func.assert_called_once_with("arg1", "arg2", kwarg1="value1")


class TestWithDbRetryDecorator:
    """Tests for with_db_retry decorator."""

    @pytest.mark.asyncio
    async def test_decorator_success(self):
        """Should work as decorator on success."""
        call_count = 0

        @with_db_retry(max_retries=3, base_delay=0.01)
        async def my_func():
            nonlocal call_count
            call_count += 1
            return "decorated_result"

        result = await my_func()

        assert result == "decorated_result"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_decorator_retry_and_succeed(self):
        """Should retry through decorator."""
        call_count = 0

        @with_db_retry(max_retries=3, base_delay=0.01)
        async def my_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise sqlite3.OperationalError("database is locked")
            return "success_after_retry"

        with patch("api.db_retry.asyncio.sleep", new_callable=AsyncMock):
            result = await my_func()

        assert result == "success_after_retry"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_decorator_exhausts_retries(self):
        """Should raise DatabaseLockedError through decorator."""

        @with_db_retry(max_retries=2, base_delay=0.01)
        async def my_func():
            raise sqlite3.OperationalError("database is locked")

        with patch("api.db_retry.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(DatabaseLockedError):
                await my_func()

    @pytest.mark.asyncio
    async def test_decorator_preserves_function_name(self):
        """Should preserve the original function name."""

        @with_db_retry()
        async def original_function_name():
            pass

        assert original_function_name.__name__ == "original_function_name"


class TestDatabaseLockedErrorExceptionHandler:
    """Tests for the DatabaseLockedError exception handler in APIs."""

    @pytest.mark.asyncio
    async def test_public_api_returns_503_on_database_locked(self):
        """Public API should return 503 with Retry-After header."""
        from httpx import ASGITransport, AsyncClient

        from api.public import app

        # Mock the database to raise DatabaseLockedError
        with patch(
            "api.public.fetch_all_with_retry",
            side_effect=DatabaseLockedError("Database locked"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/videos")

        assert response.status_code == 503
        assert "Retry-After" in response.headers
        assert response.headers["Retry-After"] == "1"
        assert "database" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_admin_api_returns_503_on_database_locked(self):
        """Admin API should return 503 with Retry-After header."""
        from httpx import ASGITransport, AsyncClient

        import api.admin
        from api.admin import app

        test_secret = "test-admin-secret-12345"

        # Mock the database to raise DatabaseLockedError and set admin secret
        # Patch ADMIN_API_SECRET in api.admin module directly (it's cached at import)
        with (
            patch(
                "api.admin.fetch_all_with_retry",
                side_effect=DatabaseLockedError("Database locked"),
            ),
            patch.object(api.admin, "ADMIN_API_SECRET", test_secret),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get(
                    "/api/videos",
                    headers={"X-Admin-Secret": test_secret},
                )

        assert response.status_code == 503
        assert "Retry-After" in response.headers
        assert response.headers["Retry-After"] == "1"


class TestFetchValWithRetry:
    """Tests for fetch_val_with_retry function."""

    @pytest.mark.asyncio
    async def test_success_on_first_try(self):
        """Should return result immediately on success."""
        mock_db = AsyncMock()
        mock_db.fetch_val = AsyncMock(return_value=42)

        with patch("api.database.database", mock_db):
            result = await fetch_val_with_retry("SELECT COUNT(*)")

        assert result == 42
        assert mock_db.fetch_val.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_database_error_then_succeed(self):
        """Should retry on database error and return result on success."""
        mock_db = AsyncMock()
        mock_db.fetch_val = AsyncMock(
            side_effect=[
                sqlite3.OperationalError("database is locked"),
                sqlite3.OperationalError("database is locked"),
                100,
            ]
        )

        with patch("api.database.database", mock_db):
            with patch("api.db_retry.asyncio.sleep", new_callable=AsyncMock):
                result = await fetch_val_with_retry(
                    "SELECT COUNT(*)", max_retries=3, base_delay=0.01
                )

        assert result == 100
        assert mock_db.fetch_val.call_count == 3

    @pytest.mark.asyncio
    async def test_exhaust_retries_raises_error(self):
        """Should raise DatabaseRetryableError after exhausting retries."""
        mock_db = AsyncMock()
        mock_db.fetch_val = AsyncMock(
            side_effect=sqlite3.OperationalError("database is locked")
        )

        with patch("api.database.database", mock_db):
            with patch("api.db_retry.asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(DatabaseRetryableError) as exc_info:
                    await fetch_val_with_retry(
                        "SELECT COUNT(*)", max_retries=2, base_delay=0.01
                    )

        assert "3 attempts" in str(exc_info.value)
        assert mock_db.fetch_val.call_count == 3

    @pytest.mark.asyncio
    async def test_returns_none_for_null_value(self):
        """Should properly return None for NULL database values."""
        mock_db = AsyncMock()
        mock_db.fetch_val = AsyncMock(return_value=None)

        with patch("api.database.database", mock_db):
            result = await fetch_val_with_retry("SELECT NULL")

        assert result is None


class TestDatabaseTimeoutError:
    """Tests for database timeout functionality (Issue #549)."""

    @pytest.mark.asyncio
    async def test_query_timeout_raises_database_timeout_error(self):
        """Should raise DatabaseTimeoutError when query times out."""
        import asyncio

        mock_db = AsyncMock()
        # Simulate a query that takes longer than timeout
        async def slow_query(*args, **kwargs):
            await asyncio.sleep(10)  # Sleep longer than timeout
            return "result"

        mock_db.fetch_one = slow_query

        with patch("api.database.database", mock_db):
            with pytest.raises(DatabaseTimeoutError) as exc_info:
                await fetch_one_with_retry("SELECT 1", timeout=0.01)

        assert "timed out" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_timeout_error_not_retried_for_fetch(self):
        """DatabaseTimeoutError from fetch should not be retried (not a retryable error)."""
        import asyncio

        call_count = 0
        mock_db = AsyncMock()

        async def slow_query(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(10)  # Sleep longer than timeout
            return "result"

        mock_db.fetch_one = slow_query

        with patch("api.database.database", mock_db):
            with pytest.raises(DatabaseTimeoutError):
                await fetch_one_with_retry(
                    "SELECT 1",
                    timeout=0.01,
                    max_retries=5,  # Would retry 5 times if timeout was retryable
                )

        # Should only be called once - DatabaseTimeoutError is not retryable
        assert call_count == 1


class TestTotalDeadline:
    """Tests for total_deadline parameter (Issue #549 review feedback)."""

    @pytest.mark.asyncio
    async def test_total_deadline_stops_retries(self):
        """Should stop retries when total_deadline is exceeded."""
        call_count = 0
        mock_time = 0.0

        async def failing_func():
            nonlocal call_count
            call_count += 1
            raise sqlite3.OperationalError("database is locked")

        def mock_monotonic():
            nonlocal mock_time
            # Simulate time advancing by 0.05s on each call
            mock_time += 0.05
            return mock_time

        with patch("api.db_retry.asyncio.sleep", new_callable=AsyncMock):
            with patch("api.db_retry.time.monotonic", mock_monotonic):
                with pytest.raises(DatabaseTimeoutError) as exc_info:
                    await execute_with_retry(
                        failing_func,
                        max_retries=100,  # High retry count
                        total_deadline=0.1,  # But short deadline (2 iterations)
                        base_delay=0.01,
                    )

        assert "deadline" in str(exc_info.value).lower()
        # Should have stopped due to deadline after ~2-3 attempts
        assert call_count < 10

    @pytest.mark.asyncio
    async def test_total_deadline_none_allows_full_retries(self):
        """Should allow all retries when total_deadline is None."""
        call_count = 0

        async def failing_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise sqlite3.OperationalError("database is locked")
            return "success"

        with patch("api.db_retry.asyncio.sleep", new_callable=AsyncMock):
            result = await execute_with_retry(
                failing_then_succeed,
                max_retries=5,
                total_deadline=None,  # No deadline
                base_delay=0.01,
            )

        assert result == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_success_before_deadline(self):
        """Should succeed normally if within deadline."""
        call_count = 0

        async def succeed_on_second():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise sqlite3.OperationalError("database is locked")
            return "success"

        with patch("api.db_retry.asyncio.sleep", new_callable=AsyncMock):
            result = await execute_with_retry(
                succeed_on_second,
                max_retries=5,
                total_deadline=60.0,  # Long deadline
                base_delay=0.01,
            )

        assert result == "success"
        assert call_count == 2
