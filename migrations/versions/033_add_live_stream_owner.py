"""add_live_stream_owner

Revision ID: 033
Revises: 032
Create Date: 2025-01-30

Adds owner_id column to live_streams table for multi-user support.
Assigns existing streams to first admin user during migration.

Implements GitHub issue #524 (Broadcaster Dashboard).
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
    # Add owner_id column
    op.add_column(
        "live_streams",
        sa.Column(
            "owner_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # Create index for ownership queries
    op.create_index("ix_live_streams_owner_id", "live_streams", ["owner_id"])

    # Data migration: Assign existing streams to first admin user
    # This ensures existing streams (created before multi-user) are owned by primary admin
    connection = op.get_bind()

    # Find the first admin user by created_at
    result = connection.execute(
        sa.text(
            "SELECT id FROM users WHERE role = 'admin' ORDER BY created_at LIMIT 1"
        )
    )
    admin_row = result.fetchone()

    if admin_row:
        admin_id = admin_row[0]
        # Update all streams with NULL owner_id
        connection.execute(
            sa.text(
                "UPDATE live_streams SET owner_id = :admin_id WHERE owner_id IS NULL"
            ),
            {"admin_id": admin_id},
        )


def downgrade() -> None:
    """Remove owner_id column from live_streams table."""
    op.drop_index("ix_live_streams_owner_id", table_name="live_streams")
    op.drop_column("live_streams", "owner_id")
