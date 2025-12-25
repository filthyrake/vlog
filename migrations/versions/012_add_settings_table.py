"""add_settings_table

Revision ID: 012
Revises: 011
Create Date: 2025-12-25

Adds settings table for database-backed runtime configuration.
This enables managing settings via Admin UI instead of environment variables,
with caching and env var fallback for backwards compatibility.

See: https://github.com/filthyrake/vlog/issues/400
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "012"
down_revision: Union[str, Sequence[str], None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create settings table for runtime configuration management."""
    op.create_table(
        "settings",
        sa.Column("id", sa.Integer, primary_key=True),
        # Setting key - unique identifier (e.g., "transcoding.hls_segment_duration")
        sa.Column("key", sa.String(255), unique=True, nullable=False),
        # Value stored as JSONB to support all types (strings, numbers, booleans, arrays, objects)
        sa.Column("value", sa.Text, nullable=False),
        # Category for UI grouping (e.g., "transcoding", "watermark", "workers")
        sa.Column("category", sa.String(100), nullable=False),
        # Human-readable description/help text for admin UI
        sa.Column("description", sa.Text, nullable=True),
        # Value type for validation and UI rendering
        sa.Column(
            "value_type",
            sa.String(50),
            sa.CheckConstraint(
                "value_type IN ('string', 'integer', 'float', 'boolean', 'enum', 'json')",
                name="ck_settings_value_type"
            ),
            nullable=False,
            default="string"
        ),
        # Constraints as JSON (min, max, enum_values, pattern, etc.)
        sa.Column("constraints", sa.Text, nullable=True),
        # Audit fields
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_by", sa.String(255), nullable=True),
    )
    # Index on key for fast lookups
    op.create_index("ix_settings_key", "settings", ["key"])
    # Index on category for UI grouping queries
    op.create_index("ix_settings_category", "settings", ["category"])


def downgrade() -> None:
    """Drop settings table."""
    op.drop_index("ix_settings_category", table_name="settings")
    op.drop_index("ix_settings_key", table_name="settings")
    op.drop_table("settings")
