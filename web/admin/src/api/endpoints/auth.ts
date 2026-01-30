/**
 * Authentication API Endpoints
 */

import { apiClient } from '../client';
import type { AuthCheckResponse, AuthLoginResponse } from '../types';

export const authApi = {
  /**
   * Check if authentication is required and current auth status
   */
  async check(): Promise<AuthCheckResponse> {
    const response = await apiClient.fetchRaw('/api/auth/check');
    if (!response.ok) {
      throw new Error(`Auth check failed: ${response.status}`);
    }
    return response.json();
  },

  /**
   * Login with admin secret
   * Server sets HTTP-only session cookie on success
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
    await apiClient.fetchRaw('/api/auth/logout', {
      method: 'POST',
    }).catch((e) => {
      console.error('Logout failed:', e);
    });
  },

  /**
   * Fetch CSRF token for state-changing requests
   */
  async fetchCsrfToken(): Promise<string> {
    return apiClient.refreshCsrfToken();
  },
};
