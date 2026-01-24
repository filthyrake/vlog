"""add_api_key_rotation

Revision ID: 030
Revises: 029
Create Date: 2025-01-24

Adds support for API key expiration and rotation (Issue #226):
- rotated_from: Self-referential FK to track key rotation chain
- Index for efficient lookup of rotation relationships
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "030"
down_revision: Union[str, Sequence[str], None] = "029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add rotated_from column to worker_api_keys for key rotation tracking."""
    # Add rotated_from column - self-referential FK to track rotation chain
    # NULL means this is an original key (not created via rotation)
    # Points to the previous key that was rotated to create this one
    op.add_column(
        "worker_api_keys",
        sa.Column(
            "rotated_from",
            sa.Integer,
            sa.ForeignKey("worker_api_keys.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # Index for looking up rotation relationships (audit trail)
    op.create_index(
        "ix_worker_api_keys_rotated_from",
        "worker_api_keys",
        ["rotated_from"],
    )


def downgrade() -> None:
    """Remove rotated_from column from worker_api_keys."""
    op.drop_index("ix_worker_api_keys_rotated_from", table_name="worker_api_keys")
    op.drop_column("worker_api_keys", "rotated_from")
