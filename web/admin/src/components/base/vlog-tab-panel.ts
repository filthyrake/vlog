/**
 * VLog Tab Panel Web Component
 *
 * A tab panel component used within vlog-tabs.
 * Handles visibility based on active state.
 *
 * @example
 * <vlog-tab-panel tab-id="videos">
 *   <p>Videos content goes here</p>
 * </vlog-tab-panel>
 */

const template = document.createElement('template');
template.innerHTML = `
  <style>
    :host {
      display: block;
    }

    :host([hidden]) {
      display: none !important;
    }

    .panel {
      outline: none;
    }

    .panel:focus-visible {
      box-shadow:
        inset 0 0 0 var(--vlog-focus-ring-width, 2px) var(--vlog-focus-ring-color, #3b82f6);
      border-radius: var(--vlog-radius-md, 0.375rem);
    }
  </style>

  <div class="panel" part="panel">
    <slot></slot>
  </div>
`;

export class VlogTabPanel extends HTMLElement {
  private panel!: HTMLDivElement;

  static get observedAttributes() {
    return ['tab-id', 'active'];
  }

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot!.appendChild(template.content.cloneNode(true));

    this.panel = this.shadowRoot!.querySelector('.panel')!;
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

    // Set tabindex for keyboard navigation into panel
    this.panel.tabIndex = isActive ? 0 : -1;
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
      this.removeAttribute('hidden');
    } else {
      this.removeAttribute('active');
      this.setAttribute('hidden', '');
    }
  }

  focus() {
    this.panel.focus();
  }
}

// Register the custom element
customElements.define('vlog-tab-panel', VlogTabPanel);
