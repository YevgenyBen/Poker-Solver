"""How far can a stored solve stand in for the bucket a player asked at?
(audit R2, M295)

The 2026-09-20 audit: 133 of 1,200 real decisions took over five seconds,
nearly all first asks at a stack bucket no warmer had reached. M284 put
every multiway preflop solve on disk, so a NEIGHBOURING bucket is usually
there already - serving it would turn those waits into a file read.

**M124 refused this shape once**, and its control is the one that decides
it: bucketing 4bb away moves the strategy no more than re-running the
same solve under a different seed does, which is what licensed 5bb
buckets. Serving a stored bucket can mean a much larger jump, so the
question is how far that holds.

**The yardstick is the solver's own noise.** Both arms are compared to
the exact-bucket solve; the comparison that matters is SUBSTITUTION
DISTANCE against SEED CHANGE at the same bucket. M290 shipped a
four-seed ensemble at six-handed, which makes that yardstick tighter
than M124's: the thing a substitution has to beat is smaller now.

**PRE-REGISTERED READING RULE (fixed before any arm was solved):**

1. For each distance D, measure the combo-weighted TVD between the
   exact solve's row and the substituted solve's row, over real preflop
   decision nodes weighted by how often they occur in the hand store,
   and the share of nodes whose TOP ACTION changes.
2. The yardstick is the same two quantities between the exact solve and
   the same bucket solved with a DIFFERENT ensemble seed set.
3. A distance is ALLOWED if its median TVD <= the yardstick's median TVD
   AND its top-action-change share <= the yardstick's, in both split
   halves of the node population.
4. Adopt the LARGEST allowed distance, capped at `MAX_DISTANCE_BB`. If
   none is allowed, R2 is REFUSED and M124's finding stands at today's
   configuration.
5. Serving a substituted solve must never NAME a bet the player cannot
   afford, so the adopted rule may only substitute a bucket at or BELOW
   the real depth (F13's floor, which `canonical_stack_depth` already
   enforces for the bucket itself).

    python -m bench.studies.stack_substitution out.json
"""
from __future__ import annotations

import json
import statistics
import sys

BASE = 100.0
DISTANCES = (5.0, 10.0, 20.0, 40.0)
MAX_DISTANCE_BB = 40.0
YARDSTICK_SEED = 101          # a fresh ensemble seed set for the noise arm
SIZE = 6


def cell(rows: list, key: str) -> dict:
    """Median TVD, and the mean share of HANDS whose top action changes.

    `..._changed` is already a combo-weighted share within its node, so
    this averages those shares. The first draft counted a node as
    "changed" whenever the share was non-zero, which made seed noise read
    94.6% and left the rule unable to separate anything - the metric, not
    the arms.
    """
    if not rows:
        return {"n": 0}
    return {"n": len(rows),
            "tvd": statistics.median(r[f"{key}_tvd"] for r in rows),
            "changed": statistics.mean(r[f"{key}_changed"] for r in rows)}


def summarise(rows: list) -> dict:
    """The yardstick and every distance, pooled and per split half. Pure."""
    out = {}
    for name, pop in (("all", rows), ("half0", rows[0::2]), ("half1", rows[1::2])):
        entry = {"seed": cell(pop, "seed")}
        for distance in DISTANCES:
            entry[str(distance)] = cell(pop, f"d{distance:g}")
        out[name] = entry
    return out


def allowed(summary: dict, distance: float) -> bool:
    """Is this distance inside the solver's own noise, in every half?"""
    key = str(distance)
    for name in ("all", "half0", "half1"):
        cells = summary[name]
        if not cells.get(key, {}).get("n") or not cells["seed"].get("n"):
            return False
        if cells[key]["tvd"] > cells["seed"]["tvd"]:
            return False
        if cells[key]["changed"] > cells["seed"]["changed"]:
            return False
    return True


def verdict(summary: dict):
    """(distance or None, text) - the pre-registered rule, mechanically."""
    passing = [d for d in DISTANCES if d <= MAX_DISTANCE_BB and allowed(summary, d)]
    if not passing:
        return None, "REFUSED: no distance stays inside the solver's own noise"
    return max(passing), f"ADOPT substitution up to {max(passing):g}bb below the ask"


def main(argv=None) -> int:                              # pragma: no cover
    import numpy as np
    from collections import Counter

    from api import config as cfg, solving
    from bench import hand_db
    from bench.real_replay import DEFAULT_WHERE, request_for
    from poker_solver.game_tree import DecisionNode, GameConfig
    from poker_solver.solver import solve_preflop

    out_path = (argv if argv is not None else sys.argv[1:])[0]
    table = cfg.MULTIWAY_TABLE_CONFIGS[SIZE]
    equity = solving._get_multiway_equity_cache(cfg.MULTIWAY_PREFLOP_HANDS)

    def solve(stack, seed):
        result = solve_preflop(
            config=GameConfig(positions=table["positions"], stack_bb=stack),
            hands=cfg.MULTIWAY_PREFLOP_HANDS, equity_cache=equity,
            iterations=table["iterations"], seed=seed,
            floor_regret=table.get("floor_regret"),
            ensemble=table.get("ensemble", 1))
        print("solved %.0fbb seed %d" % (stack, seed), flush=True)
        return result

    exact = solve(BASE, 1)
    noise = solve(BASE, YARDSTICK_SEED)
    arms = {f"d{d:g}": solve(BASE - d, 1) for d in DISTANCES}
    hands = [str(h) for h in exact.hands]
    weights = np.array([h.combo_count for h in exact.hands], dtype=float)

    db = hand_db.connect()
    paths = Counter()
    for hand in hand_db.query(db, f"({DEFAULT_WHERE}) AND n_players = {SIZE}"):
        acts = [a for a in hand.streets() if a.kind != "show"]
        for i, a in enumerate(acts):
            if a.street != "preflop":
                continue
            body, _, _ = request_for(hand, acts, i, BASE, hand.hole_cards,
                                     post=lambda b: (500, {}))
            if body is not None:
                paths[tuple(body["preflop_action_path"])] += 1

    def rows_at(result, path):
        node = result.root
        for kind in path:
            action = next((a for a in node.legal_actions if a.kind == kind), None)
            if action is None:
                return None, None
            node = node.children[action]
        if not isinstance(node, DecisionNode):
            return None, None
        strat = result.strategy_at(node)
        keys = sorted(strat[hands[0]])
        return keys, np.array([[strat[h].get(k, 0.0) for k in keys] for h in hands])

    def compare(base_keys, base_rows, other):
        keys, rows = other
        if rows is None or keys != base_keys or rows.shape != base_rows.shape:
            return None
        tvd = float((0.5 * np.abs(rows - base_rows).sum(1) * weights).sum() / weights.sum())
        changed = float((np.argmax(rows, 1) != np.argmax(base_rows, 1))
                        @ weights / weights.sum())
        return tvd, changed

    rows = []
    for path, count in sorted(paths.items()):
        keys, base = rows_at(exact, path)
        if base is None or np.ptp(base) < 1e-9:
            continue                      # the on-demand trainer owns these
        row = {"path": list(path), "count": count}
        pairs = {"seed": rows_at(noise, path)}
        pairs.update({name: rows_at(arm, path) for name, arm in arms.items()})
        ok = True
        for name, other in pairs.items():
            got = compare(keys, base, other)
            if got is None:
                ok = False
                break
            row[f"{name}_tvd"], row[f"{name}_changed"] = got
        if ok:
            rows.append(row)
    json.dump(rows, open(out_path, "w"))
    summary = summarise(rows)
    print(json.dumps(summary, indent=1))
    print(verdict(summary)[1])
    return 0


if __name__ == "__main__":                                # pragma: no cover
    sys.exit(main())
