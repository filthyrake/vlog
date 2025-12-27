/**
 * VLog Dropzone Web Component
 *
 * A drag-and-drop file upload zone with validation and progress tracking.
 *
 * @example
 * <vlog-dropzone accept="video/*" max-size="5368709120">
 *   Drop video files here or click to browse
 * </vlog-dropzone>
 *
 * @fires files-selected - When files are selected via drop or browse
 * @fires file-rejected - When a file is rejected due to validation
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

    .dropzone {
      position: relative;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      min-height: 12rem;
      padding: var(--vlog-space-8, 2rem);
      border: 2px dashed var(--vlog-border-secondary, #334155);
      border-radius: var(--vlog-radius-lg, 0.5rem);
      background-color: var(--vlog-bg-tertiary, #1e293b);
      cursor: pointer;
      transition: var(--vlog-transition-colors);
    }

    .dropzone:hover {
      border-color: var(--vlog-border-primary, #475569);
      background-color: var(--vlog-bg-secondary, #0f172a);
    }

    .dropzone:focus-visible {
      outline: none;
      border-color: var(--vlog-primary, #3b82f6);
      box-shadow: 0 0 0 var(--vlog-focus-ring-width, 2px) var(--vlog-focus-ring-color, rgba(59, 130, 246, 0.5));
    }

    .dropzone.dragover {
      border-color: var(--vlog-primary, #3b82f6);
      background-color: var(--vlog-primary-bg, rgba(59, 130, 246, 0.1));
    }

    .dropzone.disabled {
      opacity: 0.5;
      cursor: not-allowed;
      pointer-events: none;
    }

    .dropzone-icon {
      width: 3rem;
      height: 3rem;
      margin-bottom: var(--vlog-space-4, 1rem);
      color: var(--vlog-text-tertiary, #94a3b8);
      transition: var(--vlog-transition-colors);
    }

    .dropzone:hover .dropzone-icon,
    .dropzone.dragover .dropzone-icon {
      color: var(--vlog-primary, #3b82f6);
    }

    .dropzone-content {
      text-align: center;
    }

    .dropzone-text {
      margin: 0;
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
      font-size: var(--vlog-text-sm, 0.875rem);
      color: var(--vlog-text-secondary, #cbd5e1);
    }

    .dropzone-hint {
      margin-top: var(--vlog-space-2, 0.5rem);
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
      font-size: var(--vlog-text-xs, 0.75rem);
      color: var(--vlog-text-tertiary, #94a3b8);
    }

    .dropzone-input {
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      opacity: 0;
      cursor: pointer;
    }

    .dropzone.disabled .dropzone-input {
      cursor: not-allowed;
    }

    /* File list */
    .file-list {
      width: 100%;
      margin-top: var(--vlog-space-4, 1rem);
      padding-top: var(--vlog-space-4, 1rem);
      border-top: 1px solid var(--vlog-border-secondary, #334155);
    }

    .file-list:empty {
      display: none;
    }

    .file-item {
      display: flex;
      align-items: center;
      gap: var(--vlog-space-3, 0.75rem);
      padding: var(--vlog-space-2, 0.5rem) var(--vlog-space-3, 0.75rem);
      border-radius: var(--vlog-radius-md, 0.375rem);
      background-color: var(--vlog-bg-secondary, #0f172a);
    }

    .file-item + .file-item {
      margin-top: var(--vlog-space-2, 0.5rem);
    }

    .file-icon {
      flex-shrink: 0;
      width: 1.5rem;
      height: 1.5rem;
      color: var(--vlog-text-tertiary, #94a3b8);
    }

    .file-info {
      flex: 1;
      min-width: 0;
    }

    .file-name {
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
      font-size: var(--vlog-text-sm, 0.875rem);
      color: var(--vlog-text-primary, #f1f5f9);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .file-size {
      font-family: var(--vlog-font-sans, system-ui, sans-serif);
      font-size: var(--vlog-text-xs, 0.75rem);
      color: var(--vlog-text-tertiary, #94a3b8);
    }

    .file-remove {
      flex-shrink: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      width: 1.5rem;
      height: 1.5rem;
      padding: 0;
      border: none;
      border-radius: var(--vlog-radius-sm, 0.25rem);
      background: transparent;
      color: var(--vlog-text-tertiary, #94a3b8);
      cursor: pointer;
      transition: var(--vlog-transition-colors);
    }

    .file-remove:hover {
      background-color: var(--vlog-error-bg, rgba(239, 68, 68, 0.15));
      color: var(--vlog-error, #ef4444);
    }

    .file-remove:focus-visible {
      outline: none;
      box-shadow: 0 0 0 var(--vlog-focus-ring-width, 2px) var(--vlog-focus-ring-color, rgba(59, 130, 246, 0.5));
    }

    .file-remove svg {
      width: 1rem;
      height: 1rem;
    }

    /* Compact variant */
    :host([compact]) .dropzone {
      min-height: auto;
      padding: var(--vlog-space-4, 1rem);
    }

    :host([compact]) .dropzone-icon {
      width: 2rem;
      height: 2rem;
      margin-bottom: var(--vlog-space-2, 0.5rem);
    }

    /* Size variants */
    :host([size="sm"]) .dropzone {
      min-height: 8rem;
      padding: var(--vlog-space-4, 1rem);
    }

    :host([size="lg"]) .dropzone {
      min-height: 16rem;
      padding: var(--vlog-space-12, 3rem);
    }
  </style>

  <div class="dropzone" part="dropzone" tabindex="0" role="button" aria-label="Upload files">
    <input type="file" class="dropzone-input" tabindex="-1" aria-hidden="true" />
    <svg class="dropzone-icon" part="icon" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" aria-hidden="true">
      <path stroke-linecap="round" stroke-linejoin="round" d="M12 16.5V9.75m0 0 3 3m-3-3-3 3M6.75 19.5a4.5 4.5 0 0 1-1.41-8.775 5.25 5.25 0 0 1 10.233-2.33 3 3 0 0 1 3.758 3.848A3.752 3.752 0 0 1 18 19.5H6.75Z" />
    </svg>
    <div class="dropzone-content" part="content">
      <p class="dropzone-text" part="text">
        <slot>Drop files here or click to browse</slot>
      </p>
      <p class="dropzone-hint" part="hint"></p>
    </div>
    <div class="file-list" part="file-list"></div>
  </div>
`;

export interface FileInfo {
  file: File;
  id: string;
}

export class VlogDropzone extends HTMLElement {
  private dropzone!: HTMLDivElement;
  private fileInput!: HTMLInputElement;
  private hintElement!: HTMLParagraphElement;
  private fileListElement!: HTMLDivElement;
  private selectedFiles: Map<string, File> = new Map();
  private fileIdCounter = 0;

  static get observedAttributes() {
    return ['accept', 'max-size', 'max-files', 'multiple', 'disabled', 'compact', 'size'];
  }

  constructor() {
    super();
    this.attachShadow({ mode: 'open' });
    this.shadowRoot!.appendChild(template.content.cloneNode(true));

    this.dropzone = this.shadowRoot!.querySelector('.dropzone')!;
    this.fileInput = this.shadowRoot!.querySelector('.dropzone-input')!;
    this.hintElement = this.shadowRoot!.querySelector('.dropzone-hint')!;
    this.fileListElement = this.shadowRoot!.querySelector('.file-list')!;

    this.handleDragOver = this.handleDragOver.bind(this);
    this.handleDragLeave = this.handleDragLeave.bind(this);
    this.handleDrop = this.handleDrop.bind(this);
    this.handleClick = this.handleClick.bind(this);
    this.handleInputChange = this.handleInputChange.bind(this);
    this.handleKeyDown = this.handleKeyDown.bind(this);
    this.handleFileListClick = this.handleFileListClick.bind(this);
  }

  connectedCallback() {
    this.updateAttributes();
    this.updateHint();
    this.setupListeners();
  }

  disconnectedCallback() {
    this.dropzone.removeEventListener('dragover', this.handleDragOver);
    this.dropzone.removeEventListener('dragleave', this.handleDragLeave);
    this.dropzone.removeEventListener('drop', this.handleDrop);
    this.dropzone.removeEventListener('click', this.handleClick);
    this.dropzone.removeEventListener('keydown', this.handleKeyDown);
    this.fileInput.removeEventListener('change', this.handleInputChange);
    this.fileListElement.removeEventListener('click', this.handleFileListClick);
  }

  attributeChangedCallback(name: string, oldValue: string | null, newValue: string | null) {
    if (oldValue === newValue) return;

    if (name === 'accept') {
      this.fileInput.accept = newValue || '';
      this.updateHint();
    } else if (name === 'multiple') {
      this.fileInput.multiple = this.hasAttribute('multiple');
    } else if (name === 'disabled') {
      this.dropzone.classList.toggle('disabled', this.hasAttribute('disabled'));
      this.fileInput.disabled = this.hasAttribute('disabled');
    } else if (name === 'max-size' || name === 'max-files') {
      this.updateHint();
    }
  }

  private updateAttributes() {
    const accept = this.getAttribute('accept');
    if (accept) {
      this.fileInput.accept = accept;
    }

    if (this.hasAttribute('multiple')) {
      this.fileInput.multiple = true;
    }

    if (this.hasAttribute('disabled')) {
      this.dropzone.classList.add('disabled');
      this.fileInput.disabled = true;
    }
  }

  private updateHint() {
    const hints: string[] = [];

    const accept = this.getAttribute('accept');
    if (accept) {
      const types = accept.split(',').map((t) => t.trim());
      const formatted = types.map((t) => {
        if (t.startsWith('.')) return t.substring(1).toUpperCase();
        if (t.includes('/')) {
          const parts = t.split('/');
          const mainType = parts[0] || '';
          const subType = parts[1] || '';
          if (subType === '*') return mainType.charAt(0).toUpperCase() + mainType.slice(1) + ' files';
          return subType.toUpperCase();
        }
        return t;
      });
      hints.push(formatted.join(', '));
    }

    const maxSize = this.getAttribute('max-size');
    if (maxSize) {
      hints.push(`Max ${this.formatBytes(parseInt(maxSize, 10))}`);
    }

    const maxFiles = this.getAttribute('max-files');
    if (maxFiles) {
      hints.push(`Up to ${maxFiles} file${parseInt(maxFiles, 10) > 1 ? 's' : ''}`);
    }

    this.hintElement.textContent = hints.join(' • ');
  }

  private setupListeners() {
    this.dropzone.addEventListener('dragover', this.handleDragOver);
    this.dropzone.addEventListener('dragleave', this.handleDragLeave);
    this.dropzone.addEventListener('drop', this.handleDrop);
    this.dropzone.addEventListener('click', this.handleClick);
    this.dropzone.addEventListener('keydown', this.handleKeyDown);
    this.fileInput.addEventListener('change', this.handleInputChange);
    this.fileListElement.addEventListener('click', this.handleFileListClick);
  }

  private handleFileListClick(e: Event) {
    // Event delegation for remove buttons
    const removeBtn = (e.target as HTMLElement).closest('.file-remove');
    if (removeBtn) {
      e.stopPropagation();
      const id = removeBtn.getAttribute('data-file-id');
      if (id) {
        this.removeFile(id);
      }
    }
  }

  private handleDragOver(e: DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (this.hasAttribute('disabled')) return;
    this.dropzone.classList.add('dragover');
  }

  private handleDragLeave(e: DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    this.dropzone.classList.remove('dragover');
  }

  private handleDrop(e: DragEvent) {
    e.preventDefault();
    e.stopPropagation();
    this.dropzone.classList.remove('dragover');

    if (this.hasAttribute('disabled')) return;

    const files = e.dataTransfer?.files;
    if (files && files.length > 0) {
      this.processFiles(Array.from(files));
    }
  }

  private handleClick(e: Event) {
    if ((e.target as HTMLElement).closest('.file-remove')) return;
    if (this.hasAttribute('disabled')) return;
    this.fileInput.click();
  }

  private handleKeyDown(e: KeyboardEvent) {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      if (!this.hasAttribute('disabled')) {
        this.fileInput.click();
      }
    }
  }

  private handleInputChange() {
    const files = this.fileInput.files;
    if (files && files.length > 0) {
      this.processFiles(Array.from(files));
    }
    // Reset input so same file can be selected again
    this.fileInput.value = '';
  }

  private processFiles(files: File[]) {
    const maxFiles = parseInt(this.getAttribute('max-files') || '0', 10);
    const maxSize = parseInt(this.getAttribute('max-size') || '0', 10);
    const accept = this.getAttribute('accept');
    const multiple = this.hasAttribute('multiple');

    const validFiles: FileInfo[] = [];
    const rejectedFiles: { file: File; reason: string }[] = [];

    for (const file of files) {
      // Check max files
      if (maxFiles > 0 && this.selectedFiles.size + validFiles.length >= maxFiles) {
        rejectedFiles.push({ file, reason: 'Maximum files exceeded' });
        continue;
      }

      // Check single file mode
      if (!multiple && (this.selectedFiles.size > 0 || validFiles.length > 0)) {
        rejectedFiles.push({ file, reason: 'Only one file allowed' });
        continue;
      }

      // Check file type
      if (accept && !this.isAcceptedType(file, accept)) {
        rejectedFiles.push({ file, reason: 'File type not accepted' });
        continue;
      }

      // Check file size
      if (maxSize > 0 && file.size > maxSize) {
        rejectedFiles.push({ file, reason: `File exceeds maximum size of ${this.formatBytes(maxSize)}` });
        continue;
      }

      const id = `file-${++this.fileIdCounter}`;
      this.selectedFiles.set(id, file);
      validFiles.push({ file, id });
    }

    // Dispatch events
    if (rejectedFiles.length > 0) {
      for (const { file, reason } of rejectedFiles) {
        this.dispatchEvent(
          new CustomEvent('file-rejected', {
            detail: { file, reason },
            bubbles: true,
            composed: true,
          })
        );
      }
    }

    if (validFiles.length > 0) {
      this.renderFileList();
      this.dispatchEvent(
        new CustomEvent('files-selected', {
          detail: { files: validFiles },
          bubbles: true,
          composed: true,
        })
      );
    }
  }

  private isAcceptedType(file: File, accept: string): boolean {
    const types = accept.split(',').map((t) => t.trim().toLowerCase());

    for (const type of types) {
      // Extension match
      if (type.startsWith('.')) {
        if (file.name.toLowerCase().endsWith(type)) return true;
      }
      // MIME type match
      else if (type.includes('/')) {
        // Handle empty file.type by checking extension as fallback
        if (!file.type) {
          // Try extension-based matching for common video types
          const ext = file.name.toLowerCase().split('.').pop();
          const extToMime: Record<string, string> = {
            mp4: 'video/mp4',
            webm: 'video/webm',
            mov: 'video/quicktime',
            avi: 'video/x-msvideo',
            mkv: 'video/x-matroska',
          };
          const inferredType = ext ? extToMime[ext] : undefined;
          if (inferredType) {
            const [mainType, subType] = type.split('/');
            const [inferredMain, inferredSub] = inferredType.split('/');
            if (mainType === inferredMain && (subType === '*' || subType === inferredSub)) {
              return true;
            }
          }
          continue;
        }

        const typeParts = type.split('/');
        const fileParts = file.type.toLowerCase().split('/');
        const mainType = typeParts[0] || '';
        const subType = typeParts[1] || '';
        const fileMainType = fileParts[0] || '';
        const fileSubType = fileParts[1] || '';

        if (mainType === fileMainType) {
          if (subType === '*' || subType === fileSubType) return true;
        }
      }
    }

    return false;
  }

  private renderFileList() {
    this.fileListElement.innerHTML = '';

    for (const [id, file] of this.selectedFiles) {
      const item = document.createElement('div');
      item.className = 'file-item';
      item.innerHTML = `
        <svg class="file-icon" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
          <path stroke-linecap="round" stroke-linejoin="round" d="m15.75 10.5 4.72-4.72a.75.75 0 0 1 1.28.53v11.38a.75.75 0 0 1-1.28.53l-4.72-4.72M4.5 18.75h9a2.25 2.25 0 0 0 2.25-2.25v-9a2.25 2.25 0 0 0-2.25-2.25h-9A2.25 2.25 0 0 0 2.25 7.5v9a2.25 2.25 0 0 0 2.25 2.25Z" />
        </svg>
        <div class="file-info">
          <div class="file-name">${this.escapeHtml(file.name)}</div>
          <div class="file-size">${this.formatBytes(file.size)}</div>
        </div>
        <button type="button" class="file-remove" aria-label="Remove file" data-file-id="${id}">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor">
            <path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z" />
          </svg>
        </button>
      `;
      // No per-element listener needed - using event delegation on fileListElement
      this.fileListElement.appendChild(item);
    }
  }

  private removeFile(id: string) {
    const file = this.selectedFiles.get(id);
    if (file) {
      this.selectedFiles.delete(id);
      this.renderFileList();

      this.dispatchEvent(
        new CustomEvent('file-removed', {
          detail: { id, file },
          bubbles: true,
          composed: true,
        })
      );
    }
  }

  private formatBytes(bytes: number): string {
    if (bytes <= 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.min(Math.floor(Math.log(bytes) / Math.log(k)), sizes.length - 1);
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  }

  private escapeHtml(text: string): string {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  // Public API
  get files(): File[] {
    return Array.from(this.selectedFiles.values());
  }

  get fileInfos(): FileInfo[] {
    return Array.from(this.selectedFiles.entries()).map(([id, file]) => ({ id, file }));
  }

  get accept(): string {
    return this.getAttribute('accept') || '';
  }

  set accept(val: string) {
    this.setAttribute('accept', val);
  }

  get maxSize(): number {
    return parseInt(this.getAttribute('max-size') || '0', 10);
  }

  set maxSize(val: number) {
    this.setAttribute('max-size', String(val));
  }

  get maxFiles(): number {
    return parseInt(this.getAttribute('max-files') || '0', 10);
  }

  set maxFiles(val: number) {
    this.setAttribute('max-files', String(val));
  }

  get multiple(): boolean {
    return this.hasAttribute('multiple');
  }

  set multiple(val: boolean) {
    if (val) {
      this.setAttribute('multiple', '');
    } else {
      this.removeAttribute('multiple');
    }
  }

  get disabled(): boolean {
    return this.hasAttribute('disabled');
  }

  set disabled(val: boolean) {
    if (val) {
      this.setAttribute('disabled', '');
    } else {
      this.removeAttribute('disabled');
    }
  }

  clear() {
    this.selectedFiles.clear();
    this.renderFileList();
  }

  removeById(id: string) {
    this.removeFile(id);
  }
}

// Register the custom element
customElements.define('vlog-dropzone', VlogDropzone);
