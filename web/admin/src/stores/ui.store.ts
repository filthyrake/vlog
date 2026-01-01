/**
 * UI Store
 * Manages global UI state like active tab, loading states, toast notifications, etc.
 */

import type { AdminTab, SettingsTab } from './types';
import type { VlogAlertContainer, AlertConfig } from '@/components/base/vlog-alert-container';

export interface UIState {
  // Current active tab
  tab: AdminTab;

  // Settings sub-tab
  settingsTab: SettingsTab;

  // Toast notification container reference
  toastContainer: VlogAlertContainer | null;

  // Search and filter state
  searchQuery: string;
  statusFilter: string;
  categoryFilter: number | null;

  // Sort state
  sortColumn: string;
  sortDirection: 'asc' | 'desc';
}

export interface UIActions {
  setTab(tab: AdminTab): void;
  setSettingsTab(tab: SettingsTab): void;

  // Toast notifications
  initToastContainer(): void;
  showSuccess(message: string, options?: Partial<AlertConfig>): string | null;
  showError(message: string, options?: Partial<AlertConfig>): string | null;
  showWarning(message: string, options?: Partial<AlertConfig>): string | null;
  showInfo(message: string, options?: Partial<AlertConfig>): string | null;

  // Search and filter
  setSearchQuery(query: string): void;
  setStatusFilter(status: string): void;
  setCategoryFilter(categoryId: number | null): void;
  clearFilters(): void;

  // Sorting
  setSort(column: string, direction?: 'asc' | 'desc'): void;
  toggleSort(column: string): void;
}

export type UIStore = UIState & UIActions;

export function createUIStore(): UIStore {
  return {
    // Initial state
    tab: 'videos',
    settingsTab: 'watermark',
    toastContainer: null,
    searchQuery: '',
    statusFilter: '',
    categoryFilter: null,
    sortColumn: 'updated_at',
    sortDirection: 'desc',

    /**
     * Set the active tab
     */
    setTab(tab: AdminTab): void {
      this.tab = tab;
    },

    /**
     * Set the settings sub-tab
     */
    setSettingsTab(tab: SettingsTab): void {
      this.settingsTab = tab;
    },

    /**
     * Initialize the toast container reference
     */
    initToastContainer(): void {
      this.toastContainer = document.querySelector('vlog-alert-container');
    },

    /**
     * Show a success toast notification
     */
    showSuccess(message: string, options?: Partial<AlertConfig>): string | null {
      if (!this.toastContainer) this.initToastContainer();
      return this.toastContainer?.success(message, options) ?? null;
    },

    /**
     * Show an error toast notification
     */
    showError(message: string, options?: Partial<AlertConfig>): string | null {
      if (!this.toastContainer) this.initToastContainer();
      return this.toastContainer?.error(message, options) ?? null;
    },

    /**
     * Show a warning toast notification
     */
    showWarning(message: string, options?: Partial<AlertConfig>): string | null {
      if (!this.toastContainer) this.initToastContainer();
      return this.toastContainer?.warning(message, options) ?? null;
    },

    /**
     * Show an info toast notification
     */
    showInfo(message: string, options?: Partial<AlertConfig>): string | null {
      if (!this.toastContainer) this.initToastContainer();
      return this.toastContainer?.info(message, options) ?? null;
    },

    /**
     * Set the search query
     */
    setSearchQuery(query: string): void {
      this.searchQuery = query;
    },

    /**
     * Set the status filter
     */
    setStatusFilter(status: string): void {
      this.statusFilter = status;
    },

    /**
     * Set the category filter
     */
    setCategoryFilter(categoryId: number | null): void {
      this.categoryFilter = categoryId;
    },

    /**
     * Clear all filters
     */
    clearFilters(): void {
      this.searchQuery = '';
      this.statusFilter = '';
      this.categoryFilter = null;
    },

    /**
     * Set the sort column and direction
     */
    setSort(column: string, direction?: 'asc' | 'desc'): void {
      this.sortColumn = column;
      this.sortDirection = direction || 'asc';
    },

    /**
     * Toggle sort direction for a column
     */
    toggleSort(column: string): void {
      if (this.sortColumn === column) {
        this.sortDirection = this.sortDirection === 'asc' ? 'desc' : 'asc';
      } else {
        this.sortColumn = column;
        this.sortDirection = 'asc';
      }
    },
  };
}
