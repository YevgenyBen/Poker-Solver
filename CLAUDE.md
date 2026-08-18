# Poker Solver

A Texas Hold'em GTO solver engine, coupled with a webview so a user can explore
its output interactively.

## v1 scope

- Engine: Python (NumPy for hot paths).
- Preflop only — RFI / 3-bet / 4-bet / jam ranges for a chosen effective stack
  depth. No board cards, no postflop streets (yet).
- Heads-up (2 players) only, not multiway.
- CFR+ solver over a 169-starting-hand-class abstraction (single bet size per
  street, 4-raise cap before forced jam-or-fold).
- Backend: FastAPI (`GET /solve/{stack_bb}`), on-demand solve with an
  in-process cache + startup pre-warm of common stack depths.
- Frontend: React + TypeScript (Vite) in `frontend/`. `npm run dev` for a
  hot-reloading dev server (proxies `/solve` to FastAPI on :8000);
  `npm run build` produces `frontend/dist/`, which `api/main.py` serves
  directly at `/` when present. See `frontend/README.md`.

Full postflop/multiway support is out of scope for v1 but the module
boundaries (e.g. an injected `payoff_fn` at terminal tree nodes) are meant to
allow adding it later without a rewrite. **v2 is in progress**, growing
toward a full-table, any-street advisor — see the roadmap plan for the
phased approach (multiway preflop next, then postflop via on-demand subgame
solving, not a bigger precomputed table).

## Engine is standalone

`poker_solver/` has zero dependency on the API or any web framework — it's a
plain library usable on its own (`import poker_solver; poker_solver.solve_preflop(...)`).
This is enforced, not just true by convention: `tests/test_package_boundary.py`
scans every file under `poker_solver/` and fails the build if it ever imports
`fastapi`, `starlette`, `uvicorn`, or `api`. `api/` depends on `poker_solver`,
never the reverse.

Dependencies are split accordingly:
- `requirements.txt` — the engine only (`numpy`). `pip install -r requirements.txt`
  is enough to use `poker_solver` standalone.
- `requirements-api.txt` — adds the FastAPI backend (`-r requirements.txt` + `fastapi` + `uvicorn`).
- `requirements-dev.txt` — everything needed to run the full test suite (`-r requirements-api.txt` + `pytest` + `httpx2`).

## Workflow rules

- **Always work on a branch.** Never commit directly to `main`. Create a
  feature branch for every change, however small, and only merge into `main`
  when the user explicitly says to merge.
- **Tests are mandatory.** Every function gets a test.
  - Python: follow the `tests/` + pytest convention (one test module per
    source module, e.g. `poker_solver/foo.py` -> `tests/test_foo.py`).
  - Frontend: Vitest + React Testing Library, colocated as `*.test.ts(x)`
    next to the file it tests (e.g. `frontend/src/hands.ts` ->
    `frontend/src/hands.test.ts`).
- **Re-run the full suite after every change** — `python -m pytest tests/ -v`
  and, for anything under `frontend/`, `npm test` there too — before
  considering any change done, not just the tests for the file just touched.
- Ship one coherent improvement per PR (matches how this project started:
  scaffold -> missing-test PR -> merge).
