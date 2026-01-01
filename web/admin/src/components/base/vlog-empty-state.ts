/**
 * VLog Empty State Web Component
 *
 * An empty state component with illustrations and call-to-action buttons.
 * Used when there's no content to display.
 *
 * @example
 * <vlog-empty-state icon="video" title="No videos" description="Upload your first video">
 *   <div slot="actions"><vlog-button>Upload</vlog-button></div>
 * </vlog-empty-state>
 */

// Built-in icons for common empty states
const icons = {
  inbox: `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
    <path stroke-linecap="round" stroke-linejoin="round" d="M2.25 13.5h3.86a2.25 2.25 0 012.012 1.244l.256.512a2.25 2.25 0 002.013 1.244h3.218a2.25 2.25 0 002.013-1.244l.256-.512a2.25 2.25 0 012.013-1.244h3.859m-19.5.338V18a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18v-4.162c0-.224-.034-.447-.1-.661L19.24 5.338a2.25 2.25 0 00-2.15-1.588H6.911a2.25 2.25 0 00-2.15 1.588L2.35 13.177a2.25 2.25 0 00-.1.661z" />
  </svg>`,
  search: `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
    <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
  </svg>`,
  folder: `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
    <path stroke-linecap="round" stroke-linejoin="round" d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44l-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z" />
  </svg>`,
  video: `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
    <path stroke-linecap="round" d="M15.75 10.5l4.72-4.72a.75.75 0 011.28.53v11.38a.75.75 0 01-1.28.53l-4.72-4.72M4.5 18.75h9a2.25 2.25 0 002.25-2.25v-9a2.25 2.25 0 00-2.25-2.25h-9A2.25 2.25 0 002.25 7.5v9a2.25 2.25 0 002.25 2.25z" />
  </svg>`,
  settings: `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
    <path stroke-linecap="round" stroke-linejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.324.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.24-.438.613-.431.992a6.759 6.759 0 010 .255c-.007.378.138.75.43.99l1.005.828c.424.35.534.954.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.57 6.57 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.28c-.09.543-.56.941-1.11.941h-2.594c-.55 0-1.02-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.992a6.932 6.932 0 010-.255c.007-.378-.138-.75-.43-.99l-1.004-.828a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.087.22-.128.332-.183.582-.495.644-.869l.214-1.281z" />
    <path stroke-linecap="round" stroke-linejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
  </svg>`,
  users: `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
    <path stroke-linecap="round" stroke-linejoin="round" d="M15 19.128a9.38 9.38 0 002.625.372 9.337 9.337 0 004.121-.952 4.125 4.125 0 00-7.533-2.493M15 19.128v-.003c0-1.113-.285-2.16-.786-3.07M15 19.128v.106A12.318 12.318 0 018.624 21c-2.331 0-4.512-.645-6.374-1.766l-.001-.109a6.375 6.375 0 0111.964-3.07M12 6.375a3.375 3.375 0 11-6.75 0 3.375 3.375 0 016.75 0zm8.25 2.25a2.625 2.625 0 11-5.25 0 2.625 2.625 0 015.25 0z" />
  </svg>`,
  error: `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
    <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
  </svg>`,
  chart: `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
    <path stroke-linecap="round" stroke-linejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V4.125z" />
  </svg>`,
};

const template = document.createElement('template');
template.innerHTML = `
  <style>
    :host {
      display: block;
    }

    :host([hidden]) {
      display: none;
    }

    .empty-state {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      text-align: center;
      padding: var(--vlog-space-8, 2rem);
    }

    /* Size variants */
    .empty-state.size-sm {
      padding: var(--vlog-space-4, 1rem);
    }

    .empty-state.size-sm .icon-wrapper {
      width: 3rem;
      height: 3rem;
      margin-bottom: var(--vlog-space-3, 0.75rem);
    }

    .empty-state.size-sm .title {
      font-size: var(--vlog-text-base, 1rem);
    }

    .empty-state.size-sm .description {
      font-size: var(--vlog-text-xs, 0.75rem);
    }

    .empty-state.size-lg {
      padding: var(--vlog-space-12, 3rem);
    }

    .empty-state.size-lg .icon-wrapper {
      width: 6rem;
      height: 6rem;
      margin-bottom: var(--vlog-space-6, 1.5rem);
    }

    .empty-state.size-lg .title {
      font-size: var(--vlog-text-2xl, 1.5rem);
    }

    .empty-state.size-lg .description {
      font-size: var(--vlog-text-base, 1rem);
    }

    /* Icon */
    .icon-wrapper {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 4rem;
      height: 4rem;
      margin-bottom: var(--vlog-space-4, 1rem);
      color: var(--vlog-text-muted, #64748b);
    }

    .icon-wrapper svg {
      width: 100%;
      height: 100%;
    }

    ::slotted(svg) {
      width: 100%;
      height: 100%;
    }

    /* Title */
    .title {
      margin: 0 0 var(--vlog-space-2, 0.5rem) 0;
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
      font-size: var(--vlog-text-lg, 1.125rem);
      font-weight: var(--vlog-font-semibold, 600);
      color: var(--vlog-text-primary, #f1f5f9);
    }

    /* Description */
    .description {
      margin: 0 0 var(--vlog-space-6, 1.5rem) 0;
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
      font-size: var(--vlog-text-sm, 0.875rem);
      color: var(--vlog-text-tertiary, #94a3b8);
      max-width: 24rem;
      line-height: var(--vlog-leading-relaxed, 1.625);
    }

    .description:last-child {
      margin-bottom: 0;
    }

    /* Actions */
    .actions {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: center;
      gap: var(--vlog-space-3, 0.75rem);
    }

    .actions:empty:not(:has(.action-button)) {
      display: none;
    }

    /* Action button (when using action-text attribute) */
    .action-button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: var(--vlog-space-2, 0.5rem);
      padding: var(--vlog-space-2, 0.5rem) var(--vlog-space-4, 1rem);
      border: none;
      border-radius: var(--vlog-radius-lg, 0.5rem);
      background-color: var(--vlog-primary, #3b82f6);
      color: white;
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
      font-size: var(--vlog-text-sm, 0.875rem);
      font-weight: var(--vlog-font-medium, 500);
      cursor: pointer;
      transition: var(--vlog-transition-colors);
    }

    .action-button:hover {
      background-color: var(--vlog-primary-hover, #2563eb);
    }

    .action-button:focus-visible {
      outline: none;
      box-shadow: 0 0 0 var(--vlog-focus-ring-width, 2px) var(--vlog-focus-ring-color, rgba(59, 130, 246, 0.5));
    }

    .action-button:empty {
      display: none;
    }

    /* Hide sections when empty */
    .icon-wrapper:not(:has(svg)):not(:has(slot[name="icon"]::slotted(*))) {
      display: none;
    }

    .title:empty {
      display: none;
    }

    .description:empty {
      display: none;
    }
  </style>

  <div class="empty-state" role="status" part="wrapper">
    <div class="icon-wrapper" part="icon" aria-hidden="true">
      <slot name="icon"></slot>
    </div>
    <h3 class="title" part="title"></h3>
    <p class="description" part="description"></p>
    <div class="actions" part="actions">
      <button class="action-button" part="action-button"></button>
      <slot name="actions"></slot>
    </div>
  </div>
`;

export class VlogEmptyState extends HTMLElement {
  private wrapper!: HTMLDivElement;
  private iconWrapper!: HTMLDivElement;
  private titleElement!: HTMLHeadingElement;
  private descriptionElement!: HTMLParagraphElement;
  private actionButton!: HTMLButtonElement;

  static get observedAttributes() {
    return ['icon', 'title', 'description', 'size', 'action-text', 'compact'];
  }

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot!.appendChild(template.content.cloneNode(true));

    this.wrapper = this.shadowRoot!.querySelector('.empty-state')!;
    this.iconWrapper = this.shadowRoot!.querySelector('.icon-wrapper')!;
    this.titleElement = this.shadowRoot!.querySelector('.title')!;
    this.descriptionElement = this.shadowRoot!.querySelector('.description')!;
    this.actionButton = this.shadowRoot!.querySelector('.action-button')!;

    this.handleActionClick = this.handleActionClick.bind(this);
  }

  connectedCallback() {
    this.updateContent();
    this.actionButton.addEventListener('click', this.handleActionClick);
  }

  disconnectedCallback() {
    this.actionButton.removeEventListener('click', this.handleActionClick);
  }

  private handleActionClick() {
    this.dispatchEvent(
      new CustomEvent('action', {
        bubbles: true,
        composed: true,
      })
    );
  }

  attributeChangedCallback(_name: string, oldValue: string | null, newValue: string | null) {
    if (oldValue === newValue) return;
    this.updateContent();
  }

  private updateContent() {
    // Handle compact as alias for size="sm"
    const isCompact = this.hasAttribute('compact');
    const size = isCompact ? 'sm' : (this.getAttribute('size') || 'md');
    const iconName = this.getAttribute('icon') as keyof typeof icons | null;
    const title = this.getAttribute('title');
    const description = this.getAttribute('description');
    const actionText = this.getAttribute('action-text');

    // Update size class
    this.wrapper.className = `empty-state size-${size}`;

    // Update icon
    if (iconName && icons[iconName]) {
      // Only set icon if no custom icon is slotted
      const hasSlottedIcon = this.querySelector('[slot="icon"]') !== null;
      if (!hasSlottedIcon) {
        this.iconWrapper.innerHTML = icons[iconName];
      }
    } else if (!this.querySelector('[slot="icon"]')) {
      // Clear icon if no icon attribute and no slotted icon
      this.iconWrapper.innerHTML = '<slot name="icon"></slot>';
    }

    // Update title
    if (title) {
      this.titleElement.textContent = title;
    } else {
      this.titleElement.textContent = '';
    }

    // Update description
    if (description) {
      this.descriptionElement.textContent = description;
    } else {
      this.descriptionElement.textContent = '';
    }

    // Update action button
    if (actionText) {
      this.actionButton.textContent = actionText;
      this.actionButton.style.display = '';
    } else {
      this.actionButton.textContent = '';
      this.actionButton.style.display = 'none';
    }
  }

  // Public API
  get icon(): string {
    return this.getAttribute('icon') || '';
  }

  set icon(value: string) {
    if (value) {
      this.setAttribute('icon', value);
    } else {
      this.removeAttribute('icon');
    }
  }

  get title(): string {
    return this.getAttribute('title') || '';
  }

  set title(value: string) {
    if (value) {
      this.setAttribute('title', value);
    } else {
      this.removeAttribute('title');
    }
  }

  get description(): string {
    return this.getAttribute('description') || '';
  }

  set description(value: string) {
    if (value) {
      this.setAttribute('description', value);
    } else {
      this.removeAttribute('description');
    }
  }

  get size(): string {
    return this.getAttribute('size') || 'md';
  }

  set size(value: string) {
    this.setAttribute('size', value);
  }

  get actionText(): string {
    return this.getAttribute('action-text') || '';
  }

  set actionText(value: string) {
    if (value) {
      this.setAttribute('action-text', value);
    } else {
      this.removeAttribute('action-text');
    }
  }

  get compact(): boolean {
    return this.hasAttribute('compact');
  }

  set compact(value: boolean) {
    if (value) {
      this.setAttribute('compact', '');
    } else {
      this.removeAttribute('compact');
    }
  }
}

// Register the custom element
customElements.define('vlog-empty-state', VlogEmptyState);
