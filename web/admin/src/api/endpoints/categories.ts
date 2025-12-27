/**
 * Categories API Endpoints
 */

import { apiClient } from '../client';
import type { Category } from '../types';

export const categoriesApi = {
  /**
   * List all categories
   */
  async list(): Promise<Category[]> {
    return apiClient.fetch<Category[]>('/api/categories');
  },

  /**
   * Create a new category
   */
  async create(name: string, description?: string): Promise<Category> {
    const response = await apiClient.fetchResponse('/api/categories', {
      method: 'POST',
      body: JSON.stringify({ name, description }),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Create category failed: ${response.status}`);
    }

    return response.json();
  },

  /**
   * Delete a category
   */
  async delete(id: number): Promise<void> {
    const response = await apiClient.fetchResponse(`/api/categories/${id}`, {
      method: 'DELETE',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Delete category failed: ${response.status}`);
    }
  },
};
