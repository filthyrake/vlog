/**
 * Authentication Store
 * Manages authentication state and login/logout operations
 */

import { authApi } from '@/api/endpoints/auth';
import { apiClient } from '@/api/client';
import type { AlpineContext } from './types';

export interface AuthState {
  // State
  isAuthenticated: boolean;
  authRequired: boolean;
  showAuthModal: boolean;
  authSecretInput: string;
  authError: string;
  authLoading: boolean;
  csrfToken: string;
}

export interface AuthActions {
  checkAuth(): Promise<boolean>;
  submitAuth(): Promise<void>;
  logout(): Promise<void>;
  fetchCsrfToken(): Promise<void>;
}

export type AuthStore = AuthState & AuthActions;

export function createAuthStore(_context?: AlpineContext): AuthStore {
  return {
    // Initial state
    isAuthenticated: false,
    authRequired: false,
    showAuthModal: false,
    authSecretInput: '',
    authError: '',
    authLoading: false,
    csrfToken: '',

    /**
     * Check if authentication is required and current auth status
     */
    async checkAuth(): Promise<boolean> {
      try {
        const data = await authApi.check();
        this.authRequired = data.auth_required;
        this.isAuthenticated = data.authenticated;

        if (!data.authenticated && data.auth_required) {
          this.showAuthModal = true;
          return false;
        }

        return true;
      } catch (e) {
        console.error('Auth check failed:', e);
        // Allow to continue on network errors
        return true;
      }
    },

    /**
     * Submit authentication via server-side session
     */
    async submitAuth(): Promise<void> {
      this.authError = '';
      this.authLoading = true;

      try {
        const result = await authApi.login(this.authSecretInput);

        if (!result.success) {
          this.authError = result.message || 'Authentication failed';
          return;
        }

        // Success - server has set the session cookie
        this.isAuthenticated = true;
        this.showAuthModal = false;
        this.authSecretInput = '';

        // Fetch CSRF token for state-changing requests
        await this.fetchCsrfToken();
      } catch (e) {
        this.authError = 'Failed to authenticate: ' + (e instanceof Error ? e.message : String(e));
      } finally {
        this.authLoading = false;
      }
    },

    /**
     * Log out and clear session
     */
    async logout(): Promise<void> {
      await authApi.logout();
      this.isAuthenticated = false;
      this.authRequired = true;
      this.showAuthModal = true;
      this.csrfToken = '';
      apiClient.setCsrfToken('');
    },

    /**
     * Fetch CSRF token for state-changing requests
     */
    async fetchCsrfToken(): Promise<void> {
      try {
        const token = await authApi.fetchCsrfToken();
        this.csrfToken = token;
        apiClient.setCsrfToken(token);
      } catch (e) {
        console.error('Failed to fetch CSRF token:', e);
      }
    },
  };
}
