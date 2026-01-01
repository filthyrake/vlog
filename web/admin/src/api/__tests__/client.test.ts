/**
 * Tests for the API Client
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { ApiClient, apiClient } from '../client';
import { AuthenticationError, CsrfError, ApiClientError } from '../types';

// Mock fetch globally
const mockFetch = vi.fn();
global.fetch = mockFetch;

describe('ApiClient', () => {
  let client: ApiClient;

  beforeEach(() => {
    client = new ApiClient();
    mockFetch.mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe('fetchRaw', () => {
    it('should make a basic GET request', async () => {
      mockFetch.mockResolvedValueOnce(new Response('{"ok": true}', { status: 200 }));

      const response = await client.fetchRaw('/api/test');

      expect(mockFetch).toHaveBeenCalledWith('/api/test', {
        credentials: 'same-origin',
        signal: expect.any(AbortSignal),
      });
      expect(response.ok).toBe(true);
    });

    it('should include custom headers', async () => {
      mockFetch.mockResolvedValueOnce(new Response('{}', { status: 200 }));

      await client.fetchRaw('/api/test', {
        headers: { 'X-Custom': 'value' },
      });

      expect(mockFetch).toHaveBeenCalledWith('/api/test', expect.objectContaining({
        headers: { 'X-Custom': 'value' },
      }));
    });

    it('should handle timeout', async () => {
      // Mock fetch to simulate a request that takes longer than timeout
      mockFetch.mockImplementation((_url, options) => {
        return new Promise((_, reject) => {
          // Check if signal is aborted
          const signal = options?.signal as AbortSignal;
          if (signal) {
            signal.addEventListener('abort', () => {
              const error = new Error('Aborted');
              error.name = 'AbortError';
              reject(error);
            });
          }
        });
      });

      // Use a very short timeout
      await expect(client.fetchRaw('/api/test', { timeout: 10 }))
        .rejects.toThrow('Request timed out');
    });
  });

  describe('CSRF token management', () => {
    it('should add CSRF token to POST requests', async () => {
      client.setCsrfToken('test-token');
      mockFetch.mockResolvedValueOnce(new Response('{}', { status: 200 }));

      await client.fetchResponse('/api/test', { method: 'POST' });

      expect(mockFetch).toHaveBeenCalledWith('/api/test', expect.objectContaining({
        headers: expect.any(Headers),
      }));

      const callHeaders = mockFetch.mock.calls[0]?.[1]?.headers as Headers;
      expect(callHeaders.get('X-CSRF-Token')).toBe('test-token');
    });

    it('should add CSRF token to PUT requests', async () => {
      client.setCsrfToken('test-token');
      mockFetch.mockResolvedValueOnce(new Response('{}', { status: 200 }));

      await client.fetchResponse('/api/test', { method: 'PUT' });

      const callHeaders = mockFetch.mock.calls[0]?.[1]?.headers as Headers;
      expect(callHeaders.get('X-CSRF-Token')).toBe('test-token');
    });

    it('should add CSRF token to DELETE requests', async () => {
      client.setCsrfToken('test-token');
      mockFetch.mockResolvedValueOnce(new Response('{}', { status: 200 }));

      await client.fetchResponse('/api/test', { method: 'DELETE' });

      const callHeaders = mockFetch.mock.calls[0]?.[1]?.headers as Headers;
      expect(callHeaders.get('X-CSRF-Token')).toBe('test-token');
    });

    it('should NOT add CSRF token to GET requests', async () => {
      client.setCsrfToken('test-token');
      mockFetch.mockResolvedValueOnce(new Response('{}', { status: 200 }));

      await client.fetchResponse('/api/test', { method: 'GET' });

      const callHeaders = mockFetch.mock.calls[0]?.[1]?.headers as Headers;
      expect(callHeaders.get('X-CSRF-Token')).toBeNull();
    });

    it('should refresh CSRF token on 403 with CSRF error', async () => {
      client.setCsrfToken('old-token');

      // First request fails with CSRF error
      mockFetch.mockResolvedValueOnce(new Response(
        JSON.stringify({ detail: 'CSRF token invalid' }),
        { status: 403 }
      ));

      // CSRF refresh request
      mockFetch.mockResolvedValueOnce(new Response(
        JSON.stringify({ csrf_token: 'new-token' }),
        { status: 200 }
      ));

      // Retry request succeeds
      mockFetch.mockResolvedValueOnce(new Response('{"success": true}', { status: 200 }));

      const response = await client.fetchResponse('/api/test', { method: 'POST' });

      expect(response.ok).toBe(true);
      expect(mockFetch).toHaveBeenCalledTimes(3);
      expect(client.getCsrfToken()).toBe('new-token');
    });

    it('should throw CsrfError after retry fails', async () => {
      client.setCsrfToken('old-token');

      // First request fails with CSRF error
      mockFetch.mockResolvedValueOnce(new Response(
        JSON.stringify({ detail: 'CSRF token invalid' }),
        { status: 403 }
      ));

      // CSRF refresh request
      mockFetch.mockResolvedValueOnce(new Response(
        JSON.stringify({ csrf_token: 'new-token' }),
        { status: 200 }
      ));

      // Retry request also fails with CSRF error
      mockFetch.mockResolvedValueOnce(new Response(
        JSON.stringify({ detail: 'CSRF token invalid' }),
        { status: 403 }
      ));

      await expect(client.fetchResponse('/api/test', { method: 'POST' }))
        .rejects.toThrow(CsrfError);
    });
  });

  describe('authentication handling', () => {
    it('should throw AuthenticationError on 401', async () => {
      mockFetch.mockResolvedValueOnce(new Response('', { status: 401 }));

      await expect(client.fetchResponse('/api/test'))
        .rejects.toThrow(AuthenticationError);
    });

    it('should call onAuthRequired callback on 401', async () => {
      const onAuthRequired = vi.fn();
      const clientWithCallback = new ApiClient({ onAuthRequired });

      mockFetch.mockResolvedValueOnce(new Response('', { status: 401 }));

      await expect(clientWithCallback.fetchResponse('/api/test'))
        .rejects.toThrow(AuthenticationError);

      expect(onAuthRequired).toHaveBeenCalled();
    });

    it('should throw AuthenticationError on 403 without CSRF error', async () => {
      mockFetch.mockResolvedValueOnce(new Response(
        JSON.stringify({ detail: 'Forbidden' }),
        { status: 403 }
      ));

      await expect(client.fetchResponse('/api/test'))
        .rejects.toThrow(AuthenticationError);
    });
  });

  describe('fetch with JSON parsing', () => {
    it('should parse JSON response', async () => {
      mockFetch.mockResolvedValueOnce(new Response(
        JSON.stringify({ id: 1, name: 'test' }),
        { status: 200 }
      ));

      const data = await client.fetch<{ id: number; name: string }>('/api/test');

      expect(data).toEqual({ id: 1, name: 'test' });
    });

    it('should throw ApiClientError on non-ok response', async () => {
      mockFetch.mockResolvedValueOnce(new Response(
        JSON.stringify({ detail: 'Not found' }),
        { status: 404 }
      ));

      await expect(client.fetch('/api/test'))
        .rejects.toThrow(ApiClientError);
    });
  });

  describe('Content-Type handling', () => {
    it('should add Content-Type for JSON body', async () => {
      mockFetch.mockResolvedValueOnce(new Response('{}', { status: 200 }));

      await client.fetchResponse('/api/test', {
        method: 'POST',
        body: JSON.stringify({ data: 'test' }),
      });

      const callHeaders = mockFetch.mock.calls[0]?.[1]?.headers as Headers;
      expect(callHeaders.get('Content-Type')).toBe('application/json');
    });

    it('should NOT add Content-Type for FormData', async () => {
      mockFetch.mockResolvedValueOnce(new Response('{}', { status: 200 }));
      const formData = new FormData();
      formData.append('file', 'test');

      await client.fetchResponse('/api/test', {
        method: 'POST',
        body: formData,
      });

      const callHeaders = mockFetch.mock.calls[0]?.[1]?.headers as Headers;
      // FormData should not have Content-Type set (browser handles it)
      expect(callHeaders.get('Content-Type')).toBeNull();
    });
  });

  describe('singleton instance', () => {
    it('should export a singleton apiClient', () => {
      expect(apiClient).toBeInstanceOf(ApiClient);
    });
  });
});
