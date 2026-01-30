/**
 * VLog Studio API Client
 * Centralized API client with authentication, CSRF protection, and error handling
 */

import { ApiClientError, AuthenticationError, CsrfError } from './types';

export interface ApiClientConfig {
  baseUrl: string;
  defaultTimeout: number;
  onAuthRequired?: () => void;
  onCsrfRefresh?: (token: string) => void;
}

export interface RequestOptions extends RequestInit {
  timeout?: number;
}

const DEFAULT_CONFIG: ApiClientConfig = {
  baseUrl: '',
  defaultTimeout: 30000,
};

// HTTP methods that require CSRF protection
const CSRF_METHODS = ['POST', 'PUT', 'DELETE', 'PATCH'];

/**
 * API Client for VLog Studio
 */
export class ApiClient {
  private config: ApiClientConfig;
  private csrfToken: string = '';

  constructor(config: Partial<ApiClientConfig> = {}) {
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  getCsrfToken(): string {
    return this.csrfToken;
  }

  setCsrfToken(token: string): void {
    this.csrfToken = token;
  }

  async refreshCsrfToken(): Promise<string> {
    const response = await this.fetchRaw('/api/auth/csrf-token', {
      method: 'GET',
    });

    if (!response.ok) {
      throw new ApiClientError('Failed to fetch CSRF token', response.status);
    }

    const data = await response.json();
    this.csrfToken = data.csrf_token || '';
    this.config.onCsrfRefresh?.(this.csrfToken);
    return this.csrfToken;
  }

  async fetchRaw(url: string, options: RequestOptions = {}): Promise<Response> {
    const { timeout = this.config.defaultTimeout, ...fetchOptions } = options;
    const fullUrl = this.config.baseUrl + url;

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeout);

    try {
      const response = await fetch(fullUrl, {
        ...fetchOptions,
        credentials: 'same-origin',
        signal: controller.signal,
      });
      clearTimeout(timeoutId);
      return response;
    } catch (error) {
      clearTimeout(timeoutId);
      if (error instanceof Error && error.name === 'AbortError') {
        throw new ApiClientError('Request timed out', 0);
      }
      throw error;
    }
  }

  async fetch<T>(
    url: string,
    options: RequestOptions = {},
    isRetry = false
  ): Promise<T> {
    const response = await this.fetchWithAuth(url, options, isRetry);

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ detail: response.statusText }));
      throw new ApiClientError(
        errorData.detail || `Request failed: ${response.status}`,
        response.status,
        errorData.detail
      );
    }

    return response.json();
  }

  async fetchResponse(
    url: string,
    options: RequestOptions = {},
    isRetry = false
  ): Promise<Response> {
    return this.fetchWithAuth(url, options, isRetry);
  }

  private async fetchWithAuth(
    url: string,
    options: RequestOptions = {},
    isRetry = false
  ): Promise<Response> {
    const { timeout = this.config.defaultTimeout, ...fetchOptions } = options;
    const method = (fetchOptions.method || 'GET').toUpperCase();

    const headers = new Headers(fetchOptions.headers);

    // Add CSRF token for state-changing requests
    if (CSRF_METHODS.includes(method) && this.csrfToken) {
      headers.set('X-CSRF-Token', this.csrfToken);
    }

    // Add Content-Type for JSON body if not FormData
    if (fetchOptions.body && !(fetchOptions.body instanceof FormData)) {
      if (!headers.has('Content-Type')) {
        headers.set('Content-Type', 'application/json');
      }
    }

    const response = await this.fetchRaw(url, {
      ...fetchOptions,
      headers,
      timeout,
    });

    // Handle 401 Unauthorized
    if (response.status === 401) {
      this.config.onAuthRequired?.();
      throw new AuthenticationError();
    }

    // Handle 403 Forbidden (might be CSRF error)
    if (response.status === 403) {
      const data = await response.clone().json().catch(() => ({}));

      // Check if this is a CSRF error
      if (data.detail && data.detail.includes('CSRF')) {
        if (!isRetry) {
          await this.refreshCsrfToken();
          return this.fetchWithAuth(url, options, true);
        }
        throw new CsrfError();
      }

      this.config.onAuthRequired?.();
      throw new AuthenticationError();
    }

    return response;
  }
}

// Singleton instance
export const apiClient = new ApiClient();
