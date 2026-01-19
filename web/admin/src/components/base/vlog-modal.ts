/**
 * VLog Modal Web Component (No-DOM-Manipulation version)
 *
 * An accessible modal dialog that does NOT move or restructure DOM content.
 * This version is compatible with Alpine.js CSP mode because it never moves
 * child elements after they've been parsed.
 *
 * The modal structure is defined inline in HTML, and this component only
 * handles open/close state, focus trapping, and keyboard events.
 *
 * Styles are defined in tokens.css to comply with CSP (no inline styles).
 *
 * @example
 * <vlog-modal :open="showModal" @close="showModal = false" size="md">
 *   <div class="modal-container">
 *     <div class="modal-header">
 *       <h2 class="modal-title">Title</h2>
 *       <button class="modal-close">X</button>
 *     </div>
 *     <div class="modal-body">Content</div>
 *     <div class="modal-footer">Buttons</div>
 *   </div>
 * </vlog-modal>
 */

export class VlogModal extends HTMLElement {
  private previouslyFocusedElement: HTMLElement | null = null;
  private focusableElements: HTMLElement[] = [];
  private closeButton: HTMLButtonElement | null = null;

  static get observedAttributes() {
    return ['open', 'size', 'no-close'];
  }

  constructor() {
    super();
    this.handleKeyDown = this.handleKeyDown.bind(this);
    this.handleBackdropClick = this.handleBackdropClick.bind(this);
    this.handleCloseClick = this.handleCloseClick.bind(this);
  }

  connectedCallback() {
    // Find the close button and add listener
    this.closeButton = this.querySelector('.modal-close');
    this.closeButton?.addEventListener('click', this.handleCloseClick);

    // Add backdrop click handler to the element itself (the ::before pseudo-element)
    this.addEventListener('click', this.handleBackdropClick);
  }

  disconnectedCallback() {
    this.closeButton?.removeEventListener('click', this.handleCloseClick);
    this.removeEventListener('click', this.handleBackdropClick);
    document.removeEventListener('keydown', this.handleKeyDown);
    document.body.classList.remove('modal-open');
  }

  attributeChangedCallback(name: string, _oldValue: string | null, _newValue: string | null) {
    if (name === 'open') {
      this.updateOpenState();
    }
  }

  private updateOpenState() {
    if (this.hasAttribute('open')) {
      this.showModal();
    } else {
      this.hideModal();
    }
  }

  private showModal() {
    this.previouslyFocusedElement = document.activeElement as HTMLElement;
    document.body.classList.add('modal-open');
    document.addEventListener('keydown', this.handleKeyDown);

    this.setAttribute('role', 'dialog');
    this.setAttribute('aria-modal', 'true');

    requestAnimationFrame(() => {
      this.updateFocusableElements();
      this.focusFirstElement();
    });

    this.dispatchEvent(new CustomEvent('open', { bubbles: true }));
  }

  private hideModal() {
    document.body.classList.remove('modal-open');
    document.removeEventListener('keydown', this.handleKeyDown);

    if (this.previouslyFocusedElement) {
      this.previouslyFocusedElement.focus();
      this.previouslyFocusedElement = null;
    }

    this.dispatchEvent(new CustomEvent('close', { bubbles: true }));
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

    this.focusableElements = Array.from(
      this.querySelectorAll<HTMLElement>(selectors.join(','))
    );
  }

  private focusFirstElement() {
    if (this.focusableElements.length > 0) {
      this.focusableElements[0]?.focus();
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
      if (document.activeElement === firstElement) {
        event.preventDefault();
        lastElement?.focus();
      }
    } else {
      if (document.activeElement === lastElement) {
        event.preventDefault();
        firstElement?.focus();
      }
    }
  }

  private handleBackdropClick(event: MouseEvent) {
    // Only close if clicking directly on the vlog-modal (the backdrop area)
    // not on the modal-container or its children
    if (event.target === this && !this.hasAttribute('no-close')) {
      this.open = false;
    }
  }

  private handleCloseClick() {
    this.open = false;
  }

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

customElements.define('vlog-modal', VlogModal);
