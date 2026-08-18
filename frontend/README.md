# Poker Solver frontend

React + TypeScript, built with Vite. Talks to the FastAPI backend's
`GET /solve/{stack_bb}` endpoint (see `../api/`).

## Development

Two processes, in two terminals:

```bash
# Terminal 1, from the repo root — the API
uvicorn api.main:app

# Terminal 2, from this directory — hot-reloading dev server
npm install
npm run dev
```

Then open the URL Vite prints (`http://localhost:5173`). Requests to
`/solve/*` are proxied to the FastAPI server (see `vite.config.ts`), so
you don't need CORS or a second origin to deal with.

## Production build

```bash
npm run build
```

Outputs to `dist/`. `api/main.py` serves this directory directly at `/`
when it exists — run this once (or in CI/deploy), then a single
`uvicorn api.main:app` serves both the API and the built frontend.

## Tests

```bash
npm test          # run once
npm run test:watch
```

Vitest + React Testing Library. Pure logic (`hands.ts`, `colors.ts`,
`api.ts`) is tested directly; components and the `useOpeningRange` hook
are tested through React Testing Library with a mocked `fetch`.
