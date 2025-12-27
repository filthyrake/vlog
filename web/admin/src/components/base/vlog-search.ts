/**
 * VLog Search Web Component
 *
 * A search input with debounce, clear button, and keyboard support.
 * Emits 'search' event on input with debouncing.
 *
 * @example
 * <vlog-search placeholder="Search videos..." debounce="300"></vlog-search>
 *
 * @fires search - When search value changes (debounced)
 * @fires clear - When search is cleared
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

    .search-container {
      position: relative;
      display: flex;
      align-items: center;
    }

    .search-icon {
      position: absolute;
      left: var(--vlog-space-3, 0.75rem);
      pointer-events: none;
      color: var(--vlog-text-tertiary, #94a3b8);
      width: 1rem;
      height: 1rem;
    }

    .search-input {
      width: 100%;
      padding: var(--vlog-space-2, 0.5rem) var(--vlog-space-10, 2.5rem);
      padding-left: var(--vlog-space-10, 2.5rem);
      border: 1px solid var(--vlog-border-secondary, #334155);
      border-radius: var(--vlog-radius-md, 0.375rem);
      background-color: var(--vlog-bg-tertiary, #1e293b);
      color: var(--vlog-text-primary, #f1f5f9);
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
      font-size: var(--vlog-text-sm, 0.875rem);
      transition: var(--vlog-transition-colors);
    }

    .search-input::placeholder {
      color: var(--vlog-text-tertiary, #94a3b8);
    }

    .search-input:hover {
      border-color: var(--vlog-border-primary, #475569);
    }

    .search-input:focus {
      outline: none;
      border-color: var(--vlog-primary, #3b82f6);
      box-shadow: 0 0 0 var(--vlog-focus-ring-width, 2px) var(--vlog-focus-ring-color, rgba(59, 130, 246, 0.5));
    }

    .clear-button {
      position: absolute;
      right: var(--vlog-space-2, 0.5rem);
      display: flex;
      align-items: center;
      justify-content: center;
      width: 1.5rem;
      height: 1.5rem;
      padding: 0;
      border: none;
      border-radius: var(--vlog-radius-sm, 0.25rem);
      background: transparent;
      color: var(--vlog-text-tertiary, #94a3b8);
      cursor: pointer;
      opacity: 0;
      visibility: hidden;
      transition: var(--vlog-transition-colors), opacity 150ms ease;
    }

    .clear-button:hover {
      background-color: var(--vlog-bg-secondary, #0f172a);
      color: var(--vlog-text-primary, #f1f5f9);
    }

    .clear-button:focus-visible {
      outline: none;
      box-shadow: 0 0 0 var(--vlog-focus-ring-width, 2px) var(--vlog-focus-ring-color, rgba(59, 130, 246, 0.5));
    }

    .clear-button.visible {
      opacity: 1;
      visibility: visible;
    }

    .clear-button svg {
      width: 0.875rem;
      height: 0.875rem;
    }

    /* Loading state */
    .search-container.loading .search-icon {
      animation: spin 1s linear infinite;
    }

    @keyframes spin {
      to {
        transform: rotate(360deg);
      }
    }

    /* Size variants */
    :host([size="sm"]) .search-input {
      padding: var(--vlog-space-1, 0.25rem) var(--vlog-space-8, 2rem);
      padding-left: var(--vlog-space-8, 2rem);
      font-size: var(--vlog-text-xs, 0.75rem);
    }

    :host([size="sm"]) .search-icon {
      left: var(--vlog-space-2, 0.5rem);
      width: 0.875rem;
      height: 0.875rem;
    }

    :host([size="lg"]) .search-input {
      padding: var(--vlog-space-3, 0.75rem) var(--vlog-space-12, 3rem);
      padding-left: var(--vlog-space-12, 3rem);
      font-size: var(--vlog-text-base, 1rem);
    }

    :host([size="lg"]) .search-icon {
      left: var(--vlog-space-4, 1rem);
      width: 1.25rem;
      height: 1.25rem;
    }
  </style>

  <div class="search-container" part="container">
    <svg class="search-icon" part="icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
      <path fill-rule="evenodd" d="M9 3.5a5.5 5.5 0 100 11 5.5 5.5 0 000-11zM2 9a7 7 0 1112.452 4.391l3.328 3.329a.75.75 0 11-1.06 1.06l-3.329-3.328A7 7 0 012 9z" clip-rule="evenodd" />
    </svg>
    <input
      type="search"
      class="search-input"
      part="input"
      autocomplete="off"
      aria-label="Search"
    />
    <button type="button" class="clear-button" part="clear" aria-label="Clear search">
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
        <path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z" />
      </svg>
    </button>
  </div>
`;

export class VlogSearch extends HTMLElement {
  private container!: HTMLDivElement;
  private input!: HTMLInputElement;
  private clearButton!: HTMLButtonElement;
  private debounceTimer: number | null = null;

  static get observedAttributes() {
    return ['placeholder', 'value', 'debounce', 'loading', 'disabled', 'size'];
  }

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot!.appendChild(template.content.cloneNode(true));

    this.container = this.shadowRoot!.querySelector('.search-container')!;
    this.input = this.shadowRoot!.querySelector('.search-input')!;
    this.clearButton = this.shadowRoot!.querySelector('.clear-button')!;

    this.handleInput = this.handleInput.bind(this);
    this.handleClear = this.handleClear.bind(this);
    this.handleKeyDown = this.handleKeyDown.bind(this);
  }

  connectedCallback() {
    this.updateAttributes();
    this.setupListeners();
    this.updateClearButtonVisibility();
  }

  disconnectedCallback() {
    this.input.removeEventListener('input', this.handleInput);
    this.input.removeEventListener('keydown', this.handleKeyDown);
    this.clearButton.removeEventListener('click', this.handleClear);
    if (this.debounceTimer !== null) {
      clearTimeout(this.debounceTimer);
    }
  }

  attributeChangedCallback(name: string, oldValue: string | null, newValue: string | null) {
    if (oldValue === newValue) return;

    if (name === 'placeholder') {
      this.input.placeholder = newValue || '';
    } else if (name === 'value') {
      this.input.value = newValue || '';
      this.updateClearButtonVisibility();
    } else if (name === 'loading') {
      this.container.classList.toggle('loading', this.hasAttribute('loading'));
    } else if (name === 'disabled') {
      this.input.disabled = this.hasAttribute('disabled');
    }
  }

  private updateAttributes() {
    const placeholder = this.getAttribute('placeholder');
    if (placeholder) {
      this.input.placeholder = placeholder;
    }

    const value = this.getAttribute('value');
    if (value) {
      this.input.value = value;
    }

    const ariaLabel = this.getAttribute('aria-label');
    if (ariaLabel) {
      this.input.setAttribute('aria-label', ariaLabel);
    }

    if (this.hasAttribute('disabled')) {
      this.input.disabled = true;
    }

    if (this.hasAttribute('loading')) {
      this.container.classList.add('loading');
    }
  }

  private setupListeners() {
    this.input.addEventListener('input', this.handleInput);
    this.input.addEventListener('keydown', this.handleKeyDown);
    this.clearButton.addEventListener('click', this.handleClear);
  }

  private handleInput() {
    this.updateClearButtonVisibility();

    const debounceMs = parseInt(this.getAttribute('debounce') || '300', 10);

    if (this.debounceTimer !== null) {
      clearTimeout(this.debounceTimer);
    }

    this.debounceTimer = window.setTimeout(() => {
      this.dispatchEvent(
        new CustomEvent('search', {
          detail: { value: this.input.value },
          bubbles: true,
          composed: true,
        })
      );
    }, debounceMs);
  }

  private handleClear() {
    this.input.value = '';
    this.updateClearButtonVisibility();

    if (this.debounceTimer !== null) {
      clearTimeout(this.debounceTimer);
    }

    this.dispatchEvent(
      new CustomEvent('clear', {
        bubbles: true,
        composed: true,
      })
    );

    this.dispatchEvent(
      new CustomEvent('search', {
        detail: { value: '' },
        bubbles: true,
        composed: true,
      })
    );

    this.input.focus();
  }

  private handleKeyDown(e: KeyboardEvent) {
    if (e.key === 'Escape' && this.input.value) {
      e.preventDefault();
      this.handleClear();
    }
  }

  private updateClearButtonVisibility() {
    this.clearButton.classList.toggle('visible', this.input.value.length > 0);
  }

  // Public API
  get value(): string {
    return this.input.value;
  }

  set value(val: string) {
    this.input.value = val;
    this.updateClearButtonVisibility();
  }

  get placeholder(): string {
    return this.input.placeholder;
  }

  set placeholder(val: string) {
    this.input.placeholder = val;
  }

  get loading(): boolean {
    return this.hasAttribute('loading');
  }

  set loading(val: boolean) {
    if (val) {
      this.setAttribute('loading', '');
    } else {
      this.removeAttribute('loading');
    }
  }

  get disabled(): boolean {
    return this.hasAttribute('disabled');
  }

  set disabled(val: boolean) {
    if (val) {
      this.setAttribute('disabled', '');
    } else {
      this.removeAttribute('disabled');
    }
  }

  focus() {
    this.input.focus();
  }

  blur() {
    this.input.blur();
  }

  clear() {
    this.handleClear();
  }
}

// Register the custom element
customElements.define('vlog-search', VlogSearch);
