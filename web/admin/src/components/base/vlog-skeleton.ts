/**
 * VLog Skeleton Web Component
 *
 * A loading placeholder component with shimmer animation.
 * Used to indicate content is loading while preserving layout.
 *
 * @example
 * <vlog-skeleton variant="text"></vlog-skeleton>
 * <vlog-skeleton variant="avatar"></vlog-skeleton>
 * <vlog-skeleton variant="thumbnail" width="120px" height="68px"></vlog-skeleton>
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

    .skeleton {
      background: linear-gradient(
        90deg,
        var(--vlog-bg-tertiary, #1e293b) 25%,
        var(--vlog-bg-secondary, #334155) 50%,
        var(--vlog-bg-tertiary, #1e293b) 75%
      );
      background-size: 200% 100%;
      animation: shimmer 1.5s ease-in-out infinite;
      border-radius: var(--vlog-radius-md, 0.375rem);
    }

    @keyframes shimmer {
      0% {
        background-position: 200% 0;
      }
      100% {
        background-position: -200% 0;
      }
    }

    /* Variant styles */
    .skeleton.variant-text {
      height: 1em;
      width: 100%;
    }

    .skeleton.variant-text-block {
      height: 4em;
      width: 100%;
    }

    .skeleton.variant-avatar {
      width: 2.5rem;
      height: 2.5rem;
      border-radius: 50%;
    }

    .skeleton.variant-avatar.size-sm {
      width: 2rem;
      height: 2rem;
    }

    .skeleton.variant-avatar.size-lg {
      width: 3rem;
      height: 3rem;
    }

    .skeleton.variant-card {
      height: 8rem;
      width: 100%;
    }

    .skeleton.variant-table-row {
      height: 3rem;
      width: 100%;
    }

    .skeleton.variant-thumbnail {
      width: 120px;
      height: 68px;
      border-radius: var(--vlog-radius-lg, 0.5rem);
    }

    .skeleton.variant-button {
      height: 2.25rem;
      width: 6rem;
      border-radius: var(--vlog-radius-md, 0.375rem);
    }

    /* Reduced motion */
    @media (prefers-reduced-motion: reduce) {
      .skeleton {
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
    }
  </style>

  <div class="skeleton" part="skeleton" role="presentation" aria-hidden="true"></div>
`;

export type SkeletonVariant = 'text' | 'text-block' | 'avatar' | 'card' | 'table-row' | 'thumbnail' | 'button';
export type SkeletonSize = 'sm' | 'md' | 'lg';

export class VlogSkeleton extends HTMLElement {
  private skeleton!: HTMLDivElement;

  static get observedAttributes() {
    return ['variant', 'size', 'width', 'height'];
  }

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot!.appendChild(template.content.cloneNode(true));
    this.skeleton = this.shadowRoot!.querySelector('.skeleton')!;
  }

  connectedCallback() {
    this.updateStyles();
  }

  attributeChangedCallback(_name: string, _oldValue: string | null, _newValue: string | null) {
    this.updateStyles();
  }

  private updateStyles() {
    const variant = this.getAttribute('variant') || 'text';
    const size = this.getAttribute('size') || 'md';
    const width = this.getAttribute('width');
    const height = this.getAttribute('height');

    // Clear existing classes
    this.skeleton.className = 'skeleton';

    // Add variant class
    this.skeleton.classList.add(`variant-${variant}`);

    // Add size class for avatar
    if (variant === 'avatar') {
      this.skeleton.classList.add(`size-${size}`);
    }

    // Apply custom dimensions (clear if attribute removed)
    this.skeleton.style.width = width || '';
    this.skeleton.style.height = height || '';
  }

  // Getters and setters
  get variant(): SkeletonVariant {
    return (this.getAttribute('variant') as SkeletonVariant) || 'text';
  }

  set variant(value: SkeletonVariant) {
    this.setAttribute('variant', value);
  }

  get size(): SkeletonSize {
    return (this.getAttribute('size') as SkeletonSize) || 'md';
  }

  set size(value: SkeletonSize) {
    this.setAttribute('size', value);
  }
}

// Register the custom element
customElements.define('vlog-skeleton', VlogSkeleton);
