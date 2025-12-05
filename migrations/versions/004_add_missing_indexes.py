"""add_missing_indexes

Revision ID: 004
Revises: 003
Create Date: 2025-12-05

Adds indexes on frequently queried foreign key columns that were missing:
- video_qualities.video_id (used in JOINs for quality variants)
- quality_progress.job_id (used in WHERE clauses during transcoding)
- playback_sessions.viewer_id (used in analytics queries)
- transcoding_jobs.video_id (used when looking up jobs by video)

Fixes GitHub issue #117.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: Union[str, Sequence[str], None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add missing indexes on frequently queried columns."""
    op.create_index("ix_video_qualities_video_id", "video_qualities", ["video_id"])
    op.create_index("ix_quality_progress_job_id", "quality_progress", ["job_id"])
    op.create_index("ix_playback_sessions_viewer_id", "playback_sessions", ["viewer_id"])
    op.create_index("ix_transcoding_jobs_video_id", "transcoding_jobs", ["video_id"])


def downgrade() -> None:
    """Remove the added indexes."""
    op.drop_index("ix_transcoding_jobs_video_id", table_name="transcoding_jobs")
    op.drop_index("ix_playback_sessions_viewer_id", table_name="playback_sessions")
    op.drop_index("ix_quality_progress_job_id", table_name="quality_progress")
    op.drop_index("ix_video_qualities_video_id", table_name="video_qualities")
