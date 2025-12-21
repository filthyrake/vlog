"""add_admin_sessions_table

Revision ID: 009
Revises: 008
Create Date: 2025-12-21

Adds admin_sessions table for secure server-side session management.
Fixes security issue where admin secret was stored in sessionStorage (XSS vulnerable).
Sessions are stored server-side with HTTP-only cookies for browser authentication.

See: https://github.com/filthyrake/vlog/issues/324
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "009"
down_revision: Union[str, Sequence[str], None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create admin_sessions table for HTTP-only cookie-based authentication."""
    op.create_table(
        "admin_sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        # 128 chars provides safety margin for 64-char tokens from secrets.token_urlsafe(48)
        sa.Column("session_token", sa.String(128), unique=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),  # IPv6 max length
        sa.Column("user_agent", sa.String(512), nullable=True),
    )
    op.create_index("ix_admin_sessions_session_token", "admin_sessions", ["session_token"])
    op.create_index("ix_admin_sessions_expires_at", "admin_sessions", ["expires_at"])


def downgrade() -> None:
    """Drop admin_sessions table."""
    op.drop_index("ix_admin_sessions_expires_at", table_name="admin_sessions")
    op.drop_index("ix_admin_sessions_session_token", table_name="admin_sessions")
    op.drop_table("admin_sessions")
