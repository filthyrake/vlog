"""upgrade_api_key_hashing

Revision ID: 026
Revises: 025
Create Date: 2026-01-03

Upgrades API key hashing from SHA-256 to argon2id with per-key salts.
Adds hash_version column to support dual-format verification during migration.

Hash versions:
    1 = SHA-256 (legacy, 64 hex chars)
    2 = argon2id (new, ~100 chars with embedded salt)

DEPLOYMENT SEQUENCE:
    1. Deploy new code with argon2 support (reads both formats)
    2. Run this migration: alembic upgrade 026
    3. Existing keys continue working (hash_version=1, SHA-256)
    4. New keys automatically use argon2id (hash_version=2)
    5. Optional: Regenerate existing keys to upgrade to argon2

VERIFICATION (run after migration):
    -- Check migration succeeded
    SELECT COUNT(*) as legacy_keys FROM worker_api_keys WHERE hash_version = 1;
    SELECT COUNT(*) as argon2_keys FROM worker_api_keys WHERE hash_version = 2;

    -- Verify no NULL hash_versions
    SELECT COUNT(*) FROM worker_api_keys WHERE hash_version IS NULL;  -- Should be 0

ROLLBACK WARNING:
    The downgrade() function WILL FAIL if any argon2 keys exist (hash > 64 chars).
    To downgrade safely:
    1. Delete all keys with hash_version=2, OR
    2. Regenerate all argon2 keys as SHA-256 (requires plaintext, not possible)
    In practice, downgrade is DESTRUCTIVE - backup database first!
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
    """Remove hash_version column and restore key_hash width.

    WARNING: This is a DESTRUCTIVE operation!

    If any argon2 keys exist (hash_version=2), this migration will fail
    because argon2 hashes (~100 chars) won't fit in VARCHAR(64).

    Before downgrading, you must either:
    - Delete all argon2 keys: DELETE FROM worker_api_keys WHERE hash_version = 2
    - Or accept that those workers will need new keys after re-upgrading
    """
    # Check for argon2 keys that would be truncated
    connection = op.get_bind()
    result = connection.execute(
        sa.text("SELECT COUNT(*) FROM worker_api_keys WHERE hash_version = 2")
    )
    argon2_count = result.scalar()

    if argon2_count > 0:
        raise RuntimeError(
            f"Cannot downgrade: {argon2_count} argon2 keys exist (hash_version=2). "
            "These hashes are ~100 chars and won't fit in VARCHAR(64). "
            "Delete these keys first: DELETE FROM worker_api_keys WHERE hash_version = 2"
        )

    op.alter_column(
        "worker_api_keys",
        "key_hash",
        type_=sa.String(64),
        existing_type=sa.String(255),
        existing_nullable=False,
    )

    op.drop_column("worker_api_keys", "hash_version")
