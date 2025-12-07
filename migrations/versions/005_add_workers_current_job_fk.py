"""add_workers_current_job_fk

Revision ID: 005
Revises: 004
Create Date: 2025-12-07

Adds foreign key constraint on workers.current_job_id referencing transcoding_jobs.id
with ON DELETE SET NULL behavior.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "005"
down_revision: Union[str, Sequence[str], None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add FK constraint on workers.current_job_id.

    First cleans up any invalid references, then adds the constraint.
    """
    # Clean up any invalid current_job_id values that reference non-existent jobs
    # This ensures the FK constraint can be added without errors
    # Using NOT EXISTS instead of NOT IN for better performance and NULL handling
    op.execute("""
        UPDATE workers
        SET current_job_id = NULL
        WHERE current_job_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM transcoding_jobs
              WHERE transcoding_jobs.id = workers.current_job_id
          )
    """)

    # Add the foreign key constraint with ON DELETE SET NULL
    # When a transcoding job is deleted, the worker's current_job_id is set to NULL
    op.create_foreign_key(
        "fk_workers_current_job_id",
        "workers",
        "transcoding_jobs",
        ["current_job_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Remove FK constraint on workers.current_job_id."""
    op.drop_constraint("fk_workers_current_job_id", "workers", type_="foreignkey")
