# VLog User Authentication

## Overview

VLog supports multi-user authentication with role-based access control (RBAC). This replaces the legacy single admin secret with proper user accounts, sessions, and API keys.

### Key Features

- **User accounts** with username/email and password authentication
- **Role-based access control** (Admin, Editor, Viewer)
- **Session-based browser auth** with HTTP-only cookies
- **API keys** for programmatic access
- **OIDC integration** for self-hosted identity providers
- **Invite-only registration** (configurable)

---

## Roles & Permissions

| Role | Description | Key Permissions |
|------|-------------|-----------------|
| **Admin** | Full system access | All permissions including user management |
| **Editor** | Content creator | Upload, edit/delete own videos, view own analytics |
| **Viewer** | Read-only access | Browse and watch videos (for private instances) |

### Permission Matrix

| Permission | Admin | Editor | Viewer |
|------------|-------|--------|--------|
| View videos | ✅ | ✅ | ✅ |
| Upload videos | ✅ | ✅ | ❌ |
| Edit own videos | ✅ | ✅ | ❌ |
| Edit any video | ✅ | ❌ | ❌ |
| Delete own videos | ✅ | ✅ | ❌ |
| Delete any video | ✅ | ❌ | ❌ |
| Manage categories/tags | ✅ | ❌ | ❌ |
| View all analytics | ✅ | ❌ | ❌ |
| View own analytics | ✅ | ✅ | ❌ |
| Manage users | ✅ | ❌ | ❌ |
| Manage workers | ✅ | ❌ | ❌ |
| System settings | ✅ | ❌ | ❌ |

---

## Configuration

### Required Environment Variables

```bash
# REQUIRED: Session secret for signing tokens
# Generate with: openssl rand -base64 32
VLOG_SESSION_SECRET_KEY=your-secret-key-here

# Session expiry (optional, defaults shown)
VLOG_SESSION_EXPIRY_HOURS=24
VLOG_REFRESH_TOKEN_EXPIRY_DAYS=7

# Registration mode (optional)
# invite - Users must be invited by admin (default)
# open - Anyone can register (not recommended for private instances)
# disabled - No new registrations
VLOG_REGISTRATION_MODE=invite
```

### OIDC Configuration (Optional)

For integrating with self-hosted identity providers (Keycloak, Authentik, Authelia, Zitadel, etc.):

```bash
VLOG_OIDC_ENABLED=true
VLOG_OIDC_PROVIDER_NAME=SSO              # Button text: "Sign in with SSO"
VLOG_OIDC_DISCOVERY_URL=https://keycloak.example.com/realms/vlog/.well-known/openid-configuration
VLOG_OIDC_CLIENT_ID=vlog
VLOG_OIDC_CLIENT_SECRET=your-client-secret
VLOG_OIDC_SCOPES=openid,profile,email    # Standard OIDC scopes
VLOG_OIDC_AUTO_CREATE_USERS=false        # Auto-provision users on first login
VLOG_OIDC_DEFAULT_ROLE=viewer            # Role for auto-created users
VLOG_OIDC_TIMEOUT_SECONDS=10             # Request timeout
```

### Database Settings

Additional settings stored in the database:

| Setting | Default | Description |
|---------|---------|-------------|
| `auth.password_min_length` | 12 | Minimum password length |
| `auth.lockout_threshold` | 5 | Failed login attempts before lockout |
| `auth.lockout_duration_minutes` | 30 | Account lockout duration |
| `auth.max_sessions_per_user` | 10 | Maximum concurrent sessions |

---

## API Endpoints

### Authentication

#### Login

```http
POST /api/v1/auth/login
Content-Type: application/json

{
  "username_or_email": "user@example.com",
  "password": "your-password"
}
```

Response (success):
```json
{
  "user": {
    "id": "uuid",
    "username": "johndoe",
    "email": "user@example.com",
    "display_name": "John Doe",
    "role": "editor",
    "permissions": ["video:create", "video:read", ...]
  }
}
```

Response (failure):
```json
{
  "success": false,
  "message": "Invalid credentials"
}
```

#### Check Auth Status

```http
GET /api/v1/auth/check
```

Response:
```json
{
  "authenticated": true,
  "auth_required": true,
  "auth_mode": "user",
  "oidc_enabled": false,
  "oidc_provider_name": null,
  "user": { ... }
}
```

#### Logout

```http
POST /api/v1/auth/logout
```

#### Refresh Session

```http
POST /api/v1/auth/refresh
```

Returns new session tokens. Used for token rotation.

### Profile Management

#### Get Current User

```http
GET /api/v1/auth/me
```

#### Update Profile

```http
PUT /api/v1/auth/me
Content-Type: application/json

{
  "display_name": "New Name",
  "email": "new@example.com"
}
```

#### Change Password

```http
POST /api/v1/auth/password
Content-Type: application/json

{
  "current_password": "old-password",
  "new_password": "new-secure-password"
}
```

### Password Reset

#### Request Reset

```http
POST /api/v1/auth/forgot
Content-Type: application/json

{
  "email": "user@example.com"
}
```

Always returns success (prevents user enumeration).

#### Complete Reset

```http
POST /api/v1/auth/reset
Content-Type: application/json

{
  "token": "reset-token-from-email",
  "new_password": "new-secure-password"
}
```

### Session Management

#### List Active Sessions

```http
GET /api/v1/auth/sessions
```

Response:
```json
{
  "sessions": [
    {
      "id": "session-uuid",
      "ip_address": "192.168.1.1",
      "user_agent": "Mozilla/5.0...",
      "created_at": "2024-01-15T10:30:00Z",
      "is_current": true
    }
  ]
}
```

#### Revoke Session

```http
DELETE /api/v1/auth/sessions/{session_id}
```

### User Management (Admin Only)

#### List Users

```http
GET /api/v1/users
```

Query parameters:
| Parameter | Type | Description |
|-----------|------|-------------|
| limit | int | Max items (default: 50) |
| offset | int | Pagination offset |
| role | string | Filter by role |
| status | string | Filter by status (active, disabled, pending) |
| search | string | Search username/email |

#### Create User

```http
POST /api/v1/users
Content-Type: application/json

{
  "username": "newuser",
  "email": "newuser@example.com",
  "password": "secure-password",
  "display_name": "New User",
  "role": "editor"
}
```

#### Update User

```http
PUT /api/v1/users/{user_id}
Content-Type: application/json

{
  "role": "admin",
  "display_name": "Updated Name"
}
```

#### Disable User

```http
DELETE /api/v1/users/{user_id}
```

Soft-deletes (disables) the user account.

#### Force Password Reset

```http
POST /api/v1/users/{user_id}/reset-password
```

### API Key Management

#### List API Keys

```http
GET /api/v1/api-keys
```

#### Create API Key

```http
POST /api/v1/api-keys
Content-Type: application/json

{
  "name": "CI/CD Pipeline",
  "expires_in_days": 90
}
```

Response (key shown only once):
```json
{
  "id": "key-uuid",
  "name": "CI/CD Pipeline",
  "key": "vlog_ak_xxxxxxxxxxxxx",
  "key_prefix": "vlog_ak_",
  "expires_at": "2024-04-15T10:30:00Z",
  "created_at": "2024-01-15T10:30:00Z"
}
```

#### Revoke API Key

```http
DELETE /api/v1/api-keys/{key_id}
```

### Invite Management (Admin Only)

#### List Invites

```http
GET /api/v1/invites
```

#### Create Invite

```http
POST /api/v1/invites
Content-Type: application/json

{
  "email": "newuser@example.com",
  "role": "editor",
  "expires_in_days": 7
}
```

Response:
```json
{
  "id": "invite-uuid",
  "email": "newuser@example.com",
  "role": "editor",
  "invite_url": "https://your-vlog.com/invite/abc123...",
  "expires_at": "2024-01-22T10:30:00Z"
}
```

#### Revoke Invite

```http
DELETE /api/v1/invites/{invite_id}
```

---

## Using API Keys

API keys can be used for programmatic access instead of session cookies.

### Header Authentication

```http
GET /api/v1/videos
Authorization: Bearer vlog_ak_xxxxxxxxxxxxx
```

### Key Permissions

API keys inherit permissions from the user's role. A key created by an editor has editor-level access.

---

## Migration from Admin Secret

If you're upgrading from an older VLog version that used `VLOG_ADMIN_API_SECRET`:

### 1. Run Migration Command

```bash
vlog auth migrate
```

This will:
- Prompt you to create the initial admin user
- Assign existing videos to the new admin
- Invalidate old admin sessions

### 2. Remove Old Configuration

After migration, remove `VLOG_ADMIN_API_SECRET` from your environment.

### 3. Set Session Secret

```bash
export VLOG_SESSION_SECRET_KEY=$(openssl rand -base64 32)
```

### Migration Commands

```bash
# Dry-run (see what would happen)
vlog auth migrate --check

# Force re-migration (if previous attempt failed)
vlog auth migrate --force

# Create additional admin
vlog auth create-admin

# Create user with specific role
vlog auth create-user --username editor1 --role editor

# List users
vlog auth list-users

# Force password reset
vlog auth reset-password --username johndoe

# Disable user
vlog auth disable-user --username badactor
```

---

## Security Features

### Password Requirements

- Minimum 12 characters
- Must contain letters and numbers/symbols
- Argon2id hashing (memory-hard, GPU-resistant)

### Brute Force Protection

- Account lockout after 5 failed attempts
- Lockout duration: 30 minutes (configurable)
- Rate limiting on login endpoint

### Session Security

- HTTP-only, Secure, SameSite=Lax cookies
- 24-hour session expiry (configurable)
- 7-day refresh token expiry
- Refresh token rotation with theft detection
- Maximum 10 concurrent sessions per user

### OIDC Security

- State parameter for CSRF protection
- Nonce validation for replay protection
- Circuit breaker for provider failures

---

## OIDC Integration

VLog supports any OIDC-compliant identity provider.

### Supported Providers

- Keycloak
- Authentik
- Authelia
- Zitadel
- Google (configured as OIDC)
- Microsoft Entra ID
- Any standard OIDC provider

### Setup Steps

1. Create a new client in your identity provider
2. Configure the callback URL: `https://your-vlog.com/api/v1/auth/oidc/callback`
3. Set the environment variables (see Configuration section)
4. Restart VLog

### User Provisioning

When a user logs in via OIDC:

1. If `VLOG_OIDC_AUTO_CREATE_USERS=true`:
   - A new user account is created
   - The OIDC connection is linked
   - User gets the default role

2. If `VLOG_OIDC_AUTO_CREATE_USERS=false`:
   - Admin must create the user first
   - User links their OIDC account on first login

### Linking Accounts

Existing users can link their OIDC account:

```http
POST /api/v1/auth/oidc/link
```

And unlink:

```http
DELETE /api/v1/auth/oidc
```

---

## Troubleshooting

### "VLOG_SESSION_SECRET_KEY is required"

The session secret must be set in production. Generate one:

```bash
openssl rand -base64 32
```

### Account Locked

Wait for the lockout period to expire, or have an admin reset the password.

### OIDC Login Fails

1. Check the discovery URL is accessible
2. Verify client ID and secret
3. Ensure callback URL matches exactly
4. Check provider logs for errors

### Session Expired

Sessions expire after 24 hours. Use the refresh endpoint or re-login.

### API Key Not Working

1. Verify the key hasn't been revoked
2. Check if it has expired
3. Ensure proper Authorization header format
