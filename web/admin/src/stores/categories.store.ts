/**
 * Categories Store
 * Manages video categories
 */

import { categoriesApi } from '@/api/endpoints/categories';
import type { Category } from '@/api/types';
import type { AlpineContext } from './types';

export interface CategoriesState {
  // Data
  categories: Category[];

  // Form state
  newCategoryName: string;
  newCategoryDesc: string;

  // Loading/error
  loading: boolean;
  error: string | null;
}

export interface CategoriesActions {
  loadCategories(): Promise<void>;
  addCategory(): Promise<void>;
  createCategory(): Promise<void>; // Alias for addCategory
  deleteCategory(id: number): Promise<void>;
}

export type CategoriesStore = CategoriesState & CategoriesActions;

export function createCategoriesStore(_context?: AlpineContext): CategoriesStore {
  return {
    // Initial state
    categories: [],
    newCategoryName: '',
    newCategoryDesc: '',
    loading: false,
    error: null,

    /**
     * Load all categories
     */
    async loadCategories(): Promise<void> {
      this.loading = true;
      this.error = null;

      try {
        this.categories = await categoriesApi.list();
      } catch (e) {
        this.error = e instanceof Error ? e.message : 'Failed to load categories';
        this.categories = [];
      } finally {
        this.loading = false;
      }
    },

    /**
     * Add a new category
     */
    async addCategory(): Promise<void> {
      if (!this.newCategoryName.trim()) {
        return;
      }

      this.loading = true;
      this.error = null;

      try {
        const category = await categoriesApi.create(
          this.newCategoryName.trim(),
          this.newCategoryDesc.trim() || undefined
        );

        this.categories.push(category);
        this.newCategoryName = '';
        this.newCategoryDesc = '';
      } catch (e) {
        this.error = e instanceof Error ? e.message : 'Failed to create category';
      } finally {
        this.loading = false;
      }
    },

    // Alias for addCategory
    async createCategory(): Promise<void> {
      return this.addCategory();
    },

    /**
     * Delete a category
     */
    async deleteCategory(id: number): Promise<void> {
      this.loading = true;
      this.error = null;

      try {
        await categoriesApi.delete(id);
        this.categories = this.categories.filter((c) => c.id !== id);
      } catch (e) {
        this.error = e instanceof Error ? e.message : 'Failed to delete category';
      } finally {
        this.loading = false;
      }
    },
  };
}
