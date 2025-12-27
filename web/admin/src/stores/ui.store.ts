/**
 * UI Store
 * Manages global UI state like active tab, loading states, etc.
 */

import type { AdminTab, SettingsTab } from './types';

export interface UIState {
  // Current active tab
  tab: AdminTab;

  // Settings sub-tab
  settingsTab: SettingsTab;
}

export interface UIActions {
  setTab(tab: AdminTab): void;
  setSettingsTab(tab: SettingsTab): void;
}

export type UIStore = UIState & UIActions;

export function createUIStore(): UIStore {
  return {
    // Initial state
    tab: 'videos',
    settingsTab: 'watermark',

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
  };
}
