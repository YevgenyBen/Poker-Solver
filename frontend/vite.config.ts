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
      // Its own entry, not covered by '/solve' above — M25's route is
      // named /preflop_walk, not /solve_something, unlike every other
      // POST route this app has added since M14 (the exact class of bug
      // M10 hit for real with /equity, before that entry existed).
      '/preflop_walk': 'http://127.0.0.1:8000',
      // M56: same story a third time — /advise is a new prefix, not
      // covered by '/solve'. Caught by live browser verification (a
      // real 404), NOT by the unit tests, which stub fetch and so can
      // never see a proxy gap. That's now three separate milestones
      // (M10's /equity, M25's /preflop_walk, this) where adding a route
      // whose name doesn't start with '/solve' silently fell through to
      // the SPA's index.html in dev.
      '/advise': 'http://127.0.0.1:8000',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/setupTests.ts',
  },
})
