"""Fix quality_progress status constraint to include 'uploading'.

The Pydantic schema allows 'uploading' as a status but the database
constraint was missing it, causing 500 errors during transcoding.

Revision ID: 017_fix_quality_progress_status
Revises: 016_add_custom_fields
Create Date: 2025-12-26
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the old constraint
    op.drop_constraint("ck_quality_progress_status", "quality_progress", type_="check")

    # Create new constraint with 'uploading' added
    op.create_check_constraint(
        "ck_quality_progress_status",
        "quality_progress",
        "status IN ('pending', 'in_progress', 'uploading', 'completed', 'failed', 'skipped', 'uploaded')",
    )


def downgrade() -> None:
    # Drop the new constraint
    op.drop_constraint("ck_quality_progress_status", "quality_progress", type_="check")

    # Recreate old constraint without 'uploading'
    op.create_check_constraint(
        "ck_quality_progress_status",
        "quality_progress",
        "status IN ('pending', 'in_progress', 'completed', 'failed', 'skipped', 'uploaded')",
    )
