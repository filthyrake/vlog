"""upgrade_api_key_hashing

Revision ID: 026
Revises: 025
Create Date: 2026-01-03

Upgrades API key hashing from SHA-256 to argon2id with per-key salts.
Adds hash_version column to support dual-format verification during migration.

Hash versions:
    1 = SHA-256 (legacy, 64 hex chars)
    2 = argon2id (new, ~100 chars with embedded salt)
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "026"
down_revision: Union[str, Sequence[str], None] = "025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add hash_version column and widen key_hash for argon2 hashes."""
    # Add hash_version column with default 1 (SHA-256) for existing rows
    # New rows will get default 2 (argon2id) from application code
    op.add_column(
        "worker_api_keys",
        sa.Column("hash_version", sa.Integer, nullable=False, server_default="1"),
    )

    # Widen key_hash column to accommodate argon2 hashes (~100 chars)
    # SHA-256 produces 64 hex chars, argon2id produces ~97 chars
    op.alter_column(
        "worker_api_keys",
        "key_hash",
        type_=sa.String(255),
        existing_type=sa.String(64),
        existing_nullable=False,
    )

    # Explicitly set existing rows to hash_version=1 (SHA-256)
    # This is defensive in case the server_default wasn't applied
    op.execute("UPDATE worker_api_keys SET hash_version = 1 WHERE hash_version IS NULL")


def downgrade() -> None:
    """Remove hash_version column and restore key_hash width."""
    # Note: Downgrade will fail if any argon2 hashes exist (> 64 chars)
    # This is intentional - don't downgrade if you've created new keys

    op.alter_column(
        "worker_api_keys",
        "key_hash",
        type_=sa.String(64),
        existing_type=sa.String(255),
        existing_nullable=False,
    )

    op.drop_column("worker_api_keys", "hash_version")
