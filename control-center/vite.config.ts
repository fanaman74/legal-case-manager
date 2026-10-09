import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        // Forward the browser origin's host so the API can enforce same-origin writes.
        changeOrigin: false,
      },
    },
  },
});
