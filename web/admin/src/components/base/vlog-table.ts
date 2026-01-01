/**
 * VLog Table Web Component
 *
 * A data-driven table with sorting, selection, and responsive behavior.
 * Supports custom cell rendering via slots.
 *
 * @example
 * <vlog-table
 *   :columns='[{"key":"title","label":"Title","sortable":true}]'
 *   :data="videos"
 *   selectable
 *   @sort="handleSort"
 *   @row-select="handleSelect"
 * ></vlog-table>
 */

export interface TableColumn {
  key: string;
  label: string;
  sortable?: boolean;
  width?: string;
  align?: 'left' | 'center' | 'right';
  slot?: boolean;
}

export interface TableRow {
  id: string | number;
  [key: string]: unknown;
}

const template = document.createElement('template');
template.innerHTML = `
  <style>
    :host {
      display: block;
    }

    :host([hidden]) {
      display: none;
    }

    .table-wrapper {
      width: 100%;
      overflow-x: auto;
      background-color: var(--vlog-bg-secondary, #0f172a);
      border: 1px solid var(--vlog-border-primary, #334155);
      border-radius: var(--vlog-radius-xl, 0.75rem);
    }

    table {
      width: 100%;
      border-collapse: collapse;
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
    }

    /* Header */
    thead {
      background-color: var(--vlog-bg-tertiary, #1e293b);
    }

    th {
      padding: var(--vlog-space-3, 0.75rem) var(--vlog-space-4, 1rem);
      font-size: var(--vlog-text-sm, 0.875rem);
      font-weight: var(--vlog-font-semibold, 600);
      color: var(--vlog-text-secondary, #cbd5e1);
      text-align: left;
      white-space: nowrap;
      border-bottom: 1px solid var(--vlog-border-primary, #334155);
    }

    th.align-center {
      text-align: center;
    }

    th.align-right {
      text-align: right;
    }

    /* Sortable header */
    .sortable-header {
      display: inline-flex;
      align-items: center;
      gap: var(--vlog-space-1, 0.25rem);
      padding: var(--vlog-space-1, 0.25rem) var(--vlog-space-2, 0.5rem);
      margin: calc(-1 * var(--vlog-space-1, 0.25rem)) calc(-1 * var(--vlog-space-2, 0.5rem));
      border: none;
      background: transparent;
      color: inherit;
      font: inherit;
      cursor: pointer;
      border-radius: var(--vlog-radius-md, 0.375rem);
      transition: var(--vlog-transition-colors);
    }

    .sortable-header:hover {
      background-color: var(--vlog-bg-elevated, #334155);
      color: var(--vlog-text-primary, #f1f5f9);
    }

    .sortable-header:focus-visible {
      outline: none;
      box-shadow:
        0 0 0 var(--vlog-focus-ring-offset, 2px) var(--vlog-bg-tertiary, #1e293b),
        0 0 0 calc(var(--vlog-focus-ring-offset, 2px) + var(--vlog-focus-ring-width, 2px)) var(--vlog-focus-ring-color, #3b82f6);
    }

    .sort-icon {
      width: 1rem;
      height: 1rem;
      opacity: 0.5;
      transition: opacity var(--vlog-transition-fast, 150ms ease);
    }

    .sortable-header:hover .sort-icon,
    .sort-icon.active {
      opacity: 1;
    }

    .sort-icon.desc {
      transform: rotate(180deg);
    }

    /* Checkbox column */
    .checkbox-cell {
      width: 3rem;
      text-align: center;
    }

    .row-checkbox {
      width: 1rem;
      height: 1rem;
      accent-color: var(--vlog-primary, #3b82f6);
      cursor: pointer;
    }

    .row-checkbox:focus-visible {
      outline: none;
      box-shadow:
        0 0 0 var(--vlog-focus-ring-offset, 2px) var(--vlog-bg-secondary, #0f172a),
        0 0 0 calc(var(--vlog-focus-ring-offset, 2px) + var(--vlog-focus-ring-width, 2px)) var(--vlog-focus-ring-color, #3b82f6);
    }

    /* Body */
    tbody tr {
      border-bottom: 1px solid var(--vlog-border-secondary, #1e293b);
      transition: background-color var(--vlog-transition-fast, 150ms ease);
    }

    tbody tr:last-child {
      border-bottom: none;
    }

    .hoverable tbody tr:hover {
      background-color: var(--vlog-bg-tertiary, #1e293b);
    }

    tbody tr.selected {
      background-color: var(--vlog-bg-selected, rgba(59, 130, 246, 0.15));
    }

    tbody tr:focus-visible {
      outline: none;
      box-shadow: inset 0 0 0 2px var(--vlog-focus-ring-color, #3b82f6);
    }

    .striped tbody tr:nth-child(even) {
      background-color: var(--vlog-bg-tertiary, #1e293b);
    }

    .striped tbody tr:nth-child(even).selected {
      background-color: var(--vlog-bg-selected, rgba(59, 130, 246, 0.15));
    }

    td {
      padding: var(--vlog-space-3, 0.75rem) var(--vlog-space-4, 1rem);
      font-size: var(--vlog-text-sm, 0.875rem);
      color: var(--vlog-text-primary, #f1f5f9);
      vertical-align: middle;
    }

    td.align-center {
      text-align: center;
    }

    td.align-right {
      text-align: right;
    }

    /* Empty state */
    .empty-state {
      padding: var(--vlog-space-12, 3rem) var(--vlog-space-6, 1.5rem);
      text-align: center;
    }

    .empty-message {
      font-size: var(--vlog-text-sm, 0.875rem);
      color: var(--vlog-text-tertiary, #94a3b8);
    }

    /* Loading state */
    .loading-overlay {
      position: absolute;
      inset: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      background-color: rgba(2, 6, 23, 0.7);
      border-radius: var(--vlog-radius-xl, 0.75rem);
    }

    .loading-spinner {
      width: 2rem;
      height: 2rem;
      border: 2px solid var(--vlog-border-primary, #334155);
      border-top-color: var(--vlog-primary, #3b82f6);
      border-radius: 50%;
      animation: spin 0.75s linear infinite;
    }

    @keyframes spin {
      to {
        transform: rotate(360deg);
      }
    }

    .table-container {
      position: relative;
    }

    /* Screen reader announcements */
    .sr-only {
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

    /* Clickable rows */
    tbody tr.clickable {
      cursor: pointer;
    }
  </style>

  <div class="table-container" part="container">
    <div class="table-wrapper" part="wrapper">
      <table role="grid" aria-label="Data table" part="table">
        <thead part="thead">
          <tr></tr>
        </thead>
        <tbody part="tbody"></tbody>
      </table>
      <div class="empty-state" style="display: none;">
        <slot name="empty">
          <p class="empty-message">No data available</p>
        </slot>
      </div>
    </div>
    <div class="loading-overlay" style="display: none;">
      <div class="loading-spinner"></div>
    </div>
    <div class="sr-only" aria-live="polite" aria-atomic="true"></div>
  </div>
`;

export class VlogTable extends HTMLElement {
  private tableWrapper!: HTMLDivElement;
  private table!: HTMLTableElement;
  private thead!: HTMLTableSectionElement;
  private theadRow!: HTMLTableRowElement;
  private tbody!: HTMLTableSectionElement;
  private emptyState!: HTMLDivElement;
  private loadingOverlay!: HTMLDivElement;
  private announcement!: HTMLDivElement;

  private _columns: TableColumn[] = [];
  private _data: TableRow[] = [];
  private _selectedRows: Set<string | number> = new Set();
  private _sortColumn: string = '';
  private _sortDirection: 'asc' | 'desc' = 'asc';

  static get observedAttributes() {
    return ['columns', 'selectable', 'sortable', 'striped', 'hoverable', 'loading', 'empty-message', 'sort-column', 'sort-direction'];
  }

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot!.appendChild(template.content.cloneNode(true));

    this.tableWrapper = this.shadowRoot!.querySelector('.table-wrapper')!;
    this.table = this.shadowRoot!.querySelector('table')!;
    this.thead = this.shadowRoot!.querySelector('thead')!;
    this.theadRow = this.thead.querySelector('tr')!;
    this.tbody = this.shadowRoot!.querySelector('tbody')!;
    this.emptyState = this.shadowRoot!.querySelector('.empty-state')!;
    this.loadingOverlay = this.shadowRoot!.querySelector('.loading-overlay')!;
    this.announcement = this.shadowRoot!.querySelector('.sr-only')!;
  }

  connectedCallback() {
    this.updateTableClasses();
    this.render();
    this.setupKeyboardNavigation();
  }

  attributeChangedCallback(name: string, oldValue: string | null, newValue: string | null) {
    if (oldValue === newValue) return;

    if (name === 'columns' && newValue) {
      try {
        this._columns = JSON.parse(newValue);
        this.render();
      } catch (e) {
        console.error('VlogTable: Invalid columns JSON', e);
      }
    } else if (name === 'sort-column') {
      this._sortColumn = newValue || '';
      this.updateSortIndicators();
    } else if (name === 'sort-direction') {
      this._sortDirection = (newValue as 'asc' | 'desc') || 'asc';
      this.updateSortIndicators();
    } else if (name === 'loading') {
      this.loadingOverlay.style.display = newValue !== null ? 'flex' : 'none';
    } else {
      this.updateTableClasses();
    }
  }

  private updateTableClasses() {
    this.tableWrapper.classList.toggle('striped', this.hasAttribute('striped'));
    this.tableWrapper.classList.toggle('hoverable', this.hasAttribute('hoverable'));
  }

  private render() {
    this.renderHeader();
    this.renderBody();
    this.updateEmptyState();
  }

  private renderHeader() {
    this.theadRow.innerHTML = '';

    const isSelectable = this.hasAttribute('selectable');

    // Add checkbox column if selectable
    if (isSelectable) {
      const th = document.createElement('th');
      th.className = 'checkbox-cell';
      th.innerHTML = `
        <input
          type="checkbox"
          class="row-checkbox select-all"
          aria-label="Select all rows"
          ${this.allRowsSelected ? 'checked' : ''}
        />
      `;
      const checkbox = th.querySelector('input')!;
      checkbox.addEventListener('change', () => this.handleSelectAll(checkbox.checked));
      this.theadRow.appendChild(th);
    }

    // Add column headers
    for (const column of this._columns) {
      const th = document.createElement('th');
      th.setAttribute('scope', 'col');

      if (column.width) {
        th.style.width = column.width;
      }

      if (column.align) {
        th.classList.add(`align-${column.align}`);
      }

      if (column.sortable) {
        th.setAttribute('aria-sort', this.getAriaSortValue(column.key));

        // Use DOM methods to avoid XSS from column.label/column.key
        const button = document.createElement('button');
        button.className = 'sortable-header';
        button.type = 'button';
        button.dataset.column = column.key;

        // Use textContent for the label to prevent XSS
        const labelText = document.createTextNode(column.label);
        button.appendChild(labelText);

        // Create sort icon using DOM methods
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('class', `sort-icon ${this._sortColumn === column.key ? 'active' : ''} ${this._sortDirection === 'desc' && this._sortColumn === column.key ? 'desc' : ''}`);
        svg.setAttribute('viewBox', '0 0 20 20');
        svg.setAttribute('fill', 'currentColor');
        svg.setAttribute('aria-hidden', 'true');

        const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
        path.setAttribute('fill-rule', 'evenodd');
        path.setAttribute('d', 'M10 3a.75.75 0 01.55.24l3.25 3.5a.75.75 0 11-1.1 1.02L10 4.852 7.3 7.76a.75.75 0 01-1.1-1.02l3.25-3.5A.75.75 0 0110 3z');
        path.setAttribute('clip-rule', 'evenodd');
        svg.appendChild(path);

        button.appendChild(svg);
        button.addEventListener('click', () => this.handleSort(column.key));
        th.appendChild(button);
      } else {
        th.textContent = column.label;
      }

      this.theadRow.appendChild(th);
    }
  }

  private renderBody() {
    this.tbody.innerHTML = '';

    const isSelectable = this.hasAttribute('selectable');

    for (const row of this._data) {
      const tr = document.createElement('tr');
      tr.setAttribute('data-row-id', String(row.id));
      tr.setAttribute('tabindex', '0');
      tr.setAttribute('role', 'row');

      if (this._selectedRows.has(row.id)) {
        tr.classList.add('selected');
        tr.setAttribute('aria-selected', 'true');
      }

      // Row click handler
      tr.addEventListener('click', (e) => {
        const target = e.target as HTMLElement;
        if (!target.closest('input, button, a, [tabindex]') || target.classList.contains('row-checkbox')) {
          this.handleRowClick(row);
        }
      });

      // Add checkbox cell if selectable
      if (isSelectable) {
        const td = document.createElement('td');
        td.className = 'checkbox-cell';
        td.innerHTML = `
          <input
            type="checkbox"
            class="row-checkbox"
            aria-label="Select row ${row.id}"
            ${this._selectedRows.has(row.id) ? 'checked' : ''}
          />
        `;
        const checkbox = td.querySelector('input')!;
        checkbox.addEventListener('change', (e) => {
          e.stopPropagation();
          this.handleRowSelect(row.id, checkbox.checked);
        });
        tr.appendChild(td);
      }

      // Add data cells
      for (const column of this._columns) {
        const td = document.createElement('td');

        if (column.align) {
          td.classList.add(`align-${column.align}`);
        }

        if (column.slot) {
          // Use slot for custom content
          const slotName = `cell-${column.key}-${row.id}`;
          td.innerHTML = `<slot name="${slotName}"></slot>`;

          // Check if slot content exists, otherwise show raw value
          const slotContent = this.querySelector(`[slot="${slotName}"]`);
          if (!slotContent) {
            td.textContent = String(row[column.key] ?? '');
          }
        } else {
          td.textContent = String(row[column.key] ?? '');
        }

        tr.appendChild(td);
      }

      this.tbody.appendChild(tr);
    }
  }

  private updateEmptyState() {
    const isEmpty = this._data.length === 0;
    this.emptyState.style.display = isEmpty ? 'block' : 'none';
    this.table.style.display = isEmpty ? 'none' : 'table';
  }

  private updateSortIndicators() {
    const buttons = this.theadRow.querySelectorAll('.sortable-header');
    buttons.forEach((button) => {
      const column = (button as HTMLElement).dataset.column;
      const th = button.closest('th')!;
      const icon = button.querySelector('.sort-icon')!;

      if (column === this._sortColumn) {
        th.setAttribute('aria-sort', this._sortDirection === 'asc' ? 'ascending' : 'descending');
        icon.classList.add('active');
        icon.classList.toggle('desc', this._sortDirection === 'desc');
      } else {
        th.setAttribute('aria-sort', 'none');
        icon.classList.remove('active', 'desc');
      }
    });
  }

  private getAriaSortValue(columnKey: string): string {
    if (this._sortColumn !== columnKey) return 'none';
    return this._sortDirection === 'asc' ? 'ascending' : 'descending';
  }

  private handleSort(columnKey: string) {
    let direction: 'asc' | 'desc' = 'asc';

    if (this._sortColumn === columnKey) {
      direction = this._sortDirection === 'asc' ? 'desc' : 'asc';
    }

    this._sortColumn = columnKey;
    this._sortDirection = direction;

    this.setAttribute('sort-column', columnKey);
    this.setAttribute('sort-direction', direction);

    this.dispatchEvent(new CustomEvent('sort', {
      detail: { column: columnKey, direction },
      bubbles: true,
      composed: true
    }));

    this.announce(`Table sorted by ${columnKey}, ${direction === 'asc' ? 'ascending' : 'descending'}`);
  }

  private handleRowSelect(id: string | number, selected: boolean) {
    if (selected) {
      this._selectedRows.add(id);
    } else {
      this._selectedRows.delete(id);
    }

    this.updateRowSelection(id, selected);

    this.dispatchEvent(new CustomEvent('row-select', {
      detail: { id, selected, selectedRows: Array.from(this._selectedRows) },
      bubbles: true,
      composed: true
    }));

    this.announce(`${selected ? 'Selected' : 'Deselected'} row. ${this._selectedRows.size} items selected.`);
    this.updateSelectAllCheckbox();
  }

  private handleSelectAll(selected: boolean) {
    if (selected) {
      this._data.forEach(row => this._selectedRows.add(row.id));
    } else {
      this._selectedRows.clear();
    }

    // Update all row checkboxes
    this.tbody.querySelectorAll('.row-checkbox').forEach((checkbox) => {
      (checkbox as HTMLInputElement).checked = selected;
    });

    // Update all row classes
    this.tbody.querySelectorAll('tr').forEach((tr) => {
      tr.classList.toggle('selected', selected);
      tr.setAttribute('aria-selected', String(selected));
    });

    this.dispatchEvent(new CustomEvent('select-all', {
      detail: { selected, ids: Array.from(this._selectedRows) },
      bubbles: true,
      composed: true
    }));

    this.announce(`${selected ? 'Selected' : 'Deselected'} all ${this._data.length} items.`);
  }

  private handleRowClick(row: TableRow) {
    this.dispatchEvent(new CustomEvent('row-click', {
      detail: { id: row.id, data: row },
      bubbles: true,
      composed: true
    }));
  }

  private updateRowSelection(id: string | number, selected: boolean) {
    const row = this.tbody.querySelector(`tr[data-row-id="${id}"]`);
    if (row) {
      row.classList.toggle('selected', selected);
      row.setAttribute('aria-selected', String(selected));
    }
  }

  private updateSelectAllCheckbox() {
    const selectAll = this.theadRow.querySelector('.select-all') as HTMLInputElement;
    if (selectAll) {
      selectAll.checked = this.allRowsSelected;
      selectAll.indeterminate = this._selectedRows.size > 0 && !this.allRowsSelected;
    }
  }

  private get allRowsSelected(): boolean {
    return this._data.length > 0 && this._selectedRows.size === this._data.length;
  }

  private setupKeyboardNavigation() {
    this.tbody.addEventListener('keydown', (e) => {
      const target = e.target as HTMLElement;
      const row = target.closest('tr');
      if (!row) return;

      const rows = Array.from(this.tbody.querySelectorAll('tr'));
      const currentIndex = rows.indexOf(row);

      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault();
          if (currentIndex < rows.length - 1) {
            (rows[currentIndex + 1] as HTMLElement).focus();
          }
          break;
        case 'ArrowUp':
          e.preventDefault();
          if (currentIndex > 0) {
            (rows[currentIndex - 1] as HTMLElement).focus();
          }
          break;
        case ' ':
          e.preventDefault();
          if (this.hasAttribute('selectable')) {
            const id = row.dataset.rowId!;
            const isSelected = this._selectedRows.has(id) || this._selectedRows.has(Number(id));
            this.handleRowSelect(id, !isSelected);
            const checkbox = row.querySelector('.row-checkbox') as HTMLInputElement;
            if (checkbox) checkbox.checked = !isSelected;
          }
          break;
        case 'Enter':
          e.preventDefault();
          const rowId = row.dataset.rowId!;
          const rowData = this._data.find(r => String(r.id) === rowId);
          if (rowData) {
            this.handleRowClick(rowData);
          }
          break;
        case 'Home':
          e.preventDefault();
          if (rows.length > 0) {
            (rows[0] as HTMLElement).focus();
          }
          break;
        case 'End':
          e.preventDefault();
          if (rows.length > 0) {
            (rows[rows.length - 1] as HTMLElement).focus();
          }
          break;
      }
    });
  }

  private announce(message: string) {
    this.announcement.textContent = message;
    // Clear after announcement
    setTimeout(() => {
      this.announcement.textContent = '';
    }, 1000);
  }

  // Public API
  get columns(): TableColumn[] {
    return this._columns;
  }

  set columns(value: TableColumn[]) {
    this._columns = value;
    this.render();
  }

  get data(): TableRow[] {
    return this._data;
  }

  set data(value: TableRow[]) {
    this._data = value;
    this.render();
  }

  get selectedRows(): (string | number)[] {
    return Array.from(this._selectedRows);
  }

  set selectedRows(value: (string | number)[]) {
    this._selectedRows = new Set(value);
    this.render();
  }

  get sortColumn(): string {
    return this._sortColumn;
  }

  set sortColumn(value: string) {
    this._sortColumn = value;
    this.setAttribute('sort-column', value);
    this.updateSortIndicators();
  }

  get sortDirection(): 'asc' | 'desc' {
    return this._sortDirection;
  }

  set sortDirection(value: 'asc' | 'desc') {
    this._sortDirection = value;
    this.setAttribute('sort-direction', value);
    this.updateSortIndicators();
  }

  get loading(): boolean {
    return this.hasAttribute('loading');
  }

  set loading(value: boolean) {
    if (value) {
      this.setAttribute('loading', '');
    } else {
      this.removeAttribute('loading');
    }
  }

  selectAll() {
    this.handleSelectAll(true);
  }

  deselectAll() {
    this.handleSelectAll(false);
  }

  toggleRow(id: string | number) {
    const isSelected = this._selectedRows.has(id);
    this.handleRowSelect(id, !isSelected);
  }

  getSelectedRows(): (string | number)[] {
    return Array.from(this._selectedRows);
  }
}

// Register the custom element
customElements.define('vlog-table', VlogTable);
