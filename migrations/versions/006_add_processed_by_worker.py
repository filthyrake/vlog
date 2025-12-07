"""add_processed_by_worker

Revision ID: 006
Revises: 005
Create Date: 2025-12-07

Adds permanent worker tracking columns to transcoding_jobs for audit and debugging.
The existing worker_id column is used for claim tracking and gets cleared on retry.
These new columns provide a permanent record of which worker processed the job.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "006"
down_revision: Union[str, Sequence[str], None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add processed_by_worker_id and processed_by_worker_name columns.

    These columns record which worker processed the job and are preserved
    after job completion or failure for debugging and analytics.
    """
    # Add column for worker UUID that processed the job
    op.add_column(
        "transcoding_jobs",
        sa.Column("processed_by_worker_id", sa.String(36), nullable=True),
    )

    # Add column for worker name (human-readable) for easier debugging
    op.add_column(
        "transcoding_jobs",
        sa.Column("processed_by_worker_name", sa.String(100), nullable=True),
    )

    # Backfill existing completed/failed jobs with current worker_id if present
    # This preserves any historical data we have
    op.execute("""
        UPDATE transcoding_jobs
        SET processed_by_worker_id = worker_id
        WHERE worker_id IS NOT NULL
          AND (completed_at IS NOT NULL OR last_error IS NOT NULL)
    """)


def downgrade() -> None:
    """Remove processed_by_worker columns."""
    op.drop_column("transcoding_jobs", "processed_by_worker_name")
    op.drop_column("transcoding_jobs", "processed_by_worker_id")
