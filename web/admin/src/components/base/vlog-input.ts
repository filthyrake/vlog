/**
 * VLog Input Web Component
 *
 * A universal form input component supporting text, textarea, select,
 * checkbox, and radio with validation states and full accessibility.
 *
 * @example
 * <vlog-input type="text" label="Title" :value="title" @change="title = $event.detail.value"></vlog-input>
 * <vlog-input type="textarea" label="Description" rows="4"></vlog-input>
 * <vlog-input type="select" label="Category"><option value="1">Option 1</option></vlog-input>
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

    .input-wrapper {
      display: flex;
      flex-direction: column;
      gap: var(--vlog-space-1, 0.25rem);
    }

    /* Label styles */
    .label {
      display: flex;
      align-items: center;
      gap: var(--vlog-space-1, 0.25rem);
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
      font-size: var(--vlog-text-sm, 0.875rem);
      font-weight: var(--vlog-font-medium, 500);
      color: var(--vlog-text-secondary, #cbd5e1);
    }

    .label.size-sm {
      font-size: var(--vlog-text-xs, 0.75rem);
    }

    .label.size-lg {
      font-size: var(--vlog-text-base, 1rem);
    }

    .required-indicator {
      color: var(--vlog-error, #ef4444);
    }

    /* Input container for icons */
    .input-container {
      position: relative;
      display: flex;
      align-items: center;
    }

    /* Base input styles */
    .input,
    .textarea,
    .select {
      width: 100%;
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
      font-size: var(--vlog-text-sm, 0.875rem);
      color: var(--vlog-text-primary, #f1f5f9);
      background-color: var(--vlog-bg-tertiary, #1e293b);
      border: 1px solid var(--vlog-border-primary, #334155);
      border-radius: var(--vlog-radius-lg, 0.5rem);
      transition: var(--vlog-transition-colors);
    }

    .input:focus,
    .textarea:focus,
    .select:focus {
      outline: none;
      border-color: var(--vlog-border-focus, #3b82f6);
      box-shadow:
        0 0 0 var(--vlog-focus-ring-offset, 2px) var(--vlog-bg-primary, #020617),
        0 0 0 calc(var(--vlog-focus-ring-offset, 2px) + var(--vlog-focus-ring-width, 2px)) var(--vlog-focus-ring-color, #3b82f6);
    }

    .input::placeholder,
    .textarea::placeholder {
      color: var(--vlog-text-muted, #64748b);
    }

    .input:disabled,
    .textarea:disabled,
    .select:disabled {
      opacity: 0.5;
      cursor: not-allowed;
      background-color: var(--vlog-bg-elevated, #334155);
    }

    /* Size variants */
    .input.size-sm {
      height: var(--vlog-input-height-sm, 2rem);
      padding: var(--vlog-space-1, 0.25rem) var(--vlog-space-3, 0.75rem);
      font-size: var(--vlog-text-xs, 0.75rem);
    }

    .input.size-md {
      height: var(--vlog-input-height-md, 2.5rem);
      padding: var(--vlog-space-2, 0.5rem) var(--vlog-space-4, 1rem);
    }

    .input.size-lg {
      height: var(--vlog-input-height-lg, 3rem);
      padding: var(--vlog-space-3, 0.75rem) var(--vlog-space-4, 1rem);
      font-size: var(--vlog-text-base, 1rem);
    }

    .textarea.size-sm {
      padding: var(--vlog-space-2, 0.5rem) var(--vlog-space-3, 0.75rem);
      font-size: var(--vlog-text-xs, 0.75rem);
    }

    .textarea.size-md {
      padding: var(--vlog-space-3, 0.75rem) var(--vlog-space-4, 1rem);
    }

    .textarea.size-lg {
      padding: var(--vlog-space-4, 1rem);
      font-size: var(--vlog-text-base, 1rem);
    }

    .select.size-sm {
      height: var(--vlog-input-height-sm, 2rem);
      padding: var(--vlog-space-1, 0.25rem) var(--vlog-space-3, 0.75rem);
      font-size: var(--vlog-text-xs, 0.75rem);
    }

    .select.size-md {
      height: var(--vlog-input-height-md, 2.5rem);
      padding: var(--vlog-space-2, 0.5rem) var(--vlog-space-4, 1rem);
    }

    .select.size-lg {
      height: var(--vlog-input-height-lg, 3rem);
      padding: var(--vlog-space-3, 0.75rem) var(--vlog-space-4, 1rem);
      font-size: var(--vlog-text-base, 1rem);
    }

    /* With icon padding */
    .input.has-icon-left {
      padding-left: var(--vlog-space-10, 2.5rem);
    }

    .input.has-icon-right {
      padding-right: var(--vlog-space-10, 2.5rem);
    }

    /* Icon slots */
    .icon-left,
    .icon-right {
      position: absolute;
      display: flex;
      align-items: center;
      justify-content: center;
      width: var(--vlog-space-10, 2.5rem);
      height: 100%;
      color: var(--vlog-text-tertiary, #94a3b8);
      pointer-events: none;
    }

    .icon-left {
      left: 0;
    }

    .icon-right {
      right: 0;
    }

    ::slotted(svg) {
      width: 1.25rem;
      height: 1.25rem;
    }

    /* State variants */
    .input.state-error,
    .textarea.state-error,
    .select.state-error {
      border-color: var(--vlog-error, #ef4444);
    }

    .input.state-error:focus,
    .textarea.state-error:focus,
    .select.state-error:focus {
      box-shadow:
        0 0 0 var(--vlog-focus-ring-offset, 2px) var(--vlog-bg-primary, #020617),
        0 0 0 calc(var(--vlog-focus-ring-offset, 2px) + var(--vlog-focus-ring-width, 2px)) var(--vlog-error, #ef4444);
    }

    .input.state-success,
    .textarea.state-success,
    .select.state-success {
      border-color: var(--vlog-success, #22c55e);
    }

    .input.state-warning,
    .textarea.state-warning,
    .select.state-warning {
      border-color: var(--vlog-warning, #eab308);
    }

    /* Helper text */
    .helper-text {
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
      font-size: var(--vlog-text-xs, 0.75rem);
      color: var(--vlog-text-tertiary, #94a3b8);
      margin-top: var(--vlog-space-1, 0.25rem);
    }

    /* Error message */
    .error-message {
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
      font-size: var(--vlog-text-xs, 0.75rem);
      color: var(--vlog-error-text, #fca5a5);
      margin-top: var(--vlog-space-1, 0.25rem);
      display: flex;
      align-items: center;
      gap: var(--vlog-space-1, 0.25rem);
    }

    .error-message svg {
      width: 0.875rem;
      height: 0.875rem;
      flex-shrink: 0;
    }

    /* Checkbox and Radio styles */
    .checkbox-wrapper,
    .radio-wrapper {
      display: flex;
      align-items: flex-start;
      gap: var(--vlog-space-2, 0.5rem);
    }

    .checkbox,
    .radio {
      width: 1rem;
      height: 1rem;
      margin-top: 0.125rem;
      accent-color: var(--vlog-primary, #3b82f6);
      cursor: pointer;
    }

    .checkbox:focus,
    .radio:focus {
      outline: none;
      box-shadow:
        0 0 0 var(--vlog-focus-ring-offset, 2px) var(--vlog-bg-primary, #020617),
        0 0 0 calc(var(--vlog-focus-ring-offset, 2px) + var(--vlog-focus-ring-width, 2px)) var(--vlog-focus-ring-color, #3b82f6);
    }

    .checkbox:disabled,
    .radio:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }

    .checkbox-label,
    .radio-label {
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
      font-size: var(--vlog-text-sm, 0.875rem);
      color: var(--vlog-text-primary, #f1f5f9);
      cursor: pointer;
    }

    .checkbox-label.disabled,
    .radio-label.disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }

    /* Textarea resize */
    .textarea {
      resize: vertical;
      min-height: 4rem;
    }

    /* Select arrow */
    .select {
      appearance: none;
      background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 24 24' stroke='%2394a3b8'%3E%3Cpath stroke-linecap='round' stroke-linejoin='round' stroke-width='2' d='M19 9l-7 7-7-7'%3E%3C/path%3E%3C/svg%3E");
      background-repeat: no-repeat;
      background-position: right 0.75rem center;
      background-size: 1rem;
      padding-right: 2.5rem;
      cursor: pointer;
    }

    .select option {
      background-color: var(--vlog-bg-tertiary, #1e293b);
      color: var(--vlog-text-primary, #f1f5f9);
    }
  </style>

  <div class="input-wrapper" part="wrapper">
    <label class="label" part="label">
      <span class="label-text"></span>
      <span class="required-indicator" aria-hidden="true" style="display: none;">*</span>
    </label>
    <div class="input-container" part="container">
      <span class="icon-left"><slot name="icon-left"></slot></span>
      <!-- Input element will be inserted here -->
      <span class="icon-right"><slot name="icon-right"></slot></span>
    </div>
    <div class="helper-text" part="helper" style="display: none;"></div>
    <div class="error-message" part="error" role="alert" aria-live="polite" style="display: none;">
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
        <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.28 7.22a.75.75 0 00-1.06 1.06L8.94 10l-1.72 1.72a.75.75 0 101.06 1.06L10 11.06l1.72 1.72a.75.75 0 101.06-1.06L11.06 10l1.72-1.72a.75.75 0 00-1.06-1.06L10 8.94 8.28 7.22z" clip-rule="evenodd" />
      </svg>
      <span class="error-text"></span>
    </div>
  </div>
`;

// Checkbox/Radio template
const checkboxTemplate = document.createElement('template');
checkboxTemplate.innerHTML = `
  <style>
    :host {
      display: block;
    }

    :host([hidden]) {
      display: none;
    }

    .checkbox-wrapper,
    .radio-wrapper {
      display: flex;
      align-items: flex-start;
      gap: var(--vlog-space-2, 0.5rem);
    }

    .checkbox,
    .radio {
      width: 1rem;
      height: 1rem;
      margin-top: 0.125rem;
      accent-color: var(--vlog-primary, #3b82f6);
      cursor: pointer;
      flex-shrink: 0;
    }

    .checkbox:focus-visible,
    .radio:focus-visible {
      outline: none;
      box-shadow:
        0 0 0 var(--vlog-focus-ring-offset, 2px) var(--vlog-bg-primary, #020617),
        0 0 0 calc(var(--vlog-focus-ring-offset, 2px) + var(--vlog-focus-ring-width, 2px)) var(--vlog-focus-ring-color, #3b82f6);
    }

    .checkbox:disabled,
    .radio:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }

    .checkbox-label,
    .radio-label {
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
      font-size: var(--vlog-text-sm, 0.875rem);
      color: var(--vlog-text-primary, #f1f5f9);
      cursor: pointer;
      user-select: none;
    }

    .checkbox-label.disabled,
    .radio-label.disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }

    .helper-text {
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
      font-size: var(--vlog-text-xs, 0.75rem);
      color: var(--vlog-text-tertiary, #94a3b8);
      margin-top: var(--vlog-space-1, 0.25rem);
      margin-left: calc(1rem + var(--vlog-space-2, 0.5rem));
    }
  </style>

  <div class="checkbox-wrapper" part="wrapper">
    <input type="checkbox" class="checkbox" part="input" />
    <label class="checkbox-label" part="label"><slot></slot></label>
  </div>
  <div class="helper-text" part="helper" style="display: none;"></div>
`;

export class VlogInput extends HTMLElement {
  private labelElement!: HTMLLabelElement;
  private labelText!: HTMLSpanElement;
  private requiredIndicator!: HTMLSpanElement;
  private container!: HTMLDivElement;
  private inputElement!: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;
  private helperElement!: HTMLDivElement;
  private errorElement!: HTMLDivElement;
  private errorText!: HTMLSpanElement;
  private internals!: ElementInternals;
  private inputId: string;

  static formAssociated = true;

  static get observedAttributes() {
    return [
      'type', 'size', 'state', 'label', 'placeholder', 'helper-text',
      'error-message', 'disabled', 'readonly', 'required', 'rows',
      'min', 'max', 'step', 'maxlength', 'value', 'name', 'checked'
    ];
  }

  constructor() {
    super();
    this.inputId = `vlog-input-${Math.random().toString(36).substr(2, 9)}`;
    this.attachShadow({ mode: 'open', delegatesFocus: true });
    this.internals = this.attachInternals();
  }

  connectedCallback() {
    this.render();
    this.setupEventListeners();
  }

  disconnectedCallback() {
    // Clean up event listeners if needed
  }

  attributeChangedCallback(name: string, oldValue: string | null, newValue: string | null) {
    if (oldValue === newValue) return;

    // If type changes, we need to re-render
    if (name === 'type') {
      this.render();
      this.setupEventListeners();
      return;
    }

    // Update existing elements
    if (this.inputElement) {
      this.updateFromAttribute(name, newValue);
    }
  }

  private render() {
    const type = this.getAttribute('type') || 'text';

    // Use checkbox template for checkbox/radio
    if (type === 'checkbox' || type === 'radio') {
      this.renderCheckboxOrRadio(type);
      return;
    }

    // Use main template for text/textarea/select
    this.shadowRoot!.innerHTML = '';
    this.shadowRoot!.appendChild(template.content.cloneNode(true));

    this.labelElement = this.shadowRoot!.querySelector('.label')!;
    this.labelText = this.shadowRoot!.querySelector('.label-text')!;
    this.requiredIndicator = this.shadowRoot!.querySelector('.required-indicator')!;
    this.container = this.shadowRoot!.querySelector('.input-container')!;
    this.helperElement = this.shadowRoot!.querySelector('.helper-text')!;
    this.errorElement = this.shadowRoot!.querySelector('.error-message')!;
    this.errorText = this.shadowRoot!.querySelector('.error-text')!;

    // Create appropriate input element
    this.createInputElement(type);

    // Apply all attributes
    this.applyAllAttributes();
  }

  private renderCheckboxOrRadio(type: 'checkbox' | 'radio') {
    this.shadowRoot!.innerHTML = '';
    this.shadowRoot!.appendChild(checkboxTemplate.content.cloneNode(true));

    const wrapper = this.shadowRoot!.querySelector('.checkbox-wrapper')!;
    wrapper.className = type === 'radio' ? 'radio-wrapper' : 'checkbox-wrapper';

    this.inputElement = this.shadowRoot!.querySelector('input')!;
    this.inputElement.type = type;
    this.inputElement.className = type;
    this.inputElement.id = this.inputId;

    const labelEl = this.shadowRoot!.querySelector('.checkbox-label')!;
    labelEl.className = type === 'radio' ? 'radio-label' : 'checkbox-label';
    labelEl.setAttribute('for', this.inputId);

    this.helperElement = this.shadowRoot!.querySelector('.helper-text')!;

    // Apply attributes
    this.applyCheckboxAttributes();
  }

  private createInputElement(type: string) {
    // Remove existing input if any
    const existingInput = this.container.querySelector('.input, .textarea, .select');
    if (existingInput) {
      existingInput.remove();
    }

    const size = this.getAttribute('size') || 'md';
    const state = this.getAttribute('state') || 'default';
    const hasIconLeft = this.querySelector('[slot="icon-left"]') !== null;
    const hasIconRight = this.querySelector('[slot="icon-right"]') !== null;

    if (type === 'textarea') {
      this.inputElement = document.createElement('textarea');
      this.inputElement.className = `textarea size-${size}`;
      if (state !== 'default') {
        this.inputElement.classList.add(`state-${state}`);
      }
      const rows = this.getAttribute('rows');
      if (rows) {
        (this.inputElement as HTMLTextAreaElement).rows = parseInt(rows, 10);
      }
    } else if (type === 'select') {
      this.inputElement = document.createElement('select');
      this.inputElement.className = `select size-${size}`;
      if (state !== 'default') {
        this.inputElement.classList.add(`state-${state}`);
      }
      // Copy slotted options
      this.copyOptionsToSelect();
    } else {
      this.inputElement = document.createElement('input');
      this.inputElement.type = type;
      this.inputElement.className = `input size-${size}`;
      if (state !== 'default') {
        this.inputElement.classList.add(`state-${state}`);
      }
      if (hasIconLeft) {
        this.inputElement.classList.add('has-icon-left');
      }
      if (hasIconRight) {
        this.inputElement.classList.add('has-icon-right');
      }
    }

    this.inputElement.id = this.inputId;
    this.labelElement.setAttribute('for', this.inputId);

    // Insert before icon-right
    const iconRight = this.container.querySelector('.icon-right');
    this.container.insertBefore(this.inputElement, iconRight);
  }

  private copyOptionsToSelect() {
    const select = this.inputElement as HTMLSelectElement;

    // Get all option elements from light DOM
    const options = this.querySelectorAll('option');
    options.forEach(option => {
      const newOption = document.createElement('option');
      newOption.value = option.value;
      newOption.textContent = option.textContent;
      if (option.selected) {
        newOption.selected = true;
      }
      if (option.disabled) {
        newOption.disabled = true;
      }
      select.appendChild(newOption);
    });

    // Watch for changes to slotted options
    const observer = new MutationObserver(() => {
      this.copyOptionsToSelect();
    });
    observer.observe(this, { childList: true, subtree: true });
  }

  private applyAllAttributes() {
    const size = this.getAttribute('size') || 'md';

    // Label
    const label = this.getAttribute('label');
    if (label) {
      this.labelText.textContent = label;
      this.labelElement.classList.add(`size-${size}`);
    } else {
      this.labelElement.style.display = 'none';
    }

    // Required
    if (this.hasAttribute('required')) {
      this.requiredIndicator.style.display = 'inline';
      this.inputElement.required = true;
      this.inputElement.setAttribute('aria-required', 'true');
    }

    // Placeholder
    const placeholder = this.getAttribute('placeholder');
    if (placeholder && 'placeholder' in this.inputElement) {
      (this.inputElement as HTMLInputElement).placeholder = placeholder;
    }

    // Disabled
    if (this.hasAttribute('disabled')) {
      this.inputElement.disabled = true;
    }

    // Readonly
    if (this.hasAttribute('readonly') && 'readOnly' in this.inputElement) {
      (this.inputElement as HTMLInputElement).readOnly = true;
    }

    // Value
    const value = this.getAttribute('value');
    if (value !== null) {
      this.inputElement.value = value;
      this.internals.setFormValue(value);
    }

    // Name
    const name = this.getAttribute('name');
    if (name) {
      this.inputElement.name = name;
    }

    // Min/Max/Step for number inputs
    if (this.inputElement instanceof HTMLInputElement) {
      const min = this.getAttribute('min');
      const max = this.getAttribute('max');
      const step = this.getAttribute('step');
      const maxlength = this.getAttribute('maxlength');

      if (min) this.inputElement.min = min;
      if (max) this.inputElement.max = max;
      if (step) this.inputElement.step = step;
      if (maxlength) this.inputElement.maxLength = parseInt(maxlength, 10);
    }

    // Helper text
    const helperText = this.getAttribute('helper-text');
    if (helperText) {
      this.helperElement.textContent = helperText;
      this.helperElement.style.display = 'block';
      this.helperElement.id = `${this.inputId}-helper`;
    }

    // Error message
    const errorMessage = this.getAttribute('error-message');
    const state = this.getAttribute('state');
    if (errorMessage && state === 'error') {
      this.errorText.textContent = errorMessage;
      this.errorElement.style.display = 'flex';
      this.errorElement.id = `${this.inputId}-error`;
      this.inputElement.setAttribute('aria-invalid', 'true');
      this.inputElement.setAttribute('aria-describedby', `${this.inputId}-error`);
    } else if (helperText) {
      this.inputElement.setAttribute('aria-describedby', `${this.inputId}-helper`);
    }
  }

  private applyCheckboxAttributes() {
    const input = this.inputElement as HTMLInputElement;

    // Disabled
    if (this.hasAttribute('disabled')) {
      input.disabled = true;
      const label = this.shadowRoot!.querySelector('.checkbox-label, .radio-label');
      if (label) label.classList.add('disabled');
    }

    // Checked
    if (this.hasAttribute('checked')) {
      input.checked = true;
    }

    // Value
    const value = this.getAttribute('value');
    if (value) {
      input.value = value;
    }

    // Name (important for radio groups)
    const name = this.getAttribute('name');
    if (name) {
      input.name = name;
    }

    // Helper text
    const helperText = this.getAttribute('helper-text');
    if (helperText) {
      this.helperElement.textContent = helperText;
      this.helperElement.style.display = 'block';
    }
  }

  private updateFromAttribute(name: string, value: string | null) {
    switch (name) {
      case 'size':
        this.updateSizeClass(value || 'md');
        break;
      case 'state':
        this.updateStateClass(value || 'default');
        break;
      case 'label':
        if (this.labelText) {
          this.labelText.textContent = value || '';
          this.labelElement.style.display = value ? 'flex' : 'none';
        }
        break;
      case 'placeholder':
        if ('placeholder' in this.inputElement) {
          (this.inputElement as HTMLInputElement).placeholder = value || '';
        }
        break;
      case 'disabled':
        this.inputElement.disabled = value !== null;
        break;
      case 'readonly':
        if ('readOnly' in this.inputElement) {
          (this.inputElement as HTMLInputElement).readOnly = value !== null;
        }
        break;
      case 'required':
        this.inputElement.required = value !== null;
        this.inputElement.setAttribute('aria-required', value !== null ? 'true' : 'false');
        if (this.requiredIndicator) {
          this.requiredIndicator.style.display = value !== null ? 'inline' : 'none';
        }
        break;
      case 'value':
        if (this.inputElement.value !== value) {
          this.inputElement.value = value || '';
          this.internals.setFormValue(value || '');
        }
        break;
      case 'checked':
        if (this.inputElement instanceof HTMLInputElement &&
            (this.inputElement.type === 'checkbox' || this.inputElement.type === 'radio')) {
          this.inputElement.checked = value !== null;
        }
        break;
      case 'helper-text':
        if (this.helperElement) {
          this.helperElement.textContent = value || '';
          this.helperElement.style.display = value ? 'block' : 'none';
        }
        break;
      case 'error-message':
        if (this.errorElement && this.errorText) {
          const state = this.getAttribute('state');
          this.errorText.textContent = value || '';
          this.errorElement.style.display = (value && state === 'error') ? 'flex' : 'none';
          if (value && state === 'error') {
            this.inputElement.setAttribute('aria-invalid', 'true');
            this.inputElement.setAttribute('aria-describedby', `${this.inputId}-error`);
          } else {
            this.inputElement.removeAttribute('aria-invalid');
          }
        }
        break;
      case 'rows':
        if (this.inputElement instanceof HTMLTextAreaElement && value) {
          this.inputElement.rows = parseInt(value, 10);
        }
        break;
      case 'min':
      case 'max':
      case 'step':
        if (this.inputElement instanceof HTMLInputElement && value) {
          (this.inputElement as any)[name] = value;
        }
        break;
      case 'maxlength':
        if (this.inputElement instanceof HTMLInputElement && value) {
          this.inputElement.maxLength = parseInt(value, 10);
        }
        break;
    }
  }

  private updateSizeClass(size: string) {
    const classList = this.inputElement.classList;
    classList.remove('size-sm', 'size-md', 'size-lg');
    classList.add(`size-${size}`);

    if (this.labelElement) {
      this.labelElement.classList.remove('size-sm', 'size-md', 'size-lg');
      this.labelElement.classList.add(`size-${size}`);
    }
  }

  private updateStateClass(state: string) {
    const classList = this.inputElement.classList;
    classList.remove('state-error', 'state-success', 'state-warning');
    if (state !== 'default') {
      classList.add(`state-${state}`);
    }

    // Update error display
    const errorMessage = this.getAttribute('error-message');
    if (this.errorElement) {
      this.errorElement.style.display = (errorMessage && state === 'error') ? 'flex' : 'none';
    }

    // Update aria-invalid
    if (state === 'error') {
      this.inputElement.setAttribute('aria-invalid', 'true');
    } else {
      this.inputElement.removeAttribute('aria-invalid');
    }
  }

  private setupEventListeners() {
    if (!this.inputElement) return;

    const type = this.getAttribute('type') || 'text';

    this.inputElement.addEventListener('input', (e) => {
      const target = e.target as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;
      const value = type === 'checkbox' || type === 'radio'
        ? (target as HTMLInputElement).checked
        : target.value;

      this.internals.setFormValue(String(value));

      this.dispatchEvent(new CustomEvent('input', {
        detail: { value },
        bubbles: true,
        composed: true
      }));
    });

    this.inputElement.addEventListener('change', (e) => {
      const target = e.target as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;
      const value = type === 'checkbox' || type === 'radio'
        ? (target as HTMLInputElement).checked
        : target.value;

      this.internals.setFormValue(String(value));

      this.dispatchEvent(new CustomEvent('change', {
        detail: { value },
        bubbles: true,
        composed: true
      }));
    });

    this.inputElement.addEventListener('blur', () => {
      const value = type === 'checkbox' || type === 'radio'
        ? (this.inputElement as HTMLInputElement).checked
        : this.inputElement.value;

      this.dispatchEvent(new CustomEvent('blur', {
        detail: { value },
        bubbles: true,
        composed: true
      }));
    });

    this.inputElement.addEventListener('focus', () => {
      this.dispatchEvent(new CustomEvent('focus', {
        detail: {},
        bubbles: true,
        composed: true
      }));
    });
  }

  // Public API
  get value(): string | boolean {
    const type = this.getAttribute('type') || 'text';
    if (type === 'checkbox' || type === 'radio') {
      return (this.inputElement as HTMLInputElement)?.checked ?? false;
    }
    return this.inputElement?.value ?? '';
  }

  set value(val: string | boolean) {
    if (!this.inputElement) return;

    const type = this.getAttribute('type') || 'text';
    if (type === 'checkbox' || type === 'radio') {
      (this.inputElement as HTMLInputElement).checked = Boolean(val);
    } else {
      this.inputElement.value = String(val);
    }
    this.internals.setFormValue(String(val));
  }

  get checked(): boolean {
    return (this.inputElement as HTMLInputElement)?.checked ?? false;
  }

  set checked(val: boolean) {
    if (this.inputElement instanceof HTMLInputElement) {
      this.inputElement.checked = val;
      this.internals.setFormValue(val ? 'on' : '');
    }
  }

  get disabled(): boolean {
    return this.inputElement?.disabled ?? false;
  }

  set disabled(val: boolean) {
    if (this.inputElement) {
      this.inputElement.disabled = val;
    }
    if (val) {
      this.setAttribute('disabled', '');
    } else {
      this.removeAttribute('disabled');
    }
  }

  focus() {
    this.inputElement?.focus();
  }

  blur() {
    this.inputElement?.blur();
  }

  select() {
    if ('select' in this.inputElement) {
      (this.inputElement as HTMLInputElement).select();
    }
  }

  validate(): boolean {
    return this.inputElement?.checkValidity() ?? true;
  }

  // Form-associated callbacks
  formResetCallback() {
    const type = this.getAttribute('type') || 'text';
    if (type === 'checkbox' || type === 'radio') {
      (this.inputElement as HTMLInputElement).checked = false;
    } else {
      this.inputElement.value = '';
    }
    this.internals.setFormValue('');
  }

  formDisabledCallback(disabled: boolean) {
    this.inputElement.disabled = disabled;
  }

  formStateRestoreCallback(state: string) {
    this.value = state;
  }
}

// Register the custom element
customElements.define('vlog-input', VlogInput);
