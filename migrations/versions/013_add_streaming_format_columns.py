"""add_streaming_format_columns

Revision ID: 013
Revises: 012
Create Date: 2025-12-25

Adds streaming format and codec tracking columns to the videos table.
This enables tracking whether videos use legacy HLS/TS or modern CMAF format,
and which codec (H.264, HEVC, AV1) was used for encoding.

See: https://github.com/filthyrake/vlog/issues/212
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "013"
down_revision: Union[str, Sequence[str], None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add streaming_format and primary_codec columns to videos table."""

    # streaming_format: hls_ts (legacy) or cmaf (modern fMP4 with HLS+DASH)
    op.add_column(
        "videos",
        sa.Column(
            "streaming_format",
            sa.String(20),
            nullable=False,
            server_default="hls_ts",
        ),
    )
    op.create_check_constraint(
        "ck_videos_streaming_format",
        "videos",
        "streaming_format IN ('hls_ts', 'cmaf')",
    )

    # primary_codec: h264, hevc, or av1
    op.add_column(
        "videos",
        sa.Column(
            "primary_codec",
            sa.String(10),
            nullable=False,
            server_default="h264",
        ),
    )
    op.create_check_constraint(
        "ck_videos_primary_codec",
        "videos",
        "primary_codec IN ('h264', 'hevc', 'av1')",
    )

    # Index for filtering videos by format (useful for re-encode queries)
    op.create_index(
        "ix_videos_streaming_format",
        "videos",
        ["streaming_format"],
    )


def downgrade() -> None:
    """Remove streaming format columns from videos table."""
    op.drop_index("ix_videos_streaming_format", table_name="videos")
    op.drop_constraint("ck_videos_primary_codec", "videos")
    op.drop_constraint("ck_videos_streaming_format", "videos")
    op.drop_column("videos", "primary_codec")
    op.drop_column("videos", "streaming_format")
