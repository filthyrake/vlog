"""add_stream_controls

Revision ID: 037
Revises: 036
Create Date: 2026-01-31

Adds additional stream control columns to live_streams:
- stream_delay_seconds: Artificial delay for privacy protection
- quality_preset: Transcoding quality preset (auto, low, medium, high, source)
- scheduled_at: Scheduled start time for upcoming streams

Implements GitHub issue #530 (Studio Phase 2E).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "037"
down_revision: Union[str, Sequence[str], None] = "036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add stream control columns to live_streams."""

    # Stream delay for privacy protection (e.g., competitive gaming)
    op.add_column(
        "live_streams",
        sa.Column(
            "stream_delay_seconds",
            sa.Integer,
            server_default="0",
            nullable=False,
        ),
    )

    # Quality preset for transcoding
    op.add_column(
        "live_streams",
        sa.Column(
            "quality_preset",
            sa.String(20),
            server_default="'auto'",
            nullable=False,
        ),
    )

    # Check constraint for quality preset
    op.create_check_constraint(
        "ck_live_streams_quality_preset",
        "live_streams",
        "quality_preset IN ('auto', 'low', 'medium', 'high', 'source')",
    )

    # Scheduled start time for upcoming streams
    op.add_column(
        "live_streams",
        sa.Column(
            "scheduled_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # Index for finding scheduled streams
    op.create_index(
        "ix_live_streams_scheduled_at",
        "live_streams",
        ["scheduled_at"],
        postgresql_where=sa.text("scheduled_at IS NOT NULL"),
    )


def downgrade() -> None:
    """Remove stream control columns from live_streams."""

    # Drop index
    op.drop_index("ix_live_streams_scheduled_at", table_name="live_streams")

    # Drop columns (check constraint drops automatically with column)
    op.drop_constraint("ck_live_streams_quality_preset", "live_streams", type_="check")
    op.drop_column("live_streams", "scheduled_at")
    op.drop_column("live_streams", "quality_preset")
    op.drop_column("live_streams", "stream_delay_seconds")
