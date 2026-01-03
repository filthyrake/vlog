"""Add sprite sheet support (Issue #413 Phase 7B)

Adds sprite sheet columns to videos table and sprite_queue table
for background sprite generation.

Revision ID: 025_add_sprite_sheets
Revises: 024_add_chapters
Create Date: 2026-01-01
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add sprite sheet columns to videos table
    op.add_column(
        "videos",
        sa.Column(
            "sprite_sheet_status",
            sa.String(20),
            nullable=True,
            server_default=None,
        ),
    )
    op.add_column(
        "videos",
        sa.Column("sprite_sheet_error", sa.Text, nullable=True),
    )
    op.add_column(
        "videos",
        sa.Column("sprite_sheet_count", sa.Integer, nullable=True, server_default="0"),
    )
    op.add_column(
        "videos",
        sa.Column("sprite_sheet_interval", sa.Integer, nullable=True),
    )
    op.add_column(
        "videos",
        sa.Column("sprite_sheet_tile_size", sa.Integer, nullable=True),
    )
    op.add_column(
        "videos",
        sa.Column("sprite_sheet_frame_width", sa.Integer, nullable=True),
    )
    op.add_column(
        "videos",
        sa.Column("sprite_sheet_frame_height", sa.Integer, nullable=True),
    )

    # Add CHECK constraint for sprite_sheet_status
    op.create_check_constraint(
        "ck_videos_sprite_sheet_status",
        "videos",
        "sprite_sheet_status IS NULL OR sprite_sheet_status IN ('pending', 'generating', 'ready', 'failed')",
    )

    # Create sprite_queue table
    op.create_table(
        "sprite_queue",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "video_id",
            sa.Integer,
            sa.ForeignKey("videos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "priority",
            sa.String(10),
            sa.CheckConstraint(
                "priority IN ('high', 'normal', 'low')",
                name="ck_sprite_queue_priority",
            ),
            server_default="normal",
        ),
        sa.Column(
            "status",
            sa.String(20),
            sa.CheckConstraint(
                "status IN ('pending', 'processing', 'completed', 'failed', 'cancelled')",
                name="ck_sprite_queue_status",
            ),
            server_default="pending",
        ),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_by_worker_id", sa.Integer, nullable=True),
    )

    # Create indexes
    op.create_index("ix_sprite_queue_status", "sprite_queue", ["status"])
    op.create_index("ix_sprite_queue_video_id", "sprite_queue", ["video_id"])
    op.create_index(
        "ix_sprite_queue_priority_created",
        "sprite_queue",
        ["priority", "created_at"],
    )
    # Partial index for pending jobs - optimizes worker job claim query (per Brendan's review)
    op.execute(
        """
        CREATE INDEX ix_sprite_queue_pending_priority
        ON sprite_queue (priority, created_at)
        WHERE status = 'pending'
        """
    )
    op.create_index(
        "ix_videos_sprite_sheet_status",
        "videos",
        ["sprite_sheet_status"],
    )


def downgrade() -> None:
    # Drop indexes
    op.drop_index("ix_videos_sprite_sheet_status", table_name="videos")
    op.execute("DROP INDEX IF EXISTS ix_sprite_queue_pending_priority")
    op.drop_index("ix_sprite_queue_priority_created", table_name="sprite_queue")
    op.drop_index("ix_sprite_queue_video_id", table_name="sprite_queue")
    op.drop_index("ix_sprite_queue_status", table_name="sprite_queue")

    # Drop sprite_queue table
    op.drop_table("sprite_queue")

    # Drop CHECK constraint
    op.drop_constraint("ck_videos_sprite_sheet_status", "videos", type_="check")

    # Drop columns from videos
    op.drop_column("videos", "sprite_sheet_frame_height")
    op.drop_column("videos", "sprite_sheet_frame_width")
    op.drop_column("videos", "sprite_sheet_tile_size")
    op.drop_column("videos", "sprite_sheet_interval")
    op.drop_column("videos", "sprite_sheet_count")
    op.drop_column("videos", "sprite_sheet_error")
    op.drop_column("videos", "sprite_sheet_status")
