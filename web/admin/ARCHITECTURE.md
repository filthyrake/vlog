# VLog Admin UI Architecture

The VLog Admin UI is a modern, TypeScript-based single-page application built with Alpine.js and a modular store architecture.

## Technology Stack

| Component | Technology |
|-----------|------------|
| Framework | Alpine.js |
| Language | TypeScript |
| Build Tool | Vite |
| Testing | Vitest |
| CSS | Tailwind CSS v4 |
| Components | Web Components (Custom Elements) |

## Directory Structure

```
web/admin/
├── index.html              # Main HTML file with Alpine.js templates
├── src/
│   ├── main.ts             # Application entry point
│   ├── api/                # API client layer
│   │   ├── client.ts       # HTTP client with retry logic
│   │   ├── types.ts        # TypeScript interfaces
│   │   ├── index.ts        # API exports
│   │   └── endpoints/      # Endpoint-specific modules
│   │       ├── auth.ts
│   │       ├── videos.ts
│   │       ├── categories.ts
│   │       ├── workers.ts
│   │       ├── analytics.ts
│   │       ├── settings.ts
│   │       ├── custom-fields.ts
│   │       └── sse.ts
│   ├── stores/             # State management
│   │   ├── index.ts        # Store factory and composition
│   │   ├── types.ts        # Store type definitions
│   │   ├── auth.store.ts
│   │   ├── videos.store.ts
│   │   ├── categories.store.ts
│   │   ├── workers.store.ts
│   │   ├── analytics.store.ts
│   │   ├── settings.store.ts
│   │   ├── upload.store.ts
│   │   ├── bulk.store.ts
│   │   ├── sse.store.ts
│   │   └── ui.store.ts
│   ├── components/         # Reusable web components
│   │   ├── index.ts        # Component registration
│   │   └── base/           # Base components
│   │       ├── vlog-button.ts
│   │       ├── vlog-alert.ts
│   │       ├── vlog-progress.ts
│   │       ├── vlog-search.ts
│   │       ├── vlog-filter.ts
│   │       ├── vlog-dropzone.ts
│   │       └── ...
│   ├── styles/             # CSS tokens and utilities
│   │   └── tokens.css
│   └── utils/              # Utility functions
│       └── formatters.ts
├── static/                 # Static assets
│   └── vendor/             # Third-party libraries
│       ├── tailwind.css
│       └── shaka-player.compiled.min.js
├── package.json
├── tsconfig.json
├── vite.config.ts
└── vitest.config.ts
```

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                       index.html                             │
│                   (Alpine.js Templates)                      │
└─────────────────────────┬───────────────────────────────────┘
                          │ x-data="admin()"
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                      main.ts                                 │
│  - Initializes Alpine.js                                     │
│  - Registers Web Components                                  │
│  - Exports createAdminStore() to window                     │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    stores/index.ts                           │
│               (createAdminStore factory)                     │
│                                                              │
│  Composes all feature stores into single Alpine data object  │
└───────┬─────────┬─────────┬─────────┬─────────┬────────────┘
        │         │         │         │         │
        ▼         ▼         ▼         ▼         ▼
   ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐
   │ auth    │ │ videos  │ │ workers │ │settings │ │  ...    │
   │ store   │ │ store   │ │ store   │ │ store   │ │         │
   └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘
        │           │           │           │           │
        └───────────┴───────────┴───────────┴───────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      api/client.ts                           │
│              (HTTP Client with Interceptors)                 │
│                                                              │
│  - Retry logic with exponential backoff                      │
│  - Authentication header injection                           │
│  - Error handling and normalization                          │
└─────────────────────────────────────────────────────────────┘
```

## API Client Layer

### HTTP Client (`api/client.ts`)

The API client provides:
- **Typed requests:** Full TypeScript support for requests and responses
- **Retry logic:** Automatic retry with exponential backoff for transient errors
- **Auth injection:** Automatic authentication header management
- **Error handling:** Consistent error responses across all endpoints

```typescript
// Example usage
const videos = await apiClient.get<Video[]>('/api/videos');
const result = await apiClient.post<UploadResponse>('/api/videos', formData);
```

### Endpoint Modules (`api/endpoints/`)

Each endpoint module provides typed functions for specific API operations:

| Module | Purpose |
|--------|---------|
| `auth.ts` | Login, logout, session management |
| `videos.ts` | Video CRUD, search, filtering |
| `categories.ts` | Category management |
| `workers.ts` | Worker status, management |
| `analytics.ts` | Playback statistics |
| `settings.ts` | Runtime configuration |
| `custom-fields.ts` | Custom metadata fields |
| `sse.ts` | Server-Sent Events connections |

## Store Architecture

### Store Factory Pattern

The `createAdminStore()` function composes all feature stores into a single Alpine.js data object:

```typescript
// stores/index.ts
export function createAdminStore(): AdminStore {
  return {
    // State from all stores
    ...createAuthStore(),
    ...createVideosStore(),
    ...createWorkersStore(),
    ...createSettingsStore(),
    // ... etc

    // Lifecycle methods
    init() {
      this.checkAuth();
      this.loadVideos();
      // ...
    }
  };
}
```

### Feature Stores

Each feature store manages a specific domain:

| Store | Responsibility |
|-------|---------------|
| `auth.store.ts` | Authentication state, login/logout |
| `videos.store.ts` | Video list, filtering, editing |
| `categories.store.ts` | Category management |
| `workers.store.ts` | Worker status, job tracking |
| `analytics.store.ts` | Playback statistics |
| `settings.store.ts` | Watermark, runtime config |
| `upload.store.ts` | File upload with progress |
| `bulk.store.ts` | Bulk video operations |
| `sse.store.ts` | Real-time event subscriptions |
| `ui.store.ts` | Modals, notifications, tab state |

### Store Structure

Each store follows a consistent pattern:

```typescript
// Example: videos.store.ts
export function createVideosStore() {
  return {
    // State
    videos: [] as Video[],
    loading: false,
    error: null as string | null,

    // Actions
    async loadVideos() {
      this.loading = true;
      try {
        this.videos = await videosApi.list();
      } catch (e) {
        this.error = e.message;
      } finally {
        this.loading = false;
      }
    },

    // Computed (getters)
    get filteredVideos() {
      return this.videos.filter(v => v.status === this.statusFilter);
    }
  };
}
```

## Web Components

### Base Components (`components/base/`)

Reusable UI components implemented as Web Components:

| Component | Purpose |
|-----------|---------|
| `<vlog-button>` | Styled buttons with loading state |
| `<vlog-alert>` | Success, error, warning notifications |
| `<vlog-progress>` | Linear and circular progress indicators |
| `<vlog-search>` | Search input with debounce |
| `<vlog-filter>` | Dropdown filter controls |
| `<vlog-dropzone>` | File upload with drag-and-drop |
| `<vlog-modal>` | Modal dialog |
| `<vlog-toast>` | Toast notifications |

### Component Registration

Components are registered in `components/index.ts`:

```typescript
import { VlogButton } from './base/vlog-button';
import { VlogAlert } from './base/vlog-alert';
// ...

customElements.define('vlog-button', VlogButton);
customElements.define('vlog-alert', VlogAlert);
// ...
```

### Usage in Templates

```html
<vlog-button
  variant="primary"
  :loading="saving"
  @click="saveVideo"
>
  Save Changes
</vlog-button>

<vlog-alert
  variant="success"
  x-show="saved"
>
  Video saved successfully
</vlog-alert>
```

## Build System

### Vite Configuration

```typescript
// vite.config.ts
export default defineConfig({
  build: {
    outDir: 'dist',
    rollupOptions: {
      input: 'src/main.ts',
      output: {
        entryFileNames: 'admin.js'
      }
    }
  },
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src')
    }
  }
});
```

### Development

```bash
# Install dependencies
npm install

# Start dev server with hot reload
npm run dev

# Type checking
npm run type-check
```

### Production Build

```bash
# Build for production
npm run build

# Output: dist/admin.js
```

## Testing

### Test Setup

Tests use Vitest with jsdom environment:

```bash
# Run all tests
npm test

# Run with coverage
npm run test:coverage
```

### Test Structure

```typescript
// stores/__tests__/videos.store.test.ts
import { describe, it, expect, vi } from 'vitest';
import { createVideosStore } from '../videos.store';

describe('VideosStore', () => {
  it('should load videos', async () => {
    const store = createVideosStore();
    await store.loadVideos();
    expect(store.videos).toHaveLength(3);
  });
});
```

## Global Exports

The main entry point exports objects to the window for debugging and external access:

```typescript
// Available on window
window.Alpine        // Alpine.js instance
window.admin()       // Store factory
window.VLogApi       // API modules
window.VLogFormatters // Formatting utilities
```

## Mobile Responsiveness

The Admin UI is fully responsive:
- Collapsible navigation on mobile
- Touch-friendly controls
- Swipe gestures for cards
- Floating action buttons on mobile

## Contributing

### Adding a New Store

1. Create `stores/myfeature.store.ts`
2. Export `createMyFeatureStore()` function
3. Add to `stores/index.ts` composition
4. Add TypeScript types

### Adding a New Component

1. Create `components/base/vlog-mycomponent.ts`
2. Extend `HTMLElement`
3. Register in `components/index.ts`
4. Use in templates with `<vlog-mycomponent>`

### Adding an API Endpoint

1. Create `api/endpoints/myendpoint.ts`
2. Add TypeScript types to `api/types.ts`
3. Export from `api/index.ts`
4. Add to `window.VLogApi` in `main.ts`
