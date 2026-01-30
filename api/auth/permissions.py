"""
Permission definitions and role-based access control.

Roles:
- admin: Full system access + user management
- editor: Upload, edit/delete own videos, view own analytics
- viewer: Browse and watch videos (for private instances)
"""

from __future__ import annotations

from enum import Enum
from typing import FrozenSet


class Role(str, Enum):
    """User roles with different access levels."""

    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class Permission(str, Enum):
    """Individual permissions that can be checked."""

    # Video permissions
    VIDEO_CREATE = "video:create"
    VIDEO_READ = "video:read"
    VIDEO_UPDATE = "video:update"
    VIDEO_UPDATE_ANY = "video:update:any"
    VIDEO_DELETE = "video:delete"
    VIDEO_DELETE_ANY = "video:delete:any"

    # Playlist permissions
    PLAYLIST_CREATE = "playlist:create"
    PLAYLIST_READ = "playlist:read"
    PLAYLIST_UPDATE = "playlist:update"
    PLAYLIST_UPDATE_ANY = "playlist:update:any"
    PLAYLIST_DELETE = "playlist:delete"
    PLAYLIST_DELETE_ANY = "playlist:delete:any"

    # Category permissions
    CATEGORY_CREATE = "category:create"
    CATEGORY_READ = "category:read"
    CATEGORY_UPDATE = "category:update"
    CATEGORY_DELETE = "category:delete"

    # Tag permissions
    TAG_CREATE = "tag:create"
    TAG_READ = "tag:read"
    TAG_UPDATE = "tag:update"
    TAG_DELETE = "tag:delete"

    # User management
    USER_READ = "user:read"
    USER_CREATE = "user:create"
    USER_UPDATE = "user:update"
    USER_DELETE = "user:delete"

    # System settings
    SETTINGS_READ = "settings:read"
    SETTINGS_UPDATE = "settings:update"

    # Worker management
    WORKERS_READ = "workers:read"
    WORKERS_MANAGE = "workers:manage"

    # Webhook management
    WEBHOOK_READ = "webhook:read"
    WEBHOOK_CREATE = "webhook:create"
    WEBHOOK_UPDATE = "webhook:update"
    WEBHOOK_DELETE = "webhook:delete"

    # Live streaming
    LIVE_STREAM_CREATE = "live_stream:create"
    LIVE_STREAM_READ = "live_stream:read"
    LIVE_STREAM_MANAGE = "live_stream:manage"

    # Analytics
    ANALYTICS_VIEW = "analytics:view"
    ANALYTICS_VIEW_ALL = "analytics:view:all"

    # Invite management
    INVITE_CREATE = "invite:create"
    INVITE_READ = "invite:read"
    INVITE_DELETE = "invite:delete"

    # Comment permissions (Issue #213)
    COMMENT_CREATE = "comment:create"
    COMMENT_READ = "comment:read"
    COMMENT_UPDATE = "comment:update"
    COMMENT_UPDATE_ANY = "comment:update:any"
    COMMENT_DELETE = "comment:delete"
    COMMENT_DELETE_ANY = "comment:delete:any"
    COMMENT_MODERATE = "comment:moderate"

    # Rating permissions (Issue #213)
    RATING_CREATE = "rating:create"
    RATING_READ = "rating:read"
    RATING_DELETE = "rating:delete"


# Role-to-permission mappings
# Admin has all permissions, editor has limited permissions, viewer is read-only
_ROLE_PERMISSIONS: dict[Role, FrozenSet[Permission]] = {
    Role.ADMIN: frozenset(Permission),  # All permissions
    Role.EDITOR: frozenset(
        [
            # Video permissions (own videos only)
            Permission.VIDEO_CREATE,
            Permission.VIDEO_READ,
            Permission.VIDEO_UPDATE,
            Permission.VIDEO_DELETE,
            # Playlist permissions (own playlists only)
            Permission.PLAYLIST_CREATE,
            Permission.PLAYLIST_READ,
            Permission.PLAYLIST_UPDATE,
            Permission.PLAYLIST_DELETE,
            # Read-only for categories and tags
            Permission.CATEGORY_READ,
            Permission.TAG_READ,
            Permission.TAG_CREATE,  # Editors can create tags
            # Own analytics
            Permission.ANALYTICS_VIEW,
            # Live streaming (own streams only)
            Permission.LIVE_STREAM_CREATE,
            Permission.LIVE_STREAM_READ,
            # Comment/rating permissions (Issue #213)
            Permission.COMMENT_CREATE,
            Permission.COMMENT_READ,
            Permission.COMMENT_UPDATE,  # Own comments only
            Permission.COMMENT_DELETE,  # Own comments only
            Permission.RATING_CREATE,
            Permission.RATING_READ,
            Permission.RATING_DELETE,  # Own rating only
        ]
    ),
    Role.VIEWER: frozenset(
        [
            # Read-only access
            Permission.VIDEO_READ,
            Permission.PLAYLIST_READ,
            Permission.CATEGORY_READ,
            Permission.TAG_READ,
            Permission.LIVE_STREAM_READ,
            # Comment/rating permissions (Issue #213)
            Permission.COMMENT_CREATE,
            Permission.COMMENT_READ,
            Permission.COMMENT_UPDATE,  # Own comments only
            Permission.COMMENT_DELETE,  # Own comments only
            Permission.RATING_CREATE,
            Permission.RATING_READ,
            Permission.RATING_DELETE,  # Own rating only
        ]
    ),
}

# Public alias for role-permission mappings
ROLE_PERMISSIONS = _ROLE_PERMISSIONS


def get_role_permissions(role: Role) -> FrozenSet[Permission]:
    """
    Get all permissions for a role.

    Args:
        role: The user's role

    Returns:
        Frozen set of permissions for the role
    """
    if isinstance(role, str):
        try:
            role = Role(role)
        except ValueError:
            return frozenset()

    return _ROLE_PERMISSIONS.get(role, frozenset())


def has_permission(role: Role, permission: Permission) -> bool:
    """
    Check if a role has a specific permission.

    Args:
        role: The user's role
        permission: The permission to check

    Returns:
        True if the role has the permission, False otherwise
    """
    permissions = get_role_permissions(role)
    return permission in permissions


def check_ownership_permission(
    role: Role,
    permission: Permission,
    owner_id: str | None,
    user_id: str,
) -> bool:
    """
    Check if a user has permission, considering ownership.

    For "any" permissions (like VIDEO_UPDATE_ANY), returns True if user has that permission.
    For regular permissions, returns True only if user owns the resource.

    Args:
        role: The user's role
        permission: The permission to check (e.g., VIDEO_UPDATE)
        owner_id: The owner ID of the resource (can be None for unowned resources)
        user_id: The current user's ID

    Returns:
        True if the user has permission for this resource
    """
    permissions = get_role_permissions(role)

    # Check for "any" permission (admin-level)
    any_permission_name = f"{permission.value}:any"
    for perm in permissions:
        if perm.value == any_permission_name:
            return True

    # Check regular permission + ownership
    if permission not in permissions:
        return False

    # If no owner or user is owner, allow
    return owner_id is None or owner_id == user_id


def get_all_roles() -> list[Role]:
    """Get list of all available roles."""
    return list(Role)


def get_role_display_name(role: Role) -> str:
    """Get human-readable name for a role."""
    display_names = {
        Role.ADMIN: "Administrator",
        Role.EDITOR: "Editor",
        Role.VIEWER: "Viewer",
    }
    if isinstance(role, str):
        try:
            role = Role(role)
        except ValueError:
            return role
    return display_names.get(role, role.value)
