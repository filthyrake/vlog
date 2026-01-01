/**
 * VLog Button Web Component
 *
 * A customizable button component with variants, sizes, and states.
 * Supports icons, loading state, and full accessibility.
 *
 * @example
 * <vlog-button variant="primary" size="md">Save</vlog-button>
 * <vlog-button variant="danger" loading>Deleting...</vlog-button>
 * <vlog-button variant="ghost" disabled>Disabled</vlog-button>
 */

const template = document.createElement('template');
template.innerHTML = `
  <style>
    :host {
      display: inline-flex;
    }

    :host([full-width]) {
      display: flex;
      width: 100%;
    }

    :host([hidden]) {
      display: none;
    }

    button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: var(--vlog-space-2, 0.5rem);
      border: none;
      border-radius: var(--vlog-radius-lg, 0.5rem);
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
      font-weight: var(--vlog-font-medium, 500);
      cursor: pointer;
      transition: var(--vlog-transition-colors, color 200ms ease, background-color 200ms ease);
      white-space: nowrap;
      width: 100%;
    }

    button:focus-visible {
      outline: none;
      box-shadow:
        0 0 0 var(--vlog-focus-ring-offset, 2px) var(--vlog-bg-primary, #020617),
        0 0 0 calc(var(--vlog-focus-ring-offset, 2px) + var(--vlog-focus-ring-width, 2px)) var(--vlog-focus-ring-color, #3b82f6);
    }

    button:disabled {
      cursor: not-allowed;
      opacity: 0.5;
    }

    /* Size variants */
    button.size-sm {
      height: var(--vlog-button-height-sm, 2rem);
      padding: var(--vlog-button-padding-y-sm, 0.25rem) var(--vlog-button-padding-x-sm, 0.75rem);
      font-size: var(--vlog-text-sm, 0.875rem);
    }

    button.size-md {
      height: var(--vlog-button-height-md, 2.5rem);
      padding: var(--vlog-button-padding-y, 0.5rem) var(--vlog-button-padding-x, 1rem);
      font-size: var(--vlog-text-sm, 0.875rem);
    }

    button.size-lg {
      height: var(--vlog-button-height-lg, 2.75rem);
      padding: var(--vlog-button-padding-y-lg, 0.75rem) var(--vlog-button-padding-x-lg, 1.5rem);
      font-size: var(--vlog-text-base, 1rem);
    }

    /* Primary variant */
    button.variant-primary {
      background-color: var(--vlog-primary, #3b82f6);
      color: white;
    }

    button.variant-primary:hover:not(:disabled) {
      background-color: var(--vlog-primary-hover, #2563eb);
    }

    button.variant-primary:active:not(:disabled) {
      background-color: var(--vlog-primary-active, #1d4ed8);
    }

    /* Secondary variant */
    button.variant-secondary {
      background-color: var(--vlog-bg-tertiary, #1e293b);
      color: var(--vlog-text-primary, #f1f5f9);
      border: 1px solid var(--vlog-border-primary, #334155);
    }

    button.variant-secondary:hover:not(:disabled) {
      background-color: var(--vlog-bg-elevated, #334155);
    }

    /* Danger variant */
    button.variant-danger {
      background-color: var(--vlog-error, #ef4444);
      color: white;
    }

    button.variant-danger:hover:not(:disabled) {
      background-color: var(--vlog-error-hover, #dc2626);
    }

    /* Success variant */
    button.variant-success {
      background-color: var(--vlog-success, #22c55e);
      color: white;
    }

    button.variant-success:hover:not(:disabled) {
      background-color: var(--vlog-success-hover, #16a34a);
    }

    /* Info variant (cyan) */
    button.variant-info {
      background-color: var(--vlog-info, #06b6d4);
      color: white;
    }

    button.variant-info:hover:not(:disabled) {
      background-color: var(--vlog-info-hover, #0891b2);
    }

    /* Warning variant (purple for special actions) */
    button.variant-warning {
      background-color: var(--vlog-warning, #a855f7);
      color: white;
    }

    button.variant-warning:hover:not(:disabled) {
      background-color: var(--vlog-warning-hover, #9333ea);
    }

    /* Ghost variant */
    button.variant-ghost {
      background-color: transparent;
      color: var(--vlog-text-secondary, #cbd5e1);
      border: 1px solid var(--vlog-border-primary, #334155);
    }

    button.variant-ghost:hover:not(:disabled) {
      background-color: var(--vlog-bg-tertiary, #1e293b);
      color: var(--vlog-text-primary, #f1f5f9);
    }

    /* Ghost-danger variant (for delete buttons) */
    button.variant-ghost-danger {
      background-color: transparent;
      color: var(--vlog-error-text, #fca5a5);
      border: 1px solid transparent;
    }

    button.variant-ghost-danger:hover:not(:disabled) {
      background-color: var(--vlog-error-bg, rgba(239, 68, 68, 0.15));
      color: var(--vlog-error, #ef4444);
      border-color: var(--vlog-error, #ef4444);
    }

    /* Text variant (link-style) */
    button.variant-text {
      background-color: transparent;
      color: var(--vlog-primary-text, #93c5fd);
      padding-left: var(--vlog-space-2, 0.5rem);
      padding-right: var(--vlog-space-2, 0.5rem);
    }

    button.variant-text:hover:not(:disabled) {
      color: var(--vlog-text-primary, #f1f5f9);
      text-decoration: underline;
    }

    /* Icon-only variant */
    button.variant-icon {
      background-color: transparent;
      color: var(--vlog-text-tertiary, #94a3b8);
      padding: var(--vlog-space-2, 0.5rem);
      border-radius: var(--vlog-radius-md, 0.375rem);
    }

    button.variant-icon:hover:not(:disabled) {
      background-color: var(--vlog-bg-tertiary, #1e293b);
      color: var(--vlog-text-primary, #f1f5f9);
    }

    button.size-sm.variant-icon {
      width: var(--vlog-button-height-sm, 2rem);
      padding: 0;
    }

    button.size-md.variant-icon {
      width: var(--vlog-button-height-md, 2.5rem);
      padding: 0;
    }

    button.size-lg.variant-icon {
      width: var(--vlog-button-height-lg, 2.75rem);
      padding: 0;
    }

    /* Loading state */
    button.loading {
      position: relative;
      color: transparent !important;
    }

    button.loading::after {
      content: '';
      position: absolute;
      width: 1em;
      height: 1em;
      border: 2px solid currentColor;
      border-right-color: transparent;
      border-radius: 50%;
      animation: spin 0.75s linear infinite;
    }

    button.variant-primary.loading::after,
    button.variant-danger.loading::after,
    button.variant-success.loading::after,
    button.variant-info.loading::after,
    button.variant-warning.loading::after {
      border-color: white;
      border-right-color: transparent;
    }

    button.variant-secondary.loading::after,
    button.variant-ghost.loading::after,
    button.variant-ghost-danger.loading::after,
    button.variant-text.loading::after {
      border-color: var(--vlog-text-secondary, #cbd5e1);
      border-right-color: transparent;
    }

    @keyframes spin {
      to {
        transform: rotate(360deg);
      }
    }

    /* Slots */
    ::slotted(svg) {
      width: 1em;
      height: 1em;
      flex-shrink: 0;
    }

    .icon-left ::slotted(svg),
    .icon-left slot[name="icon-left"]::slotted(svg) {
      margin-right: var(--vlog-space-1, 0.25rem);
    }

    .icon-right ::slotted(svg),
    .icon-right slot[name="icon-right"]::slotted(svg) {
      margin-left: var(--vlog-space-1, 0.25rem);
    }
  </style>
  <button part="button">
    <slot name="icon-left"></slot>
    <slot></slot>
    <slot name="icon-right"></slot>
  </button>
`;

export class VlogButton extends HTMLElement {
  private button: HTMLButtonElement;

  static get observedAttributes() {
    return ['variant', 'size', 'disabled', 'loading', 'type', 'full-width'];
  }

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot!.appendChild(template.content.cloneNode(true));
    this.button = this.shadowRoot!.querySelector('button')!;
  }

  connectedCallback() {
    this.updateClasses();
    this.updateAttributes();

    // Handle form submission for type="submit" buttons
    // Shadow DOM buttons don't automatically trigger parent form submission
    this.button.addEventListener('click', this.handleClick.bind(this));
  }

  disconnectedCallback() {
    this.button.removeEventListener('click', this.handleClick.bind(this));
  }

  private handleClick(event: Event) {
    const type = this.getAttribute('type');
    if (type === 'submit') {
      // Find the parent form and submit it
      const form = this.closest('form');
      if (form) {
        // Use requestSubmit to trigger submit event handlers (like @submit.prevent)
        event.preventDefault();
        form.requestSubmit();
      }
    }
  }

  attributeChangedCallback(_name: string, _oldValue: string | null, _newValue: string | null) {
    this.updateClasses();
    this.updateAttributes();
  }

  private updateClasses() {
    const variant = this.getAttribute('variant') || 'primary';
    const size = this.getAttribute('size') || 'md';
    const loading = this.hasAttribute('loading');

    // Clear existing variant/size classes
    this.button.className = '';

    // Add new classes
    this.button.classList.add(`variant-${variant}`);
    this.button.classList.add(`size-${size}`);

    if (loading) {
      this.button.classList.add('loading');
    }
  }

  private updateAttributes() {
    const disabled = this.hasAttribute('disabled') || this.hasAttribute('loading');
    const type = this.getAttribute('type') || 'button';

    this.button.disabled = disabled;
    this.button.type = type as 'button' | 'submit' | 'reset';

    // Update ARIA attributes
    if (this.hasAttribute('loading')) {
      this.button.setAttribute('aria-busy', 'true');
    } else {
      this.button.removeAttribute('aria-busy');
    }
  }

  // Expose commonly used properties
  get disabled(): boolean {
    return this.hasAttribute('disabled');
  }

  set disabled(value: boolean) {
    if (value) {
      this.setAttribute('disabled', '');
    } else {
      this.removeAttribute('disabled');
    }
  }

  get loading(): boolean {
    return this.hasAttribute('loading');
  }

  set loading(value: boolean) {
    if (value) {
      this.setAttribute('loading', '');
    } else {
      this.removeAttribute('loading');
    }
  }

  // Forward click events
  click() {
    this.button.click();
  }

  focus() {
    this.button.focus();
  }

  blur() {
    this.button.blur();
  }
}

// Register the custom element
customElements.define('vlog-button', VlogButton);
