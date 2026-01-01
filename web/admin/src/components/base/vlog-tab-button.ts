/**
 * VLog Tab Button Web Component
 *
 * A tab button component used within vlog-tabs.
 * Handles its own styling and active state.
 *
 * @example
 * <vlog-tab-button tab-id="videos">Videos</vlog-tab-button>
 */

const template = document.createElement('template');
template.innerHTML = `
  <style>
    :host {
      display: inline-flex;
    }

    :host([hidden]) {
      display: none;
    }

    .tab-button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: var(--vlog-space-2, 0.5rem);
      padding: var(--vlog-space-2, 0.5rem) var(--vlog-space-4, 1rem);
      border: none;
      background: transparent;
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
      font-size: var(--vlog-text-sm, 0.875rem);
      font-weight: var(--vlog-font-medium, 500);
      color: var(--vlog-text-tertiary, #94a3b8);
      cursor: pointer;
      border-radius: var(--vlog-radius-md, 0.375rem);
      transition: var(--vlog-transition-colors);
      white-space: nowrap;
      position: relative;
    }

    .tab-button:hover {
      color: var(--vlog-text-primary, #f1f5f9);
      background-color: var(--vlog-bg-tertiary, #1e293b);
    }

    .tab-button:focus-visible {
      outline: none;
      box-shadow:
        0 0 0 var(--vlog-focus-ring-offset, 2px) var(--vlog-bg-primary, #020617),
        0 0 0 calc(var(--vlog-focus-ring-offset, 2px) + var(--vlog-focus-ring-width, 2px)) var(--vlog-focus-ring-color, #3b82f6);
    }

    /* Active state */
    :host([active]) .tab-button {
      color: var(--vlog-primary-text, #93c5fd);
    }

    /* Default variant - underline indicator */
    :host([active]) .tab-button::after {
      content: '';
      position: absolute;
      bottom: calc(-1 * var(--vlog-space-1, 0.25rem) - 1px);
      left: 0;
      right: 0;
      height: 2px;
      background-color: var(--vlog-primary, #3b82f6);
      border-radius: var(--vlog-radius-full, 9999px);
    }

    /* Pills variant */
    :host-context(vlog-tabs[variant="pills"]) .tab-button {
      border-radius: var(--vlog-radius-md, 0.375rem);
    }

    :host-context(vlog-tabs[variant="pills"]) .tab-button::after {
      display: none;
    }

    :host-context(vlog-tabs[variant="pills"][active]) .tab-button,
    :host([active]):host-context(vlog-tabs[variant="pills"]) .tab-button {
      background-color: var(--vlog-bg-secondary, #0f172a);
      color: var(--vlog-text-primary, #f1f5f9);
    }

    /* Underline variant */
    :host-context(vlog-tabs[variant="underline"]) .tab-button {
      border-radius: 0;
      padding-left: 0;
      padding-right: 0;
    }

    :host-context(vlog-tabs[variant="underline"]) .tab-button:hover {
      background-color: transparent;
    }

    :host-context(vlog-tabs[variant="underline"][active]) .tab-button::after,
    :host([active]):host-context(vlog-tabs[variant="underline"]) .tab-button::after {
      bottom: calc(-1 * var(--vlog-space-1, 0.25rem));
    }

    /* Disabled state */
    :host([disabled]) .tab-button {
      opacity: 0.5;
      cursor: not-allowed;
      pointer-events: none;
    }

    /* Icon slot */
    ::slotted(svg) {
      width: 1rem;
      height: 1rem;
      flex-shrink: 0;
    }

    /* Badge slot */
    ::slotted([slot="badge"]) {
      margin-left: var(--vlog-space-1, 0.25rem);
    }
  </style>

  <button class="tab-button" type="button" part="button">
    <slot name="icon"></slot>
    <slot></slot>
    <slot name="badge"></slot>
  </button>
`;

export class VlogTabButton extends HTMLElement {
  private button!: HTMLButtonElement;

  static get observedAttributes() {
    return ['tab-id', 'active', 'disabled'];
  }

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot!.appendChild(template.content.cloneNode(true));

    this.button = this.shadowRoot!.querySelector('.tab-button')!;
  }

  connectedCallback() {
    this.updateState();
  }

  attributeChangedCallback(_name: string, oldValue: string | null, newValue: string | null) {
    if (oldValue === newValue) return;
    this.updateState();
  }

  private updateState() {
    const isActive = this.hasAttribute('active');
    const isDisabled = this.hasAttribute('disabled');

    this.button.disabled = isDisabled;
    this.button.setAttribute('aria-selected', String(isActive));
  }

  // Public API
  get tabId(): string {
    return this.getAttribute('tab-id') || '';
  }

  set tabId(value: string) {
    this.setAttribute('tab-id', value);
  }

  get active(): boolean {
    return this.hasAttribute('active');
  }

  set active(value: boolean) {
    if (value) {
      this.setAttribute('active', '');
    } else {
      this.removeAttribute('active');
    }
  }

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

  focus() {
    this.button.focus();
  }

  blur() {
    this.button.blur();
  }
}

// Register the custom element
customElements.define('vlog-tab-button', VlogTabButton);
