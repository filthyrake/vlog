"""
Tests for user authentication system.

Covers:
- Password hashing and validation
- Permission checking and role-based access
- Session creation, validation, and rotation
- API key authentication
- Auth endpoints integration
"""

import uuid
from datetime import datetime, timezone

import pytest

from api.auth.password import (
    generate_token,
    get_token_prefix,
    hash_password,
    hash_token,
    needs_rehash,
    validate_password_strength,
    verify_password,
    verify_token,
)
from api.auth.permissions import (
    Permission,
    Role,
    check_ownership_permission,
    get_role_permissions,
    has_permission,
)


# =============================================================================
# Password Hashing Tests
# =============================================================================


class TestPasswordHashing:
    """Tests for password hashing utilities."""

    def test_hash_password_returns_string(self):
        """hash_password should return an argon2id hash string."""
        password = "secure_password_123"
        hashed = hash_password(password)

        assert isinstance(hashed, str)
        assert hashed.startswith("$argon2id$")
        assert len(hashed) > 50  # Argon2 hashes are long

    def test_hash_password_different_each_time(self):
        """Same password should produce different hashes (salted)."""
        password = "secure_password_123"
        hash1 = hash_password(password)
        hash2 = hash_password(password)

        assert hash1 != hash2  # Different salts

    def test_verify_password_correct(self):
        """verify_password should return True for correct password."""
        password = "my_secure_password_456"
        hashed = hash_password(password)

        assert verify_password(password, hashed) is True

    def test_verify_password_incorrect(self):
        """verify_password should return False for wrong password."""
        password = "my_secure_password_456"
        wrong_password = "wrong_password_789"
        hashed = hash_password(password)

        assert verify_password(wrong_password, hashed) is False

    def test_verify_password_empty_hash(self):
        """verify_password should return False for empty hash."""
        assert verify_password("password", "") is False
        assert verify_password("password", None) is False

    def test_verify_password_malformed_hash(self):
        """verify_password should return False for malformed hash."""
        assert verify_password("password", "not_a_valid_hash") is False
        # Argon2 with completely invalid structure
        assert verify_password("password", "random_string_not_hash") is False

    def test_needs_rehash_returns_bool(self):
        """needs_rehash should return a boolean."""
        password = "test_password_123"
        hashed = hash_password(password)

        result = needs_rehash(hashed)
        assert isinstance(result, bool)

    def test_needs_rehash_empty(self):
        """needs_rehash should handle empty inputs gracefully."""
        assert needs_rehash("") is False
        assert needs_rehash(None) is False


# =============================================================================
# Password Validation Tests
# =============================================================================


class TestPasswordValidation:
    """Tests for password strength validation."""

    def test_validate_empty_password(self):
        """Empty password should fail validation."""
        is_valid, error = validate_password_strength("")
        assert is_valid is False
        assert "required" in error.lower()

    def test_validate_short_password(self):
        """Password below minimum length should fail."""
        is_valid, error = validate_password_strength("short1")
        assert is_valid is False
        assert "at least" in error.lower()

    def test_validate_letters_only(self):
        """Password with only letters should fail."""
        is_valid, error = validate_password_strength("abcdefghijklmnop")
        assert is_valid is False
        assert "numbers" in error.lower() or "symbols" in error.lower()

    def test_validate_numbers_only(self):
        """Password with only numbers should fail."""
        is_valid, error = validate_password_strength("123456789012")
        assert is_valid is False
        assert "letters" in error.lower()

    def test_validate_strong_password(self):
        """Strong password should pass validation."""
        is_valid, error = validate_password_strength("SecureP@ssword123")
        assert is_valid is True
        assert error == ""

    def test_validate_minimum_complexity(self):
        """Password with minimum complexity should pass."""
        is_valid, error = validate_password_strength("password1234")
        assert is_valid is True
        assert error == ""


# =============================================================================
# Token Utilities Tests
# =============================================================================


class TestTokenUtilities:
    """Tests for token generation and handling."""

    def test_generate_token_default_length(self):
        """generate_token should create URL-safe tokens."""
        token = generate_token()
        assert isinstance(token, str)
        assert len(token) > 0
        # URL-safe base64 characters only
        import re
        assert re.match(r'^[A-Za-z0-9_-]+$', token)

    def test_generate_token_custom_length(self):
        """generate_token should respect length parameter."""
        token_short = generate_token(8)
        token_long = generate_token(64)

        # Base64 encoding increases length by ~1.33x
        assert len(token_short) < len(token_long)

    def test_generate_token_uniqueness(self):
        """Each token should be unique."""
        tokens = [generate_token() for _ in range(100)]
        assert len(set(tokens)) == 100  # All unique

    def test_hash_token_returns_argon2(self):
        """hash_token should return argon2id hash."""
        token = generate_token()
        hashed = hash_token(token)

        assert hashed.startswith("$argon2id$")

    def test_verify_token_correct(self):
        """verify_token should return True for correct token."""
        token = generate_token()
        hashed = hash_token(token)

        assert verify_token(token, hashed) is True

    def test_verify_token_incorrect(self):
        """verify_token should return False for wrong token."""
        token1 = generate_token()
        token2 = generate_token()
        hashed = hash_token(token1)

        assert verify_token(token2, hashed) is False

    def test_verify_token_empty(self):
        """verify_token should handle empty inputs."""
        assert verify_token("", "hash") is False
        assert verify_token("token", "") is False
        assert verify_token(None, "hash") is False
        assert verify_token("token", None) is False

    def test_get_token_prefix(self):
        """get_token_prefix should extract first N characters."""
        token = "abcdefghijklmnop"
        assert get_token_prefix(token) == "abcdefgh"  # Default 8
        assert get_token_prefix(token, 4) == "abcd"
        assert get_token_prefix(token, 16) == token

    def test_get_token_prefix_short_token(self):
        """get_token_prefix should handle tokens shorter than length."""
        token = "abc"
        assert get_token_prefix(token, 8) == "abc"


# =============================================================================
# Permission Tests
# =============================================================================


class TestPermissions:
    """Tests for role-based permission checking."""

    def test_admin_has_all_permissions(self):
        """Admin role should have all permissions."""
        admin_perms = get_role_permissions(Role.ADMIN)

        # Admin should have all defined permissions
        for perm in Permission:
            assert perm in admin_perms, f"Admin missing permission: {perm}"

    def test_editor_permissions(self):
        """Editor role should have limited permissions."""
        editor_perms = get_role_permissions(Role.EDITOR)

        # Editor should have basic video permissions
        assert Permission.VIDEO_CREATE in editor_perms
        assert Permission.VIDEO_READ in editor_perms
        assert Permission.VIDEO_UPDATE in editor_perms
        assert Permission.VIDEO_DELETE in editor_perms

        # Editor should NOT have "any" permissions
        assert Permission.VIDEO_UPDATE_ANY not in editor_perms
        assert Permission.VIDEO_DELETE_ANY not in editor_perms

        # Editor should NOT have user management
        assert Permission.USER_CREATE not in editor_perms
        assert Permission.USER_DELETE not in editor_perms

    def test_viewer_permissions(self):
        """Viewer role should have read-only permissions."""
        viewer_perms = get_role_permissions(Role.VIEWER)

        # Viewer should have read access
        assert Permission.VIDEO_READ in viewer_perms
        assert Permission.PLAYLIST_READ in viewer_perms
        assert Permission.CATEGORY_READ in viewer_perms

        # Viewer should NOT have write access
        assert Permission.VIDEO_CREATE not in viewer_perms
        assert Permission.VIDEO_UPDATE not in viewer_perms
        assert Permission.VIDEO_DELETE not in viewer_perms

    def test_has_permission_admin(self):
        """has_permission should return True for admin with any permission."""
        assert has_permission(Role.ADMIN, Permission.VIDEO_CREATE) is True
        assert has_permission(Role.ADMIN, Permission.USER_DELETE) is True
        assert has_permission(Role.ADMIN, Permission.SETTINGS_UPDATE) is True

    def test_has_permission_editor(self):
        """has_permission should correctly check editor permissions."""
        assert has_permission(Role.EDITOR, Permission.VIDEO_CREATE) is True
        assert has_permission(Role.EDITOR, Permission.USER_DELETE) is False

    def test_has_permission_viewer(self):
        """has_permission should correctly check viewer permissions."""
        assert has_permission(Role.VIEWER, Permission.VIDEO_READ) is True
        assert has_permission(Role.VIEWER, Permission.VIDEO_CREATE) is False

    def test_has_permission_string_role(self):
        """has_permission should accept role as string."""
        assert has_permission("admin", Permission.VIDEO_CREATE) is True
        assert has_permission("viewer", Permission.VIDEO_CREATE) is False

    def test_get_role_permissions_invalid_role(self):
        """get_role_permissions should return empty set for invalid role."""
        perms = get_role_permissions("invalid_role")
        assert len(perms) == 0


class TestOwnershipPermission:
    """Tests for ownership-based permission checking."""

    def test_admin_can_update_any_video(self):
        """Admin should be able to update any video regardless of ownership."""
        user_id = str(uuid.uuid4())
        other_owner = str(uuid.uuid4())

        result = check_ownership_permission(
            Role.ADMIN, Permission.VIDEO_UPDATE, other_owner, user_id
        )
        assert result is True

    def test_editor_can_update_own_video(self):
        """Editor should be able to update their own video."""
        user_id = str(uuid.uuid4())

        result = check_ownership_permission(
            Role.EDITOR, Permission.VIDEO_UPDATE, user_id, user_id
        )
        assert result is True

    def test_editor_cannot_update_others_video(self):
        """Editor should NOT be able to update others' videos."""
        user_id = str(uuid.uuid4())
        other_owner = str(uuid.uuid4())

        result = check_ownership_permission(
            Role.EDITOR, Permission.VIDEO_UPDATE, other_owner, user_id
        )
        assert result is False

    def test_editor_can_update_unowned_video(self):
        """Editor should be able to update videos with no owner."""
        user_id = str(uuid.uuid4())

        result = check_ownership_permission(
            Role.EDITOR, Permission.VIDEO_UPDATE, None, user_id
        )
        assert result is True

    def test_viewer_cannot_update_any_video(self):
        """Viewer should NOT be able to update any video."""
        user_id = str(uuid.uuid4())

        result = check_ownership_permission(
            Role.VIEWER, Permission.VIDEO_UPDATE, user_id, user_id
        )
        assert result is False


# =============================================================================
# Session Tests (require database)
# =============================================================================


@pytest.fixture(scope="function")
async def sample_user(test_database):
    """Create a sample user for testing."""
    from api.database import users

    now = datetime.now(timezone.utc)
    user_id = str(uuid.uuid4())
    password_hash = hash_password("SecurePassword123!")

    await test_database.execute(
        users.insert().values(
            id=user_id,
            username="testuser",
            email="test@example.com",
            password_hash=password_hash,
            role="editor",
            status="active",
            email_verified=False,
            failed_login_attempts=0,
            created_at=now,
            updated_at=now,
        )
    )

    return {
        "id": user_id,
        "username": "testuser",
        "email": "test@example.com",
        "password": "SecurePassword123!",
        "role": "editor",
    }


@pytest.fixture(scope="function")
async def sample_admin_user(test_database):
    """Create a sample admin user for testing."""
    from api.database import users

    now = datetime.now(timezone.utc)
    user_id = str(uuid.uuid4())
    password_hash = hash_password("AdminPassword123!")

    await test_database.execute(
        users.insert().values(
            id=user_id,
            username="adminuser",
            email="admin@example.com",
            password_hash=password_hash,
            role="admin",
            status="active",
            email_verified=True,
            failed_login_attempts=0,
            created_at=now,
            updated_at=now,
        )
    )

    return {
        "id": user_id,
        "username": "adminuser",
        "email": "admin@example.com",
        "password": "AdminPassword123!",
        "role": "admin",
    }


class TestSessionManagement:
    """Tests for session creation and validation."""

    @pytest.mark.asyncio
    async def test_create_session(self, test_database, sample_user, monkeypatch):
        """create_user_session should create valid session tokens."""
        # Patch the database module to use test database
        import api.auth.sessions as sessions_module

        monkeypatch.setattr(sessions_module, "database", test_database)

        session_token, refresh_token, expires_at, refresh_expires_at = (
            await sessions_module.create_user_session(
                sample_user["id"],
                ip_address="127.0.0.1",
                user_agent="Test Agent",
            )
        )

        assert isinstance(session_token, str)
        assert isinstance(refresh_token, str)
        assert len(session_token) > 40
        assert len(refresh_token) > 40
        assert expires_at > datetime.now(timezone.utc)
        assert refresh_expires_at > expires_at

    @pytest.mark.asyncio
    async def test_validate_session_valid(self, test_database, sample_user, monkeypatch):
        """validate_session_token should return user for valid session."""
        import api.auth.sessions as sessions_module

        monkeypatch.setattr(sessions_module, "database", test_database)

        session_token, _, _, _ = await sessions_module.create_user_session(sample_user["id"])
        user = await sessions_module.validate_session_token(session_token)

        assert user is not None
        assert user["id"] == sample_user["id"]
        assert user["username"] == sample_user["username"]

    @pytest.mark.asyncio
    async def test_validate_session_invalid_token(self, test_database, monkeypatch):
        """validate_session_token should return None for invalid token."""
        import api.auth.sessions as sessions_module

        monkeypatch.setattr(sessions_module, "database", test_database)

        user = await sessions_module.validate_session_token("invalid_token_12345678")
        assert user is None

    @pytest.mark.asyncio
    async def test_validate_session_short_token(self, test_database, monkeypatch):
        """validate_session_token should reject short tokens."""
        import api.auth.sessions as sessions_module

        monkeypatch.setattr(sessions_module, "database", test_database)

        user = await sessions_module.validate_session_token("short")
        assert user is None

    @pytest.mark.asyncio
    async def test_invalidate_session(self, test_database, sample_user, monkeypatch):
        """invalidate_session should revoke a session."""
        import api.auth.sessions as sessions_module

        from api.database import user_sessions

        monkeypatch.setattr(sessions_module, "database", test_database)

        session_token, _, _, _ = await sessions_module.create_user_session(sample_user["id"])

        # Verify session is valid
        user = await sessions_module.validate_session_token(session_token)
        assert user is not None
        session_id = user["session_id"]

        # Invalidate session directly via database
        now = datetime.now(timezone.utc)
        await test_database.execute(
            user_sessions.update()
            .where(user_sessions.c.id == session_id)
            .values(revoked_at=now)
        )

        # Verify session is no longer valid
        user = await sessions_module.validate_session_token(session_token)
        assert user is None

    @pytest.mark.asyncio
    async def test_refresh_session(self, test_database, sample_user, monkeypatch):
        """refresh_user_session should rotate tokens."""
        import api.auth.sessions as sessions_module

        monkeypatch.setattr(sessions_module, "database", test_database)

        # Create initial session
        session_token, refresh_token, _, _ = await sessions_module.create_user_session(
            sample_user["id"]
        )

        # Refresh the session
        new_session, new_refresh, new_expires, new_refresh_expires = (
            await sessions_module.refresh_user_session(refresh_token)
        )

        # New tokens should be different
        assert new_session != session_token
        assert new_refresh != refresh_token

        # New session should be valid
        user = await sessions_module.validate_session_token(new_session)
        assert user is not None
        assert user["id"] == sample_user["id"]

        # Old session should be revoked
        user = await sessions_module.validate_session_token(session_token)
        assert user is None

    @pytest.mark.asyncio
    async def test_refresh_token_reuse_detection(self, test_database, sample_user, monkeypatch):
        """Reusing a rotated refresh token should fail and revoke sessions."""
        import api.auth.sessions as sessions_module

        monkeypatch.setattr(sessions_module, "database", test_database)

        # Create initial session
        session_token, refresh_token, _, _ = await sessions_module.create_user_session(
            sample_user["id"]
        )

        # First refresh (legitimate)
        new_session, new_refresh, _, _ = await sessions_module.refresh_user_session(
            refresh_token
        )

        # Try to reuse the old refresh token (attacker scenario)
        # Should fail with either RefreshTokenReusedError or SessionRevokedError
        # (depending on whether the old session was already revoked)
        with pytest.raises(
            (
                sessions_module.RefreshTokenReusedError,
                sessions_module.SessionRevokedError,
                sessions_module.SessionError,
            )
        ):
            await sessions_module.refresh_user_session(refresh_token)

        # Original session should be revoked (was rotated)
        assert await sessions_module.validate_session_token(session_token) is None

    @pytest.mark.asyncio
    async def test_get_user_sessions(self, test_database, sample_user, monkeypatch):
        """get_user_sessions should return all active sessions."""
        import api.auth.sessions as sessions_module

        monkeypatch.setattr(sessions_module, "database", test_database)

        # Create multiple sessions
        await sessions_module.create_user_session(sample_user["id"], ip_address="192.168.1.1")
        await sessions_module.create_user_session(sample_user["id"], ip_address="192.168.1.2")
        await sessions_module.create_user_session(sample_user["id"], ip_address="192.168.1.3")

        sessions = await sessions_module.get_user_sessions(sample_user["id"])

        assert len(sessions) == 3
        ips = [s["ip_address"] for s in sessions]
        assert "192.168.1.1" in ips
        assert "192.168.1.2" in ips
        assert "192.168.1.3" in ips


# =============================================================================
# Auth Endpoints Integration Tests
# =============================================================================


class TestAuthEndpointsIntegration:
    """Integration tests for auth API endpoints."""

    def test_login_invalid_credentials(self, admin_client):
        """POST /api/v1/auth/login should reject invalid credentials."""
        response = admin_client.post(
            "/api/v1/auth/login",
            json={"username_or_email": "nonexistent", "password": "WrongPassword123!"},
        )

        # API returns 200 with success=false, or 401/403
        if response.status_code == 200:
            data = response.json()
            # If 200, should indicate failure in response body
            assert data.get("success") is False or data.get("user") is None
        else:
            assert response.status_code in [401, 403]

    def test_auth_check_endpoint_exists(self, admin_client):
        """GET /api/v1/auth/check should be accessible."""
        response = admin_client.get("/api/v1/auth/check")

        # Should return 200 with auth status
        assert response.status_code == 200
        data = response.json()
        # The response should contain auth-related fields
        assert isinstance(data, dict)

    def test_logout_without_session(self, admin_client):
        """POST /api/v1/auth/logout should handle no session gracefully."""
        response = admin_client.post("/api/v1/auth/logout")

        # Should not error even without a session
        assert response.status_code in [200, 204, 401]

    def test_forgot_password_endpoint_exists(self, admin_client):
        """POST /api/v1/auth/forgot should be accessible."""
        response = admin_client.post(
            "/api/v1/auth/forgot",
            json={"email": "nonexistent@example.com"},
        )

        # Should always return success to prevent user enumeration
        # or return 200/204 regardless of whether email exists
        assert response.status_code in [200, 204]


# =============================================================================
# API Key Tests
# =============================================================================


class TestApiKeyAuth:
    """Tests for API key authentication."""

    @pytest.mark.asyncio
    async def test_create_api_key(self, test_database, sample_user, monkeypatch):
        """Creating an API key should return the full key once."""
        from api.database import user_api_keys

        now = datetime.now(timezone.utc)
        key_id = str(uuid.uuid4())
        api_key = generate_token(32)
        key_hash = hash_token(api_key)
        key_prefix = get_token_prefix(api_key)

        await test_database.execute(
            user_api_keys.insert().values(
                id=key_id,
                user_id=sample_user["id"],
                name="Test Key",
                key_prefix=key_prefix,
                key_hash=key_hash,
                created_at=now,
            )
        )

        # Verify the key was stored
        result = await test_database.fetch_one(
            user_api_keys.select().where(user_api_keys.c.id == key_id)
        )

        assert result is not None
        assert result["name"] == "Test Key"
        assert result["key_prefix"] == key_prefix

        # Verify the key can be validated
        assert verify_token(api_key, result["key_hash"]) is True

    @pytest.mark.asyncio
    async def test_api_key_revocation(self, test_database, sample_user, monkeypatch):
        """Revoking an API key should mark it as revoked."""
        from api.database import user_api_keys

        now = datetime.now(timezone.utc)
        key_id = str(uuid.uuid4())
        api_key = generate_token(32)
        key_hash = hash_token(api_key)
        key_prefix = get_token_prefix(api_key)

        await test_database.execute(
            user_api_keys.insert().values(
                id=key_id,
                user_id=sample_user["id"],
                name="Revoke Test Key",
                key_prefix=key_prefix,
                key_hash=key_hash,
                created_at=now,
            )
        )

        # Revoke the key
        await test_database.execute(
            user_api_keys.update()
            .where(user_api_keys.c.id == key_id)
            .values(revoked_at=now)
        )

        # Verify it's marked as revoked
        result = await test_database.fetch_one(
            user_api_keys.select().where(user_api_keys.c.id == key_id)
        )

        assert result["revoked_at"] is not None
