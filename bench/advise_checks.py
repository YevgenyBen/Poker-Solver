"""Defects any `/advise` response can be checked for, in one place.

Every benchmark harness used to carry its own `check()`, and each one
missed something the others caught. M262's replay found `/advise`
returning 200 with `hero.strategy: null`, which no harness flagged,
because none checked for it. Import this instead of re-typing checks
(M213's rule).

An instrument: nothing under `poker_solver/` or `api/` imports it.
"""
from __future__ import annotations

_TOLERANCE = 1e-6


def response_defects(payload: dict, request: dict | None = None) -> list:
    """Human-readable defects in one 200 response; empty when clean."""
    problems = []
    hero = payload.get("hero") or {}
    asked_hero = bool((request or {}).get("hero_cards")) or bool(hero.get("cards"))
    row = hero.get("strategy") if isinstance(hero, dict) else None
    if asked_hero and not row:
        problems.append("no hero row")
    rows = [row] if row else []
    rows += list((payload.get("strategy") or {}).values()) if isinstance(
        payload.get("strategy"), dict) and all(
        isinstance(v, dict) for v in (payload.get("strategy") or {}).values()) else []
    cap = payload.get("max_affordable_bb")
    for r in rows:
        total = sum(float(v) for v in r.values())
        if r and abs(total - 1.0) > 1e-4:
            problems.append("row sums to %.6f" % total)
        if any(float(v) < -_TOLERANCE for v in r.values()):
            problems.append("negative frequency")
        if cap is not None:
            for action in r:
                if ":" in action and float(action.split(":")[1]) > cap + 0.01:
                    problems.append("unaffordable %s (max %.2f)" % (action, cap))
    if row and len(row) > 1:
        values = [float(v) for v in row.values()]
        if max(values) - min(values) < 1e-9 and payload.get("solver_confidence") != "low":
            problems.append("uniform hero row at high confidence")
    if row is None and asked_hero and payload.get("solver_confidence") != "low":
        problems.append("missing hero row at high confidence")
    return sorted(set(problems))
