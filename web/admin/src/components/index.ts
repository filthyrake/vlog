/**
 * VLog Admin Component Library
 *
 * Import this file to register all custom elements.
 */

// Phase 1 - Base components
export { VlogButton } from './base/vlog-button';
export { VlogBadge } from './base/vlog-badge';
export { VlogModal } from './base/vlog-modal';

// Phase 2A - Foundation components
export { VlogInput } from './base/vlog-input';
export { VlogCard } from './base/vlog-card';
export { VlogProgress } from './base/vlog-progress';

// Phase 2B - Data display components
export { VlogTable } from './base/vlog-table';
export type { TableColumn, TableRow } from './base/vlog-table';
export { VlogEmptyState } from './base/vlog-empty-state';
export { VlogTabs } from './base/vlog-tabs';
export { VlogTabButton } from './base/vlog-tab-button';
export { VlogTabPanel } from './base/vlog-tab-panel';

// Phase 2C - Feedback components
export { VlogAlert } from './base/vlog-alert';
export { VlogAlertContainer } from './base/vlog-alert-container';
export type { AlertConfig } from './base/vlog-alert-container';

// Type exports for TypeScript consumers
export type { VlogButton as VlogButtonElement } from './base/vlog-button';
export type { VlogBadge as VlogBadgeElement } from './base/vlog-badge';
export type { VlogModal as VlogModalElement } from './base/vlog-modal';
export type { VlogInput as VlogInputElement } from './base/vlog-input';
export type { VlogCard as VlogCardElement } from './base/vlog-card';
export type { VlogProgress as VlogProgressElement } from './base/vlog-progress';
export type { VlogTable as VlogTableElement } from './base/vlog-table';
export type { VlogEmptyState as VlogEmptyStateElement } from './base/vlog-empty-state';
export type { VlogTabs as VlogTabsElement } from './base/vlog-tabs';
export type { VlogTabButton as VlogTabButtonElement } from './base/vlog-tab-button';
export type { VlogTabPanel as VlogTabPanelElement } from './base/vlog-tab-panel';
export type { VlogAlert as VlogAlertElement } from './base/vlog-alert';
export type { VlogAlertContainer as VlogAlertContainerElement } from './base/vlog-alert-container';
