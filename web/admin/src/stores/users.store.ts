/**
 * Users Store
 * Manages user management state (admin only)
 */

import { usersApi } from '@/api/endpoints/users';
import type {
  User,
  UserRole,
  ApiKey,
  CreateApiKeyResponse,
  Invite,
  CreateInviteResponse,
} from '@/api/types';

export interface UsersState {
  // User list
  users: User[];
  usersLoading: boolean;
  usersError: string;
  usersTotal: number;
  usersLimit: number;
  usersOffset: number;

  // User filters
  userRoleFilter: string;
  userStatusFilter: string;
  userSearch: string;

  // User edit modal
  showUserModal: boolean;
  editingUser: User | null;
  userForm: {
    username: string;
    email: string;
    password: string;
    display_name: string;
    role: UserRole;
  };
  userFormError: string;
  userFormLoading: boolean;

  // API Keys (for current user's profile)
  apiKeys: ApiKey[];
  apiKeysLoading: boolean;
  showApiKeyModal: boolean;
  apiKeyForm: {
    name: string;
    expires_in_days: number | null;
  };
  newApiKey: CreateApiKeyResponse | null;
  apiKeyFormError: string;
  apiKeyFormLoading: boolean;

  // Invites
  invites: Invite[];
  invitesLoading: boolean;
  showInviteModal: boolean;
  inviteForm: {
    email: string;
    role: UserRole;
    expires_in_days: number;
  };
  newInvite: CreateInviteResponse | null;
  inviteFormError: string;
  inviteFormLoading: boolean;
}

export interface UsersActions {
  // User management
  loadUsers(): Promise<void>;
  loadMoreUsers(): Promise<void>;
  openCreateUserModal(): void;
  openEditUserModal(user: User): void;
  closeUserModal(): void;
  saveUser(): Promise<void>;
  deleteUser(userId: string): Promise<void>;
  forcePasswordReset(userId: string): Promise<void>;

  // API Keys
  loadApiKeys(): Promise<void>;
  openCreateApiKeyModal(): void;
  closeApiKeyModal(): void;
  createApiKey(): Promise<void>;
  revokeApiKey(keyId: string): Promise<void>;

  // Invites
  loadInvites(): Promise<void>;
  openCreateInviteModal(): void;
  closeInviteModal(): void;
  createInvite(): Promise<void>;
  revokeInvite(inviteId: string): Promise<void>;
}

export type UsersStore = UsersState & UsersActions;

export function createUsersStore(): UsersStore {
  return {
    // Initial state
    users: [],
    usersLoading: false,
    usersError: '',
    usersTotal: 0,
    usersLimit: 50,
    usersOffset: 0,

    userRoleFilter: '',
    userStatusFilter: '',
    userSearch: '',

    showUserModal: false,
    editingUser: null,
    userForm: {
      username: '',
      email: '',
      password: '',
      display_name: '',
      role: 'viewer',
    },
    userFormError: '',
    userFormLoading: false,

    apiKeys: [],
    apiKeysLoading: false,
    showApiKeyModal: false,
    apiKeyForm: {
      name: '',
      expires_in_days: null,
    },
    newApiKey: null,
    apiKeyFormError: '',
    apiKeyFormLoading: false,

    invites: [],
    invitesLoading: false,
    showInviteModal: false,
    inviteForm: {
      email: '',
      role: 'viewer',
      expires_in_days: 7,
    },
    newInvite: null,
    inviteFormError: '',
    inviteFormLoading: false,

    // =============================================================================
    // User Management
    // =============================================================================

    async loadUsers(): Promise<void> {
      this.usersLoading = true;
      this.usersError = '';
      this.usersOffset = 0;

      try {
        const data = await usersApi.list({
          limit: this.usersLimit,
          offset: 0,
          role: this.userRoleFilter || undefined,
          status: this.userStatusFilter || undefined,
          search: this.userSearch || undefined,
        });
        this.users = data.users;
        this.usersTotal = data.total;
      } catch (e: any) {
        this.usersError = e.detail || 'Failed to load users';
        console.error('Failed to load users:', e);
      } finally {
        this.usersLoading = false;
      }
    },

    async loadMoreUsers(): Promise<void> {
      if (this.users.length >= this.usersTotal) return;

      this.usersLoading = true;
      const newOffset = this.usersOffset + this.usersLimit;

      try {
        const data = await usersApi.list({
          limit: this.usersLimit,
          offset: newOffset,
          role: this.userRoleFilter || undefined,
          status: this.userStatusFilter || undefined,
          search: this.userSearch || undefined,
        });
        this.users = [...this.users, ...data.users];
        this.usersOffset = newOffset;
      } catch (e) {
        console.error('Failed to load more users:', e);
      } finally {
        this.usersLoading = false;
      }
    },

    openCreateUserModal(): void {
      this.editingUser = null;
      this.userForm = {
        username: '',
        email: '',
        password: '',
        display_name: '',
        role: 'viewer',
      };
      this.userFormError = '';
      this.showUserModal = true;
    },

    openEditUserModal(user: User): void {
      this.editingUser = user;
      this.userForm = {
        username: user.username,
        email: user.email,
        password: '',  // Don't populate password for editing
        display_name: user.display_name || '',
        role: user.role,
      };
      this.userFormError = '';
      this.showUserModal = true;
    },

    closeUserModal(): void {
      this.showUserModal = false;
      this.editingUser = null;
      this.userFormError = '';
    },

    async saveUser(): Promise<void> {
      this.userFormError = '';
      this.userFormLoading = true;

      try {
        if (this.editingUser) {
          // Update existing user
          const updateData: any = {
            role: this.userForm.role,
          };

          if (this.userForm.username !== this.editingUser.username) {
            updateData.username = this.userForm.username;
          }
          if (this.userForm.email !== this.editingUser.email) {
            updateData.email = this.userForm.email;
          }
          if (this.userForm.display_name !== (this.editingUser.display_name || '')) {
            updateData.display_name = this.userForm.display_name || null;
          }

          const updated = await usersApi.update(this.editingUser.id, updateData);

          // Update in list
          const index = this.users.findIndex(u => u.id === this.editingUser!.id);
          if (index !== -1) {
            this.users[index] = updated;
          }
        } else {
          // Create new user
          if (!this.userForm.password) {
            this.userFormError = 'Password is required for new users';
            return;
          }

          const newUser = await usersApi.create({
            username: this.userForm.username,
            email: this.userForm.email,
            password: this.userForm.password,
            display_name: this.userForm.display_name || undefined,
            role: this.userForm.role,
          });

          this.users = [newUser, ...this.users];
          this.usersTotal++;
        }

        this.closeUserModal();
      } catch (e: any) {
        this.userFormError = e.detail || 'Failed to save user';
      } finally {
        this.userFormLoading = false;
      }
    },

    async deleteUser(userId: string): Promise<void> {
      try {
        await usersApi.delete(userId);

        // Update in list (mark as disabled)
        const user = this.users.find(u => u.id === userId);
        if (user) {
          user.status = 'disabled';
        }
      } catch (e) {
        console.error('Failed to delete user:', e);
        throw e;
      }
    },

    async forcePasswordReset(userId: string): Promise<void> {
      try {
        const result = await usersApi.forcePasswordReset(userId);
        // The reset token should be communicated to the user
        // For now, just log it (in production, this would be sent via email)
        console.log('Password reset token:', result.reset_token);
        return;
      } catch (e) {
        console.error('Failed to reset password:', e);
        throw e;
      }
    },

    // =============================================================================
    // API Keys
    // =============================================================================

    async loadApiKeys(): Promise<void> {
      this.apiKeysLoading = true;

      try {
        const data = await usersApi.listApiKeys();
        this.apiKeys = data.keys;
      } catch (e) {
        console.error('Failed to load API keys:', e);
      } finally {
        this.apiKeysLoading = false;
      }
    },

    openCreateApiKeyModal(): void {
      this.apiKeyForm = {
        name: '',
        expires_in_days: null,
      };
      this.newApiKey = null;
      this.apiKeyFormError = '';
      this.showApiKeyModal = true;
    },

    closeApiKeyModal(): void {
      this.showApiKeyModal = false;
      this.newApiKey = null;
      this.apiKeyFormError = '';
    },

    async createApiKey(): Promise<void> {
      this.apiKeyFormError = '';
      this.apiKeyFormLoading = true;

      try {
        const result = await usersApi.createApiKey({
          name: this.apiKeyForm.name,
          expires_in_days: this.apiKeyForm.expires_in_days || undefined,
        });

        this.newApiKey = result;
        this.apiKeys = [
          {
            id: result.id,
            name: result.name,
            key_prefix: result.key_prefix,
            expires_at: result.expires_at,
            created_at: result.created_at,
          },
          ...this.apiKeys,
        ];
      } catch (e: any) {
        this.apiKeyFormError = e.detail || 'Failed to create API key';
      } finally {
        this.apiKeyFormLoading = false;
      }
    },

    async revokeApiKey(keyId: string): Promise<void> {
      try {
        await usersApi.revokeApiKey(keyId);
        this.apiKeys = this.apiKeys.filter(k => k.id !== keyId);
      } catch (e) {
        console.error('Failed to revoke API key:', e);
        throw e;
      }
    },

    // =============================================================================
    // Invites
    // =============================================================================

    async loadInvites(): Promise<void> {
      this.invitesLoading = true;

      try {
        const data = await usersApi.listInvites(true);
        this.invites = data.invites;
      } catch (e) {
        console.error('Failed to load invites:', e);
      } finally {
        this.invitesLoading = false;
      }
    },

    openCreateInviteModal(): void {
      this.inviteForm = {
        email: '',
        role: 'viewer',
        expires_in_days: 7,
      };
      this.newInvite = null;
      this.inviteFormError = '';
      this.showInviteModal = true;
    },

    closeInviteModal(): void {
      this.showInviteModal = false;
      this.newInvite = null;
      this.inviteFormError = '';
    },

    async createInvite(): Promise<void> {
      this.inviteFormError = '';
      this.inviteFormLoading = true;

      try {
        const result = await usersApi.createInvite({
          email: this.inviteForm.email,
          role: this.inviteForm.role,
          expires_in_days: this.inviteForm.expires_in_days,
        });

        this.newInvite = result;
        this.invites = [
          {
            id: result.id,
            email: result.email,
            role: result.role,
            expires_at: result.expires_at,
            created_at: result.created_at,
          },
          ...this.invites,
        ];
      } catch (e: any) {
        this.inviteFormError = e.detail || 'Failed to create invite';
      } finally {
        this.inviteFormLoading = false;
      }
    },

    async revokeInvite(inviteId: string): Promise<void> {
      try {
        await usersApi.revokeInvite(inviteId);
        this.invites = this.invites.filter(i => i.id !== inviteId);
      } catch (e) {
        console.error('Failed to revoke invite:', e);
        throw e;
      }
    },
  };
}
