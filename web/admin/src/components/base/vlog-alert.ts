/**
 * VLog Alert Web Component
 *
 * A toast notification component for user feedback.
 * Supports multiple variants and auto-dismiss.
 *
 * @example
 * <vlog-alert variant="success" dismissible auto-dismiss="5000">
 *   Video uploaded successfully!
 * </vlog-alert>
 */

// Icons for each variant
const icons = {
  success: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
    <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.857-9.809a.75.75 0 00-1.214-.882l-3.483 4.79-1.88-1.88a.75.75 0 10-1.06 1.061l2.5 2.5a.75.75 0 001.137-.089l4-5.5z" clip-rule="evenodd" />
  </svg>`,
  error: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
    <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.28 7.22a.75.75 0 00-1.06 1.06L8.94 10l-1.72 1.72a.75.75 0 101.06 1.06L10 11.06l1.72 1.72a.75.75 0 101.06-1.06L11.06 10l1.72-1.72a.75.75 0 00-1.06-1.06L10 8.94 8.28 7.22z" clip-rule="evenodd" />
  </svg>`,
  warning: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
    <path fill-rule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z" clip-rule="evenodd" />
  </svg>`,
  info: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
    <path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a.75.75 0 000 1.5h.253a.25.25 0 01.244.304l-.459 2.066A1.75 1.75 0 0010.747 15H11a.75.75 0 000-1.5h-.253a.25.25 0 01-.244-.304l.459-2.066A1.75 1.75 0 009.253 9H9z" clip-rule="evenodd" />
  </svg>`,
};

const template = document.createElement('template');
template.innerHTML = `
  <style>
    :host {
      display: block;
    }

    :host([hidden]) {
      display: none;
    }

    .alert {
      display: flex;
      align-items: flex-start;
      gap: var(--vlog-space-3, 0.75rem);
      padding: var(--vlog-space-4, 1rem);
      border-radius: var(--vlog-radius-lg, 0.5rem);
      border: 1px solid;
      background-color: var(--vlog-bg-secondary, #0f172a);
      box-shadow: var(--vlog-shadow-lg, 0 10px 15px -3px rgb(0 0 0 / 0.1));
      animation: slideIn var(--vlog-transition-base, 200ms ease) forwards;
    }

    @keyframes slideIn {
      from {
        opacity: 0;
        transform: translateX(100%);
      }
      to {
        opacity: 1;
        transform: translateX(0);
      }
    }

    .alert.dismissing {
      animation: slideOut var(--vlog-transition-base, 200ms ease) forwards;
    }

    @keyframes slideOut {
      from {
        opacity: 1;
        transform: translateX(0);
      }
      to {
        opacity: 0;
        transform: translateX(100%);
      }
    }

    /* Variant styles */
    .alert.variant-success {
      border-color: var(--vlog-success, #22c55e);
      background-color: var(--vlog-success-bg, rgba(34, 197, 94, 0.15));
    }

    .alert.variant-success .icon {
      color: var(--vlog-success, #22c55e);
    }

    .alert.variant-error {
      border-color: var(--vlog-error, #ef4444);
      background-color: var(--vlog-error-bg, rgba(239, 68, 68, 0.15));
    }

    .alert.variant-error .icon {
      color: var(--vlog-error, #ef4444);
    }

    .alert.variant-warning {
      border-color: var(--vlog-warning, #eab308);
      background-color: var(--vlog-warning-bg, rgba(234, 179, 8, 0.15));
    }

    .alert.variant-warning .icon {
      color: var(--vlog-warning, #eab308);
    }

    .alert.variant-info {
      border-color: var(--vlog-info, #06b6d4);
      background-color: var(--vlog-info-bg, rgba(6, 182, 212, 0.15));
    }

    .alert.variant-info .icon {
      color: var(--vlog-info, #06b6d4);
    }

    /* Icon */
    .icon {
      flex-shrink: 0;
      width: 1.25rem;
      height: 1.25rem;
    }

    .icon svg {
      width: 100%;
      height: 100%;
    }

    /* Content */
    .content {
      flex: 1;
      min-width: 0;
    }

    .title {
      margin: 0 0 var(--vlog-space-1, 0.25rem) 0;
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
      font-size: var(--vlog-text-sm, 0.875rem);
      font-weight: var(--vlog-font-semibold, 600);
      color: var(--vlog-text-primary, #f1f5f9);
    }

    .title:empty {
      display: none;
    }

    .message {
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
      font-size: var(--vlog-text-sm, 0.875rem);
      color: var(--vlog-text-secondary, #cbd5e1);
      line-height: var(--vlog-leading-relaxed, 1.625);
    }

    .actions {
      display: flex;
      align-items: center;
      gap: var(--vlog-space-2, 0.5rem);
      margin-top: var(--vlog-space-3, 0.75rem);
    }

    .actions:empty {
      display: none;
    }

    /* Close button */
    .close-button {
      flex-shrink: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      width: 1.5rem;
      height: 1.5rem;
      padding: 0;
      border: none;
      background: transparent;
      color: var(--vlog-text-tertiary, #94a3b8);
      cursor: pointer;
      border-radius: var(--vlog-radius-md, 0.375rem);
      transition: var(--vlog-transition-colors);
    }

    .close-button:hover {
      background-color: var(--vlog-bg-tertiary, #1e293b);
      color: var(--vlog-text-primary, #f1f5f9);
    }

    .close-button:focus-visible {
      outline: none;
      box-shadow:
        0 0 0 var(--vlog-focus-ring-offset, 2px) var(--vlog-bg-secondary, #0f172a),
        0 0 0 calc(var(--vlog-focus-ring-offset, 2px) + var(--vlog-focus-ring-width, 2px)) var(--vlog-focus-ring-color, #3b82f6);
    }

    .close-button svg {
      width: 1rem;
      height: 1rem;
    }

    .close-button:not(.show) {
      display: none;
    }

    /* Reduced motion */
    @media (prefers-reduced-motion: reduce) {
      .alert {
        animation: none;
      }

      .alert.dismissing {
        animation: none;
        opacity: 0;
      }
    }
  </style>

  <div class="alert" part="alert">
    <div class="icon" part="icon" aria-hidden="true">
      <slot name="icon"></slot>
    </div>
    <div class="content" part="content">
      <h4 class="title" part="title"></h4>
      <div class="message" part="message">
        <slot></slot>
      </div>
      <div class="actions" part="actions">
        <slot name="actions"></slot>
      </div>
    </div>
    <button class="close-button" type="button" aria-label="Close notification" part="close">
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
        <path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z" />
      </svg>
    </button>
  </div>
`;

export class VlogAlert extends HTMLElement {
  private alert!: HTMLDivElement;
  private iconContainer!: HTMLDivElement;
  private titleElement!: HTMLHeadingElement;
  private closeButton!: HTMLButtonElement;
  private dismissTimeout: number | null = null;

  static get observedAttributes() {
    return ['variant', 'title', 'dismissible', 'auto-dismiss'];
  }

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot!.appendChild(template.content.cloneNode(true));

    this.alert = this.shadowRoot!.querySelector('.alert')!;
    this.iconContainer = this.shadowRoot!.querySelector('.icon')!;
    this.titleElement = this.shadowRoot!.querySelector('.title')!;
    this.closeButton = this.shadowRoot!.querySelector('.close-button')!;

    this.handleClose = this.handleClose.bind(this);
    this.handleKeyDown = this.handleKeyDown.bind(this);
  }

  connectedCallback() {
    this.updateStyles();
    this.setupListeners();
    this.setupAutoDismiss();
  }

  disconnectedCallback() {
    this.clearAutoDismiss();
    this.closeButton.removeEventListener('click', this.handleClose);
    document.removeEventListener('keydown', this.handleKeyDown);
  }

  attributeChangedCallback(name: string, oldValue: string | null, newValue: string | null) {
    if (oldValue === newValue) return;

    if (name === 'auto-dismiss') {
      this.setupAutoDismiss();
    } else {
      this.updateStyles();
    }
  }

  private updateStyles() {
    const variant = this.getAttribute('variant') || 'info';
    const title = this.getAttribute('title');
    const dismissible = this.hasAttribute('dismissible');

    // Update alert class
    this.alert.className = `alert variant-${variant}`;

    // Set ARIA role based on variant
    if (variant === 'error') {
      this.alert.setAttribute('role', 'alert');
      this.alert.setAttribute('aria-live', 'assertive');
    } else {
      this.alert.setAttribute('role', 'status');
      this.alert.setAttribute('aria-live', 'polite');
    }

    this.alert.setAttribute('aria-atomic', 'true');

    // Update icon
    const hasSlottedIcon = this.querySelector('[slot="icon"]') !== null;
    if (!hasSlottedIcon && icons[variant as keyof typeof icons]) {
      this.iconContainer.innerHTML = icons[variant as keyof typeof icons];
    }

    // Update title
    this.titleElement.textContent = title || '';

    // Update close button visibility
    this.closeButton.classList.toggle('show', dismissible);
  }

  private setupListeners() {
    this.closeButton.addEventListener('click', this.handleClose);

    if (this.hasAttribute('dismissible')) {
      document.addEventListener('keydown', this.handleKeyDown);
    }
  }

  private setupAutoDismiss() {
    this.clearAutoDismiss();

    const autoDismiss = this.getAttribute('auto-dismiss');
    if (autoDismiss) {
      const delay = parseInt(autoDismiss, 10);
      if (delay > 0) {
        // Ensure minimum 5 seconds for accessibility
        const safeDelay = Math.max(delay, 5000);
        this.dismissTimeout = window.setTimeout(() => {
          this.dismiss(false);
        }, safeDelay);
      }
    }
  }

  private clearAutoDismiss() {
    if (this.dismissTimeout !== null) {
      clearTimeout(this.dismissTimeout);
      this.dismissTimeout = null;
    }
  }

  private handleClose() {
    this.dismiss(true);
  }

  private handleKeyDown(e: KeyboardEvent) {
    if (e.key === 'Escape' && this.hasAttribute('dismissible')) {
      this.dismiss(true);
    }
  }

  private dismiss(manual: boolean) {
    this.clearAutoDismiss();

    // Add dismissing animation
    this.alert.classList.add('dismissing');

    // Wait for animation then remove
    const handleAnimationEnd = () => {
      this.dispatchEvent(new CustomEvent('dismiss', {
        detail: { manual },
        bubbles: true,
        composed: true
      }));

      // Remove from DOM if not handled by container
      if (this.parentElement) {
        this.remove();
      }
    };

    // Use animation end or fallback timeout
    this.alert.addEventListener('animationend', handleAnimationEnd, { once: true });
    setTimeout(handleAnimationEnd, 250); // Fallback for reduced motion
  }

  // Public API
  get variant(): string {
    return this.getAttribute('variant') || 'info';
  }

  set variant(value: string) {
    this.setAttribute('variant', value);
  }

  get title(): string {
    return this.getAttribute('title') || '';
  }

  set title(value: string) {
    if (value) {
      this.setAttribute('title', value);
    } else {
      this.removeAttribute('title');
    }
  }

  get dismissible(): boolean {
    return this.hasAttribute('dismissible');
  }

  set dismissible(value: boolean) {
    if (value) {
      this.setAttribute('dismissible', '');
    } else {
      this.removeAttribute('dismissible');
    }
  }

  get autoDismiss(): number {
    return parseInt(this.getAttribute('auto-dismiss') || '0', 10);
  }

  set autoDismiss(value: number) {
    if (value > 0) {
      this.setAttribute('auto-dismiss', String(value));
    } else {
      this.removeAttribute('auto-dismiss');
    }
  }

  show() {
    this.removeAttribute('hidden');
    this.setupAutoDismiss();
    this.dispatchEvent(new CustomEvent('show', {
      bubbles: true,
      composed: true
    }));
  }

  hide() {
    this.dismiss(true);
  }
}

// Register the custom element
customElements.define('vlog-alert', VlogAlert);
