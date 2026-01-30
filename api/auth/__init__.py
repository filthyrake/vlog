"""
User Authentication Module (Issue #200)

Provides multi-user authentication with:
- Session-based browser auth (HTTP-only cookies)
- API keys for programmatic access
- Role-based access control (RBAC)
- OIDC integration for self-hosted identity providers
"""

from api.auth.middleware import get_current_user, require_auth, require_permission, require_role
from api.auth.password import (
    generate_token,
    get_token_prefix,
    hash_password,
    hash_token,
    validate_password_strength,
    verify_password,
    verify_token,
)
from api.auth.permissions import (
    ROLE_PERMISSIONS,
    Permission,
    Role,
    get_role_permissions,
    has_permission,
)
from api.auth.sessions import (
    cleanup_expired_sessions,
    create_user_session,
    get_user_sessions,
    invalidate_session,
    invalidate_user_sessions,
    refresh_user_session,
    validate_session_token,
)

__all__ = [
    # Password utilities
    "hash_password",
    "verify_password",
    "validate_password_strength",
    "generate_token",
    "hash_token",
    "verify_token",
    "get_token_prefix",
    # Session management
    "create_user_session",
    "validate_session_token",
    "invalidate_session",
    "invalidate_user_sessions",
    "refresh_user_session",
    "get_user_sessions",
    "cleanup_expired_sessions",
    # Permissions
    "Permission",
    "Role",
    "ROLE_PERMISSIONS",
    "get_role_permissions",
    "has_permission",
    # Middleware
    "get_current_user",
    "require_auth",
    "require_permission",
    "require_role",
]
