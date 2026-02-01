"""add_stream_moderation

Revision ID: 035
Revises: 034
Create Date: 2026-01-31

Adds moderation tables for live streaming:
- stream_bans: Tracks user bans and timeouts with history
- stream_word_filters: Automated content filtering with ReDoS protection
- moderation_logs: Audit trail for all moderation actions

Implements GitHub issue #530 (Studio Phase 2).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "035"
down_revision: Union[str, Sequence[str], None] = "034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add moderation tables."""

    # ==========================================================================
    # stream_bans table
    # ==========================================================================
    # No unique constraint on (stream_id, user_id) - allows tracking ban history
    op.create_table(
        "stream_bans",
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
        # 'timeout' or 'permanent'
        sa.Column("ban_type", sa.String(20), nullable=False),
        # Duration in seconds (for timeouts)
        sa.Column("duration_seconds", sa.Integer, nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column(
            "banned_by",
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
        # When the ban expires (for timeouts)
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        # When the ban was lifted (null if still active)
        sa.Column("unbanned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "unbanned_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Check constraint for ban_type
        sa.CheckConstraint(
            "ban_type IN ('timeout', 'permanent')",
            name="ck_stream_bans_ban_type",
        ),
    )

    # Indexes for stream_bans
    op.create_index(
        "ix_stream_bans_stream_user",
        "stream_bans",
        ["stream_id", "user_id"],
    )
    op.create_index(
        "ix_stream_bans_expires",
        "stream_bans",
        ["expires_at"],
    )

    # ==========================================================================
    # stream_word_filters table
    # ==========================================================================
    op.create_table(
        "stream_word_filters",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "stream_id",
            sa.Integer,
            sa.ForeignKey("live_streams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Pattern to match (max 100 chars for ReDoS protection)
        sa.Column("pattern", sa.String(100), nullable=False),
        # Whether pattern is a regex
        sa.Column("is_regex", sa.Boolean, server_default="false", nullable=False),
        # Action: 'delete', 'timeout', 'warn'
        sa.Column("action", sa.String(20), server_default="'delete'", nullable=False),
        # Timeout duration if action is 'timeout'
        sa.Column("timeout_seconds", sa.Integer, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Check constraint for action type
        sa.CheckConstraint(
            "action IN ('delete', 'timeout', 'warn')",
            name="ck_stream_word_filters_action",
        ),
    )

    op.create_index(
        "ix_stream_word_filters_stream",
        "stream_word_filters",
        ["stream_id"],
    )

    # ==========================================================================
    # moderation_logs table
    # ==========================================================================
    op.create_table(
        "moderation_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "stream_id",
            sa.Integer,
            sa.ForeignKey("live_streams.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "moderator_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        # Action type (e.g., 'timeout', 'ban', 'unban', 'delete_message', 'add_filter')
        sa.Column("action", sa.String(50), nullable=False),
        # Target user (for user-specific actions)
        sa.Column("target_user_id", sa.String(36), nullable=True),
        # Target message (for message-specific actions)
        sa.Column("target_message_id", sa.Integer, nullable=True),
        # Additional details as JSON
        sa.Column("details", sa.Text, nullable=True),  # JSON stored as text
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_index(
        "ix_moderation_logs_stream_created",
        "moderation_logs",
        ["stream_id", "created_at"],
    )


def downgrade() -> None:
    """Remove moderation tables."""

    # Drop moderation_logs
    op.drop_index("ix_moderation_logs_stream_created", table_name="moderation_logs")
    op.drop_table("moderation_logs")

    # Drop stream_word_filters
    op.drop_index("ix_stream_word_filters_stream", table_name="stream_word_filters")
    op.drop_table("stream_word_filters")

    # Drop stream_bans
    op.drop_index("ix_stream_bans_expires", table_name="stream_bans")
    op.drop_index("ix_stream_bans_stream_user", table_name="stream_bans")
    op.drop_table("stream_bans")
