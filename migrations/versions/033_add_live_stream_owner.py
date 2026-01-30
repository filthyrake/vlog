"""add_live_stream_owner

Revision ID: 033
Revises: 032
Create Date: 2026-01-29

Adds owner_id to live_streams table for ownership tracking.
This enables broadcaster dashboard access control.

Implements GitHub issue #524 (Broadcaster Dashboard - Phase 1).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "033"
down_revision: Union[str, Sequence[str], None] = "032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add owner_id column to live_streams table."""
    # Add owner_id column (nullable for backward compatibility with existing streams)
    op.add_column(
        "live_streams",
        sa.Column(
            "owner_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # Add index for efficient owner lookups
    op.create_index("ix_live_streams_owner_id", "live_streams", ["owner_id"])


def downgrade() -> None:
    """Remove owner_id column from live_streams table."""
    op.drop_index("ix_live_streams_owner_id", table_name="live_streams")
    op.drop_column("live_streams", "owner_id")
