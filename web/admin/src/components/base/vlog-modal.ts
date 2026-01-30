/**
 * VLog Modal Web Component
 *
 * An accessible modal dialog with focus trapping, backdrop, and keyboard support.
 * Follows WAI-ARIA dialog pattern.
 *
 * @example
 * <vlog-modal id="edit-modal" size="md">
 *   <span slot="header">Edit Video</span>
 *   <div slot="body">Modal content here</div>
 *   <div slot="footer">
 *     <vlog-button variant="secondary">Cancel</vlog-button>
 *     <vlog-button variant="primary">Save</vlog-button>
 *   </div>
 * </vlog-modal>
 *
 * // Open: document.getElementById('edit-modal').open = true;
 * // Close: document.getElementById('edit-modal').open = false;
 */

const template = document.createElement('template');
template.innerHTML = `
  <style>
    :host {
      display: contents;
    }

    .modal-overlay {
      position: fixed;
      inset: 0;
      z-index: var(--vlog-z-modal, 50);
      display: flex;
      align-items: center;
      justify-content: center;
      padding: var(--vlog-space-4, 1rem);
      opacity: 0;
      visibility: hidden;
      transition: opacity var(--vlog-transition-base, 200ms ease),
                  visibility var(--vlog-transition-base, 200ms ease);
    }

    .modal-overlay.open {
      opacity: 1;
      visibility: visible;
    }

    .backdrop {
      position: absolute;
      inset: 0;
      background-color: rgba(0, 0, 0, 0.7);
      backdrop-filter: blur(4px);
    }

    .modal {
      position: relative;
      display: flex;
      flex-direction: column;
      max-height: calc(100vh - var(--vlog-space-8, 2rem));
      background-color: var(--vlog-bg-secondary, #0f172a);
      border: 1px solid var(--vlog-border-primary, #334155);
      border-radius: var(--vlog-radius-xl, 0.75rem);
      box-shadow: var(--vlog-shadow-xl, 0 20px 25px -5px rgb(0 0 0 / 0.1));
      transform: scale(0.95) translateY(10px);
      transition: transform var(--vlog-transition-base, 200ms ease);
    }

    .modal-overlay.open .modal {
      transform: scale(1) translateY(0);
    }

    /* Size variants */
    .modal.size-sm {
      width: var(--vlog-modal-width-sm, 24rem);
    }

    .modal.size-md {
      width: var(--vlog-modal-width-md, 32rem);
    }

    .modal.size-lg {
      width: var(--vlog-modal-width-lg, 42rem);
    }

    .modal.size-xl {
      width: var(--vlog-modal-width-xl, 56rem);
    }

    .modal.size-full {
      width: var(--vlog-modal-width-full, calc(100vw - 2rem));
      max-width: 80rem;
    }

    /* Header */
    .modal-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--vlog-space-4, 1rem);
      padding: var(--vlog-modal-padding, 1.5rem);
      border-bottom: 1px solid var(--vlog-border-primary, #334155);
      flex-shrink: 0;
    }

    .modal-title {
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
      width: 2rem;
      height: 2rem;
      padding: 0;
      border: none;
      border-radius: var(--vlog-radius-md, 0.375rem);
      background-color: transparent;
      color: var(--vlog-text-tertiary, #94a3b8);
      cursor: pointer;
      transition: var(--vlog-transition-colors, color 200ms ease, background-color 200ms ease);
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
      width: 1.25rem;
      height: 1.25rem;
    }

    /* Body */
    .modal-body {
      flex: 1;
      overflow-y: auto;
      padding: var(--vlog-modal-padding, 1.5rem);
    }

    /* Footer */
    .modal-footer {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: var(--vlog-space-3, 0.75rem);
      padding: var(--vlog-modal-padding, 1.5rem);
      border-top: 1px solid var(--vlog-border-primary, #334155);
      flex-shrink: 0;
    }

    /* Hide slots when empty */
    .modal-header:not(:has(slot[name="header"]::slotted(*))) {
      display: none;
    }

    .modal-footer:not(:has(slot[name="footer"]::slotted(*))) {
      display: none;
    }

    /* Ensure header shows with no-close attribute */
    :host([no-close]) .close-button {
      display: none;
    }
  </style>

  <div class="modal-overlay" aria-hidden="true">
    <div class="backdrop" aria-hidden="true"></div>
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
      <div class="modal-header">
        <h2 class="modal-title" id="modal-title">
          <slot name="header"></slot>
        </h2>
        <button class="close-button" type="button" aria-label="Close modal">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M18 6L6 18M6 6l12 12"/>
          </svg>
        </button>
      </div>
      <div class="modal-body">
        <slot name="body"></slot>
        <slot></slot>
      </div>
      <div class="modal-footer">
        <slot name="footer"></slot>
      </div>
    </div>
  </div>
`;

export class VlogModal extends HTMLElement {
  private overlay: HTMLDivElement;
  private modalElement: HTMLDivElement;
  private closeButton: HTMLButtonElement;
  private backdrop: HTMLDivElement;
  private previouslyFocusedElement: HTMLElement | null = null;
  private focusableElements: HTMLElement[] = [];

  static get observedAttributes() {
    return ['open', 'size', 'no-close'];
  }

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot!.appendChild(template.content.cloneNode(true));

    this.overlay = this.shadowRoot!.querySelector('.modal-overlay')!;
    this.modalElement = this.shadowRoot!.querySelector('.modal')!;
    this.closeButton = this.shadowRoot!.querySelector('.close-button')!;
    this.backdrop = this.shadowRoot!.querySelector('.backdrop')!;

    // Bind event handlers
    this.handleKeyDown = this.handleKeyDown.bind(this);
    this.handleBackdropClick = this.handleBackdropClick.bind(this);
    this.handleCloseClick = this.handleCloseClick.bind(this);
  }

  connectedCallback() {
    this.updateSize();
    this.closeButton.addEventListener('click', this.handleCloseClick);
    this.backdrop.addEventListener('click', this.handleBackdropClick);
  }

  disconnectedCallback() {
    this.closeButton.removeEventListener('click', this.handleCloseClick);
    this.backdrop.removeEventListener('click', this.handleBackdropClick);
    document.removeEventListener('keydown', this.handleKeyDown);

    // Restore body scroll
    document.body.style.overflow = '';
  }

  attributeChangedCallback(name: string, _oldValue: string | null, _newValue: string | null) {
    if (name === 'open') {
      this.updateOpenState();
    } else if (name === 'size') {
      this.updateSize();
    }
  }

  private updateOpenState() {
    const isOpen = this.hasAttribute('open');

    if (isOpen) {
      this.showModal();
    } else {
      this.hideModal();
    }
  }

  private showModal() {
    // Store currently focused element
    this.previouslyFocusedElement = document.activeElement as HTMLElement;

    // Show overlay
    this.overlay.classList.add('open');
    this.overlay.setAttribute('aria-hidden', 'false');

    // Prevent body scroll
    document.body.style.overflow = 'hidden';

    // Add keyboard listener
    document.addEventListener('keydown', this.handleKeyDown);

    // Focus first focusable element
    requestAnimationFrame(() => {
      this.updateFocusableElements();
      this.focusFirstElement();
    });

    // Dispatch open event
    this.dispatchEvent(new CustomEvent('open', { bubbles: true }));
  }

  private hideModal() {
    // Hide overlay
    this.overlay.classList.remove('open');
    this.overlay.setAttribute('aria-hidden', 'true');

    // Restore body scroll
    document.body.style.overflow = '';

    // Remove keyboard listener
    document.removeEventListener('keydown', this.handleKeyDown);

    // Restore focus
    if (this.previouslyFocusedElement) {
      this.previouslyFocusedElement.focus();
      this.previouslyFocusedElement = null;
    }

    // Dispatch close event
    this.dispatchEvent(new CustomEvent('close', { bubbles: true }));
  }

  private updateSize() {
    const size = this.getAttribute('size') || 'md';
    this.modalElement.className = `modal size-${size}`;
  }

  private updateFocusableElements() {
    const selectors = [
      'button:not([disabled])',
      'input:not([disabled])',
      'select:not([disabled])',
      'textarea:not([disabled])',
      'a[href]',
      '[tabindex]:not([tabindex="-1"])',
    ];

    // Get focusable elements from both shadow DOM and slotted content
    const shadowFocusable = Array.from(
      this.shadowRoot!.querySelectorAll<HTMLElement>(selectors.join(','))
    );

    const slottedFocusable = Array.from(
      this.querySelectorAll<HTMLElement>(selectors.join(','))
    );

    this.focusableElements = [...shadowFocusable, ...slottedFocusable];
  }

  private focusFirstElement() {
    if (this.focusableElements.length > 0) {
      this.focusableElements[0]?.focus();
    } else {
      this.modalElement.focus();
    }
  }

  private handleKeyDown(event: KeyboardEvent) {
    if (event.key === 'Escape' && !this.hasAttribute('no-close')) {
      event.preventDefault();
      this.open = false;
    }

    if (event.key === 'Tab') {
      this.trapFocus(event);
    }
  }

  private trapFocus(event: KeyboardEvent) {
    this.updateFocusableElements();

    if (this.focusableElements.length === 0) return;

    const firstElement = this.focusableElements[0];
    const lastElement = this.focusableElements[this.focusableElements.length - 1];

    if (event.shiftKey) {
      // Shift + Tab
      if (document.activeElement === firstElement || this.shadowRoot!.activeElement === firstElement) {
        event.preventDefault();
        lastElement?.focus();
      }
    } else {
      // Tab
      if (document.activeElement === lastElement || this.shadowRoot!.activeElement === lastElement) {
        event.preventDefault();
        firstElement?.focus();
      }
    }
  }

  private handleBackdropClick() {
    if (!this.hasAttribute('no-close')) {
      this.open = false;
    }
  }

  private handleCloseClick() {
    this.open = false;
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

  get size(): string {
    return this.getAttribute('size') || 'md';
  }

  set size(value: string) {
    this.setAttribute('size', value);
  }

  // Methods for programmatic control
  show() {
    this.open = true;
  }

  hide() {
    this.open = false;
  }

  toggle() {
    this.open = !this.open;
  }
}

// Register the custom element
customElements.define('vlog-modal', VlogModal);
