"""add_session_token_unique_constraint

Revision ID: 002
Revises: 001
Create Date: 2025-12-04

This migration adds a unique constraint to the playback_sessions.session_token column.
"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, Sequence[str], None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add unique constraint to playback_sessions.session_token column."""
    # SQLite doesn't support adding constraints directly, so we need to:
    # 1. Drop the old index (if it exists)
    # 2. Create a unique index (which acts as a unique constraint in SQLite)

    # Check if the non-unique index exists and drop it
    conn = op.get_bind()
    inspector = inspect(conn)
    indexes = [idx["name"] for idx in inspector.get_indexes("playback_sessions")]

    if "ix_playback_sessions_session_token" in indexes:
        # Check if it's already unique
        for idx in inspector.get_indexes("playback_sessions"):
            if idx["name"] == "ix_playback_sessions_session_token" and idx["unique"]:
                # Already unique, nothing to do
                return

        # Drop the old non-unique index
        op.drop_index("ix_playback_sessions_session_token", table_name="playback_sessions")

    # Create a unique index (SQLite uses unique indexes for unique constraints)
    op.create_index(
        "ix_playback_sessions_session_token",
        "playback_sessions",
        ["session_token"],
        unique=True,
    )


def downgrade() -> None:
    """Remove unique constraint from playback_sessions.session_token column."""
    # Check if the index exists before trying to drop it
    conn = op.get_bind()
    inspector = inspect(conn)
    indexes = [idx["name"] for idx in inspector.get_indexes("playback_sessions")]

    if "ix_playback_sessions_session_token" in indexes:
        # Drop the unique index
        op.drop_index("ix_playback_sessions_session_token", table_name="playback_sessions")

        # Recreate the non-unique index
        op.create_index(
            "ix_playback_sessions_session_token",
            "playback_sessions",
            ["session_token"],
            unique=False,
        )
