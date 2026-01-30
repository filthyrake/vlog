/**
 * Authentication Store for Studio
 */

import { authApi } from '@/api/endpoints/auth';
import { apiClient } from '@/api/client';
import type { CurrentUser } from '@/api/types';

export interface AuthState {
  isAuthenticated: boolean;
  authRequired: boolean;
  showAuthModal: boolean;
  currentUser: CurrentUser | null;
  loginUsername: string;
  loginPassword: string;
  oidcEnabled: boolean;
  oidcProviderName: string;
  authError: string;
  authLoading: boolean;
  csrfToken: string;
}

export interface AuthActions {
  checkAuth(): Promise<boolean>;
  submitAuth(): Promise<void>;
  logout(): Promise<void>;
  fetchCsrfToken(): Promise<void>;
  startOidcLogin(): Promise<void>;
  hasPermission(permission: string): boolean;
  isAdmin(): boolean;
}

export type AuthStore = AuthState & AuthActions;

export function createAuthStore(): AuthStore {
  return {
    // Initial state
    isAuthenticated: false,
    authRequired: false,
    showAuthModal: false,
    currentUser: null,
    loginUsername: '',
    loginPassword: '',
    oidcEnabled: false,
    oidcProviderName: 'SSO',
    authError: '',
    authLoading: false,
    csrfToken: '',

    /**
     * Check authentication status
     */
    async checkAuth(): Promise<boolean> {
      try {
        const data = await authApi.check();
        this.authRequired = data.auth_required;
        this.isAuthenticated = data.authenticated;
        this.oidcEnabled = data.oidc_enabled || false;
        this.oidcProviderName = data.oidc_provider_name || 'SSO';

        if (data.user) {
          this.currentUser = data.user;
        }

        if (!data.authenticated && data.auth_required) {
          this.showAuthModal = true;
          return false;
        }

        // Fetch CSRF token if authenticated
        if (data.authenticated) {
          await this.fetchCsrfToken();
        }

        return true;
      } catch (e) {
        console.error('Auth check failed:', e);
        return true;
      }
    },

    /**
     * Submit authentication
     */
    async submitAuth(): Promise<void> {
      this.authError = '';
      this.authLoading = true;

      try {
        const result = await authApi.login(this.loginUsername, this.loginPassword);

        if (!result.success) {
          this.authError = result.message || 'Authentication failed';
          return;
        }

        this.isAuthenticated = true;
        this.showAuthModal = false;
        this.loginUsername = '';
        this.loginPassword = '';

        if (result.user) {
          this.currentUser = result.user;
        }

        await this.fetchCsrfToken();
      } catch (e) {
        this.authError = 'Failed to authenticate: ' + (e instanceof Error ? e.message : String(e));
      } finally {
        this.authLoading = false;
      }
    },

    /**
     * Logout
     */
    async logout(): Promise<void> {
      await authApi.logout();
      this.isAuthenticated = false;
      this.authRequired = true;
      this.showAuthModal = true;
      this.currentUser = null;
      this.csrfToken = '';
      apiClient.setCsrfToken('');
    },

    /**
     * Fetch CSRF token
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

    /**
     * Start OIDC login
     */
    async startOidcLogin(): Promise<void> {
      try {
        const { url } = await authApi.getOidcAuthUrl();
        window.location.href = url;
      } catch (e) {
        this.authError = 'Failed to start SSO login';
        console.error('OIDC login failed:', e);
      }
    },

    /**
     * Check permission
     */
    hasPermission(permission: string): boolean {
      if (!this.currentUser) return false;
      if (this.currentUser.role === 'admin') return true;
      return this.currentUser.permissions.includes(permission);
    },

    /**
     * Check if admin
     */
    isAdmin(): boolean {
      return this.currentUser?.role === 'admin';
    },
  };
}
