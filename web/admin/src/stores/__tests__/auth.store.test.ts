/**
 * Tests for Auth Store
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { createAuthStore } from '../auth.store';
import { authApi } from '@/api/endpoints/auth';
import { apiClient } from '@/api/client';

// Mock the API modules
vi.mock('@/api/endpoints/auth', () => ({
  authApi: {
    check: vi.fn(),
    login: vi.fn(),
    logout: vi.fn(),
    fetchCsrfToken: vi.fn(),
  },
}));

vi.mock('@/api/client', () => ({
  apiClient: {
    setCsrfToken: vi.fn(),
  },
}));

const mockAuthApi = vi.mocked(authApi);
const mockApiClient = vi.mocked(apiClient);

describe('AuthStore', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('initial state', () => {
    it('should have correct initial values', () => {
      const store = createAuthStore();

      expect(store.isAuthenticated).toBe(false);
      expect(store.authRequired).toBe(false);
      expect(store.showAuthModal).toBe(false);
      expect(store.authSecretInput).toBe('');
      expect(store.authError).toBe('');
      expect(store.authLoading).toBe(false);
      expect(store.csrfToken).toBe('');
    });
  });

  describe('checkAuth', () => {
    it('should set authenticated state when user is authenticated', async () => {
      mockAuthApi.check.mockResolvedValueOnce({
        auth_required: true,
        authenticated: true,
      });

      const store = createAuthStore();
      const result = await store.checkAuth();

      expect(result).toBe(true);
      expect(store.authRequired).toBe(true);
      expect(store.isAuthenticated).toBe(true);
      expect(store.showAuthModal).toBe(false);
    });

    it('should show auth modal when auth is required but not authenticated', async () => {
      mockAuthApi.check.mockResolvedValueOnce({
        auth_required: true,
        authenticated: false,
      });

      const store = createAuthStore();
      const result = await store.checkAuth();

      expect(result).toBe(false);
      expect(store.showAuthModal).toBe(true);
      expect(store.isAuthenticated).toBe(false);
    });

    it('should handle network errors gracefully', async () => {
      mockAuthApi.check.mockRejectedValueOnce(new Error('Network error'));

      const store = createAuthStore();
      const result = await store.checkAuth();

      // Should allow to continue on network errors
      expect(result).toBe(true);
    });
  });

  describe('submitAuth', () => {
    it('should authenticate successfully', async () => {
      mockAuthApi.login.mockResolvedValueOnce({ success: true });
      mockAuthApi.fetchCsrfToken.mockResolvedValueOnce('new-token');

      const store = createAuthStore();
      store.authSecretInput = 'secret123';

      await store.submitAuth();

      expect(mockAuthApi.login).toHaveBeenCalledWith('secret123');
      expect(store.isAuthenticated).toBe(true);
      expect(store.showAuthModal).toBe(false);
      expect(store.authSecretInput).toBe('');
      expect(store.authError).toBe('');
    });

    it('should handle login failure', async () => {
      mockAuthApi.login.mockResolvedValueOnce({
        success: false,
        message: 'Invalid admin secret',
      });

      const store = createAuthStore();
      store.authSecretInput = 'wrong-secret';

      await store.submitAuth();

      expect(store.isAuthenticated).toBe(false);
      expect(store.authError).toBe('Invalid admin secret');
    });

    it('should set loading state during auth', async () => {
      mockAuthApi.login.mockImplementation(
        () => new Promise((resolve) => setTimeout(() => resolve({ success: true }), 100))
      );

      const store = createAuthStore();
      store.authSecretInput = 'secret';

      const promise = store.submitAuth();
      expect(store.authLoading).toBe(true);

      await promise;
      expect(store.authLoading).toBe(false);
    });
  });

  describe('logout', () => {
    it('should clear auth state on logout', async () => {
      mockAuthApi.logout.mockResolvedValueOnce(undefined);

      const store = createAuthStore();
      store.isAuthenticated = true;
      store.csrfToken = 'token';

      await store.logout();

      expect(store.isAuthenticated).toBe(false);
      expect(store.authRequired).toBe(true);
      expect(store.showAuthModal).toBe(true);
      expect(store.csrfToken).toBe('');
      expect(mockApiClient.setCsrfToken).toHaveBeenCalledWith('');
    });
  });

  describe('fetchCsrfToken', () => {
    it('should fetch and store CSRF token', async () => {
      mockAuthApi.fetchCsrfToken.mockResolvedValueOnce('csrf-token-123');

      const store = createAuthStore();
      await store.fetchCsrfToken();

      expect(store.csrfToken).toBe('csrf-token-123');
      expect(mockApiClient.setCsrfToken).toHaveBeenCalledWith('csrf-token-123');
    });

    it('should handle fetch errors gracefully', async () => {
      mockAuthApi.fetchCsrfToken.mockRejectedValueOnce(new Error('Failed'));

      const store = createAuthStore();
      await store.fetchCsrfToken();

      // Should not throw
      expect(store.csrfToken).toBe('');
    });
  });
});
