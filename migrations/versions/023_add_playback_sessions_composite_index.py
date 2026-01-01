"""add_playback_sessions_composite_index

Revision ID: 023
Revises: 022
Create Date: 2026-01-01

Adds composite index on playback_sessions (video_id, id) for efficient
view count aggregation in bulk video queries.

This index significantly improves performance of COUNT(DISTINCT id) operations
in the /api/videos/bulk endpoint, reducing query time from O(N×P) to O(N log P)
where N = videos and P = playback sessions per video.

See: https://github.com/filthyrake/vlog/issues/413 (Phase 3 Performance Fix)
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "023"
down_revision: Union[str, Sequence[str], None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add composite index for efficient view count aggregation."""
    # Composite index on (video_id, id) enables efficient COUNT(DISTINCT id)
    # without full table scans. The video_id comes first for equality filtering,
    # then id for the distinct count operation.
    op.create_index(
        "ix_playback_sessions_video_id_id",
        "playback_sessions",
        ["video_id", "id"],
    )


def downgrade() -> None:
    """Remove the composite index."""
    op.drop_index("ix_playback_sessions_video_id_id", table_name="playback_sessions")
