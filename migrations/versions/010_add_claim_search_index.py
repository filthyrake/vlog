"""Add compound index for job claiming queries.

Fixes issue #343: Common job claiming queries would benefit from a compound index.

The worker_api claim_job endpoint queries:
    SELECT * FROM transcoding_jobs
    WHERE status = 'pending' (via videos join)
      AND claimed_at IS NULL
      AND completed_at IS NULL
    ORDER BY created_at ASC
    LIMIT 1

This migration adds a compound index to optimize these queries.

Revision ID: 010
Revises: 009
Create Date: 2025-12-21
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade():
    """Add compound index for job claiming queries."""
    # Create a compound index for the job claim search pattern.
    # The transcoding_jobs table is queried with:
    # - claimed_at IS NULL (unclaimed jobs)
    # - completed_at IS NULL (incomplete jobs)
    # - ORDER BY videos.created_at (in the joined query, not transcoding_jobs)
    #
    # Note: The actual claim query joins with videos table for status check
    # and ordering. This index helps filter transcoding_jobs rows quickly.
    op.create_index(
        "ix_transcoding_jobs_claim_search",
        "transcoding_jobs",
        ["claimed_at", "completed_at"],
        postgresql_where="claimed_at IS NULL AND completed_at IS NULL",
    )


def downgrade():
    """Remove compound index."""
    op.drop_index("ix_transcoding_jobs_claim_search", table_name="transcoding_jobs")
