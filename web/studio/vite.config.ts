import { defineConfig } from 'vite';
import { resolve } from 'path';

export default defineConfig({
  root: resolve(import.meta.dirname),

  // Base path for assets when served under /studio/
  base: '/studio/',

  // Build configuration
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        main: resolve(import.meta.dirname, 'index.html'),
        studio: resolve(import.meta.dirname, 'src/main.ts'),
      },
      output: {
        assetFileNames: 'assets/[name].[hash][extname]',
        chunkFileNames: 'assets/[name].[hash].js',
        entryFileNames: 'assets/[name].[hash].js',
      },
    },
  },

  // Development server configuration
  server: {
    port: 3002,
    open: false,
    // Proxy API requests to the admin API server
    proxy: {
      '/api': {
        target: 'http://localhost:9001',
        changeOrigin: true,
        secure: false,
      },
    },
  },

  // Preview server configuration
  preview: {
    port: 3003,
    proxy: {
      '/api': {
        target: 'http://localhost:9001',
        changeOrigin: true,
        secure: false,
      },
    },
  },

  // Resolve configuration
  resolve: {
    alias: {
      '@': resolve(import.meta.dirname, 'src'),
      '@api': resolve(import.meta.dirname, 'src/api'),
      '@stores': resolve(import.meta.dirname, 'src/stores'),
      '@utils': resolve(import.meta.dirname, 'src/utils'),
      '@styles': resolve(import.meta.dirname, 'src/styles'),
    },
  },

  // CSS configuration
  css: {
    devSourcemap: true,
  },
});
