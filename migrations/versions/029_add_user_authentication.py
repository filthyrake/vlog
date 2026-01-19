"""add_user_authentication

Revision ID: 029
Revises: 028
Create Date: 2025-01-19

Implements multi-user authentication system (Issue #200):
- users: User accounts with role-based access control
- user_sessions: Session tokens with refresh token rotation
- user_api_keys: API keys for programmatic access
- oidc_connections: OIDC provider linking
- oidc_states: CSRF/replay protection for OIDC
- password_reset_tokens: Password reset flow
- user_invites: Invite-only registration

Also adds owner_id column to videos table for editor ownership.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "029"
down_revision: Union[str, Sequence[str], None] = "028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create user authentication tables and add owner_id to videos."""
    # ==========================================================================
    # Create users table
    # ==========================================================================
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),  # UUID
        sa.Column("username", sa.String(100), unique=True, nullable=False),
        sa.Column("email", sa.String(255), unique=True, nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=True),  # NULL for OIDC-only
        sa.Column("display_name", sa.String(100), nullable=True),
        sa.Column("avatar_url", sa.String(500), nullable=True),
        sa.Column(
            "role",
            sa.String(20),
            sa.CheckConstraint(
                "role IN ('admin', 'editor', 'viewer')",
                name="ck_users_role",
            ),
            default="viewer",
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(20),
            sa.CheckConstraint(
                "status IN ('active', 'disabled', 'pending')",
                name="ck_users_status",
            ),
            default="active",
            nullable=False,
        ),
        sa.Column("email_verified", sa.Boolean, default=False, nullable=False),
        sa.Column("failed_login_attempts", sa.Integer, default=0, nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),  # Self-referential FK added later
    )
    op.create_index("ix_users_username", "users", ["username"])
    op.create_index("ix_users_email", "users", ["email"])
    op.create_index("ix_users_role", "users", ["role"])
    op.create_index("ix_users_status", "users", ["status"])
    op.create_index("ix_users_created_at", "users", ["created_at"])

    # ==========================================================================
    # Create user_sessions table
    # ==========================================================================
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.String(36), primary_key=True),  # UUID
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(255), unique=True, nullable=False),
        sa.Column("token_prefix", sa.String(8), nullable=True),  # For indexed lookup
        sa.Column("refresh_token_hash", sa.String(255), unique=True, nullable=True),
        sa.Column("refresh_token_prefix", sa.String(8), nullable=True),  # For indexed lookup
        sa.Column("refresh_family_id", sa.String(36), nullable=True),  # UUID
        sa.Column("refresh_generation", sa.Integer, default=0, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),  # Track session usage
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_token_hash", "user_sessions", ["token_hash"])
    op.create_index("ix_user_sessions_token_prefix", "user_sessions", ["token_prefix"])
    op.create_index("ix_user_sessions_refresh_token_hash", "user_sessions", ["refresh_token_hash"])
    op.create_index("ix_user_sessions_refresh_token_prefix", "user_sessions", ["refresh_token_prefix"])
    op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])
    op.create_index("ix_user_sessions_refresh_family_id", "user_sessions", ["refresh_family_id"])

    # ==========================================================================
    # Create user_api_keys table
    # ==========================================================================
    op.create_table(
        "user_api_keys",
        sa.Column("id", sa.String(36), primary_key=True),  # UUID
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("key_prefix", sa.String(8), nullable=False),
        sa.Column("key_hash", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_user_api_keys_user_id", "user_api_keys", ["user_id"])
    op.create_index("ix_user_api_keys_key_prefix", "user_api_keys", ["key_prefix"])

    # ==========================================================================
    # Create oidc_connections table
    # ==========================================================================
    op.create_table(
        "oidc_connections",
        sa.Column("id", sa.String(36), primary_key=True),  # UUID
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider_user_id", sa.String(255), nullable=False),
        sa.Column("provider_email", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("provider_user_id", name="uq_oidc_connections_provider_user_id"),
    )
    op.create_index("ix_oidc_connections_user_id", "oidc_connections", ["user_id"])
    op.create_index("ix_oidc_connections_provider_user_id", "oidc_connections", ["provider_user_id"])

    # ==========================================================================
    # Create oidc_states table
    # ==========================================================================
    op.create_table(
        "oidc_states",
        sa.Column("id", sa.String(36), primary_key=True),  # UUID
        sa.Column("state", sa.String(64), unique=True, nullable=False),
        sa.Column("nonce", sa.String(64), nullable=False),
        sa.Column("redirect_uri", sa.String(500), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_oidc_states_state", "oidc_states", ["state"])
    op.create_index("ix_oidc_states_expires_at", "oidc_states", ["expires_at"])

    # ==========================================================================
    # Create password_reset_tokens table
    # ==========================================================================
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.String(36), primary_key=True),  # UUID
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(255), unique=True, nullable=False),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"])
    op.create_index("ix_password_reset_tokens_token_hash", "password_reset_tokens", ["token_hash"])
    op.create_index("ix_password_reset_tokens_expires_at", "password_reset_tokens", ["expires_at"])

    # ==========================================================================
    # Create user_invites table
    # ==========================================================================
    op.create_table(
        "user_invites",
        sa.Column("id", sa.String(36), primary_key=True),  # UUID
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column(
            "role",
            sa.String(20),
            sa.CheckConstraint(
                "role IN ('admin', 'editor', 'viewer')",
                name="ck_user_invites_role",
            ),
            default="viewer",
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(255), unique=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "used_by",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_user_invites_email", "user_invites", ["email"])
    op.create_index("ix_user_invites_token_hash", "user_invites", ["token_hash"])
    op.create_index("ix_user_invites_expires_at", "user_invites", ["expires_at"])

    # ==========================================================================
    # Add owner_id column to videos table
    # ==========================================================================
    # Nullable for backward compatibility - existing videos assigned to first admin during migration
    op.add_column(
        "videos",
        sa.Column(
            "owner_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_videos_owner_id", "videos", ["owner_id"])


def downgrade() -> None:
    """Remove user authentication tables and owner_id from videos."""
    # Remove owner_id from videos
    op.drop_index("ix_videos_owner_id", table_name="videos")
    op.drop_column("videos", "owner_id")

    # Drop user_invites
    op.drop_index("ix_user_invites_expires_at", table_name="user_invites")
    op.drop_index("ix_user_invites_token_hash", table_name="user_invites")
    op.drop_index("ix_user_invites_email", table_name="user_invites")
    op.drop_table("user_invites")

    # Drop password_reset_tokens
    op.drop_index("ix_password_reset_tokens_expires_at", table_name="password_reset_tokens")
    op.drop_index("ix_password_reset_tokens_token_hash", table_name="password_reset_tokens")
    op.drop_index("ix_password_reset_tokens_user_id", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")

    # Drop oidc_states
    op.drop_index("ix_oidc_states_expires_at", table_name="oidc_states")
    op.drop_index("ix_oidc_states_state", table_name="oidc_states")
    op.drop_table("oidc_states")

    # Drop oidc_connections
    op.drop_index("ix_oidc_connections_provider_user_id", table_name="oidc_connections")
    op.drop_index("ix_oidc_connections_user_id", table_name="oidc_connections")
    op.drop_table("oidc_connections")

    # Drop user_api_keys
    op.drop_index("ix_user_api_keys_key_prefix", table_name="user_api_keys")
    op.drop_index("ix_user_api_keys_user_id", table_name="user_api_keys")
    op.drop_table("user_api_keys")

    # Drop user_sessions
    op.drop_index("ix_user_sessions_refresh_family_id", table_name="user_sessions")
    op.drop_index("ix_user_sessions_expires_at", table_name="user_sessions")
    op.drop_index("ix_user_sessions_refresh_token_hash", table_name="user_sessions")
    op.drop_index("ix_user_sessions_token_hash", table_name="user_sessions")
    op.drop_index("ix_user_sessions_user_id", table_name="user_sessions")
    op.drop_table("user_sessions")

    # Drop users
    op.drop_index("ix_users_created_at", table_name="users")
    op.drop_index("ix_users_status", table_name="users")
    op.drop_index("ix_users_role", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
