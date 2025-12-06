"""Tests for database retry functionality."""

import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from api.db_retry import (
    DatabaseLockedError,
    execute_with_retry,
    is_database_locked_error,
    with_db_retry,
)


class TestIsDatabaseLockedError:
    """Tests for is_database_locked_error function."""

    def test_database_is_locked_message(self):
        """Should detect 'database is locked' message."""
        exc = sqlite3.OperationalError("database is locked")
        assert is_database_locked_error(exc) is True

    def test_database_table_is_locked_message(self):
        """Should detect 'database table is locked' message."""
        exc = sqlite3.OperationalError("database table is locked")
        assert is_database_locked_error(exc) is True

    def test_sqlite_busy_message(self):
        """Should detect 'SQLITE_BUSY' message."""
        exc = Exception("SQLITE_BUSY: some other text")
        assert is_database_locked_error(exc) is True

    def test_sqlite_locked_message(self):
        """Should detect 'SQLITE_LOCKED' message."""
        exc = Exception("Error: SQLITE_LOCKED")
        assert is_database_locked_error(exc) is True

    def test_case_insensitive(self):
        """Should be case insensitive."""
        exc = Exception("DATABASE IS LOCKED")
        assert is_database_locked_error(exc) is True

    def test_non_locking_error(self):
        """Should return False for non-locking errors."""
        exc = Exception("connection refused")
        assert is_database_locked_error(exc) is False

    def test_other_sqlite_error(self):
        """Should return False for other SQLite errors."""
        exc = sqlite3.OperationalError("no such table: users")
        assert is_database_locked_error(exc) is False


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

        from api.admin import app

        # Mock the database to raise DatabaseLockedError
        with patch(
            "api.admin.fetch_all_with_retry",
            side_effect=DatabaseLockedError("Database locked"),
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/api/videos")

        assert response.status_code == 503
        assert "Retry-After" in response.headers
        assert response.headers["Retry-After"] == "1"
