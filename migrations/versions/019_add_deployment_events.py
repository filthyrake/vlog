"""add_deployment_events

Revision ID: 019
Revises: 018
Create Date: 2025-12-26

Add deployment_events table for worker management (Issue #410 Phase 4).
Tracks worker restarts, updates, version changes, and other deployment events.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "019"
down_revision: Union[str, Sequence[str], None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create deployment_events table for worker management tracking."""
    op.create_table(
        "deployment_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("worker_id", sa.String(36), nullable=False),
        sa.Column("worker_name", sa.String(100), nullable=True),
        sa.Column(
            "event_type",
            sa.String(20),
            nullable=False,
        ),
        sa.Column("old_version", sa.String(64), nullable=True),
        sa.Column("new_version", sa.String(64), nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            default="pending",
        ),
        sa.Column("triggered_by", sa.String(100), nullable=True),
        sa.Column("details", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Add check constraints for event_type and status
    op.create_check_constraint(
        "ck_deployment_events_type",
        "deployment_events",
        "event_type IN ('restart', 'stop', 'update', 'deploy', 'rollback', 'version_change')",
    )
    op.create_check_constraint(
        "ck_deployment_events_status",
        "deployment_events",
        "status IN ('pending', 'in_progress', 'completed', 'failed')",
    )

    # Add indexes for common queries
    op.create_index(
        "ix_deployment_events_worker_id",
        "deployment_events",
        ["worker_id"],
    )
    op.create_index(
        "ix_deployment_events_created_at",
        "deployment_events",
        ["created_at"],
    )


def downgrade() -> None:
    """Drop deployment_events table."""
    op.drop_index("ix_deployment_events_created_at", table_name="deployment_events")
    op.drop_index("ix_deployment_events_worker_id", table_name="deployment_events")
    op.drop_constraint("ck_deployment_events_status", "deployment_events", type_="check")
    op.drop_constraint("ck_deployment_events_type", "deployment_events", type_="check")
    op.drop_table("deployment_events")
