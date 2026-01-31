/**
 * Authentication API Endpoints for Studio
 */

import { apiClient } from '../client';
import type { AuthCheckResponse, AuthLoginResponse } from '../types';

export const authApi = {
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
   * Login with username/email and password
   */
  async login(usernameOrEmail: string, password: string): Promise<AuthLoginResponse> {
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
   * Fetch CSRF token
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
