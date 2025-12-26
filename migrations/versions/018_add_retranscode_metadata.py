"""add_retranscode_metadata

Revision ID: 018
Revises: 017
Create Date: 2025-12-26

Adds retranscode_metadata column to transcoding_jobs table to store cleanup
information for deferred retranscode operations. This allows videos to remain
playable until a worker actually claims and starts processing the job.

Implements GitHub issue #408.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "018"
down_revision: Union[str, Sequence[str], None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add retranscode_metadata column to transcoding_jobs."""
    op.add_column(
        "transcoding_jobs",
        sa.Column("retranscode_metadata", sa.Text, nullable=True),
    )


def downgrade() -> None:
    """Remove retranscode_metadata column from transcoding_jobs."""
    op.drop_column("transcoding_jobs", "retranscode_metadata")
