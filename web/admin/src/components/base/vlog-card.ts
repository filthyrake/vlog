/**
 * VLog Card Web Component
 *
 * A content container with optional header, body, and footer sections.
 * Supports multiple variants and interactive states.
 *
 * @example
 * <vlog-card variant="elevated">
 *   <h3 slot="header">Card Title</h3>
 *   <p>Card content goes here</p>
 *   <div slot="footer"><vlog-button>Action</vlog-button></div>
 * </vlog-card>
 */

const template = document.createElement('template');
template.innerHTML = `
  <style>
    :host {
      display: block;
    }

    :host([hidden]) {
      display: none;
    }

    .card {
      display: flex;
      flex-direction: column;
      background-color: var(--vlog-bg-secondary, #0f172a);
      border: 1px solid var(--vlog-border-primary, #334155);
      border-radius: var(--vlog-radius-xl, 0.75rem);
      overflow: hidden;
      transition: var(--vlog-transition-all);
    }

    /* Variant: elevated */
    .card.variant-elevated {
      border: none;
      box-shadow: var(--vlog-shadow-lg, 0 10px 15px -3px rgb(0 0 0 / 0.1));
    }

    /* Variant: outlined */
    .card.variant-outlined {
      background-color: var(--vlog-bg-primary, #020617);
      border: 1px solid var(--vlog-border-primary, #334155);
    }

    /* Variant: ghost */
    .card.variant-ghost {
      background-color: transparent;
      border: 1px solid var(--vlog-border-secondary, #1e293b);
    }

    /* Clickable state */
    .card.clickable {
      cursor: pointer;
    }

    .card.clickable:hover {
      border-color: var(--vlog-border-focus, #3b82f6);
      transform: translateY(-1px);
    }

    .card.clickable:focus-visible {
      outline: none;
      box-shadow:
        0 0 0 var(--vlog-focus-ring-offset, 2px) var(--vlog-bg-primary, #020617),
        0 0 0 calc(var(--vlog-focus-ring-offset, 2px) + var(--vlog-focus-ring-width, 2px)) var(--vlog-focus-ring-color, #3b82f6);
    }

    .card.clickable:active {
      transform: translateY(0);
    }

    .card.variant-elevated.clickable:hover {
      box-shadow: var(--vlog-shadow-xl, 0 20px 25px -5px rgb(0 0 0 / 0.1));
    }

    /* Loading state */
    .card.loading {
      position: relative;
      pointer-events: none;
    }

    .card.loading::after {
      content: '';
      position: absolute;
      inset: 0;
      background: linear-gradient(
        90deg,
        transparent,
        rgba(255, 255, 255, 0.05),
        transparent
      );
      animation: shimmer 1.5s infinite;
    }

    @keyframes shimmer {
      0% {
        transform: translateX(-100%);
      }
      100% {
        transform: translateX(100%);
      }
    }

    /* Header section */
    .card-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--vlog-space-4, 1rem);
      border-bottom: 1px solid var(--vlog-border-primary, #334155);
    }

    .card-header.padding-none {
      padding: 0;
    }

    .card-header.padding-sm {
      padding: var(--vlog-space-3, 0.75rem) var(--vlog-space-4, 1rem);
    }

    .card-header.padding-md {
      padding: var(--vlog-space-4, 1rem) var(--vlog-space-6, 1.5rem);
    }

    .card-header.padding-lg {
      padding: var(--vlog-space-6, 1.5rem) var(--vlog-space-8, 2rem);
    }

    .card-header:not(:has(slot[name="header"]::slotted(*))) {
      display: none;
    }

    .header-content {
      flex: 1;
      min-width: 0;
    }

    .header-content ::slotted(*) {
      margin: 0;
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
      font-size: var(--vlog-text-lg, 1.125rem);
      font-weight: var(--vlog-font-semibold, 600);
      color: var(--vlog-text-primary, #f1f5f9);
    }

    .header-actions {
      display: flex;
      align-items: center;
      gap: var(--vlog-space-2, 0.5rem);
      flex-shrink: 0;
    }

    .header-actions:not(:has(slot[name="actions"]::slotted(*))) {
      display: none;
    }

    /* Body section */
    .card-body {
      flex: 1;
    }

    .card-body.padding-none {
      padding: 0;
    }

    .card-body.padding-sm {
      padding: var(--vlog-space-3, 0.75rem) var(--vlog-space-4, 1rem);
    }

    .card-body.padding-md {
      padding: var(--vlog-space-4, 1rem) var(--vlog-space-6, 1.5rem);
    }

    .card-body.padding-lg {
      padding: var(--vlog-space-6, 1.5rem) var(--vlog-space-8, 2rem);
    }

    .card-body ::slotted(*) {
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
      color: var(--vlog-text-secondary, #cbd5e1);
    }

    /* Footer section */
    .card-footer {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: var(--vlog-space-3, 0.75rem);
      border-top: 1px solid var(--vlog-border-primary, #334155);
    }

    .card-footer.padding-none {
      padding: 0;
    }

    .card-footer.padding-sm {
      padding: var(--vlog-space-3, 0.75rem) var(--vlog-space-4, 1rem);
    }

    .card-footer.padding-md {
      padding: var(--vlog-space-4, 1rem) var(--vlog-space-6, 1.5rem);
    }

    .card-footer.padding-lg {
      padding: var(--vlog-space-6, 1.5rem) var(--vlog-space-8, 2rem);
    }

    .card-footer:not(:has(slot[name="footer"]::slotted(*))) {
      display: none;
    }
  </style>

  <div class="card" part="card">
    <div class="card-header" part="header">
      <div class="header-content">
        <slot name="header"></slot>
      </div>
      <div class="header-actions">
        <slot name="actions"></slot>
      </div>
    </div>
    <div class="card-body" part="body">
      <slot></slot>
    </div>
    <div class="card-footer" part="footer">
      <slot name="footer"></slot>
    </div>
  </div>
`;

export class VlogCard extends HTMLElement {
  private card!: HTMLDivElement;
  private header!: HTMLDivElement;
  private body!: HTMLDivElement;
  private footer!: HTMLDivElement;

  static get observedAttributes() {
    return ['variant', 'padding', 'clickable', 'loading'];
  }

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot!.appendChild(template.content.cloneNode(true));

    this.card = this.shadowRoot!.querySelector('.card')!;
    this.header = this.shadowRoot!.querySelector('.card-header')!;
    this.body = this.shadowRoot!.querySelector('.card-body')!;
    this.footer = this.shadowRoot!.querySelector('.card-footer')!;

    this.handleClick = this.handleClick.bind(this);
    this.handleKeyDown = this.handleKeyDown.bind(this);
  }

  connectedCallback() {
    this.updateStyles();
    this.updateClickable();
  }

  disconnectedCallback() {
    this.card.removeEventListener('click', this.handleClick);
    this.card.removeEventListener('keydown', this.handleKeyDown);
  }

  attributeChangedCallback(name: string, oldValue: string | null, newValue: string | null) {
    if (oldValue === newValue) return;

    if (name === 'clickable') {
      this.updateClickable();
    } else {
      this.updateStyles();
    }
  }

  private updateStyles() {
    const variant = this.getAttribute('variant') || 'default';
    const padding = this.getAttribute('padding') || 'md';
    const loading = this.hasAttribute('loading');

    // Reset classes
    this.card.className = 'card';

    // Add variant
    if (variant !== 'default') {
      this.card.classList.add(`variant-${variant}`);
    }

    // Add clickable
    if (this.hasAttribute('clickable')) {
      this.card.classList.add('clickable');
    }

    // Add loading
    if (loading) {
      this.card.classList.add('loading');
    }

    // Set padding on sections
    this.header.className = `card-header padding-${padding}`;
    this.body.className = `card-body padding-${padding}`;
    this.footer.className = `card-footer padding-${padding}`;
  }

  private updateClickable() {
    const isClickable = this.hasAttribute('clickable');

    if (isClickable) {
      this.card.classList.add('clickable');
      this.card.setAttribute('tabindex', '0');
      this.card.setAttribute('role', 'button');
      this.card.addEventListener('click', this.handleClick);
      this.card.addEventListener('keydown', this.handleKeyDown);
    } else {
      this.card.classList.remove('clickable');
      this.card.removeAttribute('tabindex');
      this.card.removeAttribute('role');
      this.card.removeEventListener('click', this.handleClick);
      this.card.removeEventListener('keydown', this.handleKeyDown);
    }
  }

  private handleClick(e: MouseEvent) {
    // Don't trigger if clicking on interactive elements inside the card
    const target = e.target as HTMLElement;
    if (target.closest('button, a, input, select, textarea, [tabindex]') &&
        target !== this.card) {
      return;
    }

    this.dispatchEvent(new CustomEvent('card-click', {
      detail: {},
      bubbles: true,
      composed: true
    }));
  }

  private handleKeyDown(e: KeyboardEvent) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      this.dispatchEvent(new CustomEvent('card-click', {
        detail: {},
        bubbles: true,
        composed: true
      }));
    }
  }

  // Public API
  get variant(): string {
    return this.getAttribute('variant') || 'default';
  }

  set variant(value: string) {
    this.setAttribute('variant', value);
  }

  get padding(): string {
    return this.getAttribute('padding') || 'md';
  }

  set padding(value: string) {
    this.setAttribute('padding', value);
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

  get clickable(): boolean {
    return this.hasAttribute('clickable');
  }

  set clickable(value: boolean) {
    if (value) {
      this.setAttribute('clickable', '');
    } else {
      this.removeAttribute('clickable');
    }
  }
}

// Register the custom element
customElements.define('vlog-card', VlogCard);
