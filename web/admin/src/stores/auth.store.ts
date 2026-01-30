/**
 * Authentication Store
 * Manages authentication state and login/logout operations
 * Supports both legacy (admin secret) and user-based authentication
 */

import { authApi } from '@/api/endpoints/auth';
import { apiClient } from '@/api/client';
import type { CurrentUser, UserSession } from '@/api/types';

export interface AuthState {
  // State
  isAuthenticated: boolean;
  authRequired: boolean;
  authMode: 'legacy' | 'user';
  showAuthModal: boolean;
  showForgotPasswordModal: boolean;

  // Setup wizard state
  needsSetup: boolean;
  showSetupWizard: boolean;
  setupUsername: string;
  setupEmail: string;
  setupPassword: string;
  setupConfirmPassword: string;
  setupDisplayName: string;
  setupError: string;
  setupLoading: boolean;

  // Current user (user-based auth)
  currentUser: CurrentUser | null;

  // Login form state
  loginUsername: string;
  loginPassword: string;

  // Legacy auth (admin secret)
  authSecretInput: string;

  // OIDC state
  oidcEnabled: boolean;
  oidcProviderName: string;

  // UI state
  authError: string;
  authLoading: boolean;
  csrfToken: string;

  // Profile/sessions
  sessions: UserSession[];
  sessionsLoading: boolean;

  // Password change
  showChangePasswordModal: boolean;
  currentPassword: string;
  newPassword: string;
  confirmPassword: string;
  passwordError: string;
  passwordLoading: boolean;

  // Forgot password
  forgotPasswordEmail: string;
  forgotPasswordSent: boolean;
  forgotPasswordError: string;
  forgotPasswordLoading: boolean;
}

export interface AuthActions {
  checkAuth(): Promise<boolean>;
  submitAuth(): Promise<void>;
  submitUserAuth(): Promise<void>;
  submitSetup(): Promise<void>;
  logout(): Promise<void>;
  fetchCsrfToken(): Promise<void>;

  // Profile actions
  loadSessions(): Promise<void>;
  revokeSession(sessionId: string): Promise<void>;

  // Password actions
  openChangePasswordModal(): void;
  closeChangePasswordModal(): void;
  submitChangePassword(): Promise<void>;

  // Forgot password actions
  openForgotPasswordModal(): void;
  closeForgotPasswordModal(): void;
  submitForgotPassword(): Promise<void>;

  // OIDC
  startOidcLogin(): Promise<void>;

  // Helpers
  hasPermission(permission: string): boolean;
  isAdmin(): boolean;
}

export type AuthStore = AuthState & AuthActions;

export function createAuthStore(): AuthStore {
  return {
    // Initial state
    isAuthenticated: false,
    authRequired: false,
    authMode: 'user',
    showAuthModal: false,
    showForgotPasswordModal: false,

    // Setup wizard state
    needsSetup: false,
    showSetupWizard: false,
    setupUsername: '',
    setupEmail: '',
    setupPassword: '',
    setupConfirmPassword: '',
    setupDisplayName: '',
    setupError: '',
    setupLoading: false,

    currentUser: null,

    loginUsername: '',
    loginPassword: '',
    authSecretInput: '',

    oidcEnabled: false,
    oidcProviderName: 'SSO',

    authError: '',
    authLoading: false,
    csrfToken: '',

    sessions: [],
    sessionsLoading: false,

    showChangePasswordModal: false,
    currentPassword: '',
    newPassword: '',
    confirmPassword: '',
    passwordError: '',
    passwordLoading: false,

    forgotPasswordEmail: '',
    forgotPasswordSent: false,
    forgotPasswordError: '',
    forgotPasswordLoading: false,

    /**
     * Check if authentication is required and current auth status
     */
    async checkAuth(): Promise<boolean> {
      try {
        // First check if initial setup is needed
        try {
          const setupStatus = await authApi.checkSetup();
          this.needsSetup = setupStatus.needs_setup;
          if (setupStatus.needs_setup) {
            this.showSetupWizard = true;
            return false;
          }
        } catch (e) {
          // Setup check failed, continue with normal auth flow
          console.warn('Setup check failed:', e);
        }

        const data = await authApi.check();
        this.authRequired = data.auth_required;
        this.isAuthenticated = data.authenticated;
        this.authMode = data.auth_mode || 'user';
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
        // Allow to continue on network errors
        return true;
      }
    },

    /**
     * Submit authentication via user credentials
     */
    async submitUserAuth(): Promise<void> {
      this.authError = '';
      this.authLoading = true;

      try {
        const result = await authApi.loginUser(this.loginUsername, this.loginPassword);

        if (!result.success) {
          this.authError = result.message || 'Authentication failed';
          return;
        }

        // Success - server has set the session cookie
        this.isAuthenticated = true;
        this.showAuthModal = false;
        this.loginUsername = '';
        this.loginPassword = '';

        if (result.user) {
          this.currentUser = result.user;
        }

        // Fetch CSRF token for state-changing requests
        await this.fetchCsrfToken();
      } catch (e) {
        this.authError = 'Failed to authenticate: ' + (e instanceof Error ? e.message : String(e));
      } finally {
        this.authLoading = false;
      }
    },

    /**
     * Submit initial admin setup
     */
    async submitSetup(): Promise<void> {
      this.setupError = '';

      // Validate passwords match
      if (this.setupPassword !== this.setupConfirmPassword) {
        this.setupError = 'Passwords do not match';
        return;
      }

      // Validate password length
      if (this.setupPassword.length < 12) {
        this.setupError = 'Password must be at least 12 characters';
        return;
      }

      this.setupLoading = true;

      try {
        const result = await authApi.setup({
          username: this.setupUsername,
          email: this.setupEmail,
          password: this.setupPassword,
          display_name: this.setupDisplayName || undefined,
        });

        if (!result.success) {
          this.setupError = result.message || 'Setup failed';
          return;
        }

        // Success - server has set the session cookie and logged us in
        this.isAuthenticated = true;
        this.needsSetup = false;
        this.showSetupWizard = false;
        this.currentUser = {
          id: result.user_id!,
          username: result.username!,
          email: result.email!,
          display_name: this.setupDisplayName || undefined,
          role: 'admin',
          avatar_url: undefined,
          permissions: [],
        };

        // Clear form
        this.setupUsername = '';
        this.setupEmail = '';
        this.setupPassword = '';
        this.setupConfirmPassword = '';
        this.setupDisplayName = '';

        // Fetch CSRF token
        await this.fetchCsrfToken();
      } catch (e) {
        this.setupError = 'Setup failed: ' + (e instanceof Error ? e.message : String(e));
      } finally {
        this.setupLoading = false;
      }
    },

    /**
     * Submit authentication via admin secret (legacy)
     */
    async submitAuth(): Promise<void> {
      // If in user mode, use the user auth flow
      if (this.authMode === 'user') {
        return this.submitUserAuth();
      }

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
      this.currentUser = null;
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

    /**
     * Load user sessions
     */
    async loadSessions(): Promise<void> {
      if (this.authMode !== 'user' || !this.currentUser) return;

      this.sessionsLoading = true;
      try {
        const data = await authApi.listSessions();
        this.sessions = data.sessions;
      } catch (e) {
        console.error('Failed to load sessions:', e);
      } finally {
        this.sessionsLoading = false;
      }
    },

    /**
     * Revoke a session
     */
    async revokeSession(sessionId: string): Promise<void> {
      try {
        await authApi.revokeSession(sessionId);
        this.sessions = this.sessions.filter(s => s.id !== sessionId);
      } catch (e) {
        console.error('Failed to revoke session:', e);
        throw e;
      }
    },

    /**
     * Open change password modal
     */
    openChangePasswordModal(): void {
      this.showChangePasswordModal = true;
      this.currentPassword = '';
      this.newPassword = '';
      this.confirmPassword = '';
      this.passwordError = '';
    },

    /**
     * Close change password modal
     */
    closeChangePasswordModal(): void {
      this.showChangePasswordModal = false;
      this.currentPassword = '';
      this.newPassword = '';
      this.confirmPassword = '';
      this.passwordError = '';
    },

    /**
     * Submit password change
     */
    async submitChangePassword(): Promise<void> {
      this.passwordError = '';

      if (this.newPassword !== this.confirmPassword) {
        this.passwordError = 'Passwords do not match';
        return;
      }

      if (this.newPassword.length < 12) {
        this.passwordError = 'Password must be at least 12 characters';
        return;
      }

      this.passwordLoading = true;
      try {
        await authApi.changePassword({
          current_password: this.currentPassword,
          new_password: this.newPassword,
        });
        this.closeChangePasswordModal();
        // Show success toast - will be handled by parent store
      } catch (e: any) {
        this.passwordError = e.detail || 'Failed to change password';
      } finally {
        this.passwordLoading = false;
      }
    },

    /**
     * Open forgot password modal
     */
    openForgotPasswordModal(): void {
      this.showForgotPasswordModal = true;
      this.forgotPasswordEmail = '';
      this.forgotPasswordSent = false;
      this.forgotPasswordError = '';
    },

    /**
     * Close forgot password modal
     */
    closeForgotPasswordModal(): void {
      this.showForgotPasswordModal = false;
      this.forgotPasswordEmail = '';
      this.forgotPasswordSent = false;
      this.forgotPasswordError = '';
    },

    /**
     * Submit forgot password request
     */
    async submitForgotPassword(): Promise<void> {
      this.forgotPasswordError = '';
      this.forgotPasswordLoading = true;

      try {
        await authApi.forgotPassword(this.forgotPasswordEmail);
        this.forgotPasswordSent = true;
      } catch (e) {
        // Always show success to prevent user enumeration
        this.forgotPasswordSent = true;
      } finally {
        this.forgotPasswordLoading = false;
      }
    },

    /**
     * Start OIDC login flow
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
     * Check if current user has a permission
     */
    hasPermission(permission: string): boolean {
      if (!this.currentUser) return false;
      if (this.currentUser.role === 'admin') return true;
      return this.currentUser.permissions.includes(permission);
    },

    /**
     * Check if current user is admin
     */
    isAdmin(): boolean {
      return this.currentUser?.role === 'admin';
    },
  };
}
