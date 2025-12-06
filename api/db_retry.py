"""
Database retry utilities for handling SQLite locking errors.

SQLite has limited concurrent write support, and when multiple services
(worker API, local worker, public/admin API) access the same database,
'database is locked' errors can occur despite WAL mode and busy_timeout.

This module provides retry logic with exponential backoff to handle these
transient locking errors gracefully.
"""

import asyncio
import functools
import logging
import sqlite3
from typing import Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

# Type variable for return type preservation
T = TypeVar("T")

# Default retry configuration
DEFAULT_MAX_RETRIES = 5
DEFAULT_BASE_DELAY = 0.1  # 100ms
DEFAULT_MAX_DELAY = 2.0  # 2 seconds
DEFAULT_EXPONENTIAL_BASE = 2


class DatabaseLockedError(Exception):
    """Raised when database is locked after all retries exhausted."""

    pass


def is_database_locked_error(exc: Exception) -> bool:
    """Check if an exception is a database locked error."""
    error_messages = [
        "database is locked",
        "database table is locked",
        "SQLITE_BUSY",
        "SQLITE_LOCKED",
    ]
    error_str = str(exc).lower()
    return any(msg.lower() in error_str for msg in error_messages)


async def execute_with_retry(
    func: Callable,
    *args,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    **kwargs,
) -> T:
    """
    Execute an async function with retry logic for database locking errors.

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
        DatabaseLockedError: If all retries are exhausted
        Other exceptions: Non-locking errors are re-raised immediately
    """
    import random

    last_exception: Optional[Exception] = None

    for attempt in range(max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except (sqlite3.OperationalError, Exception) as e:
            if not is_database_locked_error(e):
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
                    f"Database locked (attempt {attempt + 1}/{max_retries + 1}), retrying in {delay:.2f}s: {e}"
                )
                await asyncio.sleep(delay)
            else:
                logger.error(f"Database locked after {max_retries + 1} attempts, giving up: {e}")

    raise DatabaseLockedError(f"Database operation failed after {max_retries + 1} attempts: {last_exception}")


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
    Execute a fetch_one query with retry logic for database locking errors.

    Args:
        query: SQLAlchemy query to execute
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay between retries (seconds)
        max_delay: Maximum delay between retries (seconds)

    Returns:
        The query result (single row or None)

    Raises:
        DatabaseLockedError: If all retries are exhausted
    """
    from api.database import database

    async def _fetch():
        return await database.fetch_one(query)

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
    Execute a fetch_all query with retry logic for database locking errors.

    Args:
        query: SQLAlchemy query to execute
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay between retries (seconds)
        max_delay: Maximum delay between retries (seconds)

    Returns:
        The query result (list of rows)

    Raises:
        DatabaseLockedError: If all retries are exhausted
    """
    from api.database import database

    async def _fetch():
        return await database.fetch_all(query)

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
    Execute a database write query with retry logic for database locking errors.

    Args:
        query: SQLAlchemy query to execute
        values: Optional values dict for the query
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay between retries (seconds)
        max_delay: Maximum delay between retries (seconds)

    Returns:
        The query result (typically row ID for inserts)

    Raises:
        DatabaseLockedError: If all retries are exhausted
    """
    from api.database import database

    async def _execute():
        if values is not None:
            return await database.execute(query, values)
        return await database.execute(query)

    return await execute_with_retry(
        _execute,
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
    )
