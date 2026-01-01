import { defineConfig } from 'vitest/config';
import { resolve } from 'path';

export default defineConfig({
  test: {
    globals: true,
    environment: 'node',
    include: ['src/**/*.test.ts'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      include: ['src/**/*.ts'],
      exclude: ['src/**/*.test.ts', 'src/**/index.ts'],
    },
  },
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src'),
      '@api': resolve(__dirname, 'src/api'),
      '@components': resolve(__dirname, 'src/components'),
      '@styles': resolve(__dirname, 'src/styles'),
    },
  },
});
