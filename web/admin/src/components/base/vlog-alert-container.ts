/**
 * VLog Alert Container Web Component
 *
 * A container for managing multiple toast notifications.
 * Handles stacking, positioning, and programmatic alert creation.
 *
 * @example
 * <vlog-alert-container position="top-right" max-alerts="3"></vlog-alert-container>
 *
 * // Programmatic usage:
 * const container = document.querySelector('vlog-alert-container');
 * container.addAlert({
 *   variant: 'success',
 *   message: 'Video uploaded!',
 *   autoDismiss: 5000
 * });
 */

export interface AlertConfig {
  variant?: 'success' | 'error' | 'warning' | 'info';
  title?: string;
  message: string;
  dismissible?: boolean;
  autoDismiss?: number;
}

const template = document.createElement('template');
template.innerHTML = `
  <style>
    :host {
      display: block;
      position: fixed;
      z-index: var(--vlog-z-tooltip, 70);
      pointer-events: none;
    }

    /* Position variants */
    :host([position="top-right"]) {
      top: var(--vlog-space-4, 1rem);
      right: var(--vlog-space-4, 1rem);
    }

    :host([position="top-left"]) {
      top: var(--vlog-space-4, 1rem);
      left: var(--vlog-space-4, 1rem);
    }

    :host([position="bottom-right"]) {
      bottom: var(--vlog-space-4, 1rem);
      right: var(--vlog-space-4, 1rem);
    }

    :host([position="bottom-left"]) {
      bottom: var(--vlog-space-4, 1rem);
      left: var(--vlog-space-4, 1rem);
    }

    :host([position="top-center"]) {
      top: var(--vlog-space-4, 1rem);
      left: 50%;
      transform: translateX(-50%);
    }

    :host([position="bottom-center"]) {
      bottom: var(--vlog-space-4, 1rem);
      left: 50%;
      transform: translateX(-50%);
    }

    .container {
      display: flex;
      flex-direction: column;
      gap: var(--vlog-space-3, 0.75rem);
      width: 100%;
      max-width: 24rem;
    }

    /* Reverse order for bottom positions */
    :host([position^="bottom"]) .container {
      flex-direction: column-reverse;
    }

    ::slotted(vlog-alert) {
      pointer-events: auto;
    }
  </style>

  <div class="container" part="container" role="region" aria-label="Notifications">
    <slot></slot>
  </div>
`;

export class VlogAlertContainer extends HTMLElement {
  private alertCounter: number = 0;

  static get observedAttributes() {
    return ['position', 'max-alerts'];
  }

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot!.appendChild(template.content.cloneNode(true));
  }

  connectedCallback() {
    // Set default position if not specified
    if (!this.hasAttribute('position')) {
      this.setAttribute('position', 'top-right');
    }

    // Listen for dismiss events from child alerts
    this.addEventListener('dismiss', this.handleDismiss.bind(this));
  }

  private handleDismiss(e: Event) {
    const alert = e.target as HTMLElement;
    if (alert.tagName.toLowerCase() === 'vlog-alert') {
      // Alert will remove itself, no action needed
    }
  }

  private enforceMaxAlerts() {
    const maxAlerts = parseInt(this.getAttribute('max-alerts') || '0', 10);
    if (maxAlerts <= 0) return;

    const alerts = this.querySelectorAll('vlog-alert');
    if (alerts.length > maxAlerts) {
      // Remove oldest alerts (first in DOM)
      const toRemove = alerts.length - maxAlerts;
      for (let i = 0; i < toRemove; i++) {
        const alert = alerts[i];
        if (alert) {
          alert.remove();
        }
      }
    }
  }

  // Public API
  addAlert(config: AlertConfig): string {
    const id = `alert-${++this.alertCounter}`;

    const alert = document.createElement('vlog-alert');
    alert.id = id;
    alert.setAttribute('variant', config.variant || 'info');

    if (config.title) {
      alert.setAttribute('title', config.title);
    }

    if (config.dismissible !== false) {
      alert.setAttribute('dismissible', '');
    }

    if (config.autoDismiss) {
      alert.setAttribute('auto-dismiss', String(config.autoDismiss));
    } else if (config.variant !== 'error') {
      // Default auto-dismiss for non-error alerts
      alert.setAttribute('auto-dismiss', '5000');
    }

    alert.textContent = config.message;

    // Add to container
    this.appendChild(alert);

    // Enforce max alerts
    this.enforceMaxAlerts();

    return id;
  }

  removeAlert(id: string) {
    const alert = this.querySelector(`#${id}`);
    if (alert) {
      alert.remove();
    }
  }

  clearAll() {
    const alerts = this.querySelectorAll('vlog-alert');
    alerts.forEach(alert => alert.remove());
  }

  // Convenience methods for common alert types
  success(message: string, options?: Partial<AlertConfig>): string {
    return this.addAlert({ variant: 'success', message, ...options });
  }

  error(message: string, options?: Partial<AlertConfig>): string {
    return this.addAlert({ variant: 'error', message, autoDismiss: 0, ...options });
  }

  warning(message: string, options?: Partial<AlertConfig>): string {
    return this.addAlert({ variant: 'warning', message, autoDismiss: 8000, ...options });
  }

  info(message: string, options?: Partial<AlertConfig>): string {
    return this.addAlert({ variant: 'info', message, ...options });
  }

  get position(): string {
    return this.getAttribute('position') || 'top-right';
  }

  set position(value: string) {
    this.setAttribute('position', value);
  }

  get maxAlerts(): number {
    return parseInt(this.getAttribute('max-alerts') || '0', 10);
  }

  set maxAlerts(value: number) {
    if (value > 0) {
      this.setAttribute('max-alerts', String(value));
    } else {
      this.removeAttribute('max-alerts');
    }
  }
}

// Register the custom element
customElements.define('vlog-alert-container', VlogAlertContainer);
