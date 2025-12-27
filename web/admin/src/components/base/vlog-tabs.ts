/**
 * VLog Tabs Web Component
 *
 * A tab navigation component with ARIA support and keyboard navigation.
 * Uses roving tabindex for proper accessibility.
 *
 * @example
 * <vlog-tabs active="videos" @tab-change="handleChange">
 *   <div slot="tabs">
 *     <vlog-tab-button tab-id="videos">Videos</vlog-tab-button>
 *     <vlog-tab-button tab-id="settings">Settings</vlog-tab-button>
 *   </div>
 *   <vlog-tab-panel tab-id="videos">Videos content</vlog-tab-panel>
 *   <vlog-tab-panel tab-id="settings">Settings content</vlog-tab-panel>
 * </vlog-tabs>
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

    .tabs-container {
      display: flex;
      flex-direction: column;
    }

    /* Tab list */
    .tab-list {
      display: flex;
      gap: var(--vlog-space-1, 0.25rem);
      border-bottom: 1px solid var(--vlog-border-primary, #334155);
      padding-bottom: var(--vlog-space-1, 0.25rem);
      margin-bottom: var(--vlog-space-4, 1rem);
    }

    /* Variant: pills */
    :host([variant="pills"]) .tab-list {
      border-bottom: none;
      background-color: var(--vlog-bg-tertiary, #1e293b);
      padding: var(--vlog-space-1, 0.25rem);
      border-radius: var(--vlog-radius-lg, 0.5rem);
      gap: var(--vlog-space-1, 0.25rem);
    }

    /* Variant: underline */
    :host([variant="underline"]) .tab-list {
      gap: var(--vlog-space-4, 1rem);
    }

    /* Full width */
    :host([full-width]) .tab-list {
      width: 100%;
    }

    :host([full-width]) .tab-list ::slotted(vlog-tab-button) {
      flex: 1;
    }

    /* Panels container */
    .panels-container {
      min-height: 0;
    }
  </style>

  <div class="tabs-container" part="container">
    <div class="tab-list" role="tablist" part="tablist">
      <slot name="tabs"></slot>
    </div>
    <div class="panels-container" part="panels">
      <slot></slot>
    </div>
  </div>
`;

export class VlogTabs extends HTMLElement {
  private tabList!: HTMLDivElement;
  private tabButtons: HTMLElement[] = [];
  private tabPanels: HTMLElement[] = [];
  private _activeTab: string = '';

  static get observedAttributes() {
    return ['active', 'variant', 'full-width'];
  }

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot!.appendChild(template.content.cloneNode(true));

    this.tabList = this.shadowRoot!.querySelector('.tab-list')!;

    this.handleKeyDown = this.handleKeyDown.bind(this);
    this.handleTabClick = this.handleTabClick.bind(this);
  }

  connectedCallback() {
    this.setupTabs();

    // Set initial active tab
    const initialActive = this.getAttribute('active');
    if (initialActive) {
      this._activeTab = initialActive;
      this.updateActiveTab();
    } else if (this.tabButtons.length > 0) {
      // Default to first tab
      const firstTab = this.tabButtons[0];
      const firstTabId = firstTab?.getAttribute('tab-id');
      if (firstTabId) {
        this._activeTab = firstTabId;
        this.updateActiveTab();
      }
    }

    // Listen for slot changes
    const tabSlot = this.shadowRoot!.querySelector('slot[name="tabs"]') as HTMLSlotElement;
    const panelSlot = this.shadowRoot!.querySelector('slot:not([name])') as HTMLSlotElement;

    tabSlot?.addEventListener('slotchange', () => this.setupTabs());
    panelSlot?.addEventListener('slotchange', () => this.setupTabs());
  }

  disconnectedCallback() {
    this.tabList.removeEventListener('keydown', this.handleKeyDown);
    this.tabButtons.forEach(btn => {
      btn.removeEventListener('click', this.handleTabClick);
    });
  }

  attributeChangedCallback(name: string, oldValue: string | null, newValue: string | null) {
    if (oldValue === newValue) return;

    if (name === 'active' && newValue) {
      this._activeTab = newValue;
      this.updateActiveTab();
    }
  }

  private setupTabs() {
    // Get tab buttons
    const tabSlot = this.shadowRoot!.querySelector('slot[name="tabs"]') as HTMLSlotElement;
    const slottedTabs = tabSlot?.assignedElements() || [];

    // Find vlog-tab-button elements (they might be nested in a div)
    this.tabButtons = [];
    slottedTabs.forEach(el => {
      if (el.tagName.toLowerCase() === 'vlog-tab-button') {
        this.tabButtons.push(el as HTMLElement);
      } else {
        const nested = el.querySelectorAll('vlog-tab-button');
        nested.forEach(btn => this.tabButtons.push(btn as HTMLElement));
      }
    });

    // Get tab panels
    const panelSlot = this.shadowRoot!.querySelector('slot:not([name])') as HTMLSlotElement;
    const slottedPanels = panelSlot?.assignedElements() || [];

    this.tabPanels = slottedPanels.filter(
      el => el.tagName.toLowerCase() === 'vlog-tab-panel'
    ) as HTMLElement[];

    // Set up ARIA relationships and event listeners
    this.tabButtons.forEach((btn, index) => {
      const tabId = btn.getAttribute('tab-id');
      if (!tabId) return;

      // Set ARIA attributes
      btn.setAttribute('role', 'tab');
      btn.setAttribute('id', `tab-${tabId}`);
      btn.setAttribute('aria-controls', `panel-${tabId}`);

      // Set initial tabindex (roving tabindex pattern)
      if (index === 0 && !this._activeTab) {
        btn.setAttribute('tabindex', '0');
      } else if (tabId === this._activeTab) {
        btn.setAttribute('tabindex', '0');
      } else {
        btn.setAttribute('tabindex', '-1');
      }

      // Add click listener
      btn.removeEventListener('click', this.handleTabClick);
      btn.addEventListener('click', this.handleTabClick);
    });

    // Set up panels
    this.tabPanels.forEach(panel => {
      const panelId = panel.getAttribute('tab-id');
      if (!panelId) return;

      panel.setAttribute('role', 'tabpanel');
      panel.setAttribute('id', `panel-${panelId}`);
      panel.setAttribute('aria-labelledby', `tab-${panelId}`);
      panel.setAttribute('tabindex', '0');
    });

    // Add keyboard listener to tab list
    this.tabList.removeEventListener('keydown', this.handleKeyDown);
    this.tabList.addEventListener('keydown', this.handleKeyDown);

    // Update active state
    if (this._activeTab) {
      this.updateActiveTab();
    }
  }

  private handleTabClick(e: Event) {
    const button = (e.target as HTMLElement).closest('vlog-tab-button');
    if (!button) return;

    const tabId = button.getAttribute('tab-id');
    if (tabId) {
      this.activateTab(tabId);
    }
  }

  private handleKeyDown(e: KeyboardEvent) {
    const target = e.target as HTMLElement;
    if (!target.closest('vlog-tab-button')) return;

    const currentIndex = this.tabButtons.findIndex(
      btn => btn.getAttribute('tab-id') === this._activeTab
    );

    let newIndex = currentIndex;

    switch (e.key) {
      case 'ArrowRight':
      case 'ArrowDown':
        e.preventDefault();
        newIndex = (currentIndex + 1) % this.tabButtons.length;
        break;
      case 'ArrowLeft':
      case 'ArrowUp':
        e.preventDefault();
        newIndex = (currentIndex - 1 + this.tabButtons.length) % this.tabButtons.length;
        break;
      case 'Home':
        e.preventDefault();
        newIndex = 0;
        break;
      case 'End':
        e.preventDefault();
        newIndex = this.tabButtons.length - 1;
        break;
      default:
        return;
    }

    if (newIndex !== currentIndex) {
      const newTab = this.tabButtons[newIndex];
      const newTabId = newTab?.getAttribute('tab-id');
      if (newTabId && newTab) {
        this.activateTab(newTabId);
        newTab.focus();
      }
    }
  }

  private updateActiveTab() {
    // Update tab buttons
    this.tabButtons.forEach(btn => {
      const tabId = btn.getAttribute('tab-id');
      const isActive = tabId === this._activeTab;

      btn.setAttribute('aria-selected', String(isActive));
      btn.setAttribute('tabindex', isActive ? '0' : '-1');

      // Update the button's active state (it will handle its own styling)
      if (isActive) {
        btn.setAttribute('active', '');
      } else {
        btn.removeAttribute('active');
      }
    });

    // Update panels
    this.tabPanels.forEach(panel => {
      const panelId = panel.getAttribute('tab-id');
      const isActive = panelId === this._activeTab;

      if (isActive) {
        panel.removeAttribute('hidden');
        panel.setAttribute('active', '');
      } else {
        panel.setAttribute('hidden', '');
        panel.removeAttribute('active');
      }
    });
  }

  // Public API
  activateTab(tabId: string) {
    if (tabId === this._activeTab) return;

    const previousId = this._activeTab;
    this._activeTab = tabId;

    this.setAttribute('active', tabId);
    this.updateActiveTab();

    this.dispatchEvent(new CustomEvent('tab-change', {
      detail: { id: tabId, previousId },
      bubbles: true,
      composed: true
    }));
  }

  get activeTab(): string {
    return this._activeTab;
  }

  set activeTab(value: string) {
    this.activateTab(value);
  }

  nextTab() {
    const currentIndex = this.tabButtons.findIndex(
      btn => btn.getAttribute('tab-id') === this._activeTab
    );
    const nextIndex = (currentIndex + 1) % this.tabButtons.length;
    const nextTab = this.tabButtons[nextIndex];
    const nextTabId = nextTab?.getAttribute('tab-id');
    if (nextTabId) {
      this.activateTab(nextTabId);
    }
  }

  previousTab() {
    const currentIndex = this.tabButtons.findIndex(
      btn => btn.getAttribute('tab-id') === this._activeTab
    );
    const prevIndex = (currentIndex - 1 + this.tabButtons.length) % this.tabButtons.length;
    const prevTab = this.tabButtons[prevIndex];
    const prevTabId = prevTab?.getAttribute('tab-id');
    if (prevTabId) {
      this.activateTab(prevTabId);
    }
  }
}

// Register the custom element
customElements.define('vlog-tabs', VlogTabs);
