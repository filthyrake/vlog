/**
 * Custom Fields API Endpoints
 */

import { apiClient } from '../client';
import type { CustomField, CustomFieldConstraint, CustomFieldType } from '../types';

export interface CreateCustomFieldRequest {
  name: string;
  field_key: string;
  field_type: CustomFieldType;
  description?: string;
  required?: boolean;
  constraints?: CustomFieldConstraint;
  applies_to_categories?: number[];
  display_order?: number;
}

export interface UpdateCustomFieldRequest extends Partial<CreateCustomFieldRequest> {
  id: number;
}

export const customFieldsApi = {
  /**
   * List all custom field definitions
   */
  async list(): Promise<CustomField[]> {
    return apiClient.fetch<CustomField[]>('/api/custom-fields');
  },

  /**
   * Create a new custom field definition
   */
  async create(data: CreateCustomFieldRequest): Promise<CustomField> {
    const response = await apiClient.fetchResponse('/api/custom-fields', {
      method: 'POST',
      body: JSON.stringify(data),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Create custom field failed: ${response.status}`);
    }

    return response.json();
  },

  /**
   * Update an existing custom field definition
   */
  async update(data: UpdateCustomFieldRequest): Promise<CustomField> {
    const response = await apiClient.fetchResponse(`/api/custom-fields/${data.id}`, {
      method: 'PUT',
      body: JSON.stringify(data),
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Update custom field failed: ${response.status}`);
    }

    return response.json();
  },

  /**
   * Delete a custom field definition
   */
  async delete(id: number): Promise<void> {
    const response = await apiClient.fetchResponse(`/api/custom-fields/${id}`, {
      method: 'DELETE',
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new Error(errorData.detail || `Delete custom field failed: ${response.status}`);
    }
  },
};
