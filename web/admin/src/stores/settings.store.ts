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
  settingsModified: Record<string, boolean>;
  settingsOriginal: Record<string, string | number | boolean | null>;
  settingsLoading: boolean;
  settingsLoadingCategory: string | null;
  settingsSaving: boolean;
  settingsMessage: string;
  settingsError: string;

  // Watermark settings
  watermarkSettings: WatermarkSettings | null;
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
  resetSettingsCategory(category: string): Promise<void>;
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
  openEditCustomFieldModal(field: CustomField): void;
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
    settingsModified: {},
    settingsOriginal: {},
    settingsLoading: false,
    settingsLoadingCategory: null,
    settingsSaving: false,
    settingsMessage: '',
    settingsError: '',

    // Watermark state
    watermarkSettings: null,
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

        // Store original values for change tracking
        for (const settings of Object.values(allSettings)) {
          for (const setting of settings) {
            this.settingsOriginal[setting.key] = setting.value;
          }
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

        for (const setting of settings) {
          this.settingsOriginal[setting.key] = setting.value;
        }
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
        this.settingsOriginal[key] = value;
        this.settingsModified[key] = false;
        this.settingsMessage = 'Setting saved';
      } catch (e) {
        this.settingsError = e instanceof Error ? e.message : 'Failed to save setting';
      } finally {
        this.settingsSaving = false;
      }
    },

    async resetSettingsCategory(category: string): Promise<void> {
      const settings = this.settingsByCategory[category];
      if (!settings) return;

      for (const setting of settings) {
        if (this.settingsModified[setting.key]) {
          try {
            await settingsApi.resetValue(setting.key);
            setting.value = setting.default_value ?? null;
            this.settingsOriginal[setting.key] = setting.value;
            this.settingsModified[setting.key] = false;
          } catch (e) {
            console.error(`Failed to reset ${setting.key}:`, e);
          }
        }
      }
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
      if (!this.watermarkSettings) return;

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
          if (this.watermarkSettings) {
            this.watermarkSettings.image_url = imageUrl;
          }
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
        if (this.watermarkSettings) {
          this.watermarkSettings.image_url = undefined;
        }
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
