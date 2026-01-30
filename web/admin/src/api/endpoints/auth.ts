/**
 * Authentication API Endpoints
 * Supports both legacy (admin secret) and user-based authentication
 */

import { apiClient } from '../client';
import type {
  AuthCheckResponse,
  AuthLoginResponse,
  CurrentUser,
  UpdateProfileRequest,
  ChangePasswordRequest,
  SessionListResponse,
} from '../types';

export interface SetupStatusResponse {
  needs_setup: boolean;
  message: string;
}

export interface SetupRequest {
  username: string;
  email: string;
  password: string;
  display_name?: string;
}

export interface SetupResponse {
  success: boolean;
  user_id?: string;
  username?: string;
  email?: string;
  message: string;
}

export const authApi = {
  /**
   * Check if initial setup is required (no users exist)
   */
  async checkSetup(): Promise<SetupStatusResponse> {
    const response = await apiClient.fetchRaw('/api/v1/auth/setup');
    if (!response.ok) {
      throw new Error(`Setup check failed: ${response.status}`);
    }
    return response.json();
  },

  /**
   * Create initial admin account (only works when no users exist)
   */
  async setup(data: SetupRequest): Promise<SetupResponse> {
    const response = await apiClient.fetchRaw('/api/v1/auth/setup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });

    const result = await response.json().catch(() => ({}));

    if (!response.ok) {
      return { success: false, message: result.detail || `Setup failed: ${response.status}` };
    }

    return {
      success: true,
      user_id: result.user_id,
      username: result.username,
      email: result.email,
      message: result.message,
    };
  },

  /**
   * Check if authentication is required and current auth status
   */
  async check(): Promise<AuthCheckResponse> {
    const response = await apiClient.fetchRaw('/api/v1/auth/check');
    if (!response.ok) {
      throw new Error(`Auth check failed: ${response.status}`);
    }
    return response.json();
  },

  /**
   * Login with username/email and password (user-based auth)
   */
  async loginUser(usernameOrEmail: string, password: string): Promise<AuthLoginResponse> {
    const response = await apiClient.fetchRaw('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username_or_email: usernameOrEmail, password }),
    });

    const data = await response.json().catch(() => ({}));

    if (response.status === 401 || response.status === 403) {
      return { success: false, message: data.detail || 'Invalid credentials' };
    }

    if (response.status === 423) {
      return { success: false, message: data.detail || 'Account is locked' };
    }

    if (response.status === 429) {
      return { success: false, message: 'Too many login attempts. Please try again later.' };
    }

    if (!response.ok) {
      return { success: false, message: data.detail || `Server error: ${response.status}` };
    }

    return { success: true, user: data.user };
  },

  /**
   * Login with admin secret (legacy auth - for backward compatibility)
   */
  async login(secret: string): Promise<AuthLoginResponse> {
    const response = await apiClient.fetchRaw('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ secret }),
    });

    if (response.status === 403) {
      return { success: false, message: 'Invalid admin secret' };
    }

    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      return { success: false, message: data.detail || `Server error: ${response.status}` };
    }

    return { success: true };
  },

  /**
   * Logout and clear session
   */
  async logout(): Promise<void> {
    await apiClient.fetchRaw('/api/v1/auth/logout', {
      method: 'POST',
    }).catch((e) => {
      console.error('Logout failed:', e);
    });
  },

  /**
   * Refresh the session token
   */
  async refresh(): Promise<AuthLoginResponse> {
    const response = await apiClient.fetchRaw('/api/v1/auth/refresh', {
      method: 'POST',
    });

    if (!response.ok) {
      return { success: false, message: 'Session expired' };
    }

    const data = await response.json().catch(() => ({}));
    return { success: true, user: data.user };
  },

  /**
   * Get current user info
   */
  async getCurrentUser(): Promise<CurrentUser> {
    const response = await apiClient.fetch<CurrentUser>('/api/v1/auth/me');
    return response;
  },

  /**
   * Update current user profile
   */
  async updateProfile(data: UpdateProfileRequest): Promise<CurrentUser> {
    const response = await apiClient.fetch<CurrentUser>('/api/v1/auth/me', {
      method: 'PUT',
      body: JSON.stringify(data),
    });
    return response;
  },

  /**
   * Change password
   */
  async changePassword(data: ChangePasswordRequest): Promise<void> {
    await apiClient.fetch('/api/v1/auth/password', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  /**
   * Request password reset
   */
  async forgotPassword(email: string): Promise<void> {
    await apiClient.fetchRaw('/api/v1/auth/forgot', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email }),
    });
    // Always returns success to prevent user enumeration
  },

  /**
   * Reset password with token
   */
  async resetPassword(token: string, newPassword: string): Promise<{ success: boolean; message?: string }> {
    const response = await apiClient.fetchRaw('/api/v1/auth/reset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token, new_password: newPassword }),
    });

    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      return { success: false, message: data.detail || 'Password reset failed' };
    }

    return { success: true };
  },

  /**
   * List active sessions
   */
  async listSessions(): Promise<SessionListResponse> {
    return apiClient.fetch<SessionListResponse>('/api/v1/auth/sessions');
  },

  /**
   * Revoke a session
   */
  async revokeSession(sessionId: string): Promise<void> {
    await apiClient.fetch(`/api/v1/auth/sessions/${sessionId}`, {
      method: 'DELETE',
    });
  },

  /**
   * Fetch CSRF token for state-changing requests
   */
  async fetchCsrfToken(): Promise<string> {
    return apiClient.refreshCsrfToken();
  },

  /**
   * Get OIDC authorization URL
   */
  async getOidcAuthUrl(): Promise<{ url: string }> {
    return apiClient.fetch<{ url: string }>('/api/v1/auth/oidc/authorize');
  },
};
