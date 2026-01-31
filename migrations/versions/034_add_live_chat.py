"""add_live_chat

Revision ID: 034
Revises: 033
Create Date: 2026-01-31

Adds chat tables for live streaming:
- chat_messages: Stores chat messages with soft-delete support
- stream_moderators: Tracks moderators per stream with granular permissions
- Chat settings columns on live_streams table

Implements GitHub issue #530 (Studio Phase 2).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "034"
down_revision: Union[str, Sequence[str], None] = "033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add chat tables and columns."""

    # ==========================================================================
    # chat_messages table
    # ==========================================================================
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "stream_id",
            sa.Integer,
            sa.ForeignKey("live_streams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,  # Allow anonymous/system messages
        ),
        sa.Column("content", sa.String(500), nullable=False),  # 500 char limit
        sa.Column("stream_offset_ms", sa.Integer, nullable=True),  # For VOD replay sync
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),  # Soft delete
        sa.Column(
            "deleted_by_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # Indexes for chat_messages (per Gafton's review)
    op.create_index(
        "ix_chat_messages_stream_created",
        "chat_messages",
        ["stream_id", "created_at"],
    )
    op.create_index(
        "ix_chat_messages_user",
        "chat_messages",
        ["user_id"],
    )
    op.create_index(
        "ix_chat_messages_stream_offset",
        "chat_messages",
        ["stream_id", "stream_offset_ms"],
    )

    # ==========================================================================
    # stream_moderators table
    # ==========================================================================
    op.create_table(
        "stream_moderators",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "stream_id",
            sa.Integer,
            sa.ForeignKey("live_streams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Granular permissions as JSONB (per Ada's review)
        # Default: ["delete_message", "timeout"]
        sa.Column(
            "permissions",
            sa.Text,  # JSON stored as text for SQLite compatibility
            nullable=False,
            server_default='["delete_message", "timeout"]',
        ),
        sa.Column(
            "granted_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("stream_id", "user_id", name="uq_stream_moderators_stream_user"),
    )

    op.create_index(
        "ix_stream_moderators_stream",
        "stream_moderators",
        ["stream_id"],
    )

    # ==========================================================================
    # Chat settings columns on live_streams
    # ==========================================================================
    # Adding columns to existing live_streams table (1:1 relationship, simpler than separate table)

    op.add_column(
        "live_streams",
        sa.Column("chat_enabled", sa.Boolean, server_default="true", nullable=False),
    )
    op.add_column(
        "live_streams",
        sa.Column("chat_slow_mode_seconds", sa.Integer, server_default="0", nullable=False),
    )
    op.add_column(
        "live_streams",
        sa.Column("chat_subscriber_only", sa.Boolean, server_default="false", nullable=False),
    )
    op.add_column(
        "live_streams",
        sa.Column("chat_follower_only", sa.Boolean, server_default="false", nullable=False),
    )
    op.add_column(
        "live_streams",
        sa.Column("chat_follower_min_minutes", sa.Integer, server_default="0", nullable=False),
    )
    op.add_column(
        "live_streams",
        sa.Column("chat_emote_only", sa.Boolean, server_default="false", nullable=False),
    )
    op.add_column(
        "live_streams",
        sa.Column("chat_links_allowed", sa.Boolean, server_default="true", nullable=False),
    )


def downgrade() -> None:
    """Remove chat tables and columns."""

    # Remove chat settings columns from live_streams
    op.drop_column("live_streams", "chat_links_allowed")
    op.drop_column("live_streams", "chat_emote_only")
    op.drop_column("live_streams", "chat_follower_min_minutes")
    op.drop_column("live_streams", "chat_follower_only")
    op.drop_column("live_streams", "chat_subscriber_only")
    op.drop_column("live_streams", "chat_slow_mode_seconds")
    op.drop_column("live_streams", "chat_enabled")

    # Drop stream_moderators
    op.drop_index("ix_stream_moderators_stream", table_name="stream_moderators")
    op.drop_table("stream_moderators")

    # Drop chat_messages
    op.drop_index("ix_chat_messages_stream_offset", table_name="chat_messages")
    op.drop_index("ix_chat_messages_user", table_name="chat_messages")
    op.drop_index("ix_chat_messages_stream_created", table_name="chat_messages")
    op.drop_table("chat_messages")
