"""add_check_constraints

Revision ID: 011
Revises: 010
Create Date: 2025-12-22

Add CHECK constraints to enum columns for data integrity validation.
This ensures that only valid enum values can be inserted via raw SQL,
and validates progress_percent ranges (0-100).

Implements GitHub issue: Enhancement: Add CHECK constraints on enum columns in database
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "011"
down_revision: Union[str, Sequence[str], None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add CHECK constraints to enum columns."""
    
    # videos.status - VideoStatus enum values
    op.create_check_constraint(
        "ck_videos_status",
        "videos",
        "status IN ('pending', 'processing', 'ready', 'failed')"
    )
    
    # videos.thumbnail_source - ThumbnailSource enum values
    op.create_check_constraint(
        "ck_videos_thumbnail_source",
        "videos",
        "thumbnail_source IN ('auto', 'selected', 'custom')"
    )
    
    # quality_progress.status - QualityStatus enum values
    op.create_check_constraint(
        "ck_quality_progress_status",
        "quality_progress",
        "status IN ('pending', 'in_progress', 'completed', 'failed', 'skipped')"
    )
    
    # quality_progress.progress_percent - Range validation (0-100)
    op.create_check_constraint(
        "ck_quality_progress_percent_range",
        "quality_progress",
        "progress_percent >= 0 AND progress_percent <= 100"
    )
    
    # transcoding_jobs.progress_percent - Range validation (0-100)
    op.create_check_constraint(
        "ck_transcoding_jobs_progress_percent_range",
        "transcoding_jobs",
        "progress_percent >= 0 AND progress_percent <= 100"
    )
    
    # transcriptions.status - TranscriptionStatus enum values
    op.create_check_constraint(
        "ck_transcriptions_status",
        "transcriptions",
        "status IN ('pending', 'processing', 'completed', 'failed')"
    )
    
    # workers.status - WorkerStatus enum values
    op.create_check_constraint(
        "ck_workers_status",
        "workers",
        "status IN ('active', 'offline', 'disabled')"
    )
    
    # workers.worker_type - WorkerType enum values
    op.create_check_constraint(
        "ck_workers_worker_type",
        "workers",
        "worker_type IN ('local', 'remote')"
    )
    
    # video_qualities.quality - Quality preset names
    op.create_check_constraint(
        "ck_video_qualities_quality",
        "video_qualities",
        "quality IN ('2160p', '1440p', '1080p', '720p', '480p', '360p', 'original')"
    )
    
    # playback_sessions.quality_used - Quality preset names
    op.create_check_constraint(
        "ck_playback_sessions_quality_used",
        "playback_sessions",
        "quality_used IN ('2160p', '1440p', '1080p', '720p', '480p', '360p', 'original') OR quality_used IS NULL"
    )


def downgrade() -> None:
    """Remove CHECK constraints from enum columns."""
    
    # Drop all CHECK constraints in reverse order
    op.drop_constraint("ck_playback_sessions_quality_used", "playback_sessions")
    op.drop_constraint("ck_video_qualities_quality", "video_qualities")
    op.drop_constraint("ck_workers_worker_type", "workers")
    op.drop_constraint("ck_workers_status", "workers")
    op.drop_constraint("ck_transcriptions_status", "transcriptions")
    op.drop_constraint("ck_transcoding_jobs_progress_percent_range", "transcoding_jobs")
    op.drop_constraint("ck_quality_progress_percent_range", "quality_progress")
    op.drop_constraint("ck_quality_progress_status", "quality_progress")
    op.drop_constraint("ck_videos_thumbnail_source", "videos")
    op.drop_constraint("ck_videos_status", "videos")
