/**
 * Keyboard Shortcuts Manager
 *
 * Provides a centralized way to register and manage keyboard shortcuts
 * with support for scopes, modifiers, and platform-aware key display.
 *
 * @example
 * const shortcuts = new KeyboardManager();
 * shortcuts.register({
 *   key: 'k',
 *   ctrl: true,
 *   description: 'Open search',
 *   handler: () => searchInput.focus()
 * });
 */

export interface Shortcut {
  key: string;
  ctrl?: boolean;
  meta?: boolean;
  alt?: boolean;
  shift?: boolean;
  scope?: string;
  description: string;
  handler: (e: KeyboardEvent) => void | boolean;
}

export interface RegisteredShortcut extends Shortcut {
  id: string;
}

/**
 * Detect if running on Mac/iOS platform
 * Uses modern userAgentData API with fallback to deprecated navigator.platform
 */
function detectMac(): boolean {
  if (typeof navigator === 'undefined') return false;

  // Modern API (Chromium browsers)
  const uaData = (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData;
  if (uaData?.platform) {
    return /macOS|iOS/i.test(uaData.platform);
  }

  // Fallback for Safari and older browsers
  // eslint-disable-next-line @typescript-eslint/no-deprecated
  return /Mac|iPod|iPhone|iPad/.test(navigator.platform);
}

const isMac = detectMac();

/**
 * Format a key combination for display
 */
export function formatShortcut(shortcut: Shortcut): string {
  const parts: string[] = [];

  if (shortcut.ctrl) {
    parts.push(isMac ? '⌃' : 'Ctrl');
  }
  if (shortcut.meta) {
    parts.push(isMac ? '⌘' : 'Win');
  }
  if (shortcut.alt) {
    parts.push(isMac ? '⌥' : 'Alt');
  }
  if (shortcut.shift) {
    parts.push(isMac ? '⇧' : 'Shift');
  }

  // Format the key
  let keyDisplay = shortcut.key.toUpperCase();
  const keyMap: Record<string, string> = {
    ESCAPE: 'Esc',
    ARROWUP: '↑',
    ARROWDOWN: '↓',
    ARROWLEFT: '←',
    ARROWRIGHT: '→',
    ENTER: '↵',
    BACKSPACE: '⌫',
    DELETE: 'Del',
    TAB: '⇥',
    SPACE: 'Space',
  };
  const mappedKey = keyMap[keyDisplay];
  if (mappedKey) {
    keyDisplay = mappedKey;
  }

  parts.push(keyDisplay);
  return isMac ? parts.join('') : parts.join('+');
}

/**
 * Check if a keyboard event matches a shortcut
 */
export function matchesShortcut(e: KeyboardEvent, shortcut: Shortcut): boolean {
  // Check modifiers
  if (shortcut.ctrl && !e.ctrlKey) return false;
  if (shortcut.meta && !e.metaKey) return false;
  if (shortcut.alt && !e.altKey) return false;
  if (shortcut.shift && !e.shiftKey) return false;

  // Check that no extra modifiers are pressed
  if (!shortcut.ctrl && e.ctrlKey) return false;
  if (!shortcut.meta && e.metaKey) return false;
  if (!shortcut.alt && e.altKey) return false;
  // Note: We don't check shift as it's often needed for capital letters

  // Check key
  return e.key.toLowerCase() === shortcut.key.toLowerCase();
}

export class KeyboardManager {
  private shortcuts: Map<string, RegisteredShortcut> = new Map();
  private activeScope: string = 'global';
  private shortcutIdCounter = 0;
  private enabled = true;
  private boundHandler: (e: KeyboardEvent) => void;

  constructor() {
    this.boundHandler = this.handleKeyDown.bind(this);
    if (typeof document !== 'undefined') {
      document.addEventListener('keydown', this.boundHandler);
    }
  }

  /**
   * Register a keyboard shortcut
   */
  register(shortcut: Shortcut): string {
    const id = `shortcut-${++this.shortcutIdCounter}`;
    this.shortcuts.set(id, {
      ...shortcut,
      id,
      scope: shortcut.scope || 'global',
    });
    return id;
  }

  /**
   * Unregister a keyboard shortcut by ID
   */
  unregister(id: string): boolean {
    return this.shortcuts.delete(id);
  }

  /**
   * Unregister all shortcuts in a scope
   */
  unregisterScope(scope: string): void {
    for (const [id, shortcut] of this.shortcuts) {
      if (shortcut.scope === scope) {
        this.shortcuts.delete(id);
      }
    }
  }

  /**
   * Set the active scope
   */
  setScope(scope: string): void {
    this.activeScope = scope;
  }

  /**
   * Get the current active scope
   */
  getScope(): string {
    return this.activeScope;
  }

  /**
   * Enable/disable the keyboard manager
   */
  setEnabled(enabled: boolean): void {
    this.enabled = enabled;
  }

  /**
   * Check if the keyboard manager is enabled
   */
  isEnabled(): boolean {
    return this.enabled;
  }

  /**
   * Get all registered shortcuts, optionally filtered by scope
   */
  getShortcuts(scope?: string): RegisteredShortcut[] {
    const shortcuts = Array.from(this.shortcuts.values());
    if (scope) {
      return shortcuts.filter((s) => s.scope === scope || s.scope === 'global');
    }
    return shortcuts;
  }

  /**
   * Handle keydown events
   */
  private handleKeyDown(e: KeyboardEvent): void {
    if (!this.enabled) return;

    // Ignore if typing in an input
    const target = e.target as HTMLElement;
    const isInput =
      target.tagName === 'INPUT' ||
      target.tagName === 'TEXTAREA' ||
      target.tagName === 'SELECT' ||
      target.isContentEditable;

    // For inputs, only handle escape and specific modifier combinations
    if (isInput) {
      const hasModifier = e.ctrlKey || e.metaKey || e.altKey;
      if (!hasModifier && e.key !== 'Escape') return;
    }

    // Find matching shortcuts
    for (const shortcut of this.shortcuts.values()) {
      // Check scope
      if (shortcut.scope !== 'global' && shortcut.scope !== this.activeScope) {
        continue;
      }

      if (matchesShortcut(e, shortcut)) {
        const result = shortcut.handler(e);
        // If handler returns false, continue checking other shortcuts
        if (result !== false) {
          e.preventDefault();
          e.stopPropagation();
          return;
        }
      }
    }
  }

  /**
   * Clean up event listeners
   */
  destroy(): void {
    if (typeof document !== 'undefined') {
      document.removeEventListener('keydown', this.boundHandler);
    }
    this.shortcuts.clear();
  }
}

// Default shortcuts for the admin UI
export const defaultShortcuts: Shortcut[] = [
  {
    key: 'k',
    ctrl: true,
    description: 'Focus search',
    scope: 'global',
    handler: () => {
      const search = document.querySelector('vlog-search');
      if (search) {
        (search as HTMLElement).focus();
        return true;
      }
      return false;
    },
  },
  {
    key: '/',
    description: 'Focus search',
    scope: 'global',
    handler: () => {
      const search = document.querySelector('vlog-search');
      if (search) {
        (search as HTMLElement).focus();
        return true;
      }
      return false;
    },
  },
  {
    key: 'Escape',
    description: 'Close modal',
    scope: 'global',
    handler: () => {
      const modal = document.querySelector('vlog-modal[open]');
      if (modal) {
        modal.removeAttribute('open');
        return true;
      }
      return false;
    },
  },
  {
    key: '?',
    shift: true,
    description: 'Show keyboard shortcuts',
    scope: 'global',
    handler: () => {
      const helpModal = document.querySelector('#shortcuts-help');
      if (helpModal) {
        helpModal.setAttribute('open', '');
        return true;
      }
      return false;
    },
  },
  {
    key: '1',
    alt: true,
    description: 'Go to Videos tab',
    scope: 'global',
    handler: () => {
      const videosTab = document.querySelector('[data-tab="videos"]');
      if (videosTab) {
        (videosTab as HTMLElement).click();
        return true;
      }
      return false;
    },
  },
  {
    key: '2',
    alt: true,
    description: 'Go to Upload tab',
    scope: 'global',
    handler: () => {
      const uploadTab = document.querySelector('[data-tab="upload"]');
      if (uploadTab) {
        (uploadTab as HTMLElement).click();
        return true;
      }
      return false;
    },
  },
  {
    key: '3',
    alt: true,
    description: 'Go to Workers tab',
    scope: 'global',
    handler: () => {
      const workersTab = document.querySelector('[data-tab="workers"]');
      if (workersTab) {
        (workersTab as HTMLElement).click();
        return true;
      }
      return false;
    },
  },
  {
    key: '4',
    alt: true,
    description: 'Go to Analytics tab',
    scope: 'global',
    handler: () => {
      const analyticsTab = document.querySelector('[data-tab="analytics"]');
      if (analyticsTab) {
        (analyticsTab as HTMLElement).click();
        return true;
      }
      return false;
    },
  },
  {
    key: '5',
    alt: true,
    description: 'Go to Settings tab',
    scope: 'global',
    handler: () => {
      const settingsTab = document.querySelector('[data-tab="settings"]');
      if (settingsTab) {
        (settingsTab as HTMLElement).click();
        return true;
      }
      return false;
    },
  },
];

// Singleton instance for the admin app
let keyboardManagerInstance: KeyboardManager | null = null;

export function getKeyboardManager(): KeyboardManager {
  if (!keyboardManagerInstance) {
    keyboardManagerInstance = new KeyboardManager();
    // Register default shortcuts
    for (const shortcut of defaultShortcuts) {
      keyboardManagerInstance.register(shortcut);
    }
  }
  return keyboardManagerInstance;
}

export function destroyKeyboardManager(): void {
  if (keyboardManagerInstance) {
    keyboardManagerInstance.destroy();
    keyboardManagerInstance = null;
  }
}
