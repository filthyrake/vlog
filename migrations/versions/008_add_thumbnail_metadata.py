"""add_thumbnail_metadata

Revision ID: 008
Revises: 007
Create Date: 2025-12-17

Adds thumbnail metadata tracking for custom thumbnail selection.
- thumbnail_source: 'auto' (default), 'selected', or 'custom'
- thumbnail_timestamp: timestamp in seconds for selected thumbnails

Implements GitHub issue #317.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "008"
down_revision: Union[str, Sequence[str], None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add thumbnail metadata columns to videos table."""
    # thumbnail_source: 'auto' (default), 'selected', 'custom'
    op.add_column(
        "videos",
        sa.Column("thumbnail_source", sa.String(20), server_default="auto", nullable=False),
    )
    # thumbnail_timestamp: timestamp in seconds for 'selected' thumbnails
    op.add_column(
        "videos",
        sa.Column("thumbnail_timestamp", sa.Float, nullable=True),
    )


def downgrade() -> None:
    """Remove thumbnail metadata columns."""
    op.drop_column("videos", "thumbnail_timestamp")
    op.drop_column("videos", "thumbnail_source")
