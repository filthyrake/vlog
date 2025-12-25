"""extend_video_qualities

Revision ID: 015
Revises: 014
Create Date: 2025-12-25

Extends video_qualities table with codec and segment_format columns.
This enables tracking which codec and container format was used for
each quality variant, supporting mixed-format videos during migration.

See: https://github.com/filthyrake/vlog/issues/212
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "015"
down_revision: Union[str, Sequence[str], None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add codec and segment_format columns to video_qualities table."""

    # codec: h264, hevc, or av1
    op.add_column(
        "video_qualities",
        sa.Column(
            "codec",
            sa.String(10),
            nullable=False,
            server_default="h264",
        ),
    )
    op.create_check_constraint(
        "ck_video_qualities_codec",
        "video_qualities",
        "codec IN ('h264', 'hevc', 'av1')",
    )

    # segment_format: ts (legacy MPEG-TS) or fmp4 (fragmented MP4/CMAF)
    op.add_column(
        "video_qualities",
        sa.Column(
            "segment_format",
            sa.String(10),
            nullable=False,
            server_default="ts",
        ),
    )
    op.create_check_constraint(
        "ck_video_qualities_segment_format",
        "video_qualities",
        "segment_format IN ('ts', 'fmp4')",
    )


def downgrade() -> None:
    """Remove codec and segment_format columns from video_qualities table."""
    op.drop_constraint("ck_video_qualities_segment_format", "video_qualities")
    op.drop_constraint("ck_video_qualities_codec", "video_qualities")
    op.drop_column("video_qualities", "segment_format")
    op.drop_column("video_qualities", "codec")
