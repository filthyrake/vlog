"""add_webhooks

Revision ID: 027
Revises: 026
Create Date: 2025-01-04

Adds webhook notification system for external integrations:
- webhooks: Webhook subscription configuration
- webhook_deliveries: Delivery attempt history and retry tracking

Implements GitHub issue #203.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "027"
down_revision: Union[str, Sequence[str], None] = "026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create webhooks and webhook_deliveries tables."""
    # Create webhooks table
    op.create_table(
        "webhooks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),  # Human-readable name
        sa.Column("url", sa.String(500), nullable=False),  # Webhook endpoint URL
        sa.Column("events", sa.Text, nullable=False),  # JSON array: ["video.ready", "video.failed"]
        sa.Column("secret", sa.String(64), nullable=True),  # HMAC-SHA256 signing key
        sa.Column("active", sa.Boolean, default=True, nullable=False),  # Can be disabled
        sa.Column("headers", sa.Text, nullable=True),  # JSON: custom headers to include
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        # Statistics
        sa.Column("total_deliveries", sa.Integer, default=0, nullable=False),
        sa.Column("successful_deliveries", sa.Integer, default=0, nullable=False),
        sa.Column("failed_deliveries", sa.Integer, default=0, nullable=False),
    )
    op.create_index("ix_webhooks_active", "webhooks", ["active"])
    op.create_index("ix_webhooks_created_at", "webhooks", ["created_at"])

    # Create webhook_deliveries table
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "webhook_id",
            sa.Integer,
            sa.ForeignKey("webhooks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(50), nullable=False),  # video.ready, etc.
        sa.Column("event_data", sa.Text, nullable=False),  # JSON payload
        sa.Column("request_body", sa.Text, nullable=True),  # Full request sent
        sa.Column("response_status", sa.Integer, nullable=True),  # HTTP status code
        sa.Column("response_body", sa.Text, nullable=True),  # Response (truncated)
        sa.Column("error_message", sa.Text, nullable=True),  # Error if request failed
        sa.Column("attempt_number", sa.Integer, default=1, nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            sa.CheckConstraint(
                "status IN ('pending', 'delivered', 'failed', 'failed_permanent')",
                name="ck_webhook_deliveries_status",
            ),
            default="pending",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),  # Request duration
    )
    op.create_index("ix_webhook_deliveries_webhook_id", "webhook_deliveries", ["webhook_id"])
    op.create_index("ix_webhook_deliveries_status", "webhook_deliveries", ["status"])
    op.create_index("ix_webhook_deliveries_event_type", "webhook_deliveries", ["event_type"])
    op.create_index("ix_webhook_deliveries_next_retry_at", "webhook_deliveries", ["next_retry_at"])
    op.create_index("ix_webhook_deliveries_created_at", "webhook_deliveries", ["created_at"])
    # Composite index for efficient pending delivery queries
    op.create_index(
        "ix_webhook_deliveries_status_next_retry",
        "webhook_deliveries",
        ["status", "next_retry_at"],
    )


def downgrade() -> None:
    """Remove webhooks and webhook_deliveries tables."""
    op.drop_index("ix_webhook_deliveries_status_next_retry", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_created_at", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_next_retry_at", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_event_type", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_status", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_webhook_id", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")
    op.drop_index("ix_webhooks_created_at", table_name="webhooks")
    op.drop_index("ix_webhooks_active", table_name="webhooks")
    op.drop_table("webhooks")
