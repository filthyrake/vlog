/**
 * Studio stores composition
 */

import { createAuthStore } from './auth.store';
import { createStudioStore } from './studio.store';

export function createStores() {
  return {
    auth: createAuthStore(),
    studio: createStudioStore(),
  };
}

export type Stores = ReturnType<typeof createStores>;
