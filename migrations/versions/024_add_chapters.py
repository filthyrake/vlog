"""add_chapters

Revision ID: 024
Revises: 023
Create Date: 2026-01-01

Adds video chapters for timeline navigation:
- chapters: Chapter definitions with timestamps and ordering
- has_chapters column on videos for performance optimization

Implements GitHub issue #413 Phase 7.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "024"
down_revision: Union[str, Sequence[str], None] = "023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create chapters table and add has_chapters column to videos."""
    # Create chapters table
    op.create_table(
        "chapters",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "video_id",
            sa.Integer,
            sa.ForeignKey("videos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("start_time", sa.Float, nullable=False),
        sa.Column("end_time", sa.Float, nullable=True),
        sa.Column("position", sa.Integer, default=0, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        # Constraints per reviewer feedback
        sa.CheckConstraint("start_time >= 0", name="ck_chapters_start_time_positive"),
        sa.CheckConstraint(
            "end_time IS NULL OR end_time > start_time",
            name="ck_chapters_end_time_valid"
        ),
        sa.UniqueConstraint("video_id", "position", name="uq_chapter_video_position"),
    )
    op.create_index("ix_chapters_video_id", "chapters", ["video_id"])
    op.create_index("ix_chapters_position", "chapters", ["position"])
    # Composite index for efficient ordered retrieval: WHERE video_id = ? ORDER BY position
    op.create_index(
        "ix_chapters_video_position",
        "chapters",
        ["video_id", "position"]
    )

    # Add has_chapters column to videos for performance optimization
    # This avoids querying the chapters table when video has no chapters (most videos)
    op.add_column(
        "videos",
        sa.Column("has_chapters", sa.Boolean, default=False, nullable=False, server_default="false"),
    )


def downgrade() -> None:
    """Remove chapters table and has_chapters column from videos."""
    # Remove has_chapters column from videos
    op.drop_column("videos", "has_chapters")

    # Drop chapters table and indexes
    op.drop_index("ix_chapters_video_position", table_name="chapters")
    op.drop_index("ix_chapters_position", table_name="chapters")
    op.drop_index("ix_chapters_video_id", table_name="chapters")
    op.drop_table("chapters")
