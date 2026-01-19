/**
 * Settings Store
 * Manages settings, watermark, and custom fields
 */

import { settingsApi, type BrandingSettings } from '@/api/endpoints/settings';
import { customFieldsApi } from '@/api/endpoints/custom-fields';
import type { SettingDefinition, WatermarkSettings, CustomField, CustomFieldType, CustomFieldConstraint } from '@/api/types';

export interface SettingsState {
  // Settings categories and values
  settingsCategories: string[];
  settingsByCategory: Record<string, SettingDefinition[]>;
  settingsModified: Record<string, Record<string, boolean>>; // category -> key -> modified
  settingsOriginal: Record<string, SettingDefinition[]>; // category -> original settings
  settingsLoading: boolean;
  settingsLoadingCategory: string | null;
  settingsSaving: boolean;
  settingsMessage: string;
  settingsError: string;

  // Watermark settings - always has a value (defaults provided)
  watermarkSettings: WatermarkSettings;
  watermarkLoading: boolean;
  watermarkImageFile: File | null;
  watermarkUploading: boolean;
  watermarkUploadProgress: number;
  watermarkMessage: string;
  watermarkError: string;

  // Custom fields
  customFields: CustomField[];
  customFieldsLoading: boolean;
  customFieldModal: boolean;
  customFieldEditing: CustomField | null;
  customFieldForm: {
    name: string;
    field_key: string;
    field_type: CustomFieldType;
    description: string;
    required: boolean;
    constraints: CustomFieldConstraint;
    applies_to_categories: number[];
  };
  customFieldSaving: boolean;
  customFieldMessage: string;
  customFieldError: string;

  // Branding settings (Issue #214)
  brandingSettings: BrandingSettings | null;
  brandingLoading: boolean;
  brandingSaving: boolean;
  brandingSiteName: string;
  brandingFooterText: string;
  brandingModified: boolean;
  brandingFooterModified: boolean;
  logoFile: File | null;
  logoUploading: boolean;
  logoUploadProgress: number;
  faviconFile: File | null;
  faviconUploading: boolean;
  faviconUploadProgress: number;
  brandingMessage: string;
  brandingError: boolean;
}

export interface SettingsActions {
  // Settings operations
  loadAllSettings(): Promise<void>;
  loadSettingsCategories(): Promise<void>;
  loadSettingsCategory(category: string): Promise<void>;
  saveSettingValue(key: string, value: string | number | boolean | null): Promise<void>;
  resetSettingsCategory(category: string): void;
  saveAllCategorySettings(category: string): Promise<void>;
  resetCategorySettings(category: string): void; // Alias for resetSettingsCategory
  hasModifiedSettings(category: string): boolean;
  getSettingInputType(valueType: string): string;
  formatSettingValue(value: unknown, valueType: string): string;
  markSettingModified(category: string, key: string): void;
  exportSettings(): Promise<void>;
  importSettings(file: File): Promise<void>;

  // Watermark operations
  loadWatermarkSettings(): Promise<void>;
  saveWatermarkSettings(): Promise<void>;
  uploadWatermarkImage(): XMLHttpRequest | null;
  deleteWatermarkImage(): Promise<void>;

  // Custom field operations
  loadCustomFields(): Promise<void>;
  openCreateCustomFieldModal(): void;
  openCreateFieldModal(): void; // Alias
  openEditCustomFieldModal(field: CustomField): void;
  openEditFieldModal(field: CustomField): void; // Alias
  closeCustomFieldModal(): void;
  saveCustomField(): Promise<void>;
  deleteCustomField(field: CustomField): Promise<void>;

  // CSP-safe helpers (Alpine.js CSP build doesn't support arrow functions or ?.)
  hasGlobalCustomFields(): boolean;
  getGlobalCustomFields(): CustomField[];
  hasCategoryCustomFields(categoryId: number): boolean;
  getCategoryCustomFields(categoryId: number): CustomField[];
  hasNullCategoryCustomFields(): boolean;
  getNullCategoryCustomFields(): CustomField[];

  // Constraint helpers
  getConstraint(obj: { constraints?: CustomFieldConstraint }, prop: string): unknown;
  hasConstraint(obj: { constraints?: CustomFieldConstraint }, prop: string): boolean;
  getEnumValues(obj: { constraints?: CustomFieldConstraint & { enum_values?: string[] } }): string[];

  // Branding operations (Issue #214)
  loadBrandingSettings(): Promise<void>;
  saveBrandingSiteName(): Promise<void>;
  saveBrandingFooterText(): Promise<void>;
  uploadLogo(): XMLHttpRequest | null;
  deleteLogo(): Promise<void>;
  uploadFavicon(): XMLHttpRequest | null;
  deleteFavicon(): Promise<void>;
}

export type SettingsStore = SettingsState & SettingsActions;

export function createSettingsStore(): SettingsStore {
  return {
    // Settings state
    settingsCategories: [],
    settingsByCategory: {},
    settingsModified: {}, // category -> key -> modified
    settingsOriginal: {}, // category -> original settings array
    settingsLoading: false,
    settingsLoadingCategory: null,
    settingsSaving: false,
    settingsMessage: '',
    settingsError: '',

    // Watermark state - provide defaults to prevent null access errors
    watermarkSettings: {
      enabled: false,
      type: 'image',
      position: 'bottom-right',
      opacity: 0.5,
      image_url: undefined,
      text: undefined,
      font_size: 24,
      font_color: '#ffffff',
    },
    watermarkLoading: false,
    watermarkImageFile: null,
    watermarkUploading: false,
    watermarkUploadProgress: 0,
    watermarkMessage: '',
    watermarkError: '',

    // Custom fields state
    customFields: [],
    customFieldsLoading: false,
    customFieldModal: false,
    customFieldEditing: null,
    customFieldForm: {
      name: '',
      field_key: '',
      field_type: 'text',
      description: '',
      required: false,
      constraints: {},
      applies_to_categories: [],
    },
    customFieldSaving: false,
    customFieldMessage: '',
    customFieldError: '',

    // Branding state (Issue #214)
    brandingSettings: null,
    brandingLoading: false,
    brandingSaving: false,
    brandingSiteName: '',
    brandingFooterText: '',
    brandingModified: false,
    brandingFooterModified: false,
    logoFile: null,
    logoUploading: false,
    logoUploadProgress: 0,
    faviconFile: null,
    faviconUploading: false,
    faviconUploadProgress: 0,
    brandingMessage: '',
    brandingError: false,

    // ===========================================================================
    // Settings Operations
    // ===========================================================================

    async loadAllSettings(): Promise<void> {
      this.settingsLoading = true;
      this.settingsError = '';

      try {
        const [categories, allSettings] = await Promise.all([
          settingsApi.getCategories(),
          settingsApi.getAll(),
        ]);

        this.settingsCategories = categories;
        this.settingsByCategory = allSettings;

        // Store original values for change tracking (deep copy per category)
        for (const [category, settings] of Object.entries(allSettings)) {
          this.settingsOriginal[category] = JSON.parse(JSON.stringify(settings));
          this.settingsModified[category] = {};
        }
      } catch (e) {
        this.settingsError = e instanceof Error ? e.message : 'Failed to load settings';
      } finally {
        this.settingsLoading = false;
      }
    },

    async loadSettingsCategories(): Promise<void> {
      try {
        this.settingsCategories = await settingsApi.getCategories();
      } catch (e) {
        console.error('Failed to load settings categories:', e);
      }
    },

    async loadSettingsCategory(category: string): Promise<void> {
      this.settingsLoadingCategory = category;

      try {
        const settings = await settingsApi.getCategory(category);
        this.settingsByCategory[category] = settings;
        this.settingsOriginal[category] = JSON.parse(JSON.stringify(settings));
        this.settingsModified[category] = {};
      } catch (e) {
        this.settingsError = e instanceof Error ? e.message : 'Failed to load category';
      } finally {
        this.settingsLoadingCategory = null;
      }
    },

    async saveSettingValue(key: string, value: string | number | boolean | null): Promise<void> {
      this.settingsSaving = true;
      this.settingsMessage = '';
      this.settingsError = '';

      try {
        await settingsApi.setValue(key, value);

        // Find the category and update original value
        for (const [category, settings] of Object.entries(this.settingsByCategory)) {
          const setting = settings.find((s) => s.key === key);
          if (setting) {
            const origSetting = this.settingsOriginal[category]?.find((s) => s.key === key);
            if (origSetting) {
              origSetting.value = value;
            }
            if (this.settingsModified[category]) {
              delete this.settingsModified[category][key];
            }
            break;
          }
        }

        this.settingsMessage = 'Setting saved';
      } catch (e) {
        this.settingsError = e instanceof Error ? e.message : 'Failed to save setting';
      } finally {
        this.settingsSaving = false;
      }
    },

    resetSettingsCategory(category: string): void {
      if (!this.settingsOriginal[category]) return;

      // Restore original values
      this.settingsByCategory[category] = JSON.parse(JSON.stringify(this.settingsOriginal[category]));
      // Clear modified tracking
      this.settingsModified[category] = {};
      this.settingsMessage = 'Settings reset to last saved values';
      this.settingsError = '';
    },

    // Alias for resetSettingsCategory
    resetCategorySettings(category: string): void {
      return this.resetSettingsCategory(category);
    },

    async saveAllCategorySettings(category: string): Promise<void> {
      if (!this.hasModifiedSettings(category)) return;

      this.settingsSaving = true;
      this.settingsMessage = '';
      this.settingsError = '';

      const modifiedKeys = Object.keys(this.settingsModified[category] || {});
      let savedCount = 0;
      const errors: string[] = [];

      for (const key of modifiedKeys) {
        try {
          const setting = this.settingsByCategory[category]?.find((s) => s.key === key);
          if (!setting) continue;

          await settingsApi.setValue(key, setting.value);
          savedCount++;
          if (this.settingsModified[category]) {
            delete this.settingsModified[category][key];
          }

          // Update original value
          const origSetting = this.settingsOriginal[category]?.find((s) => s.key === key);
          if (origSetting) {
            origSetting.value = setting.value;
          }
        } catch (e) {
          errors.push(`${key}: ${e instanceof Error ? e.message : 'Failed'}`);
        }
      }

      this.settingsSaving = false;

      if (errors.length === 0) {
        this.settingsMessage = `Saved ${savedCount} setting(s) successfully`;
        this.settingsError = '';
      } else {
        this.settingsMessage = `Saved ${savedCount}, failed ${errors.length}: ${errors.join(', ')}`;
        this.settingsError = errors.join(', ');
      }
    },

    hasModifiedSettings(category: string): boolean {
      const modified = this.settingsModified[category];
      return modified ? Object.keys(modified).length > 0 : false;
    },

    getSettingInputType(valueType: string): string {
      switch (valueType) {
        case 'boolean':
          return 'checkbox';
        case 'integer':
        case 'float':
          return 'number';
        case 'enum':
          return 'select';
        case 'json':
          return 'textarea';
        default:
          return 'text';
      }
    },

    formatSettingValue(value: unknown, valueType: string): string {
      if (valueType === 'json') {
        try {
          return JSON.stringify(value, null, 2);
        } catch {
          return String(value);
        }
      }
      return String(value ?? '');
    },

    markSettingModified(category: string, key: string): void {
      if (!this.settingsModified[category]) {
        this.settingsModified[category] = {};
      }
      this.settingsModified[category][key] = true;
    },

    async exportSettings(): Promise<void> {
      try {
        const data = await settingsApi.export();
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `settings-${new Date().toISOString().split('T')[0]}.json`;
        a.click();
        URL.revokeObjectURL(url);
      } catch (e) {
        this.settingsError = e instanceof Error ? e.message : 'Failed to export settings';
      }
    },

    async importSettings(file: File): Promise<void> {
      try {
        const text = await file.text();

        // Parse JSON with user-friendly error handling
        let data;
        try {
          data = JSON.parse(text);
        } catch {
          this.settingsError = 'Invalid settings file format. Please select a valid JSON file.';
          return;
        }

        const result = await settingsApi.import(data);
        this.settingsMessage = `Imported ${result.imported} settings (${result.skipped} skipped)`;
        await this.loadAllSettings();
      } catch (e) {
        this.settingsError = e instanceof Error ? e.message : 'Failed to import settings';
      }
    },

    // ===========================================================================
    // Watermark Operations
    // ===========================================================================

    async loadWatermarkSettings(): Promise<void> {
      this.watermarkLoading = true;
      this.watermarkError = '';

      try {
        this.watermarkSettings = await settingsApi.watermark.get();
      } catch (e) {
        this.watermarkError = e instanceof Error ? e.message : 'Failed to load watermark settings';
      } finally {
        this.watermarkLoading = false;
      }
    },

    async saveWatermarkSettings(): Promise<void> {
      this.watermarkLoading = true;
      this.watermarkMessage = '';
      this.watermarkError = '';

      try {
        await settingsApi.watermark.save(this.watermarkSettings);
        this.watermarkMessage = 'Watermark settings saved';
      } catch (e) {
        this.watermarkError = e instanceof Error ? e.message : 'Failed to save watermark settings';
      } finally {
        this.watermarkLoading = false;
      }
    },

    uploadWatermarkImage(): XMLHttpRequest | null {
      if (!this.watermarkImageFile) return null;

      this.watermarkUploading = true;
      this.watermarkUploadProgress = 0;
      this.watermarkMessage = '';
      this.watermarkError = '';

      return settingsApi.watermark.upload(
        this.watermarkImageFile,
        (percent) => {
          this.watermarkUploadProgress = percent;
        },
        (imageUrl) => {
          this.watermarkSettings.image_url = imageUrl;
          this.watermarkMessage = 'Watermark image uploaded';
          this.watermarkUploading = false;
          this.watermarkImageFile = null;
        },
        (error) => {
          this.watermarkError = error.message;
          this.watermarkUploading = false;
        }
      );
    },

    async deleteWatermarkImage(): Promise<void> {
      this.watermarkLoading = true;
      this.watermarkMessage = '';
      this.watermarkError = '';

      try {
        await settingsApi.watermark.deleteImage();
        this.watermarkSettings.image_url = undefined;
        this.watermarkMessage = 'Watermark image deleted';
      } catch (e) {
        this.watermarkError = e instanceof Error ? e.message : 'Failed to delete watermark image';
      } finally {
        this.watermarkLoading = false;
      }
    },

    // ===========================================================================
    // Custom Field Operations
    // ===========================================================================

    async loadCustomFields(): Promise<void> {
      this.customFieldsLoading = true;

      try {
        this.customFields = await customFieldsApi.list();
      } catch (e) {
        console.error('Failed to load custom fields:', e);
        this.customFields = [];
      } finally {
        this.customFieldsLoading = false;
      }
    },

    openCreateCustomFieldModal(): void {
      this.customFieldEditing = null;
      this.customFieldForm = {
        name: '',
        field_key: '',
        field_type: 'text',
        description: '',
        required: false,
        constraints: {},
        applies_to_categories: [],
      };
      this.customFieldMessage = '';
      this.customFieldError = '';
      this.customFieldModal = true;
    },

    // Alias for openCreateCustomFieldModal
    openCreateFieldModal(): void {
      return this.openCreateCustomFieldModal();
    },

    openEditCustomFieldModal(field: CustomField): void {
      this.customFieldEditing = field;
      this.customFieldForm = {
        name: field.name,
        field_key: field.field_key,
        field_type: field.field_type,
        description: field.description || '',
        required: field.required,
        constraints: field.constraints || {},
        applies_to_categories: field.applies_to_categories || [],
      };
      this.customFieldMessage = '';
      this.customFieldError = '';
      this.customFieldModal = true;
    },

    // Alias for openEditCustomFieldModal
    openEditFieldModal(field: CustomField): void {
      return this.openEditCustomFieldModal(field);
    },

    closeCustomFieldModal(): void {
      this.customFieldModal = false;
      this.customFieldEditing = null;
    },

    async saveCustomField(): Promise<void> {
      this.customFieldSaving = true;
      this.customFieldMessage = '';
      this.customFieldError = '';

      try {
        if (this.customFieldEditing) {
          // Update existing
          const updated = await customFieldsApi.update({
            id: this.customFieldEditing.id,
            ...this.customFieldForm,
          });

          const idx = this.customFields.findIndex((f) => f.id === this.customFieldEditing!.id);
          if (idx !== -1) {
            this.customFields[idx] = updated;
          }

          this.customFieldMessage = 'Custom field updated';
        } else {
          // Create new
          const created = await customFieldsApi.create(this.customFieldForm);
          this.customFields.push(created);
          this.customFieldMessage = 'Custom field created';
        }

        setTimeout(() => this.closeCustomFieldModal(), 1500);
      } catch (e) {
        this.customFieldError = e instanceof Error ? e.message : 'Failed to save custom field';
      } finally {
        this.customFieldSaving = false;
      }
    },

    async deleteCustomField(field: CustomField): Promise<void> {
      try {
        await customFieldsApi.delete(field.id);
        this.customFields = this.customFields.filter((f) => f.id !== field.id);
      } catch (e) {
        this.customFieldError = e instanceof Error ? e.message : 'Failed to delete custom field';
      }
    },

    // ===========================================================================
    // CSP-safe Custom Field Helpers
    // ===========================================================================

    /**
     * Check if there are any global custom fields (fields without category restrictions)
     */
    hasGlobalCustomFields(): boolean {
      return this.customFields.some((f) => !f.applies_to_categories || f.applies_to_categories.length === 0);
    },

    /**
     * Get all global custom fields (fields without category restrictions)
     */
    getGlobalCustomFields(): CustomField[] {
      return this.customFields.filter((f) => !f.applies_to_categories || f.applies_to_categories.length === 0);
    },

    /**
     * Check if there are any custom fields specific to a category
     */
    hasCategoryCustomFields(categoryId: number): boolean {
      return this.customFields.some((f) => f.applies_to_categories && f.applies_to_categories.includes(categoryId));
    },

    /**
     * Get custom fields specific to a category
     */
    getCategoryCustomFields(categoryId: number): CustomField[] {
      return this.customFields.filter((f) => f.applies_to_categories && f.applies_to_categories.includes(categoryId));
    },

    /**
     * Check if there are any global custom fields (alias for hasGlobalCustomFields for bulk modal)
     */
    hasNullCategoryCustomFields(): boolean {
      return this.hasGlobalCustomFields();
    },

    /**
     * Get global custom fields (alias for getGlobalCustomFields for bulk modal)
     */
    getNullCategoryCustomFields(): CustomField[] {
      return this.getGlobalCustomFields();
    },

    /**
     * Safely get a constraint property from an object with constraints
     */
    getConstraint(obj: { constraints?: CustomFieldConstraint }, prop: string): unknown {
      if (!obj || !obj.constraints) return undefined;
      return (obj.constraints as Record<string, unknown>)[prop];
    },

    /**
     * Check if a constraint property exists and is not undefined
     */
    hasConstraint(obj: { constraints?: CustomFieldConstraint }, prop: string): boolean {
      if (!obj || !obj.constraints) return false;
      return (obj.constraints as Record<string, unknown>)[prop] !== undefined;
    },

    /**
     * Get enum values from constraints, or empty array if not present
     */
    getEnumValues(obj: { constraints?: CustomFieldConstraint & { enum_values?: string[] } }): string[] {
      if (!obj || !obj.constraints || !obj.constraints.enum_values) return [];
      return obj.constraints.enum_values;
    },

    // ===========================================================================
    // Branding Operations (Issue #214)
    // ===========================================================================

    async loadBrandingSettings(): Promise<void> {
      this.brandingLoading = true;
      this.brandingError = false;
      this.brandingMessage = '';

      try {
        this.brandingSettings = await settingsApi.branding.get();
        this.brandingSiteName = this.brandingSettings.site_name || '';
        this.brandingFooterText = this.brandingSettings.footer_text || '';
        this.brandingModified = false;
        this.brandingFooterModified = false;
      } catch (e) {
        this.brandingError = true;
        this.brandingMessage = e instanceof Error ? e.message : 'Failed to load branding settings';
      } finally {
        this.brandingLoading = false;
      }
    },

    async saveBrandingSiteName(): Promise<void> {
      this.brandingSaving = true;
      this.brandingError = false;
      this.brandingMessage = '';

      try {
        await settingsApi.setValue('branding.site_name', this.brandingSiteName || 'VLog');
        this.brandingModified = false;
        this.brandingMessage = 'Site name saved';
        // Reload to get updated state
        await this.loadBrandingSettings();
      } catch (e) {
        this.brandingError = true;
        this.brandingMessage = e instanceof Error ? e.message : 'Failed to save site name';
      } finally {
        this.brandingSaving = false;
      }
    },

    async saveBrandingFooterText(): Promise<void> {
      this.brandingSaving = true;
      this.brandingError = false;
      this.brandingMessage = '';

      try {
        await settingsApi.setValue('branding.footer_text', this.brandingFooterText || null);
        this.brandingFooterModified = false;
        this.brandingMessage = 'Footer text saved';
        // Reload to get updated state
        await this.loadBrandingSettings();
      } catch (e) {
        this.brandingError = true;
        this.brandingMessage = e instanceof Error ? e.message : 'Failed to save footer text';
      } finally {
        this.brandingSaving = false;
      }
    },

    uploadLogo(): XMLHttpRequest | null {
      if (!this.logoFile) return null;

      this.logoUploading = true;
      this.logoUploadProgress = 0;
      this.brandingMessage = '';
      this.brandingError = false;

      return settingsApi.branding.upload(
        this.logoFile,
        (percent) => {
          this.logoUploadProgress = percent;
        },
        async () => {
          this.brandingMessage = 'Logo uploaded successfully';
          this.logoUploading = false;
          this.logoFile = null;
          // Reload settings to get new image URL
          await this.loadBrandingSettings();
        },
        (error) => {
          this.brandingError = true;
          this.brandingMessage = error.message;
          this.logoUploading = false;
        }
      );
    },

    async deleteLogo(): Promise<void> {
      this.brandingLoading = true;
      this.brandingMessage = '';
      this.brandingError = false;

      try {
        await settingsApi.branding.deleteLogo();
        this.brandingMessage = 'Logo deleted';
        await this.loadBrandingSettings();
      } catch (e) {
        this.brandingError = true;
        this.brandingMessage = e instanceof Error ? e.message : 'Failed to delete logo';
      } finally {
        this.brandingLoading = false;
      }
    },

    uploadFavicon(): XMLHttpRequest | null {
      if (!this.faviconFile) return null;

      this.faviconUploading = true;
      this.faviconUploadProgress = 0;
      this.brandingMessage = '';
      this.brandingError = false;

      return settingsApi.branding.uploadFavicon(
        this.faviconFile,
        (percent) => {
          this.faviconUploadProgress = percent;
        },
        async () => {
          this.brandingMessage = 'Favicon uploaded successfully';
          this.faviconUploading = false;
          this.faviconFile = null;
          // Reload settings to get new URL
          await this.loadBrandingSettings();
        },
        (error) => {
          this.brandingError = true;
          this.brandingMessage = error.message;
          this.faviconUploading = false;
        }
      );
    },

    async deleteFavicon(): Promise<void> {
      this.brandingLoading = true;
      this.brandingMessage = '';
      this.brandingError = false;

      try {
        await settingsApi.branding.deleteFavicon();
        this.brandingMessage = 'Favicon deleted';
        await this.loadBrandingSettings();
      } catch (e) {
        this.brandingError = true;
        this.brandingMessage = e instanceof Error ? e.message : 'Failed to delete favicon';
      } finally {
        this.brandingLoading = false;
      }
    },
  };
}
