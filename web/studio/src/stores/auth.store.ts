/**
 * Auth Store for Studio Dashboard
 */

import type { CurrentUser, AuthCheckResponse } from '../api/types';
import { apiClient, authApi } from '../api';

export interface AuthState {
  user: CurrentUser | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;
}

export function createAuthStore() {
  return {
    // State
    user: null as CurrentUser | null,
    isAuthenticated: false,
    isLoading: true,
    error: null as string | null,

    // Actions
    async checkAuth() {
      this.isLoading = true;
      this.error = null;

      try {
        // Get CSRF token first
        await authApi.getCsrfToken();

        // Check auth status
        const response: AuthCheckResponse = await authApi.checkAuth();

        if (response.authenticated && response.user) {
          this.user = response.user;
          this.isAuthenticated = true;
        } else {
          this.user = null;
          this.isAuthenticated = false;
        }
      } catch (err) {
        console.error('Auth check failed:', err);
        this.user = null;
        this.isAuthenticated = false;
        this.error = err instanceof Error ? err.message : 'Authentication check failed';
      } finally {
        this.isLoading = false;
      }
    },

    async login(username: string, password: string): Promise<boolean> {
      this.isLoading = true;
      this.error = null;

      try {
        const response = await authApi.login(username, password);
        this.user = response.user;
        this.isAuthenticated = true;
        return true;
      } catch (err) {
        this.error = err instanceof Error ? err.message : 'Login failed';
        return false;
      } finally {
        this.isLoading = false;
      }
    },

    async logout(): Promise<void> {
      try {
        await authApi.logout();
      } catch (err) {
        console.error('Logout error:', err);
      } finally {
        this.user = null;
        this.isAuthenticated = false;
        window.location.href = '/admin/';
      }
    },

    // Getters
    get isAdmin(): boolean {
      return this.user?.role === 'admin';
    },

    get canAccessStudio(): boolean {
      return this.isAuthenticated && (this.user?.role === 'admin' || this.user?.role === 'editor');
    },
  };
}
