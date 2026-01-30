/**
 * Auth API Endpoints
 */

import { apiClient } from '../client';
import type { AuthCheckResponse, CurrentUser } from '../types';

/**
 * Check authentication status
 */
export async function checkAuth(): Promise<AuthCheckResponse> {
  return apiClient.fetch('/api/auth/check');
}

/**
 * Get current user info
 */
export async function getCurrentUser(): Promise<CurrentUser> {
  return apiClient.fetch('/api/v1/users/me');
}

/**
 * Login
 */
export async function login(username: string, password: string): Promise<{ user: CurrentUser }> {
  return apiClient.fetch('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
}

/**
 * Logout
 */
export async function logout(): Promise<void> {
  await apiClient.fetch('/api/auth/logout', {
    method: 'POST',
  });
}

/**
 * Get CSRF token
 */
export async function getCsrfToken(): Promise<string> {
  return apiClient.refreshCsrfToken();
}
