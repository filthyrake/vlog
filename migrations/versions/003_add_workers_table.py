"""add_workers_table

Revision ID: 003
Revises: 002
Create Date: 2025-12-04

Adds worker registration, API key management, and distributed job claiming support
for containerized remote transcoding workers.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: Union[str, Sequence[str], None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create workers and worker_api_keys tables, add job claiming columns."""
    # Workers table - tracks registered transcoding workers
    op.create_table(
        "workers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("worker_id", sa.String(36), unique=True, nullable=False),  # UUID
        sa.Column("worker_name", sa.String(100), nullable=True),
        sa.Column("worker_type", sa.String(20), server_default="remote"),  # 'local' or 'remote'
        sa.Column("registered_at", sa.DateTime, nullable=False),
        sa.Column("last_heartbeat", sa.DateTime, nullable=True),
        sa.Column("status", sa.String(20), server_default="active"),  # 'active', 'offline', 'disabled'
        sa.Column("current_job_id", sa.Integer, nullable=True),  # FK added later to avoid circular ref
        sa.Column("capabilities", sa.Text, nullable=True),  # JSON: {"max_resolution": 2160, "gpu": false}
        sa.Column("metadata", sa.Text, nullable=True),  # JSON: {"kubernetes_pod": "...", "node": "..."}
    )
    op.create_index("ix_workers_status", "workers", ["status"])
    op.create_index("ix_workers_last_heartbeat", "workers", ["last_heartbeat"])
    op.create_index("ix_workers_worker_id", "workers", ["worker_id"])

    # Worker API keys table - supports key rotation and revocation
    op.create_table(
        "worker_api_keys",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "worker_id",
            sa.Integer,
            sa.ForeignKey("workers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key_hash", sa.String(64), nullable=False),  # SHA-256 hash
        sa.Column("key_prefix", sa.String(8), nullable=False),  # First 8 chars for lookup
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("expires_at", sa.DateTime, nullable=True),
        sa.Column("revoked_at", sa.DateTime, nullable=True),
        sa.Column("last_used_at", sa.DateTime, nullable=True),
    )
    op.create_index("ix_worker_api_keys_key_prefix", "worker_api_keys", ["key_prefix"])
    op.create_index("ix_worker_api_keys_worker_id", "worker_api_keys", ["worker_id"])

    # Add job claiming columns to transcoding_jobs
    op.add_column("transcoding_jobs", sa.Column("claimed_at", sa.DateTime, nullable=True))
    op.add_column("transcoding_jobs", sa.Column("claim_expires_at", sa.DateTime, nullable=True))
    op.create_index("ix_transcoding_jobs_claim_expires", "transcoding_jobs", ["claim_expires_at"])


def downgrade() -> None:
    """Remove workers tables and job claiming columns."""
    # Remove job claiming columns
    op.drop_index("ix_transcoding_jobs_claim_expires", table_name="transcoding_jobs")
    op.drop_column("transcoding_jobs", "claim_expires_at")
    op.drop_column("transcoding_jobs", "claimed_at")

    # Drop worker_api_keys table
    op.drop_index("ix_worker_api_keys_worker_id", table_name="worker_api_keys")
    op.drop_index("ix_worker_api_keys_key_prefix", table_name="worker_api_keys")
    op.drop_table("worker_api_keys")

    # Drop workers table
    op.drop_index("ix_workers_worker_id", table_name="workers")
    op.drop_index("ix_workers_last_heartbeat", table_name="workers")
    op.drop_index("ix_workers_status", table_name="workers")
    op.drop_table("workers")
