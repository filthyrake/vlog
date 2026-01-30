/**
 * VLog Badge Web Component
 *
 * A status badge component with semantic colors and accessibility icons.
 * Icons are included by default to ensure colorblind accessibility.
 *
 * @example
 * <vlog-badge variant="success">Ready</vlog-badge>
 * <vlog-badge variant="warning">Processing</vlog-badge>
 * <vlog-badge variant="error">Failed</vlog-badge>
 * <vlog-badge variant="info">Pending</vlog-badge>
 */

// SVG icons for each variant (accessible alternatives to color-only)
const icons = {
  success: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
    <path fill-rule="evenodd" d="M16.704 4.153a.75.75 0 01.143 1.052l-8 10.5a.75.75 0 01-1.127.075l-4.5-4.5a.75.75 0 011.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 011.05-.143z" clip-rule="evenodd" />
  </svg>`,
  warning: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
    <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm.75-13a.75.75 0 00-1.5 0v5a.75.75 0 001.5 0V5zm-1.5 8.25a.75.75 0 011.5 0v.01a.75.75 0 01-1.5 0v-.01z" clip-rule="evenodd" />
  </svg>`,
  error: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
    <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.28 7.22a.75.75 0 00-1.06 1.06L8.94 10l-1.72 1.72a.75.75 0 101.06 1.06L10 11.06l1.72 1.72a.75.75 0 101.06-1.06L11.06 10l1.72-1.72a.75.75 0 00-1.06-1.06L10 8.94 8.28 7.22z" clip-rule="evenodd" />
  </svg>`,
  info: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
    <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm.75-13a.75.75 0 00-1.5 0v5a.75.75 0 001.5 0V5zm-1.5 8.25a.75.75 0 011.5 0v.01a.75.75 0 01-1.5 0v-.01z" clip-rule="evenodd" />
  </svg>`,
  neutral: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
    <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.857-9.809a.75.75 0 00-1.214-.882l-3.483 4.79-1.88-1.88a.75.75 0 10-1.06 1.061l2.5 2.5a.75.75 0 001.137-.089l4-5.5z" clip-rule="evenodd" />
  </svg>`,
  processing: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true" class="spin">
    <path fill-rule="evenodd" d="M15.312 11.424a5.5 5.5 0 01-9.201 2.466l-.312-.311h2.433a.75.75 0 000-1.5H3.989a.75.75 0 00-.75.75v4.242a.75.75 0 001.5 0v-2.43l.31.31a7 7 0 0011.712-3.138.75.75 0 00-1.449-.39zm1.23-3.723a.75.75 0 00.219-.53V2.929a.75.75 0 00-1.5 0V5.36l-.31-.31A7 7 0 003.239 8.188a.75.75 0 101.448.389A5.5 5.5 0 0113.89 6.11l.311.31h-2.432a.75.75 0 000 1.5h4.243a.75.75 0 00.53-.219z" clip-rule="evenodd" />
  </svg>`,
  pending: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
    <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm.75-13a.75.75 0 00-1.5 0v5c0 .414.336.75.75.75h4a.75.75 0 000-1.5h-3.25V5z" clip-rule="evenodd" />
  </svg>`,
};

const template = document.createElement('template');
template.innerHTML = `
  <style>
    :host {
      display: inline-flex;
    }

    :host([hidden]) {
      display: none;
    }

    .badge {
      display: inline-flex;
      align-items: center;
      gap: var(--vlog-space-1, 0.25rem);
      border-radius: var(--vlog-radius-md, 0.375rem);
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
      font-weight: var(--vlog-font-medium, 500);
      white-space: nowrap;
    }

    /* Size variants */
    .badge.size-sm {
      padding: 0.125rem 0.375rem;
      font-size: var(--vlog-text-xs, 0.75rem);
    }

    .badge.size-md {
      padding: 0.25rem 0.5rem;
      font-size: var(--vlog-text-sm, 0.875rem);
    }

    /* Variant styles */
    .badge.variant-success {
      background-color: var(--vlog-success-bg, rgba(34, 197, 94, 0.15));
      color: var(--vlog-success-text, #86efac);
    }

    .badge.variant-warning {
      background-color: var(--vlog-warning-bg, rgba(234, 179, 8, 0.15));
      color: var(--vlog-warning-text, #fde047);
    }

    .badge.variant-error {
      background-color: var(--vlog-error-bg, rgba(239, 68, 68, 0.15));
      color: var(--vlog-error-text, #fca5a5);
    }

    .badge.variant-info {
      background-color: var(--vlog-info-bg, rgba(6, 182, 212, 0.15));
      color: var(--vlog-info-text, #67e8f9);
    }

    .badge.variant-neutral {
      background-color: var(--vlog-bg-tertiary, #1e293b);
      color: var(--vlog-text-tertiary, #94a3b8);
    }

    .badge.variant-processing {
      background-color: var(--vlog-warning-bg, rgba(234, 179, 8, 0.15));
      color: var(--vlog-warning-text, #fde047);
    }

    .badge.variant-pending {
      background-color: var(--vlog-info-bg, rgba(6, 182, 212, 0.15));
      color: var(--vlog-info-text, #67e8f9);
    }

    /* Icon styles */
    .icon {
      display: flex;
      align-items: center;
      flex-shrink: 0;
    }

    .icon svg {
      width: 1em;
      height: 1em;
    }

    .icon svg.spin {
      animation: spin 1s linear infinite;
    }

    @keyframes spin {
      to {
        transform: rotate(360deg);
      }
    }

    /* Dot indicator (alternative to icon) */
    .dot {
      width: 0.5em;
      height: 0.5em;
      border-radius: 50%;
      flex-shrink: 0;
    }

    .variant-success .dot {
      background-color: var(--vlog-success, #22c55e);
    }

    .variant-warning .dot,
    .variant-processing .dot {
      background-color: var(--vlog-warning, #eab308);
    }

    .variant-error .dot {
      background-color: var(--vlog-error, #ef4444);
    }

    .variant-info .dot,
    .variant-pending .dot {
      background-color: var(--vlog-info, #06b6d4);
    }

    .variant-neutral .dot {
      background-color: var(--vlog-text-tertiary, #94a3b8);
    }

    /* Pulsing dot for processing */
    .variant-processing .dot {
      animation: pulse 2s ease-in-out infinite;
    }

    @keyframes pulse {
      0%, 100% {
        opacity: 1;
      }
      50% {
        opacity: 0.5;
      }
    }
  </style>
  <span class="badge" role="status">
    <span class="icon"></span>
    <slot></slot>
  </span>
`;

export class VlogBadge extends HTMLElement {
  private badge: HTMLSpanElement;
  private iconContainer: HTMLSpanElement;

  static get observedAttributes() {
    return ['variant', 'size', 'no-icon', 'dot'];
  }

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot!.appendChild(template.content.cloneNode(true));
    this.badge = this.shadowRoot!.querySelector('.badge')!;
    this.iconContainer = this.shadowRoot!.querySelector('.icon')!;
  }

  connectedCallback() {
    this.updateStyles();
  }

  attributeChangedCallback(_name: string, _oldValue: string | null, _newValue: string | null) {
    this.updateStyles();
  }

  private updateStyles() {
    const variant = this.getAttribute('variant') || 'neutral';
    const size = this.getAttribute('size') || 'sm';
    const noIcon = this.hasAttribute('no-icon');
    const useDot = this.hasAttribute('dot');

    // Clear existing classes
    this.badge.className = 'badge';

    // Add variant and size classes
    this.badge.classList.add(`variant-${variant}`);
    this.badge.classList.add(`size-${size}`);

    // Update icon/dot
    if (noIcon) {
      this.iconContainer.innerHTML = '';
      this.iconContainer.style.display = 'none';
    } else if (useDot) {
      this.iconContainer.innerHTML = '<span class="dot"></span>';
      this.iconContainer.style.display = 'flex';
    } else {
      const iconHtml = icons[variant as keyof typeof icons] || icons.neutral;
      this.iconContainer.innerHTML = iconHtml;
      this.iconContainer.style.display = 'flex';
    }
  }

  // Getters and setters
  get variant(): string {
    return this.getAttribute('variant') || 'neutral';
  }

  set variant(value: string) {
    this.setAttribute('variant', value);
  }

  get size(): string {
    return this.getAttribute('size') || 'sm';
  }

  set size(value: string) {
    this.setAttribute('size', value);
  }
}

// Register the custom element
customElements.define('vlog-badge', VlogBadge);
