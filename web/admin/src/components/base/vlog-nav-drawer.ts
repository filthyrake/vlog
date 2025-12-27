/**
 * VLog Navigation Drawer Web Component
 *
 * A slide-out navigation drawer for mobile devices.
 * Supports backdrop click to close and focus trapping.
 *
 * @example
 * <vlog-nav-drawer id="mobile-nav">
 *   <nav slot="nav">...</nav>
 * </vlog-nav-drawer>
 *
 * @fires open - When the drawer opens
 * @fires close - When the drawer closes
 */

const template = document.createElement('template');
template.innerHTML = `
  <style>
    :host {
      display: contents;
    }

    :host([hidden]) {
      display: none;
    }

    .backdrop {
      position: fixed;
      inset: 0;
      z-index: var(--vlog-z-drawer-backdrop, 40);
      background-color: rgba(0, 0, 0, 0.5);
      opacity: 0;
      visibility: hidden;
      transition: opacity 200ms ease, visibility 200ms ease;
    }

    :host([open]) .backdrop {
      opacity: 1;
      visibility: visible;
    }

    .drawer {
      position: fixed;
      top: 0;
      left: 0;
      z-index: var(--vlog-z-drawer, 50);
      width: 280px;
      max-width: 85vw;
      height: 100vh;
      padding: var(--vlog-space-4, 1rem);
      background-color: var(--vlog-bg-secondary, #0f172a);
      border-right: 1px solid var(--vlog-border-secondary, #334155);
      transform: translateX(-100%);
      transition: transform 200ms ease;
      overflow-y: auto;
    }

    :host([open]) .drawer {
      transform: translateX(0);
    }

    /* Right side variant */
    :host([position="right"]) .drawer {
      left: auto;
      right: 0;
      border-right: none;
      border-left: 1px solid var(--vlog-border-secondary, #334155);
      transform: translateX(100%);
    }

    :host([open][position="right"]) .drawer {
      transform: translateX(0);
    }

    .drawer-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding-bottom: var(--vlog-space-4, 1rem);
      margin-bottom: var(--vlog-space-4, 1rem);
      border-bottom: 1px solid var(--vlog-border-secondary, #334155);
    }

    .drawer-title {
      margin: 0;
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
      font-size: var(--vlog-text-lg, 1.125rem);
      font-weight: var(--vlog-font-semibold, 600);
      color: var(--vlog-text-primary, #f1f5f9);
    }

    .close-button {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 44px;
      height: 44px;
      padding: 0;
      border: none;
      border-radius: var(--vlog-radius-md, 0.375rem);
      background: transparent;
      color: var(--vlog-text-tertiary, #94a3b8);
      cursor: pointer;
      transition: var(--vlog-transition-colors);
    }

    .close-button:hover {
      background-color: var(--vlog-bg-tertiary, #1e293b);
      color: var(--vlog-text-primary, #f1f5f9);
    }

    .close-button:focus-visible {
      outline: none;
      box-shadow: 0 0 0 var(--vlog-focus-ring-width, 2px) var(--vlog-focus-ring-color, rgba(59, 130, 246, 0.5));
    }

    .close-button svg {
      width: 24px;
      height: 24px;
    }

    .drawer-content {
      display: flex;
      flex-direction: column;
      gap: var(--vlog-space-2, 0.5rem);
    }

    /* Hide on desktop by default */
    @media (min-width: 768px) {
      :host(:not([always-visible])) {
        display: none;
      }
    }

    /* Reduced motion */
    @media (prefers-reduced-motion: reduce) {
      .backdrop,
      .drawer {
        transition: none;
      }
    }
  </style>

  <div class="backdrop" part="backdrop"></div>
  <aside class="drawer" part="drawer" role="dialog" aria-modal="true" aria-label="Navigation menu">
    <div class="drawer-header" part="header">
      <h2 class="drawer-title" part="title">
        <slot name="title">Menu</slot>
      </h2>
      <button type="button" class="close-button" part="close" aria-label="Close navigation">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">
          <path fill-rule="evenodd" d="M5.47 5.47a.75.75 0 011.06 0L12 10.94l5.47-5.47a.75.75 0 111.06 1.06L13.06 12l5.47 5.47a.75.75 0 11-1.06 1.06L12 13.06l-5.47 5.47a.75.75 0 01-1.06-1.06L10.94 12 5.47 6.53a.75.75 0 010-1.06z" clip-rule="evenodd" />
        </svg>
      </button>
    </div>
    <div class="drawer-content" part="content">
      <slot></slot>
    </div>
  </aside>
`;

export class VlogNavDrawer extends HTMLElement {
  private backdrop!: HTMLDivElement;
  private drawer!: HTMLElement;
  private closeButton!: HTMLButtonElement;
  private previousActiveElement: Element | null = null;

  static get observedAttributes() {
    return ['open', 'position'];
  }

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot!.appendChild(template.content.cloneNode(true));

    this.backdrop = this.shadowRoot!.querySelector('.backdrop')!;
    this.drawer = this.shadowRoot!.querySelector('.drawer')!;
    this.closeButton = this.shadowRoot!.querySelector('.close-button')!;

    this.handleBackdropClick = this.handleBackdropClick.bind(this);
    this.handleCloseClick = this.handleCloseClick.bind(this);
    this.handleKeyDown = this.handleKeyDown.bind(this);
  }

  connectedCallback() {
    this.backdrop.addEventListener('click', this.handleBackdropClick);
    this.closeButton.addEventListener('click', this.handleCloseClick);
    document.addEventListener('keydown', this.handleKeyDown);
  }

  disconnectedCallback() {
    this.backdrop.removeEventListener('click', this.handleBackdropClick);
    this.closeButton.removeEventListener('click', this.handleCloseClick);
    document.removeEventListener('keydown', this.handleKeyDown);
    // Reset body styles if component is removed while open
    if (this.hasAttribute('open')) {
      document.body.style.overflow = '';
      document.body.style.paddingRight = '';
    }
  }

  attributeChangedCallback(name: string, oldValue: string | null, newValue: string | null) {
    if (oldValue === newValue) return;

    if (name === 'open') {
      if (this.hasAttribute('open')) {
        this.onOpen();
      } else {
        this.onClose();
      }
    }
  }

  private handleBackdropClick() {
    this.close();
  }

  private handleCloseClick() {
    this.close();
  }

  private getActiveElement(): Element | null {
    // Handle focus in shadow DOM - drill into shadow roots
    let active = document.activeElement;
    while (active?.shadowRoot?.activeElement) {
      active = active.shadowRoot.activeElement;
    }
    return active;
  }

  private handleKeyDown(e: KeyboardEvent) {
    if (!this.open) return;

    if (e.key === 'Escape') {
      e.preventDefault();
      this.close();
    }

    // Focus trap
    if (e.key === 'Tab') {
      const focusableElements = this.drawer.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
      const slottedFocusable = this.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
      const allFocusable = [...Array.from(focusableElements), ...Array.from(slottedFocusable)];

      if (allFocusable.length === 0) return;

      const firstFocusable = allFocusable[0];
      const lastFocusable = allFocusable[allFocusable.length - 1];
      const activeElement = this.getActiveElement();

      if (e.shiftKey && activeElement === firstFocusable) {
        e.preventDefault();
        lastFocusable?.focus();
      } else if (!e.shiftKey && activeElement === lastFocusable) {
        e.preventDefault();
        firstFocusable?.focus();
      }
    }
  }

  private getScrollbarWidth(): number {
    return window.innerWidth - document.documentElement.clientWidth;
  }

  private onOpen() {
    this.previousActiveElement = document.activeElement;

    // Compensate for scrollbar width to prevent layout shift
    const scrollbarWidth = this.getScrollbarWidth();
    if (scrollbarWidth > 0) {
      document.body.style.paddingRight = `${scrollbarWidth}px`;
    }
    document.body.style.overflow = 'hidden';
    this.closeButton.focus();

    this.dispatchEvent(
      new CustomEvent('open', {
        bubbles: true,
        composed: true,
      })
    );
  }

  private onClose() {
    document.body.style.overflow = '';
    document.body.style.paddingRight = '';
    if (this.previousActiveElement instanceof HTMLElement) {
      this.previousActiveElement.focus();
    }

    this.dispatchEvent(
      new CustomEvent('close', {
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

  show() {
    this.open = true;
  }

  close() {
    this.open = false;
  }

  toggle(): boolean {
    this.open = !this.open;
    return this.open;
  }
}

// Register the custom element
customElements.define('vlog-nav-drawer', VlogNavDrawer);
