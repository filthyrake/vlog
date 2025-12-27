/**
 * Settings Store
 * Manages settings, watermark, and custom fields
 */

import { settingsApi } from '@/api/endpoints/settings';
import { customFieldsApi } from '@/api/endpoints/custom-fields';
import type { SettingDefinition, WatermarkSettings, CustomField, CustomFieldType, CustomFieldConstraint } from '@/api/types';
import type { AlpineContext } from './types';

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
}

export type SettingsStore = SettingsState & SettingsActions;

export function createSettingsStore(_context?: AlpineContext): SettingsStore {
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
        const data = JSON.parse(text);
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
  };
}
