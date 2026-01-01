"""add_featured_video_columns

Revision ID: 022
Revises: 021
Create Date: 2026-01-01

Adds is_featured and featured_at columns to the videos table.
This enables admin-curated featured videos for the homepage hero section.

See: https://github.com/filthyrake/vlog/issues/413 (Phase 3)
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "022"
down_revision: Union[str, Sequence[str], None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add is_featured and featured_at columns to videos table."""

    # is_featured: Boolean flag for admin-curated featured videos
    op.add_column(
        "videos",
        sa.Column(
            "is_featured",
            sa.Boolean,
            nullable=False,
            server_default="false",
        ),
    )

    # featured_at: Timestamp when video was marked as featured (for ordering/rotation)
    op.add_column(
        "videos",
        sa.Column(
            "featured_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # Index for quickly finding featured videos
    op.create_index(
        "ix_videos_is_featured",
        "videos",
        ["is_featured"],
        postgresql_where=sa.text("is_featured = true"),
    )


def downgrade() -> None:
    """Remove featured video columns from videos table."""
    op.drop_index("ix_videos_is_featured", table_name="videos")
    op.drop_column("videos", "featured_at")
    op.drop_column("videos", "is_featured")
