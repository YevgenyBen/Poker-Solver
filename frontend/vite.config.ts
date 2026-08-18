/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Forward API calls to the FastAPI dev server (uvicorn on :8000) so
    // `npm run dev` can be used standalone with hot reload, while the
    // production build gets served *by* FastAPI itself (see api/main.py).
    proxy: {
      '/solve': 'http://127.0.0.1:8000',
      '/equity': 'http://127.0.0.1:8000',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/setupTests.ts',
  },
})
