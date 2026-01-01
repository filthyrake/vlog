"""partition_playback_sessions

Revision ID: 021
Revises: 020
Create Date: 2025-12-31

Converts the playback_sessions table to a partitioned table for improved
query performance and efficient data cleanup. Uses monthly partitions
based on the started_at column.

Also adds a compound index on videos(published_at, id) to support efficient
cursor-based pagination.

Implements GitHub issue #463.

IMPORTANT: This migration is PostgreSQL-specific and requires PostgreSQL 10+.

BREAKING CHANGE: session_token uniqueness is now enforced per-partition rather
than globally. This is acceptable because session tokens are never reused and
include timestamps in their generation.

ESTIMATED TIME: For large tables (>1M rows), this migration may take several
minutes due to data copying. Ensure sufficient disk space for temporary
data duplication.
"""

from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from dateutil.relativedelta import relativedelta

# revision identifiers, used by Alembic.
revision: str = "021"
down_revision: Union[str, Sequence[str], None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def get_partition_bounds():
    """
    Calculate partition bounds from existing data plus future months.

    Returns list of (year, month) tuples for partitions to create.
    """
    now = datetime.now(timezone.utc)

    # Create partitions from 2024-01 (or earlier if data exists) through 3 months ahead
    # We'll query the actual data range in the upgrade function
    partitions = []

    # Start from 2024-01 as a reasonable default (adjust based on your data)
    start_date = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end_date = now + relativedelta(months=3)

    current = start_date
    while current <= end_date:
        partitions.append((current.year, current.month))
        current += relativedelta(months=1)

    return partitions


def upgrade() -> None:
    """
    Convert playback_sessions to a partitioned table.

    Steps:
    1. Rename existing table to backup
    2. Create new partitioned table structure
    3. Create monthly partitions
    4. Copy data from backup to partitioned table
    5. Drop backup table
    """
    conn = op.get_bind()

    # Step 1: Check if we have any data and get the date range
    result = conn.execute(
        sa.text("""
        SELECT
            MIN(started_at) as min_date,
            MAX(started_at) as max_date,
            COUNT(*) as row_count
        FROM playback_sessions
    """)
    ).fetchone()

    min_date = result[0]
    # max_date = result[1]  # Not used, but kept for reference
    row_count = result[2]

    # Step 2: Rename existing table to backup
    op.rename_table("playback_sessions", "playback_sessions_old")

    # Step 3: Drop foreign key constraints on the old table's indexes
    # (They'll be recreated on the new table)
    op.drop_index("ix_playback_sessions_video_id", table_name="playback_sessions_old")
    op.drop_index("ix_playback_sessions_viewer_id", table_name="playback_sessions_old")
    op.drop_index("ix_playback_sessions_started_at", table_name="playback_sessions_old")

    # Step 4: Create new partitioned table
    # Note: Using raw SQL because Alembic doesn't have native partition support
    conn.execute(
        sa.text("""
        CREATE TABLE playback_sessions (
            id SERIAL,
            video_id INTEGER NOT NULL,
            viewer_id INTEGER,
            session_token VARCHAR(64) NOT NULL,
            started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            ended_at TIMESTAMP WITH TIME ZONE,
            duration_watched FLOAT DEFAULT 0,
            max_position FLOAT DEFAULT 0,
            quality_used VARCHAR(10),
            completed BOOLEAN DEFAULT FALSE,
            PRIMARY KEY (id, started_at),
            CONSTRAINT ck_playback_sessions_quality_used
                CHECK (quality_used IN ('2160p', '1440p', '1080p', '720p', '480p', '360p', 'original') OR quality_used IS NULL)
        ) PARTITION BY RANGE (started_at)
    """)
    )

    # Step 5: Create indexes on the partitioned table
    # Note: Indexes on partitioned tables are automatically created on each partition
    op.create_index("ix_playback_sessions_video_id", "playback_sessions", ["video_id"])
    op.create_index("ix_playback_sessions_viewer_id", "playback_sessions", ["viewer_id"])
    op.create_index("ix_playback_sessions_started_at", "playback_sessions", ["started_at"])

    # Create unique index on session_token within each partition
    # Note: session_token uniqueness is now per-partition, which is acceptable
    # since tokens are never reused across time periods
    op.create_index(
        "ix_playback_sessions_session_token", "playback_sessions", ["session_token", "started_at"], unique=True
    )

    # Step 6: Add foreign key constraints
    # Note: Foreign keys on partitioned tables require the partition key in the constraint
    conn.execute(
        sa.text("""
        ALTER TABLE playback_sessions
        ADD CONSTRAINT fk_playback_sessions_video_id
        FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE
    """)
    )

    conn.execute(
        sa.text("""
        ALTER TABLE playback_sessions
        ADD CONSTRAINT fk_playback_sessions_viewer_id
        FOREIGN KEY (viewer_id) REFERENCES viewers(id) ON DELETE SET NULL
    """)
    )

    # Step 7: Calculate partitions to create
    now = datetime.now(timezone.utc)
    partitions_to_create = []

    if min_date:
        # Create partitions from data start to 3 months ahead
        start_date = datetime(min_date.year, min_date.month, 1, tzinfo=timezone.utc)
    else:
        # No data, start from current month
        start_date = datetime(now.year, now.month, 1, tzinfo=timezone.utc)

    end_date = now + relativedelta(months=3)

    current = start_date
    while current <= end_date:
        partitions_to_create.append((current.year, current.month))
        current += relativedelta(months=1)

    # Step 8: Create partitions
    for year, month in partitions_to_create:
        partition_name = f"playback_sessions_{year:04d}{month:02d}"
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        end = start + relativedelta(months=1)

        start_str = start.strftime("%Y-%m-%d")
        end_str = end.strftime("%Y-%m-%d")

        conn.execute(
            sa.text(f"""
            CREATE TABLE {partition_name}
            PARTITION OF playback_sessions
            FOR VALUES FROM ('{start_str}') TO ('{end_str}')
        """)
        )

    # Step 9: Copy data from old table to new partitioned table
    if row_count and row_count > 0:
        conn.execute(
            sa.text("""
            INSERT INTO playback_sessions (
                id, video_id, viewer_id, session_token, started_at,
                ended_at, duration_watched, max_position, quality_used, completed
            )
            SELECT
                id, video_id, viewer_id, session_token, started_at,
                ended_at, duration_watched, max_position, quality_used, completed
            FROM playback_sessions_old
        """)
        )

        # Step 9a: Verify data integrity - row counts must match
        new_count_result = conn.execute(
            sa.text("SELECT COUNT(*) FROM playback_sessions")
        ).fetchone()
        new_count = new_count_result[0]

        if new_count != row_count:
            raise RuntimeError(
                f"Data integrity check failed: expected {row_count} rows, "
                f"but partitioned table has {new_count} rows. "
                f"Rolling back migration."
            )

        # Update the sequence to continue from the max id
        conn.execute(
            sa.text("""
            SELECT setval('playback_sessions_id_seq', COALESCE((SELECT MAX(id) FROM playback_sessions), 1))
        """)
        )

    # Step 10: Drop the old table
    op.drop_table("playback_sessions_old")

    # Step 11: Add compound index for cursor-based pagination (Issue #463)
    # This index supports efficient keyset pagination on videos table
    op.create_index(
        "ix_videos_published_at_id",
        "videos",
        ["published_at", "id"],
        postgresql_using="btree",
    )


def downgrade() -> None:
    """
    Convert back to a regular (non-partitioned) table.

    Note: This will lose the partitioning benefits but preserves all data.
    """
    conn = op.get_bind()

    # Step 0: Drop the compound index for cursor pagination
    op.drop_index("ix_videos_published_at_id", table_name="videos")

    # Step 1: Create a regular table with the same structure
    conn.execute(
        sa.text("""
        CREATE TABLE playback_sessions_new (
            id SERIAL PRIMARY KEY,
            video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
            viewer_id INTEGER REFERENCES viewers(id) ON DELETE SET NULL,
            session_token VARCHAR(64) NOT NULL UNIQUE,
            started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            ended_at TIMESTAMP WITH TIME ZONE,
            duration_watched FLOAT DEFAULT 0,
            max_position FLOAT DEFAULT 0,
            quality_used VARCHAR(10),
            completed BOOLEAN DEFAULT FALSE,
            CONSTRAINT ck_playback_sessions_quality_used
                CHECK (quality_used IN ('2160p', '1440p', '1080p', '720p', '480p', '360p', 'original') OR quality_used IS NULL)
        )
    """)
    )

    # Step 2: Copy data from partitioned table
    conn.execute(
        sa.text("""
        INSERT INTO playback_sessions_new (
            id, video_id, viewer_id, session_token, started_at,
            ended_at, duration_watched, max_position, quality_used, completed
        )
        SELECT
            id, video_id, viewer_id, session_token, started_at,
            ended_at, duration_watched, max_position, quality_used, completed
        FROM playback_sessions
    """)
    )

    # Step 3: Drop the partitioned table (this also drops all partitions)
    conn.execute(sa.text("DROP TABLE playback_sessions CASCADE"))

    # Step 4: Rename new table to original name
    op.rename_table("playback_sessions_new", "playback_sessions")

    # Step 5: Recreate indexes
    op.create_index("ix_playback_sessions_video_id", "playback_sessions", ["video_id"])
    op.create_index("ix_playback_sessions_viewer_id", "playback_sessions", ["viewer_id"])
    op.create_index("ix_playback_sessions_started_at", "playback_sessions", ["started_at"])

    # Step 6: Update sequence
    conn.execute(
        sa.text("""
        SELECT setval('playback_sessions_id_seq', COALESCE((SELECT MAX(id) FROM playback_sessions), 1))
    """)
    )
