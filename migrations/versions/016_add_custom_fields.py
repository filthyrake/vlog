"""add_custom_fields

Revision ID: 016
Revises: 015
Create Date: 2025-12-26

Adds custom metadata fields for videos:
- custom_field_definitions: Field definitions (global or per-category)
- video_custom_fields: Field values per video

Implements GitHub issue #224.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "016"
down_revision: Union[str, Sequence[str], None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create custom field tables."""
    # Create custom_field_definitions table
    op.create_table(
        "custom_field_definitions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column(
            "field_type",
            sa.String(20),
            sa.CheckConstraint(
                "field_type IN ('text', 'number', 'date', 'select', 'multi_select', 'url')",
                name="ck_custom_field_definitions_field_type",
            ),
            nullable=False,
        ),
        sa.Column("options", sa.Text, nullable=True),
        sa.Column("required", sa.Boolean, default=False, nullable=False),
        sa.Column(
            "category_id",
            sa.Integer,
            sa.ForeignKey("categories.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("position", sa.Integer, default=0, nullable=False),
        sa.Column("constraints", sa.Text, nullable=True),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("slug", "category_id", name="uq_custom_field_slug_category"),
    )
    op.create_index(
        "ix_custom_field_definitions_category_id",
        "custom_field_definitions",
        ["category_id"],
    )
    op.create_index(
        "ix_custom_field_definitions_position",
        "custom_field_definitions",
        ["position"],
    )

    # Partial unique index for global fields (category_id IS NULL)
    # This ensures unique slugs among global fields since UNIQUE(slug, category_id)
    # treats NULL values as distinct in PostgreSQL
    op.execute(
        "CREATE UNIQUE INDEX ix_custom_field_slug_global "
        "ON custom_field_definitions(slug) WHERE category_id IS NULL"
    )

    # Create video_custom_fields junction table
    op.create_table(
        "video_custom_fields",
        sa.Column(
            "video_id",
            sa.Integer,
            sa.ForeignKey("videos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "field_id",
            sa.Integer,
            sa.ForeignKey("custom_field_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("value", sa.Text, nullable=True),
        sa.PrimaryKeyConstraint("video_id", "field_id"),
    )
    op.create_index(
        "ix_video_custom_fields_video_id", "video_custom_fields", ["video_id"]
    )
    op.create_index(
        "ix_video_custom_fields_field_id", "video_custom_fields", ["field_id"]
    )


def downgrade() -> None:
    """Remove custom field tables."""
    op.drop_index("ix_video_custom_fields_field_id", table_name="video_custom_fields")
    op.drop_index("ix_video_custom_fields_video_id", table_name="video_custom_fields")
    op.drop_table("video_custom_fields")
    op.execute("DROP INDEX IF EXISTS ix_custom_field_slug_global")
    op.drop_index(
        "ix_custom_field_definitions_position", table_name="custom_field_definitions"
    )
    op.drop_index(
        "ix_custom_field_definitions_category_id", table_name="custom_field_definitions"
    )
    op.drop_table("custom_field_definitions")
