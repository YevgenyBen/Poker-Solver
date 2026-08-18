# Poker Solver

A heads-up Texas Hold'em preflop GTO solver (CFR+), with a FastAPI backend
and a React frontend for exploring the resulting strategies.

## Getting started

The engine (`poker_solver/`) is a standalone library with no web-framework
dependency — see `CLAUDE.md`. Install just what you need:

```bash
pip install -r requirements.txt       # engine only (numpy)
pip install -r requirements-api.txt   # + FastAPI backend
pip install -r requirements-dev.txt   # + pytest, for running the test suite
```

```bash
# Backend
pip install -r requirements-dev.txt
pytest

# Frontend (in another terminal)
cd frontend
npm install
npm run dev
```

Then run the API (`uvicorn api.main:app`) alongside `npm run dev` and open
the URL Vite prints — see `frontend/README.md` for the dev-vs-production
workflow.

## Project layout

- `poker_solver/` — the solver engine (cards, equity, game tree, CFR+)
- `api/` — FastAPI app exposing the solver over HTTP
- `frontend/` — React + TypeScript UI (Vite)
- `tests/` — Python unit tests (pytest)

## Status

v1 complete: preflop-only, heads-up, single-bet-size abstraction. v2 is in
progress, growing toward a full-table, any-street advisor. See `CLAUDE.md`
for the full scope and workflow rules.
