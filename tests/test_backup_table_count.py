"""Backup metadata must work without command-line PostgreSQL clients."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from backup.database import PostgreSQLBackupHandler
from backup.exceptions import BackupError


@pytest.fixture
def table_connection(monkeypatch):
    connection = Mock()
    connection.fetchval = AsyncMock(return_value=56)
    connection.close = AsyncMock()
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr("backup.database.asyncpg.connect", connect)
    # A minimal runtime has no psql; any regression to subprocess use fails.
    monkeypatch.setattr(
        "backup.database.asyncio.create_subprocess_exec",
        Mock(side_effect=AssertionError("psql is unavailable")),
    )
    return connection, connect


async def test_nonzero_count_uses_driver_and_closes_connection(table_connection):
    connection, connect = table_connection
    dsn = "postgresql://user:encoded%40password@localhost/vlog?sslmode=require"

    assert await PostgreSQLBackupHandler(dsn).get_table_count() == 56

    connect.assert_awaited_once_with(dsn, timeout=10)
    query = connection.fetchval.await_args
    assert "table_schema = 'public'" in query.args[0]
    assert query.kwargs["timeout"] == 10
    connection.close.assert_awaited_once_with(timeout=5)
    connection.terminate.assert_not_called()


async def test_empty_database_is_a_valid_zero(table_connection):
    connection, _ = table_connection
    connection.fetchval.return_value = 0
    assert await PostgreSQLBackupHandler("postgresql://localhost/vlog").get_table_count() == 0


@pytest.mark.parametrize("stage", ["connect", "query"])
@pytest.mark.parametrize("error", [OSError("private-credential"), asyncio.TimeoutError()])
async def test_failure_is_reported_and_any_connection_is_closed(table_connection, stage, error):
    connection, connect = table_connection
    failing_call = connect if stage == "connect" else connection.fetchval
    failing_call.side_effect = error

    with pytest.raises(BackupError, match="Failed to count PostgreSQL tables") as caught:
        await PostgreSQLBackupHandler("postgresql://localhost/vlog").get_table_count()

    assert "private-credential" not in str(caught.value)
    assert caught.value.__suppress_context__
    if stage == "connect":
        connection.close.assert_not_awaited()
    else:
        connection.close.assert_awaited_once_with(timeout=5)


@pytest.mark.parametrize("error", [asyncio.TimeoutError(), OSError("close failed")])
async def test_close_failure_forces_termination(table_connection, error):
    connection, _ = table_connection
    connection.close.side_effect = error

    assert await PostgreSQLBackupHandler("postgresql://localhost/vlog").get_table_count() == 56
    connection.close.assert_awaited_once_with(timeout=5)
    connection.terminate.assert_called_once_with()


@pytest.mark.parametrize("stage", ["query", "close"])
async def test_cancellation_propagates_after_cleanup(table_connection, stage):
    connection, _ = table_connection
    operation = connection.fetchval if stage == "query" else connection.close
    operation.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await PostgreSQLBackupHandler("postgresql://localhost/vlog").get_table_count()

    connection.close.assert_awaited_once_with(timeout=5)
    if stage == "close":
        connection.terminate.assert_called_once_with()
