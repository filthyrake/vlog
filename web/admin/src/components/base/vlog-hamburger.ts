/**
 * VLog Hamburger Menu Button Web Component
 *
 * An animated hamburger menu button for mobile navigation.
 * Toggles between hamburger and X states with smooth animation.
 *
 * @example
 * <vlog-hamburger aria-controls="mobile-nav" @click="toggleNav()"></vlog-hamburger>
 *
 * @fires toggle - When the button is clicked
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

    .hamburger {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 44px;
      height: 44px;
      padding: 0;
      border: none;
      border-radius: var(--vlog-radius-md, 0.375rem);
      background: transparent;
      cursor: pointer;
      transition: var(--vlog-transition-colors);
    }

    .hamburger:hover {
      background-color: var(--vlog-bg-tertiary, #1e293b);
    }

    .hamburger:focus-visible {
      outline: none;
      box-shadow: 0 0 0 var(--vlog-focus-ring-width, 2px) var(--vlog-focus-ring-color, rgba(59, 130, 246, 0.5));
    }

    .hamburger-lines {
      position: relative;
      width: 24px;
      height: 18px;
    }

    .hamburger-line {
      position: absolute;
      left: 0;
      width: 100%;
      height: 2px;
      background-color: var(--vlog-text-primary, #f1f5f9);
      border-radius: 1px;
      transition: transform 200ms ease, opacity 200ms ease;
    }

    .hamburger-line:nth-child(1) {
      top: 0;
    }

    .hamburger-line:nth-child(2) {
      top: 50%;
      transform: translateY(-50%);
    }

    .hamburger-line:nth-child(3) {
      bottom: 0;
    }

    /* Open state - X shape */
    :host([open]) .hamburger-line:nth-child(1) {
      transform: translateY(8px) rotate(45deg);
    }

    :host([open]) .hamburger-line:nth-child(2) {
      opacity: 0;
    }

    :host([open]) .hamburger-line:nth-child(3) {
      transform: translateY(-8px) rotate(-45deg);
    }

    /* Hide on desktop */
    @media (min-width: 768px) {
      :host(:not([always-visible])) {
        display: none;
      }
    }
  </style>

  <button
    type="button"
    class="hamburger"
    part="button"
    aria-label="Toggle navigation menu"
    aria-expanded="false"
  >
    <div class="hamburger-lines" part="lines">
      <span class="hamburger-line"></span>
      <span class="hamburger-line"></span>
      <span class="hamburger-line"></span>
    </div>
  </button>
`;

export class VlogHamburger extends HTMLElement {
  private button!: HTMLButtonElement;

  static get observedAttributes() {
    return ['open', 'aria-controls'];
  }

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot!.appendChild(template.content.cloneNode(true));
    this.button = this.shadowRoot!.querySelector('.hamburger')!;

    this.handleClick = this.handleClick.bind(this);
  }

  connectedCallback() {
    this.button.addEventListener('click', this.handleClick);
    this.updateAriaState();
  }

  disconnectedCallback() {
    this.button.removeEventListener('click', this.handleClick);
  }

  attributeChangedCallback(name: string, oldValue: string | null, newValue: string | null) {
    if (oldValue === newValue) return;

    if (name === 'open') {
      this.updateAriaState();
    } else if (name === 'aria-controls') {
      this.button.setAttribute('aria-controls', newValue || '');
    }
  }

  private updateAriaState() {
    const isOpen = this.hasAttribute('open');
    this.button.setAttribute('aria-expanded', String(isOpen));
    this.button.setAttribute('aria-label', isOpen ? 'Close navigation menu' : 'Open navigation menu');
  }

  private handleClick() {
    this.toggle();
    this.dispatchEvent(
      new CustomEvent('toggle', {
        detail: { open: this.open },
        bubbles: true,
        composed: true,
      })
    );
  }

  // Public API
  get open(): boolean {
    return this.hasAttribute('open');
  }

  set open(value: boolean) {
    if (value) {
      this.setAttribute('open', '');
    } else {
      this.removeAttribute('open');
    }
  }

  toggle(): boolean {
    this.open = !this.open;
    return this.open;
  }
}

// Register the custom element
customElements.define('vlog-hamburger', VlogHamburger);
