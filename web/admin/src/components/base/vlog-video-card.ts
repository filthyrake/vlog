/**
 * VLog Video Card Web Component
 *
 * A card component for displaying video information on mobile.
 * Alternative to table rows for smaller screens.
 *
 * @example
 * <vlog-video-card
 *   title="My Video"
 *   thumbnail="/thumb.jpg"
 *   status="ready"
 *   duration="12:34"
 * ></vlog-video-card>
 *
 * @fires click - When the card is clicked
 * @fires action - When an action button is clicked
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
      gap: var(--vlog-space-3, 0.75rem);
      padding: var(--vlog-space-3, 0.75rem);
      border: 1px solid var(--vlog-border-secondary, #334155);
      border-radius: var(--vlog-radius-lg, 0.5rem);
      background-color: var(--vlog-bg-secondary, #0f172a);
      cursor: pointer;
      transition: var(--vlog-transition-colors);
    }

    .card:hover {
      border-color: var(--vlog-border-primary, #475569);
      background-color: var(--vlog-bg-tertiary, #1e293b);
    }

    .card:focus-visible {
      outline: none;
      border-color: var(--vlog-primary, #3b82f6);
      box-shadow: 0 0 0 var(--vlog-focus-ring-width, 2px) var(--vlog-focus-ring-color, rgba(59, 130, 246, 0.5));
    }

    .thumbnail-container {
      position: relative;
      flex-shrink: 0;
      width: 120px;
      height: 68px;
      border-radius: var(--vlog-radius-md, 0.375rem);
      overflow: hidden;
      background-color: var(--vlog-bg-tertiary, #1e293b);
    }

    .thumbnail {
      width: 100%;
      height: 100%;
      object-fit: cover;
    }

    .thumbnail-placeholder {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 100%;
      height: 100%;
      color: var(--vlog-text-tertiary, #94a3b8);
    }

    .thumbnail-placeholder svg {
      width: 32px;
      height: 32px;
    }

    .duration {
      position: absolute;
      bottom: var(--vlog-space-1, 0.25rem);
      right: var(--vlog-space-1, 0.25rem);
      padding: 0.125rem 0.375rem;
      border-radius: var(--vlog-radius-sm, 0.25rem);
      background-color: rgba(0, 0, 0, 0.75);
      font-family: var(--vlog-font-mono, ui-monospace, monospace);
      font-size: var(--vlog-text-xs, 0.75rem);
      color: white;
    }

    .content {
      flex: 1;
      min-width: 0;
      display: flex;
      flex-direction: column;
      gap: var(--vlog-space-1, 0.25rem);
    }

    .title {
      margin: 0;
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
      font-size: var(--vlog-text-sm, 0.875rem);
      font-weight: var(--vlog-font-medium, 500);
      color: var(--vlog-text-primary, #f1f5f9);
      line-height: 1.4;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }

    .meta {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: var(--vlog-space-2, 0.5rem);
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
      font-size: var(--vlog-text-xs, 0.75rem);
      color: var(--vlog-text-tertiary, #94a3b8);
    }

    .meta-item {
      display: flex;
      align-items: center;
      gap: var(--vlog-space-1, 0.25rem);
    }

    .meta-item svg {
      width: 12px;
      height: 12px;
    }

    .status-badge {
      display: inline-flex;
      align-items: center;
      gap: var(--vlog-space-1, 0.25rem);
      padding: 0.125rem 0.375rem;
      border-radius: var(--vlog-radius-sm, 0.25rem);
      font-size: var(--vlog-text-xs, 0.75rem);
      font-weight: var(--vlog-font-medium, 500);
    }

    .status-ready {
      background-color: var(--vlog-success-bg, rgba(34, 197, 94, 0.15));
      color: var(--vlog-success-text, #86efac);
    }

    .status-processing {
      background-color: var(--vlog-warning-bg, rgba(234, 179, 8, 0.15));
      color: var(--vlog-warning-text, #fde047);
    }

    .status-failed {
      background-color: var(--vlog-error-bg, rgba(239, 68, 68, 0.15));
      color: var(--vlog-error-text, #fca5a5);
    }

    .status-pending {
      background-color: var(--vlog-info-bg, rgba(6, 182, 212, 0.15));
      color: var(--vlog-info-text, #67e8f9);
    }

    .actions {
      display: flex;
      align-items: flex-start;
      gap: var(--vlog-space-1, 0.25rem);
    }

    .action-button {
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

    .action-button:hover {
      background-color: var(--vlog-bg-primary, #020617);
      color: var(--vlog-text-primary, #f1f5f9);
    }

    .action-button:focus-visible {
      outline: none;
      box-shadow: 0 0 0 var(--vlog-focus-ring-width, 2px) var(--vlog-focus-ring-color, rgba(59, 130, 246, 0.5));
    }

    .action-button svg {
      width: 18px;
      height: 18px;
    }

    /* Compact variant */
    :host([compact]) .card {
      padding: var(--vlog-space-2, 0.5rem);
    }

    :host([compact]) .thumbnail-container {
      width: 80px;
      height: 45px;
    }

    /* Show on mobile only by default */
    @media (min-width: 768px) {
      :host(:not([always-visible])) {
        display: none;
      }
    }
  </style>

  <article class="card" part="card" tabindex="0" role="button">
    <div class="thumbnail-container" part="thumbnail-container">
      <div class="thumbnail-placeholder" part="placeholder">
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" d="m15.75 10.5 4.72-4.72a.75.75 0 0 1 1.28.53v11.38a.75.75 0 0 1-1.28.53l-4.72-4.72M4.5 18.75h9a2.25 2.25 0 0 0 2.25-2.25v-9a2.25 2.25 0 0 0-2.25-2.25h-9A2.25 2.25 0 0 0 2.25 7.5v9a2.25 2.25 0 0 0 2.25 2.25Z" />
        </svg>
      </div>
      <span class="duration" part="duration"></span>
    </div>
    <div class="content" part="content">
      <h3 class="title" part="title"></h3>
      <div class="meta" part="meta">
        <span class="status-badge" part="status"></span>
        <slot name="meta"></slot>
      </div>
    </div>
    <div class="actions" part="actions">
      <slot name="actions"></slot>
    </div>
  </article>
`;

export class VlogVideoCard extends HTMLElement {
  private card!: HTMLElement;
  private thumbnailContainer!: HTMLDivElement;
  private placeholder!: HTMLDivElement;
  private durationElement!: HTMLSpanElement;
  private titleElement!: HTMLHeadingElement;
  private statusElement!: HTMLSpanElement;

  static get observedAttributes() {
    return ['title', 'thumbnail', 'status', 'duration', 'category', 'views'];
  }

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot!.appendChild(template.content.cloneNode(true));

    this.card = this.shadowRoot!.querySelector('.card')!;
    this.thumbnailContainer = this.shadowRoot!.querySelector('.thumbnail-container')!;
    this.placeholder = this.shadowRoot!.querySelector('.thumbnail-placeholder')!;
    this.durationElement = this.shadowRoot!.querySelector('.duration')!;
    this.titleElement = this.shadowRoot!.querySelector('.title')!;
    this.statusElement = this.shadowRoot!.querySelector('.status-badge')!;

    this.handleClick = this.handleClick.bind(this);
    this.handleKeyDown = this.handleKeyDown.bind(this);
  }

  connectedCallback() {
    this.updateContent();
    this.card.addEventListener('click', this.handleClick);
    this.card.addEventListener('keydown', this.handleKeyDown);
  }

  disconnectedCallback() {
    this.card.removeEventListener('click', this.handleClick);
    this.card.removeEventListener('keydown', this.handleKeyDown);
  }

  attributeChangedCallback(_name: string, _oldValue: string | null, _newValue: string | null) {
    this.updateContent();
  }

  private updateContent() {
    const title = this.getAttribute('title') || '';
    const thumbnail = this.getAttribute('thumbnail');
    const status = this.getAttribute('status') || 'pending';
    const duration = this.getAttribute('duration');

    // Update title
    this.titleElement.textContent = title;

    // Update thumbnail
    if (thumbnail) {
      const existingImg = this.thumbnailContainer.querySelector('img');
      if (existingImg) {
        existingImg.src = thumbnail;
      } else {
        const img = document.createElement('img');
        img.className = 'thumbnail';
        img.src = thumbnail;
        img.alt = title;
        img.loading = 'lazy';
        this.placeholder.style.display = 'none';
        this.thumbnailContainer.insertBefore(img, this.thumbnailContainer.firstChild);
      }
    } else {
      const existingImg = this.thumbnailContainer.querySelector('img');
      if (existingImg) {
        existingImg.remove();
      }
      this.placeholder.style.display = 'flex';
    }

    // Update duration
    if (duration) {
      this.durationElement.textContent = duration;
      this.durationElement.style.display = 'block';
    } else {
      this.durationElement.style.display = 'none';
    }

    // Update status badge
    this.statusElement.className = `status-badge status-${status}`;
    this.statusElement.textContent = status.charAt(0).toUpperCase() + status.slice(1);
  }

  private handleClick(e: Event) {
    // Don't emit click if clicking action buttons
    if ((e.target as HTMLElement).closest('[slot="actions"]')) {
      return;
    }

    this.dispatchEvent(
      new CustomEvent('card-click', {
        detail: { id: this.id },
        bubbles: true,
        composed: true,
      })
    );
  }

  private handleKeyDown(e: KeyboardEvent) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      this.handleClick(e);
    }
  }

  // Public API
  get title(): string {
    return this.getAttribute('title') || '';
  }

  set title(value: string) {
    this.setAttribute('title', value);
  }

  get thumbnail(): string {
    return this.getAttribute('thumbnail') || '';
  }

  set thumbnail(value: string) {
    this.setAttribute('thumbnail', value);
  }

  get status(): string {
    return this.getAttribute('status') || 'pending';
  }

  set status(value: string) {
    this.setAttribute('status', value);
  }

  get duration(): string {
    return this.getAttribute('duration') || '';
  }

  set duration(value: string) {
    this.setAttribute('duration', value);
  }
}

// Register the custom element
customElements.define('vlog-video-card', VlogVideoCard);
