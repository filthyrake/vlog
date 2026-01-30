/**
 * VLog Progress Web Component
 *
 * A progress indicator supporting linear and circular types,
 * with determinate and indeterminate states.
 *
 * Uses constructable stylesheets for CSP compliance.
 *
 * @example
 * <vlog-progress type="linear" value="45" show-label></vlog-progress>
 * <vlog-progress type="circular" value="75" size="lg"></vlog-progress>
 * <vlog-progress type="linear" indeterminate></vlog-progress>
 */

// CSS extracted for constructable stylesheets (CSP-compliant)
const styles = `
  :host {
    display: inline-flex;
    align-items: center;
    gap: var(--vlog-space-2, 0.5rem);
  }

  :host([hidden]) {
    display: none;
  }

  :host([type="linear"]) {
    display: flex;
    width: 100%;
  }

  /* Linear progress container */
  .linear-container {
    flex: 1;
    display: flex;
    align-items: center;
    gap: var(--vlog-space-2, 0.5rem);
  }

  .linear-track {
    flex: 1;
    height: 0.5rem;
    background-color: var(--vlog-bg-elevated, #334155);
    border-radius: var(--vlog-radius-full, 9999px);
    overflow: hidden;
  }

  .linear-track.size-sm {
    height: 0.25rem;
  }

  .linear-track.size-lg {
    height: 0.75rem;
  }

  .linear-fill {
    height: 100%;
    border-radius: var(--vlog-radius-full, 9999px);
    transition: width var(--vlog-transition-slow, 300ms ease);
  }

  /* Width classes for CSP compliance (generated for 0-100%) */
  .w-0 { width: 0%; } .w-1 { width: 1%; } .w-2 { width: 2%; } .w-3 { width: 3%; } .w-4 { width: 4%; }
  .w-5 { width: 5%; } .w-6 { width: 6%; } .w-7 { width: 7%; } .w-8 { width: 8%; } .w-9 { width: 9%; }
  .w-10 { width: 10%; } .w-11 { width: 11%; } .w-12 { width: 12%; } .w-13 { width: 13%; } .w-14 { width: 14%; }
  .w-15 { width: 15%; } .w-16 { width: 16%; } .w-17 { width: 17%; } .w-18 { width: 18%; } .w-19 { width: 19%; }
  .w-20 { width: 20%; } .w-21 { width: 21%; } .w-22 { width: 22%; } .w-23 { width: 23%; } .w-24 { width: 24%; }
  .w-25 { width: 25%; } .w-26 { width: 26%; } .w-27 { width: 27%; } .w-28 { width: 28%; } .w-29 { width: 29%; }
  .w-30 { width: 30%; } .w-31 { width: 31%; } .w-32 { width: 32%; } .w-33 { width: 33%; } .w-34 { width: 34%; }
  .w-35 { width: 35%; } .w-36 { width: 36%; } .w-37 { width: 37%; } .w-38 { width: 38%; } .w-39 { width: 39%; }
  .w-40 { width: 40%; } .w-41 { width: 41%; } .w-42 { width: 42%; } .w-43 { width: 43%; } .w-44 { width: 44%; }
  .w-45 { width: 45%; } .w-46 { width: 46%; } .w-47 { width: 47%; } .w-48 { width: 48%; } .w-49 { width: 49%; }
  .w-50 { width: 50%; } .w-51 { width: 51%; } .w-52 { width: 52%; } .w-53 { width: 53%; } .w-54 { width: 54%; }
  .w-55 { width: 55%; } .w-56 { width: 56%; } .w-57 { width: 57%; } .w-58 { width: 58%; } .w-59 { width: 59%; }
  .w-60 { width: 60%; } .w-61 { width: 61%; } .w-62 { width: 62%; } .w-63 { width: 63%; } .w-64 { width: 64%; }
  .w-65 { width: 65%; } .w-66 { width: 66%; } .w-67 { width: 67%; } .w-68 { width: 68%; } .w-69 { width: 69%; }
  .w-70 { width: 70%; } .w-71 { width: 71%; } .w-72 { width: 72%; } .w-73 { width: 73%; } .w-74 { width: 74%; }
  .w-75 { width: 75%; } .w-76 { width: 76%; } .w-77 { width: 77%; } .w-78 { width: 78%; } .w-79 { width: 79%; }
  .w-80 { width: 80%; } .w-81 { width: 81%; } .w-82 { width: 82%; } .w-83 { width: 83%; } .w-84 { width: 84%; }
  .w-85 { width: 85%; } .w-86 { width: 86%; } .w-87 { width: 87%; } .w-88 { width: 88%; } .w-89 { width: 89%; }
  .w-90 { width: 90%; } .w-91 { width: 91%; } .w-92 { width: 92%; } .w-93 { width: 93%; } .w-94 { width: 94%; }
  .w-95 { width: 95%; } .w-96 { width: 96%; } .w-97 { width: 97%; } .w-98 { width: 98%; } .w-99 { width: 99%; }
  .w-100 { width: 100%; }

  /* Variant colors */
  .linear-fill.variant-primary {
    background-color: var(--vlog-primary, #3b82f6);
  }

  .linear-fill.variant-success {
    background-color: var(--vlog-success, #22c55e);
  }

  .linear-fill.variant-warning {
    background-color: var(--vlog-warning, #eab308);
  }

  .linear-fill.variant-error {
    background-color: var(--vlog-error, #ef4444);
  }

  /* Indeterminate animation */
  .linear-fill.indeterminate {
    width: 50% !important;
    animation: linear-indeterminate 1.5s ease-in-out infinite;
  }

  @keyframes linear-indeterminate {
    0% {
      transform: translateX(-100%);
    }
    100% {
      transform: translateX(200%);
    }
  }

  /* Label */
  .label {
    font-family: var(--vlog-font-sans, system-ui, sans-serif);
    font-size: var(--vlog-text-sm, 0.875rem);
    font-weight: var(--vlog-font-medium, 500);
    color: var(--vlog-text-secondary, #cbd5e1);
    white-space: nowrap;
    min-width: 2.5rem;
    text-align: right;
  }

  .label.size-sm {
    font-size: var(--vlog-text-xs, 0.75rem);
    min-width: 2rem;
  }

  .label.size-lg {
    font-size: var(--vlog-text-base, 1rem);
    min-width: 3rem;
  }

  /* Circular progress */
  .circular-container {
    position: relative;
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }

  .circular-container.size-sm {
    width: 2rem;
    height: 2rem;
  }

  .circular-container.size-md {
    width: 3rem;
    height: 3rem;
  }

  .circular-container.size-lg {
    width: 4rem;
    height: 4rem;
  }

  .circular-svg {
    width: 100%;
    height: 100%;
    transform: rotate(-90deg);
  }

  .circular-track {
    fill: none;
    stroke: var(--vlog-bg-elevated, #334155);
  }

  .circular-fill {
    fill: none;
    transition: stroke-dashoffset var(--vlog-transition-slow, 300ms ease);
  }

  .circular-fill.variant-primary {
    stroke: var(--vlog-primary, #3b82f6);
  }

  .circular-fill.variant-success {
    stroke: var(--vlog-success, #22c55e);
  }

  .circular-fill.variant-warning {
    stroke: var(--vlog-warning, #eab308);
  }

  .circular-fill.variant-error {
    stroke: var(--vlog-error, #ef4444);
  }

  /* Circular indeterminate */
  .circular-svg.indeterminate {
    animation: circular-rotate 2s linear infinite;
  }

  .circular-svg.indeterminate .circular-fill {
    stroke-dasharray: 80, 200;
    stroke-dashoffset: 0;
    animation: circular-dash 1.5s ease-in-out infinite;
  }

  @keyframes circular-rotate {
    100% {
      transform: rotate(270deg);
    }
  }

  @keyframes circular-dash {
    0% {
      stroke-dasharray: 1, 200;
      stroke-dashoffset: 0;
    }
    50% {
      stroke-dasharray: 100, 200;
      stroke-dashoffset: -15;
    }
    100% {
      stroke-dasharray: 100, 200;
      stroke-dashoffset: -125;
    }
  }

  /* Circular label */
  .circular-label {
    position: absolute;
    font-family: var(--vlog-font-sans, system-ui, sans-serif);
    font-weight: var(--vlog-font-semibold, 600);
    color: var(--vlog-text-primary, #f1f5f9);
  }

  .circular-label.size-sm {
    font-size: var(--vlog-text-xs, 0.75rem);
  }

  .circular-label.size-md {
    font-size: var(--vlog-text-sm, 0.875rem);
  }

  .circular-label.size-lg {
    font-size: var(--vlog-text-base, 1rem);
  }

  /* Screen reader announcements */
  .sr-announcement {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border-width: 0;
  }

  /* Reduced motion */
  @media (prefers-reduced-motion: reduce) {
    .linear-fill {
      transition: none;
    }

    .linear-fill.indeterminate {
      animation: none;
      width: 100% !important;
      opacity: 0.5;
    }

    .circular-svg.indeterminate {
      animation: none;
    }

    .circular-svg.indeterminate .circular-fill {
      animation: none;
      stroke-dasharray: 50, 200;
    }
  }
`;

// Create constructable stylesheet (CSP-compliant)
const sheet = new CSSStyleSheet();
sheet.replaceSync(styles);

// HTML template without styles
const template = document.createElement('template');
template.innerHTML = `
  <div class="progress-wrapper" part="wrapper">
    <!-- Content will be rendered based on type -->
  </div>
  <div class="sr-announcement" aria-live="polite" aria-atomic="true"></div>
`;

// Valid values for size and variant attributes
const VALID_SIZES = ['sm', 'md', 'lg'] as const;
const VALID_VARIANTS = ['primary', 'success', 'warning', 'error'] as const;

type Size = typeof VALID_SIZES[number];
type Variant = typeof VALID_VARIANTS[number];

export class VlogProgress extends HTMLElement {
  private wrapper!: HTMLDivElement;
  private announcement!: HTMLDivElement;
  private lastAnnouncedValue: number = -1;

  static get observedAttributes() {
    return ['type', 'value', 'size', 'variant', 'indeterminate', 'show-label'];
  }

  // Validate and sanitize size attribute
  private validateSize(size: string | null): Size {
    if (size && VALID_SIZES.includes(size as Size)) {
      return size as Size;
    }
    return 'md';
  }

  // Validate and sanitize variant attribute
  private validateVariant(variant: string | null): Variant {
    if (variant && VALID_VARIANTS.includes(variant as Variant)) {
      return variant as Variant;
    }
    return 'primary';
  }

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });

    // Use adoptedStyleSheets for CSP compliance
    this.shadowRoot!.adoptedStyleSheets = [sheet];
    this.shadowRoot!.appendChild(template.content.cloneNode(true));

    this.wrapper = this.shadowRoot!.querySelector('.progress-wrapper')!;
    this.announcement = this.shadowRoot!.querySelector('.sr-announcement')!;
  }

  connectedCallback() {
    this.render();
  }

  attributeChangedCallback(name: string, oldValue: string | null, newValue: string | null) {
    if (oldValue === newValue) return;
    this.render();

    // Announce progress milestones (every 10%) for screen readers
    if (name === 'value' && !this.hasAttribute('indeterminate')) {
      const value = parseInt(newValue || '0', 10);
      const milestone = Math.floor(value / 10) * 10;

      if (milestone !== this.lastAnnouncedValue && milestone > 0) {
        this.lastAnnouncedValue = milestone;
        this.announceProgress(value);
      }

      // Always announce completion
      if (value >= 100 && this.lastAnnouncedValue !== 100) {
        this.lastAnnouncedValue = 100;
        this.announceCompletion();
      }
    }
  }

  private render() {
    const type = this.getAttribute('type') || 'linear';
    const value = Math.min(100, Math.max(0, parseInt(this.getAttribute('value') || '0', 10)));
    // Validate size and variant to prevent XSS via innerHTML interpolation
    const size = this.validateSize(this.getAttribute('size'));
    const variant = this.validateVariant(this.getAttribute('variant'));
    const indeterminate = this.hasAttribute('indeterminate');
    const showLabel = this.hasAttribute('show-label');

    if (type === 'circular') {
      this.renderCircular(value, size, variant, indeterminate, showLabel);
    } else {
      this.renderLinear(value, size, variant, indeterminate, showLabel);
    }
  }

  private renderLinear(
    value: number,
    size: Size,
    variant: Variant,
    indeterminate: boolean,
    showLabel: boolean
  ) {
    const ariaAttrs = indeterminate
      ? 'aria-valuemin="0" aria-valuemax="100"'
      : `aria-valuenow="${value}" aria-valuemin="0" aria-valuemax="100"`;

    this.wrapper.innerHTML = `
      <div class="linear-container">
        <div
          class="linear-track size-${size}"
          role="progressbar"
          ${ariaAttrs}
          aria-label="${indeterminate ? 'Loading' : `${value}% complete`}"
          part="track"
        >
          <div
            class="linear-fill variant-${variant}${indeterminate ? ' indeterminate' : ` w-${value}`}"
            part="fill"
          ></div>
        </div>
        ${showLabel && !indeterminate ? `
          <span class="label size-${size}" part="label">${value}%</span>
        ` : ''}
      </div>
    `;
  }

  private renderCircular(
    value: number,
    size: Size,
    variant: Variant,
    indeterminate: boolean,
    showLabel: boolean
  ) {
    // Calculate SVG dimensions based on size
    const dimensions = {
      sm: { size: 32, strokeWidth: 3, radius: 12 },
      md: { size: 48, strokeWidth: 4, radius: 18 },
      lg: { size: 64, strokeWidth: 5, radius: 24 }
    };

    const dim = dimensions[size as keyof typeof dimensions] || dimensions.md;
    const circumference = 2 * Math.PI * dim.radius;
    const offset = circumference - (value / 100) * circumference;

    const ariaAttrs = indeterminate
      ? 'aria-valuemin="0" aria-valuemax="100"'
      : `aria-valuenow="${value}" aria-valuemin="0" aria-valuemax="100"`;

    this.wrapper.innerHTML = `
      <div class="circular-container size-${size}">
        <svg
          class="circular-svg${indeterminate ? ' indeterminate' : ''}"
          viewBox="0 0 ${dim.size} ${dim.size}"
          role="progressbar"
          ${ariaAttrs}
          aria-label="${indeterminate ? 'Loading' : `${value}% complete`}"
          part="svg"
        >
          <circle
            class="circular-track"
            cx="${dim.size / 2}"
            cy="${dim.size / 2}"
            r="${dim.radius}"
            stroke-width="${dim.strokeWidth}"
            part="track"
          />
          <circle
            class="circular-fill variant-${variant}"
            cx="${dim.size / 2}"
            cy="${dim.size / 2}"
            r="${dim.radius}"
            stroke-width="${dim.strokeWidth}"
            stroke-linecap="round"
            stroke-dasharray="${circumference}"
            stroke-dashoffset="${indeterminate ? 0 : offset}"
            part="fill"
          />
        </svg>
        ${showLabel && !indeterminate ? `
          <span class="circular-label size-${size}" part="label">${value}%</span>
        ` : ''}
      </div>
    `;
  }

  private announceProgress(value: number) {
    const label = this.getAttribute('aria-label') || 'Progress';
    this.announcement.textContent = `${label}: ${value}% complete`;
  }

  private announceCompletion() {
    const label = this.getAttribute('aria-label') || 'Progress';
    this.announcement.textContent = `${label}: Complete`;

    this.dispatchEvent(new CustomEvent('complete', {
      detail: { value: 100 },
      bubbles: true,
      composed: true
    }));
  }

  // Public API
  get value(): number {
    return parseInt(this.getAttribute('value') || '0', 10);
  }

  set value(val: number) {
    const clampedValue = Math.min(100, Math.max(0, val));
    this.setAttribute('value', String(clampedValue));
  }

  get type(): string {
    return this.getAttribute('type') || 'linear';
  }

  set type(val: string) {
    this.setAttribute('type', val);
  }

  get size(): string {
    return this.getAttribute('size') || 'md';
  }

  set size(val: string) {
    this.setAttribute('size', val);
  }

  get variant(): string {
    return this.getAttribute('variant') || 'primary';
  }

  set variant(val: string) {
    this.setAttribute('variant', val);
  }

  get indeterminate(): boolean {
    return this.hasAttribute('indeterminate');
  }

  set indeterminate(val: boolean) {
    if (val) {
      this.setAttribute('indeterminate', '');
    } else {
      this.removeAttribute('indeterminate');
    }
  }

  setValue(value: number) {
    this.value = value;
  }

  reset() {
    this.value = 0;
    this.lastAnnouncedValue = -1;
  }
}

// Register the custom element
customElements.define('vlog-progress', VlogProgress);
