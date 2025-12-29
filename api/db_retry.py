"""
Database retry utilities for handling transient database errors.

This module provides retry logic with exponential backoff to handle transient
errors gracefully, supporting both SQLite and PostgreSQL backends:

SQLite errors:
- "database is locked" - concurrent write contention
- "SQLITE_BUSY" / "SQLITE_LOCKED" - database busy states

PostgreSQL errors:
- Deadlocks (40P01)
- Serialization failures (40001)
- Connection errors
- "could not obtain lock" - lock contention
"""

import asyncio
import functools
import logging
import time
from typing import Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

# Slow query threshold in seconds (Issue #429)
SLOW_QUERY_THRESHOLD = 1.0

# Type variable for return type preservation
T = TypeVar("T")

# Default retry configuration
DEFAULT_MAX_RETRIES = 5
DEFAULT_BASE_DELAY = 0.1  # 100ms
DEFAULT_MAX_DELAY = 2.0  # 2 seconds
DEFAULT_EXPONENTIAL_BASE = 2


class DatabaseRetryableError(Exception):
    """Raised when a database operation fails after all retries exhausted."""

    pass


# Keep the old name as an alias for backwards compatibility
DatabaseLockedError = DatabaseRetryableError


def is_retryable_database_error(exc: Exception) -> bool:
    """
    Check if an exception is a retryable database error.

    Supports both SQLite and PostgreSQL error patterns.
    """
    error_str = str(exc).lower()

    # SQLite error patterns
    sqlite_patterns = [
        "database is locked",
        "database table is locked",
        "sqlite_busy",
        "sqlite_locked",
    ]

    # PostgreSQL error patterns
    postgres_patterns = [
        "deadlock detected",  # 40P01
        "could not serialize access",  # 40001 serialization failure
        "could not obtain lock",  # Lock contention
        "connection refused",  # Transient connection error
        "connection reset",  # Connection dropped
        "server closed the connection unexpectedly",
        "canceling statement due to lock timeout",
        "lock timeout",
    ]

    # Check for SQLite errors
    for pattern in sqlite_patterns:
        if pattern in error_str:
            return True

    # Check for PostgreSQL errors
    for pattern in postgres_patterns:
        if pattern in error_str:
            return True

    # Check for PostgreSQL error codes in exception attributes
    # asyncpg and psycopg2 may expose these
    if hasattr(exc, "sqlstate"):
        sqlstate = getattr(exc, "sqlstate", "")
        # 40P01 = deadlock, 40001 = serialization failure
        if sqlstate in ("40P01", "40001"):
            return True

    # Check wrapped exceptions (databases library wraps underlying driver exceptions)
    if hasattr(exc, "__cause__") and exc.__cause__ is not None:
        return is_retryable_database_error(exc.__cause__)

    return False


# Keep the old function name as an alias for backwards compatibility
is_database_locked_error = is_retryable_database_error


async def execute_with_retry(
    func: Callable,
    *args,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    **kwargs,
) -> T:
    """
    Execute an async function with retry logic for transient database errors.

    Uses exponential backoff with jitter to reduce contention.

    Args:
        func: Async function to execute
        *args: Positional arguments for func
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay between retries (seconds)
        max_delay: Maximum delay between retries (seconds)
        **kwargs: Keyword arguments for func

    Returns:
        Result of the function

    Raises:
        DatabaseRetryableError: If all retries are exhausted
        Other exceptions: Non-retryable errors are re-raised immediately
    """
    import random

    last_exception: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            if not is_retryable_database_error(e):
                raise

            last_exception = e

            if attempt < max_retries:
                # Calculate delay with exponential backoff and jitter
                delay = min(
                    base_delay * (DEFAULT_EXPONENTIAL_BASE**attempt),
                    max_delay,
                )
                # Add jitter (±25%) to prevent thundering herd
                jitter = delay * 0.25 * (2 * random.random() - 1)
                delay = max(0.01, delay + jitter)

                logger.warning(
                    f"Database error (attempt {attempt + 1}/{max_retries + 1}), retrying in {delay:.2f}s: {e}"
                )
                await asyncio.sleep(delay)
            else:
                logger.error(f"Database error after {max_retries + 1} attempts, giving up: {e}")

    raise DatabaseRetryableError(f"Database operation failed after {max_retries + 1} attempts: {last_exception}")


def with_db_retry(
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
):
    """
    Decorator to add database retry logic to async functions.

    Usage:
        @with_db_retry()
        async def my_database_operation():
            ...

        @with_db_retry(max_retries=10)
        async def critical_operation():
            ...
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            return await execute_with_retry(
                func,
                *args,
                max_retries=max_retries,
                base_delay=base_delay,
                max_delay=max_delay,
                **kwargs,
            )

        return wrapper

    return decorator


# =============================================================================
# Database Operation Wrappers
# =============================================================================


async def fetch_one_with_retry(
    query,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
):
    """
    Execute a fetch_one query with retry logic for transient database errors.

    Args:
        query: SQLAlchemy query to execute
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay between retries (seconds)
        max_delay: Maximum delay between retries (seconds)

    Returns:
        The query result (single row or None)

    Raises:
        DatabaseRetryableError: If all retries are exhausted
    """
    from api.database import database

    async def _fetch():
        start_time = time.monotonic()
        result = await database.fetch_one(query)
        elapsed = time.monotonic() - start_time
        if elapsed >= SLOW_QUERY_THRESHOLD:
            # Log slow query with truncated SQL for debugging (Issue #429)
            query_str = str(query)[:500]
            logger.warning(f"Slow query ({elapsed:.2f}s): {query_str}")
        return result

    return await execute_with_retry(
        _fetch,
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
    )


async def fetch_all_with_retry(
    query,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
):
    """
    Execute a fetch_all query with retry logic for transient database errors.

    Args:
        query: SQLAlchemy query to execute
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay between retries (seconds)
        max_delay: Maximum delay between retries (seconds)

    Returns:
        The query result (list of rows)

    Raises:
        DatabaseRetryableError: If all retries are exhausted
    """
    from api.database import database

    async def _fetch():
        start_time = time.monotonic()
        result = await database.fetch_all(query)
        elapsed = time.monotonic() - start_time
        if elapsed >= SLOW_QUERY_THRESHOLD:
            # Log slow query with truncated SQL for debugging (Issue #429)
            query_str = str(query)[:500]
            logger.warning(f"Slow query ({elapsed:.2f}s): {query_str}")
        return result

    return await execute_with_retry(
        _fetch,
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
    )


async def fetch_val_with_retry(
    query,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
):
    """
    Execute a fetch_val query with retry logic for transient database errors.

    Args:
        query: SQLAlchemy query to execute
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay between retries (seconds)
        max_delay: Maximum delay between retries (seconds)

    Returns:
        The query result (single scalar value or None)

    Raises:
        DatabaseRetryableError: If all retries are exhausted
    """
    from api.database import database

    async def _fetch():
        start_time = time.monotonic()
        result = await database.fetch_val(query)
        elapsed = time.monotonic() - start_time
        if elapsed >= SLOW_QUERY_THRESHOLD:
            # Log slow query with truncated SQL for debugging (Issue #429)
            query_str = str(query)[:500]
            logger.warning(f"Slow query ({elapsed:.2f}s): {query_str}")
        return result

    return await execute_with_retry(
        _fetch,
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
    )


async def db_execute_with_retry(
    query,
    values=None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
):
    """
    Execute a database write query with retry logic for transient database errors.

    Args:
        query: SQLAlchemy query to execute
        values: Optional values dict for the query
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay between retries (seconds)
        max_delay: Maximum delay between retries (seconds)

    Returns:
        The query result (typically row ID for inserts)

    Raises:
        DatabaseRetryableError: If all retries are exhausted
    """
    from api.database import database

    async def _execute():
        start_time = time.monotonic()
        if values is not None:
            result = await database.execute(query, values)
        else:
            result = await database.execute(query)
        elapsed = time.monotonic() - start_time
        if elapsed >= SLOW_QUERY_THRESHOLD:
            # Log slow query with truncated SQL for debugging (Issue #429)
            query_str = str(query)[:500]
            logger.warning(f"Slow query ({elapsed:.2f}s): {query_str}")
        return result

    return await execute_with_retry(
        _execute,
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
    )
