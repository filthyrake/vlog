"""add_playlists

Revision ID: 020
Revises: 019
Create Date: 2025-12-28

Adds playlists and collections for organizing videos:
- playlists: Playlist/collection definitions with metadata
- playlist_items: Many-to-many relationship between playlists and videos

Implements GitHub issue #223.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "020"
down_revision: Union[str, Sequence[str], None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create playlists and playlist_items tables."""
    # Create playlists table
    op.create_table(
        "playlists",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(255), unique=True, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("thumbnail_path", sa.String(500), nullable=True),
        sa.Column(
            "visibility",
            sa.String(20),
            sa.CheckConstraint(
                "visibility IN ('public', 'private', 'unlisted')",
                name="ck_playlists_visibility",
            ),
            default="public",
            nullable=False,
        ),
        sa.Column(
            "playlist_type",
            sa.String(20),
            sa.CheckConstraint(
                "playlist_type IN ('playlist', 'collection', 'series', 'course')",
                name="ck_playlists_type",
            ),
            default="playlist",
            nullable=False,
        ),
        sa.Column("is_featured", sa.Boolean, default=False, nullable=False),
        sa.Column("user_id", sa.String(100), nullable=True),  # Future: user playlists
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),  # Soft delete
    )
    op.create_index("ix_playlists_slug", "playlists", ["slug"])
    op.create_index("ix_playlists_visibility", "playlists", ["visibility"])
    op.create_index("ix_playlists_is_featured", "playlists", ["is_featured"])
    op.create_index("ix_playlists_deleted_at", "playlists", ["deleted_at"])
    op.create_index("ix_playlists_playlist_type", "playlists", ["playlist_type"])

    # Create playlist_items junction table
    op.create_table(
        "playlist_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "playlist_id",
            sa.Integer,
            sa.ForeignKey("playlists.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "video_id",
            sa.Integer,
            sa.ForeignKey("videos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer, default=0, nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("playlist_id", "video_id", name="uq_playlist_video"),
    )
    op.create_index("ix_playlist_items_playlist_id", "playlist_items", ["playlist_id"])
    op.create_index("ix_playlist_items_video_id", "playlist_items", ["video_id"])
    op.create_index("ix_playlist_items_position", "playlist_items", ["position"])
    # Composite index for efficient ordered retrieval: WHERE playlist_id = ? ORDER BY position
    op.create_index(
        "ix_playlist_items_playlist_position",
        "playlist_items",
        ["playlist_id", "position"]
    )


def downgrade() -> None:
    """Remove playlists and playlist_items tables."""
    op.drop_index("ix_playlist_items_playlist_position", table_name="playlist_items")
    op.drop_index("ix_playlist_items_position", table_name="playlist_items")
    op.drop_index("ix_playlist_items_video_id", table_name="playlist_items")
    op.drop_index("ix_playlist_items_playlist_id", table_name="playlist_items")
    op.drop_table("playlist_items")
    op.drop_index("ix_playlists_playlist_type", table_name="playlists")
    op.drop_index("ix_playlists_deleted_at", table_name="playlists")
    op.drop_index("ix_playlists_is_featured", table_name="playlists")
    op.drop_index("ix_playlists_visibility", table_name="playlists")
    op.drop_index("ix_playlists_slug", table_name="playlists")
    op.drop_table("playlists")
