"""
Partition manager for PostgreSQL table partitioning.

Handles automatic creation of monthly partitions for the playback_sessions table
to improve query performance and enable efficient data cleanup.

See: https://github.com/filthyrake/vlog/issues/463
"""

import logging
from datetime import datetime, timezone
from typing import List

from dateutil.relativedelta import relativedelta

from api.database import database

logger = logging.getLogger(__name__)

# Partition naming convention: playback_sessions_YYYYMM
PARTITION_PREFIX = "playback_sessions_"

# How many months ahead to create partitions
PARTITION_LOOKAHEAD_MONTHS = 3


async def get_existing_partitions() -> List[str]:
    """
    Get list of existing partition names for playback_sessions table.

    Returns:
        List of partition table names (e.g., ['playback_sessions_202501', ...])
    """
    query = """
        SELECT tablename
        FROM pg_tables
        WHERE schemaname = 'public'
          AND tablename LIKE 'playback_sessions_%'
          AND tablename ~ '^playback_sessions_[0-9]{6}$'
        ORDER BY tablename
    """
    rows = await database.fetch_all(query)
    return [row["tablename"] for row in rows]


async def partition_exists(year: int, month: int) -> bool:
    """
    Check if a partition exists for the given year and month.

    Args:
        year: The year (e.g., 2025)
        month: The month (1-12)

    Returns:
        True if partition exists, False otherwise
    """
    partition_name = f"{PARTITION_PREFIX}{year:04d}{month:02d}"
    query = """
        SELECT EXISTS (
            SELECT 1 FROM pg_tables
            WHERE schemaname = 'public' AND tablename = :partition_name
        )
    """
    result = await database.fetch_val(query, {"partition_name": partition_name})
    return bool(result)


async def create_partition(year: int, month: int) -> bool:
    """
    Create a monthly partition for playback_sessions.

    Args:
        year: The year (e.g., 2025)
        month: The month (1-12)

    Returns:
        True if partition was created, False if it already exists
    """
    partition_name = f"{PARTITION_PREFIX}{year:04d}{month:02d}"

    # Check if partition already exists
    if await partition_exists(year, month):
        logger.debug(f"Partition {partition_name} already exists")
        return False

    # Calculate partition bounds
    start_date = datetime(year, month, 1, tzinfo=timezone.utc)
    end_date = start_date + relativedelta(months=1)

    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    # Create the partition
    # Note: This requires the parent table to be set up as a partitioned table
    query = f"""
        CREATE TABLE IF NOT EXISTS {partition_name}
        PARTITION OF playback_sessions
        FOR VALUES FROM ('{start_str}') TO ('{end_str}')
    """

    try:
        await database.execute(query)
        logger.info(f"Created partition {partition_name} for {start_str} to {end_str}")
        return True
    except Exception as e:
        # Check if it's a "partition already exists" error
        if "already exists" in str(e).lower():
            logger.debug(f"Partition {partition_name} already exists (race condition)")
            return False
        raise


async def ensure_partitions_exist(months_ahead: int = PARTITION_LOOKAHEAD_MONTHS) -> List[str]:
    """
    Ensure partitions exist for the current month and future months.

    Creates partitions for the current month plus the specified number of months ahead.

    Args:
        months_ahead: Number of months ahead to create partitions for

    Returns:
        List of partition names that were created
    """
    created = []
    now = datetime.now(timezone.utc)

    # Create partitions from current month to months_ahead
    for i in range(months_ahead + 1):
        target_date = now + relativedelta(months=i)
        if await create_partition(target_date.year, target_date.month):
            partition_name = f"{PARTITION_PREFIX}{target_date.year:04d}{target_date.month:02d}"
            created.append(partition_name)

    return created


async def drop_partition(year: int, month: int) -> bool:
    """
    Drop a partition (for data retention/cleanup).

    WARNING: This permanently deletes all data in the partition.

    Args:
        year: The year (e.g., 2024)
        month: The month (1-12)

    Returns:
        True if partition was dropped, False if it didn't exist
    """
    partition_name = f"{PARTITION_PREFIX}{year:04d}{month:02d}"

    if not await partition_exists(year, month):
        logger.debug(f"Partition {partition_name} does not exist")
        return False

    query = f"DROP TABLE IF EXISTS {partition_name}"
    await database.execute(query)
    logger.info(f"Dropped partition {partition_name}")
    return True


async def get_partition_stats() -> List[dict]:
    """
    Get statistics for all playback_sessions partitions.

    Returns:
        List of dicts with partition info (name, row_count, size)
    """
    query = """
        SELECT
            t.tablename as name,
            pg_total_relation_size(quote_ident(t.tablename)::regclass) as size_bytes,
            (
                SELECT reltuples::bigint
                FROM pg_class
                WHERE relname = t.tablename
            ) as estimated_rows
        FROM pg_tables t
        WHERE t.schemaname = 'public'
          AND t.tablename LIKE 'playback_sessions_%'
          AND t.tablename ~ '^playback_sessions_[0-9]{6}$'
        ORDER BY t.tablename DESC
    """
    rows = await database.fetch_all(query)
    return [
        {
            "name": row["name"],
            "size_bytes": row["size_bytes"],
            "estimated_rows": row["estimated_rows"],
        }
        for row in rows
    ]


async def cleanup_old_partitions(retention_months: int = 12) -> List[str]:
    """
    Drop partitions older than the retention period.

    Args:
        retention_months: Number of months to retain (default 12)

    Returns:
        List of partition names that were dropped
    """
    dropped = []
    now = datetime.now(timezone.utc)
    cutoff_date = now - relativedelta(months=retention_months)

    existing = await get_existing_partitions()
    for partition_name in existing:
        # Parse partition date from name (playback_sessions_YYYYMM)
        try:
            date_str = partition_name.replace(PARTITION_PREFIX, "")
            year = int(date_str[:4])
            month = int(date_str[4:6])
            partition_date = datetime(year, month, 1, tzinfo=timezone.utc)

            if partition_date < cutoff_date:
                if await drop_partition(year, month):
                    dropped.append(partition_name)
        except (ValueError, IndexError):
            logger.warning(f"Could not parse partition name: {partition_name}")
            continue

    return dropped


async def is_table_partitioned() -> bool:
    """
    Check if the playback_sessions table is set up as a partitioned table.

    Returns:
        True if table is partitioned, False otherwise
    """
    query = """
        SELECT pt.partstrat IS NOT NULL as is_partitioned
        FROM pg_class c
        LEFT JOIN pg_partitioned_table pt ON c.oid = pt.partrelid
        WHERE c.relname = 'playback_sessions'
          AND c.relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public')
    """
    result = await database.fetch_one(query)
    if result is None:
        return False
    return bool(result["is_partitioned"])
