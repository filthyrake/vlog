/**
 * User Management API Endpoints
 * Admin-only endpoints for managing users
 */

import { apiClient } from '../client';
import type {
  User,
  UserListResponse,
  CreateUserRequest,
  UpdateUserRequest,
  ApiKeyListResponse,
  CreateApiKeyRequest,
  CreateApiKeyResponse,
  InviteListResponse,
  CreateInviteRequest,
  CreateInviteResponse,
  ApiKey,
} from '../types';

export const usersApi = {
  // =============================================================================
  // User Management (Admin only)
  // =============================================================================

  /**
   * List all users
   */
  async list(params?: {
    limit?: number;
    offset?: number;
    role?: string;
    status?: string;
    search?: string;
  }): Promise<UserListResponse> {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set('limit', String(params.limit));
    if (params?.offset) searchParams.set('offset', String(params.offset));
    if (params?.role) searchParams.set('role', params.role);
    if (params?.status) searchParams.set('status', params.status);
    if (params?.search) searchParams.set('search', params.search);

    const query = searchParams.toString();
    const url = `/api/v1/users${query ? `?${query}` : ''}`;
    return apiClient.fetch<UserListResponse>(url);
  },

  /**
   * Get a user by ID
   */
  async get(userId: string): Promise<User> {
    return apiClient.fetch<User>(`/api/v1/users/${userId}`);
  },

  /**
   * Create a new user
   */
  async create(data: CreateUserRequest): Promise<User> {
    return apiClient.fetch<User>('/api/v1/users', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  /**
   * Update a user
   */
  async update(userId: string, data: UpdateUserRequest): Promise<User> {
    return apiClient.fetch<User>(`/api/v1/users/${userId}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });
  },

  /**
   * Delete (disable) a user
   */
  async delete(userId: string): Promise<void> {
    await apiClient.fetch(`/api/v1/users/${userId}`, {
      method: 'DELETE',
    });
  },

  /**
   * Force password reset for a user
   */
  async forcePasswordReset(userId: string): Promise<{ reset_token: string }> {
    return apiClient.fetch<{ reset_token: string }>(`/api/v1/users/${userId}/reset-password`, {
      method: 'POST',
    });
  },

  // =============================================================================
  // API Key Management
  // =============================================================================

  /**
   * List current user's API keys
   */
  async listApiKeys(): Promise<ApiKeyListResponse> {
    return apiClient.fetch<ApiKeyListResponse>('/api/v1/api-keys');
  },

  /**
   * Create a new API key
   */
  async createApiKey(data: CreateApiKeyRequest): Promise<CreateApiKeyResponse> {
    return apiClient.fetch<CreateApiKeyResponse>('/api/v1/api-keys', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  /**
   * Get an API key by ID
   */
  async getApiKey(keyId: string): Promise<ApiKey> {
    return apiClient.fetch<ApiKey>(`/api/v1/api-keys/${keyId}`);
  },

  /**
   * Revoke an API key
   */
  async revokeApiKey(keyId: string): Promise<void> {
    await apiClient.fetch(`/api/v1/api-keys/${keyId}`, {
      method: 'DELETE',
    });
  },

  // =============================================================================
  // Invite Management (Admin only)
  // =============================================================================

  /**
   * List all invites
   */
  async listInvites(pendingOnly: boolean = true): Promise<InviteListResponse> {
    const url = `/api/v1/invites?pending_only=${pendingOnly}`;
    return apiClient.fetch<InviteListResponse>(url);
  },

  /**
   * Create a new invite
   */
  async createInvite(data: CreateInviteRequest): Promise<CreateInviteResponse> {
    return apiClient.fetch<CreateInviteResponse>('/api/v1/invites', {
      method: 'POST',
      body: JSON.stringify(data),
    });
  },

  /**
   * Revoke an invite
   */
  async revokeInvite(inviteId: string): Promise<void> {
    await apiClient.fetch(`/api/v1/invites/${inviteId}`, {
      method: 'DELETE',
    });
  },
};
