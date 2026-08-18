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
- Frontend: plain HTML/CSS/vanilla JS in `webview/`, no build step.

Full postflop/multiway support is out of scope for now but the module
boundaries (e.g. an injected `payoff_fn` at terminal tree nodes) are meant to
allow adding it later without a rewrite.

## Workflow rules

- **Always work on a branch.** Never commit directly to `main`. Create a
  feature branch for every change, however small, and only merge into `main`
  when the user explicitly says to merge.
- **Tests are mandatory.** Every function gets a test. Follow the existing
  `tests/` + pytest convention (one test module per source module, e.g.
  `poker_solver/foo.py` -> `tests/test_foo.py`).
- **Re-run the full suite after every change** — `python -m pytest tests/ -v`
  — before considering any change done, not just the tests for the file just
  touched.
- Ship one coherent improvement per PR (matches how this project started:
  scaffold -> missing-test PR -> merge).
