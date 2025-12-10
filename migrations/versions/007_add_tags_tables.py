"""add_tags_tables

Revision ID: 007
Revises: 006
Create Date: 2025-12-09

Adds tags system for granular content organization:
- tags: Tag definitions with name and slug
- video_tags: Many-to-many relationship between videos and tags

Implements GitHub issue #205.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "007"
down_revision: Union[str, Sequence[str], None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create tags and video_tags tables."""
    # Create tags table
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(50), unique=True, nullable=False),
        sa.Column("slug", sa.String(50), unique=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_tags_slug", "tags", ["slug"])

    # Create video_tags junction table
    op.create_table(
        "video_tags",
        sa.Column("video_id", sa.Integer, sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tag_id", sa.Integer, sa.ForeignKey("tags.id", ondelete="CASCADE"), nullable=False),
        sa.PrimaryKeyConstraint("video_id", "tag_id"),
    )
    op.create_index("ix_video_tags_video_id", "video_tags", ["video_id"])
    op.create_index("ix_video_tags_tag_id", "video_tags", ["tag_id"])


def downgrade() -> None:
    """Remove tags and video_tags tables."""
    op.drop_index("ix_video_tags_tag_id", table_name="video_tags")
    op.drop_index("ix_video_tags_video_id", table_name="video_tags")
    op.drop_table("video_tags")
    op.drop_index("ix_tags_slug", table_name="tags")
    op.drop_table("tags")
