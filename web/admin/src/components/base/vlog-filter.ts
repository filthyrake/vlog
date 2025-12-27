/**
 * VLog Filter Web Component
 *
 * A dropdown filter component with badge showing active filter count.
 * Supports single or multiple selection.
 *
 * @example
 * <vlog-filter label="Status" name="status">
 *   <vlog-filter-option value="ready">Ready</vlog-filter-option>
 *   <vlog-filter-option value="processing">Processing</vlog-filter-option>
 *   <vlog-filter-option value="failed">Failed</vlog-filter-option>
 * </vlog-filter>
 *
 * @fires change - When filter selection changes
 */

const template = document.createElement('template');
template.innerHTML = `
  <style>
    :host {
      display: inline-block;
      position: relative;
    }

    :host([hidden]) {
      display: none;
    }

    .filter-trigger {
      display: inline-flex;
      align-items: center;
      gap: var(--vlog-space-2, 0.5rem);
      padding: var(--vlog-space-2, 0.5rem) var(--vlog-space-3, 0.75rem);
      border: 1px solid var(--vlog-border-secondary, #334155);
      border-radius: var(--vlog-radius-md, 0.375rem);
      background-color: var(--vlog-bg-tertiary, #1e293b);
      color: var(--vlog-text-secondary, #cbd5e1);
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
      font-size: var(--vlog-text-sm, 0.875rem);
      cursor: pointer;
      transition: var(--vlog-transition-colors);
    }

    .filter-trigger:hover {
      border-color: var(--vlog-border-primary, #475569);
      color: var(--vlog-text-primary, #f1f5f9);
    }

    .filter-trigger:focus-visible {
      outline: none;
      border-color: var(--vlog-primary, #3b82f6);
      box-shadow: 0 0 0 var(--vlog-focus-ring-width, 2px) var(--vlog-focus-ring-color, rgba(59, 130, 246, 0.5));
    }

    .filter-trigger.active {
      border-color: var(--vlog-primary, #3b82f6);
      background-color: var(--vlog-primary-bg, rgba(59, 130, 246, 0.1));
      color: var(--vlog-primary-light, #60a5fa);
    }

    .filter-icon {
      width: 1rem;
      height: 1rem;
      transition: transform 150ms ease;
    }

    .filter-trigger[aria-expanded="true"] .filter-icon {
      transform: rotate(180deg);
    }

    .filter-badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 1.25rem;
      height: 1.25rem;
      padding: 0 var(--vlog-space-1, 0.25rem);
      border-radius: var(--vlog-radius-full, 9999px);
      background-color: var(--vlog-primary, #3b82f6);
      color: white;
      font-size: var(--vlog-text-xs, 0.75rem);
      font-weight: var(--vlog-font-medium, 500);
    }

    .filter-badge:empty {
      display: none;
    }

    .filter-dropdown {
      position: absolute;
      top: 100%;
      left: 0;
      z-index: var(--vlog-z-dropdown, 50);
      min-width: 12rem;
      max-height: 16rem;
      margin-top: var(--vlog-space-1, 0.25rem);
      padding: var(--vlog-space-1, 0.25rem);
      border: 1px solid var(--vlog-border-secondary, #334155);
      border-radius: var(--vlog-radius-lg, 0.5rem);
      background-color: var(--vlog-bg-secondary, #0f172a);
      box-shadow: var(--vlog-shadow-lg, 0 10px 15px -3px rgb(0 0 0 / 0.1));
      overflow-y: auto;
      opacity: 0;
      visibility: hidden;
      transform: translateY(-0.5rem);
      transition: opacity 150ms ease, transform 150ms ease, visibility 150ms ease;
    }

    .filter-dropdown.open {
      opacity: 1;
      visibility: visible;
      transform: translateY(0);
    }

    /* Position variants */
    :host([position="right"]) .filter-dropdown {
      left: auto;
      right: 0;
    }

    .filter-option {
      display: flex;
      align-items: center;
      gap: var(--vlog-space-2, 0.5rem);
      width: 100%;
      padding: var(--vlog-space-2, 0.5rem) var(--vlog-space-3, 0.75rem);
      border: none;
      border-radius: var(--vlog-radius-md, 0.375rem);
      background: transparent;
      color: var(--vlog-text-secondary, #cbd5e1);
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
      font-size: var(--vlog-text-sm, 0.875rem);
      text-align: left;
      cursor: pointer;
      transition: var(--vlog-transition-colors);
    }

    .filter-option:hover {
      background-color: var(--vlog-bg-tertiary, #1e293b);
      color: var(--vlog-text-primary, #f1f5f9);
    }

    .filter-option:focus-visible {
      outline: none;
      background-color: var(--vlog-bg-tertiary, #1e293b);
      box-shadow: inset 0 0 0 2px var(--vlog-primary, #3b82f6);
    }

    .filter-option.selected {
      background-color: var(--vlog-primary-bg, rgba(59, 130, 246, 0.1));
      color: var(--vlog-primary-light, #60a5fa);
    }

    .filter-option .check-icon {
      width: 1rem;
      height: 1rem;
      opacity: 0;
      transition: opacity 150ms ease;
    }

    .filter-option.selected .check-icon {
      opacity: 1;
    }

    .filter-actions {
      display: flex;
      justify-content: flex-end;
      padding: var(--vlog-space-2, 0.5rem);
      border-top: 1px solid var(--vlog-border-secondary, #334155);
      margin-top: var(--vlog-space-1, 0.25rem);
    }

    .filter-clear {
      padding: var(--vlog-space-1, 0.25rem) var(--vlog-space-2, 0.5rem);
      border: none;
      border-radius: var(--vlog-radius-sm, 0.25rem);
      background: transparent;
      color: var(--vlog-text-tertiary, #94a3b8);
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
      font-size: var(--vlog-text-xs, 0.75rem);
      cursor: pointer;
      transition: var(--vlog-transition-colors);
    }

    .filter-clear:hover {
      color: var(--vlog-text-primary, #f1f5f9);
    }

    .filter-clear:focus-visible {
      outline: none;
      box-shadow: 0 0 0 var(--vlog-focus-ring-width, 2px) var(--vlog-focus-ring-color, rgba(59, 130, 246, 0.5));
    }
  </style>

  <button type="button" class="filter-trigger" part="trigger" aria-haspopup="listbox" aria-expanded="false">
    <span class="filter-label" part="label"></span>
    <span class="filter-badge" part="badge"></span>
    <svg class="filter-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
      <path fill-rule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z" clip-rule="evenodd" />
    </svg>
  </button>

  <div class="filter-dropdown" part="dropdown" role="listbox">
    <div class="filter-options" part="options">
      <slot></slot>
    </div>
    <div class="filter-actions" part="actions">
      <button type="button" class="filter-clear" part="clear">Clear</button>
    </div>
  </div>
`;

export interface FilterOption {
  value: string;
  label: string;
}

export class VlogFilter extends HTMLElement {
  private trigger!: HTMLButtonElement;
  private dropdown!: HTMLDivElement;
  private optionsContainer!: HTMLDivElement;
  private labelElement!: HTMLSpanElement;
  private badgeElement!: HTMLSpanElement;
  private clearButton!: HTMLButtonElement;
  private selectedValues: Set<string> = new Set();
  private isOpen = false;
  private mutationObserver: MutationObserver | null = null;

  static get observedAttributes() {
    return ['label', 'name', 'multiple', 'value'];
  }

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot!.appendChild(template.content.cloneNode(true));

    this.trigger = this.shadowRoot!.querySelector('.filter-trigger')!;
    this.dropdown = this.shadowRoot!.querySelector('.filter-dropdown')!;
    this.optionsContainer = this.shadowRoot!.querySelector('.filter-options')!;
    this.labelElement = this.shadowRoot!.querySelector('.filter-label')!;
    this.badgeElement = this.shadowRoot!.querySelector('.filter-badge')!;
    this.clearButton = this.shadowRoot!.querySelector('.filter-clear')!;

    this.handleTriggerClick = this.handleTriggerClick.bind(this);
    this.handleOptionsClick = this.handleOptionsClick.bind(this);
    this.handleClearClick = this.handleClearClick.bind(this);
    this.handleOutsideClick = this.handleOutsideClick.bind(this);
    this.handleKeyDown = this.handleKeyDown.bind(this);
  }

  connectedCallback() {
    this.updateLabel();
    this.setupListeners();
    this.setupOptions();
  }

  disconnectedCallback() {
    this.trigger.removeEventListener('click', this.handleTriggerClick);
    this.optionsContainer.removeEventListener('click', this.handleOptionsClick);
    this.clearButton.removeEventListener('click', this.handleClearClick);
    document.removeEventListener('click', this.handleOutsideClick);
    document.removeEventListener('keydown', this.handleKeyDown);
    this.mutationObserver?.disconnect();
    this.mutationObserver = null;
  }

  attributeChangedCallback(name: string, oldValue: string | null, newValue: string | null) {
    if (oldValue === newValue) return;

    if (name === 'label') {
      this.updateLabel();
    } else if (name === 'value') {
      this.setValueFromAttribute(newValue);
    }
  }

  private updateLabel() {
    this.labelElement.textContent = this.getAttribute('label') || 'Filter';
  }

  private setupListeners() {
    this.trigger.addEventListener('click', this.handleTriggerClick);
    this.optionsContainer.addEventListener('click', this.handleOptionsClick);
    this.clearButton.addEventListener('click', this.handleClearClick);
  }

  private setupOptions() {
    // Set up mutation observer for dynamic options
    this.mutationObserver = new MutationObserver(() => {
      this.renderOptions();
    });
    this.mutationObserver.observe(this, { childList: true, subtree: true });
    this.renderOptions();
  }

  private renderOptions() {
    // Clear existing options (but keep slot)
    const existingButtons = this.optionsContainer.querySelectorAll('button.filter-option');
    existingButtons.forEach((btn) => btn.remove());

    // Get options from slotted content or attribute
    const slottedOptions = this.querySelectorAll('vlog-filter-option, option');
    slottedOptions.forEach((opt) => {
      const value = opt.getAttribute('value') || '';
      const label = opt.textContent || value;

      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'filter-option';
      button.dataset.value = value;
      button.setAttribute('role', 'option');
      button.setAttribute('aria-selected', String(this.selectedValues.has(value)));

      if (this.selectedValues.has(value)) {
        button.classList.add('selected');
      }

      // Create check icon
      const checkIcon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      checkIcon.setAttribute('class', 'check-icon');
      checkIcon.setAttribute('viewBox', '0 0 20 20');
      checkIcon.setAttribute('fill', 'currentColor');
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('fill-rule', 'evenodd');
      path.setAttribute('d', 'M16.704 4.153a.75.75 0 01.143 1.052l-8 10.5a.75.75 0 01-1.127.075l-4.5-4.5a.75.75 0 011.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 011.05-.143z');
      path.setAttribute('clip-rule', 'evenodd');
      checkIcon.appendChild(path);
      button.appendChild(checkIcon);

      // Create label span with textContent for safety
      const labelSpan = document.createElement('span');
      labelSpan.textContent = label;
      button.appendChild(labelSpan);

      // No per-element listener needed - using event delegation
      this.optionsContainer.insertBefore(button, this.optionsContainer.firstChild);
    });

    // Hide slot since we've rendered buttons
    const slot = this.optionsContainer.querySelector('slot');
    if (slot) {
      slot.style.display = 'none';
    }
  }

  private handleTriggerClick(e: Event) {
    e.stopPropagation();
    this.toggle();
  }

  private handleOptionsClick(e: Event) {
    // Event delegation for option buttons
    const button = (e.target as HTMLElement).closest('.filter-option') as HTMLButtonElement | null;
    if (!button) return;

    const value = button.dataset.value || '';

    if (this.hasAttribute('multiple')) {
      if (this.selectedValues.has(value)) {
        this.selectedValues.delete(value);
      } else {
        this.selectedValues.add(value);
      }
    } else {
      this.selectedValues.clear();
      this.selectedValues.add(value);
      this.close();
    }

    this.updateSelection();
    this.dispatchChangeEvent();
  }

  private handleClearClick(e: Event) {
    e.stopPropagation();
    this.selectedValues.clear();
    this.updateSelection();
    this.dispatchChangeEvent();
    this.close();
  }

  private handleOutsideClick(e: Event) {
    if (!this.contains(e.target as Node)) {
      this.close();
    }
  }

  private handleKeyDown(e: KeyboardEvent) {
    if (e.key === 'Escape' && this.isOpen) {
      this.close();
      this.trigger.focus();
    }
  }

  private toggle() {
    if (this.isOpen) {
      this.close();
    } else {
      this.open();
    }
  }

  private open() {
    this.isOpen = true;
    this.dropdown.classList.add('open');
    this.trigger.setAttribute('aria-expanded', 'true');
    document.addEventListener('click', this.handleOutsideClick);
    document.addEventListener('keydown', this.handleKeyDown);
  }

  private close() {
    this.isOpen = false;
    this.dropdown.classList.remove('open');
    this.trigger.setAttribute('aria-expanded', 'false');
    document.removeEventListener('click', this.handleOutsideClick);
    document.removeEventListener('keydown', this.handleKeyDown);
  }

  private updateSelection() {
    const buttons = this.shadowRoot!.querySelectorAll('.filter-option');
    buttons.forEach((btn) => {
      const value = (btn as HTMLButtonElement).dataset.value || '';
      const isSelected = this.selectedValues.has(value);
      btn.classList.toggle('selected', isSelected);
      btn.setAttribute('aria-selected', String(isSelected));
    });

    // Update badge
    const count = this.selectedValues.size;
    this.badgeElement.textContent = count > 0 ? String(count) : '';

    // Update trigger active state
    this.trigger.classList.toggle('active', count > 0);
  }

  private setValueFromAttribute(value: string | null) {
    this.selectedValues.clear();
    if (value) {
      value.split(',').forEach((v) => {
        if (v.trim()) {
          this.selectedValues.add(v.trim());
        }
      });
    }
    this.updateSelection();
  }

  private dispatchChangeEvent() {
    this.dispatchEvent(
      new CustomEvent('change', {
        detail: {
          name: this.getAttribute('name') || '',
          value: this.hasAttribute('multiple')
            ? Array.from(this.selectedValues)
            : this.selectedValues.size > 0
              ? Array.from(this.selectedValues)[0]
              : null,
          values: Array.from(this.selectedValues),
        },
        bubbles: true,
        composed: true,
      })
    );
  }

  // Public API
  get value(): string | string[] {
    if (this.hasAttribute('multiple')) {
      return Array.from(this.selectedValues);
    }
    const values = Array.from(this.selectedValues);
    return values.length > 0 ? values[0] ?? '' : '';
  }

  set value(val: string | string[]) {
    this.selectedValues.clear();
    if (Array.isArray(val)) {
      val.forEach((v) => this.selectedValues.add(v));
    } else if (val) {
      this.selectedValues.add(val);
    }
    this.updateSelection();
  }

  get name(): string {
    return this.getAttribute('name') || '';
  }

  set name(val: string) {
    this.setAttribute('name', val);
  }

  clear() {
    this.selectedValues.clear();
    this.updateSelection();
    this.dispatchChangeEvent();
  }

  getSelectedValues(): string[] {
    return Array.from(this.selectedValues);
  }
}

// Register the custom element
customElements.define('vlog-filter', VlogFilter);

/**
 * VLog Filter Option Element
 * A simple element to define filter options in markup.
 * Attributes are read by the parent VlogFilter component.
 */
export class VlogFilterOption extends HTMLElement {
  // This is a marker element - attributes are read by parent VlogFilter
}

customElements.define('vlog-filter-option', VlogFilterOption);
