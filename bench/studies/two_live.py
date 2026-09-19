"""The two-live preflop study, re-runnable from the repository (M285).

Produces every `PREFLOP_TWO_LIVE_*` and `PREFLOP_MANY_LIVE_*` constant the
two-live warnings and `SIZING_CAVEAT_REASON` quote. Before M285 the only
copy of this study was a scratch file in a session temp directory, and
this is the figure that went stale three times (M281, M282, and the
sizing caveat's duplicate found in M285).

**Method (M282).** Reject-sample the gate's own predicate off the real
tree - exactly two live, owed at least `PREFLOP_TWO_LIVE_RERAISE_MIN_TO_CALL_BB` -
at every table size the warning can fire at, and ask `/advise` with the
five trash hands M251 used. The control is the same sampler with three or
more live. A hand-written list of paths generates itself (M252), which is
how M251's figure understated its own defect.

    python -m bench.studies.two_live            # measure and compare (~1-2h)
    python -m bench.studies.two_live --quick    # 2 spots a size, smoke only

Compares the fresh figures with `api/config.py` and exits non-zero on
drift beyond the sample's own 95% interval - the reading rule M282 fixed
before its run.
"""
from __future__ import annotations

import json
import random
import statistics
import sys

TRASH = ("7c2d", "8c3d", "9c2d", "Tc2d", "6c2d")
STACK = 100.0
TABLE_SIZES = (3, 4, 5, 6, 7, 8, 9)
TARGET_PER_SIZE = 12
SEED = 20260918
RAISE_WEIGHT = 3.0

def seats(players: int) -> tuple:
    """The product's own seat list for a table size, so the study walks
    exactly the tree `/advise` builds rather than a copy of it."""
    from api import config as cfg
    return tuple(cfg.MULTIWAY_TABLE_CONFIGS[players]["positions"])


def draw_spots(min_to_call: float, control: bool = False, target: int = TARGET_PER_SIZE,
               seed: int = SEED, sizes=TABLE_SIZES, max_walks: int = 4000) -> list:
    """Decision nodes the gate fires on (or, with `control`, the same
    nodes with three or more live), drawn off the real tree."""
    from poker_solver.game_tree import DecisionNode, GameConfig, build_game_tree

    rng = random.Random(seed)
    spots = []
    for players in sizes:
        tree = build_game_tree(GameConfig(positions=seats(players), stack_bb=STACK))
        seen, walks = {}, 0
        while len(seen) < target and walks < max_walks:
            walks += 1
            node, path = tree, []
            while isinstance(node, DecisionNode) and len(path) < 12:
                live = [p for p in node.invested if p not in node.folded]
                owed = max(node.invested.values()) - node.invested[node.player_to_act]
                hit = len(live) >= 3 if control else len(live) == 2
                if hit and owed >= min_to_call:
                    key = tuple(path)
                    if key not in seen:
                        seen[key] = {"players": players, "path": list(path),
                                     "raises": node.raises_so_far, "owed": round(owed, 2)}
                    break
                legal = [a for a in node.legal_actions if a.kind != "all_in"]
                if not legal:
                    break
                weights = [RAISE_WEIGHT if a.kind == "raise" else 1.0 for a in legal]
                action = rng.choices(legal, weights=weights, k=1)[0]
                path.append(action.kind)
                node = node.children[action]
        spots.extend(seen.values())
    return spots


def _continues(row: dict) -> float:
    return sum(v for k, v in row.items() if k.split(":")[0] in ("call_or_check", "raise", "all_in"))


def measure(spots: list, post) -> list:
    """Ask `/advise` at each spot. `post(body) -> (status, json)`."""
    rows = []
    for spot in spots:
        trash, fired = [], None
        for hero in TRASH:
            status, js = post({"stack_bb": STACK, "players": spot["players"],
                               "hero_cards": hero, "preflop_action_path": spot["path"]})
            if status != 200:
                continue
            trash.append(_continues(js["hero"]["strategy"] or {}))
            if fired is None:
                fired = "two-player pot" in (js.get("solver_confidence_reason") or "")
        if trash:
            rows.append(dict(spot, trash_continue=sum(trash) / len(trash), warning_fired=fired))
    return rows


def summarise(rows: list, control_rows: list, grade_min_raises: int) -> dict:
    """The constants the copy quotes, from measured rows. Pure, so it is
    tested without paying for a run."""
    t = [r["trash_continue"] for r in rows]
    four = [r["trash_continue"] for r in rows if r["raises"] >= grade_min_raises]
    three = [r["trash_continue"] for r in rows if r["raises"] < grade_min_raises]
    ctl = [r["trash_continue"] for r in control_rows]
    return {
        "PREFLOP_TWO_LIVE_TRASH_CONTINUES": round(statistics.mean(t), 4),
        "PREFLOP_TWO_LIVE_WORST": round(max(t), 4),
        "PREFLOP_TWO_LIVE_NODES_OVER_90": sum(1 for x in t if x > 0.90),
        "PREFLOP_TWO_LIVE_NODES": len(t),
        "PREFLOP_TWO_LIVE_FOUR_BET_CONTINUES": round(statistics.mean(four), 4) if four else None,
        "PREFLOP_TWO_LIVE_FOUR_BET_NODES": len(four),
        "PREFLOP_TWO_LIVE_THREE_BET_CONTINUES": round(statistics.mean(three), 4) if three else None,
        "PREFLOP_TWO_LIVE_THREE_BET_NODES": len(three),
        "PREFLOP_MANY_LIVE_TRASH_CONTINUES": round(statistics.mean(ctl), 4) if ctl else None,
        "PREFLOP_MANY_LIVE_NODES": len(ctl),
        # Not quoted - the width the rule reads drift against.
        "_ci95_trash": 1.96 * statistics.stdev(t) / len(t) ** 0.5 if len(t) > 1 else None,
        "_warning_fired": sum(1 for r in rows if r["warning_fired"]),
    }


def drift(fresh: dict, cfg) -> list:
    """Quoted means that moved by more than the fresh sample's own 95%
    interval (M282's rule), or counts that changed at all."""
    out = []
    ci = fresh.get("_ci95_trash") or 0.0
    for name, value in fresh.items():
        if name.startswith("_") or value is None:
            continue
        shipped = getattr(cfg, name)
        if isinstance(value, float) and abs(value - shipped) > ci:
            out.append(f"{name}: shipped {shipped}, measured {value} (95% width {ci:.4f})")
        elif isinstance(value, int) and value != shipped:
            # The sample is seeded, so a count moves only if the tree or the
            # engine did - which is exactly what should be reported.
            out.append(f"{name}: shipped {shipped}, measured {value}")
    return out


def main(argv=None) -> int:                              # pragma: no cover
    argv = sys.argv[1:] if argv is None else argv
    target = 2 if "--quick" in argv else TARGET_PER_SIZE
    from fastapi.testclient import TestClient
    from api import config as cfg
    from api.main import app

    client = TestClient(app)

    def post(body):
        r = client.post("/advise", json=body)
        return r.status_code, (r.json() if r.status_code == 200 else {})

    gate = cfg.PREFLOP_TWO_LIVE_RERAISE_MIN_TO_CALL_BB
    rows = measure(draw_spots(gate, target=target), post)
    control = measure(draw_spots(gate, control=True, target=target), post)
    fresh = summarise(rows, control, cfg.PREFLOP_TWO_LIVE_GRADE_MIN_RAISES)
    print(json.dumps(fresh, indent=1))
    if fresh["_warning_fired"] != len(rows):
        print(f"CONTROL FAILED: the warning fired on {fresh['_warning_fired']} of {len(rows)}")
        return 2
    moved = drift(fresh, cfg)
    for line in moved:
        print("DRIFT", line)
    return 1 if moved else 0


if __name__ == "__main__":                                # pragma: no cover
    sys.exit(main())
