"""Does a preflop node's SPREAD across hands say whether it was learned?
(audit R8 / F60, M288)

F60: facing an under-the-gun open with everyone else folded, the big
blind's row is close to uniform for every hand - 72o and KQo alike - and
was served at "high". Nothing fired, because `_row_is_the_prior` wants
EXACT uniformity, and M149 kept it exact on purpose: one row near
indifference can be a real, close decision.

**The signal here is the NODE, not the row.** A whole preflop node at
which AA and 72o play the same is not a close decision; it is a node the
solve never differentiated. `spread` is the combo-weighted mean TVD of
each hand's row from the node's mean row: 0 when every hand plays alike,
0.25-0.43 at nodes measured as learned.

**Why not train such nodes instead.** Forcing M150's trainer onto every
node of M287's population moved WELL-LEARNED nodes as much as the
near-uniform one (TVD 0.27-0.72), and at many deep nodes pushed weak
hands to continue 0.96-1.00 - M251's defect, larger. A trained row is
another answer, not ground truth, so this study discloses; it does not
repair.

**Ground truth: reproducibility.** The same 6-max 100bb preflop solve at
the shipped seed and three more, equity held fixed (only the traversal
seed moves). A node is UNSTABLE when its combo-weighted row TVD between
the shipped seed and another, averaged over the three, exceeds
`UNSTABLE_TVD` - 0.15, twice the 0.0725 M254 measured on multiway rows
it calls stable and half the 0.30 M245 disclosed as irreproducible.

**Population:** every distinct preflop path in the hand store's
six-handed hands, weighted by how often it occurs, at nodes where the
on-demand trainer does NOT fire (not every row exactly the prior) -
because the gate is only ever read at those.

**PRE-REGISTERED READING RULE (fixed before any extra seed was solved):**

1. For each T in `THRESHOLDS`, "fires" means spread < T. Precision is
   the occurrence-weighted share of firing decisions at UNSTABLE nodes;
   the silent rate is the same share where it does not fire.
2. A threshold QUALIFIES when precision >= 0.75, precision exceeds the
   silent rate by >= 0.30, and both hold in each split half (nodes
   alternated by path order).
3. Adopt the LARGEST qualifying T (widest coverage that still meets the
   bar). If none qualifies, the result is NULL: no gate ships, and F60
   stays disclosed only through M287's two-live warning.
4. Adoption means DISCLOSURE (`solver_confidence` "low" with a reason),
   never training.

**RESULT OF THE RULE ABOVE (seeds 1-4): NULL.** 69% of real decisions
(occurrence-weighted) sit at nodes whose whole row moves by more than
0.15 between seeds - the under-the-gun open itself moves 0.14, and the
busiest nodes 0.17-0.34 - so no threshold could beat silence by 0.30.
The ground truth was the wrong axis: a whole row is dominated by the
split among NON-fold actions (raise vs all-in), which M98 measured as
seed-driven and `SIZING_CAVEAT_REASON` already discloses. F60's claim is
about the FOLD axis.

**FOLLOW-UP RULE (written after that NULL, run on FRESH seeds 5-7 so it
cannot reuse the data that motivated it).** Identical thresholds, bars
and halves. Ground truth moves to the fold axis: a node is UNSTABLE when
the combo-weighted mean |fold probability difference| between the
shipped seed and each fresh seed, averaged, exceeds `UNSTABLE_FOLD`
(0.10 - the average hand's fold call moving by ten points). Nodes with
no fold action (a checked option) have no fold axis and are excluded.
Because it follows a NULL, it needs to clear the SAME bar with no
loosening; a pass here is weaker evidence than a pass on the first rule
would have been, and is recorded as such.

**RESULT OF THE FOLLOW-UP (seeds 1, 5-7): NULL.** Pooled, spread < 0.15
clears both bars (precision 0.83 against a silent rate of 0.36), and one
split half does not (0.38) - the replication M166 failed and M189 passed.
The F60 node is itself low-spread AND unstable (fold moves 0.157), so the
signal is right about the case that prompted it and not as a rule.

**What both runs found instead (F61):** 37% of real six-handed preflop
decisions sit at nodes where the average hand's fold call moves by more
than ten points between seeds; nodes facing an open move 0.15-0.20,
while the first-in opens are stable (under the gun 0.023). Both runs'
rows are committed under `tests/data/node_spread_*_m288.json`.

    python -m bench.studies.node_spread --fold
"""
from __future__ import annotations

import json
import sys

UNSTABLE_TVD = 0.15
UNSTABLE_FOLD = 0.10
FOLD_SEEDS = (1, 5, 6, 7)       # the follow-up's fresh seeds
THRESHOLDS = (0.05, 0.10, 0.15, 0.20)
SEEDS = (1, 2, 3, 4)            # 1 is the shipped seed
MIN_PRECISION = 0.75
MIN_SEPARATION = 0.30


def spread(rows, weights) -> float:
    """Combo-weighted mean TVD of each row from the weighted mean row.
    `rows` is hands x actions, `weights` the combo count per hand."""
    import numpy as np
    rows = np.asarray(rows, float)
    w = np.asarray(weights, float)
    mean = (rows * w[:, None]).sum(0) / w.sum()
    return float((0.5 * np.abs(rows - mean).sum(1) * w).sum() / w.sum())


def row_tvd(a, b, weights) -> float:
    """Combo-weighted mean TVD between two solves' rows at one node."""
    import numpy as np
    a, b, w = np.asarray(a, float), np.asarray(b, float), np.asarray(weights, float)
    return float((0.5 * np.abs(a - b).sum(1) * w).sum() / w.sum())


def fold_tvd(a, b, fold_index, weights) -> float:
    """Combo-weighted mean |difference| in the probability of folding."""
    import numpy as np
    a, b, w = np.asarray(a, float), np.asarray(b, float), np.asarray(weights, float)
    return float((np.abs(a[:, fold_index] - b[:, fold_index]) * w).sum() / w.sum())


def _cell(nodes, keep):
    chosen = [n for n in nodes if keep(n)]
    weight = sum(n["count"] for n in chosen)
    if not weight:
        return None, 0
    return sum(n["count"] for n in chosen if n["unstable"]) / weight, weight


def summarise(nodes: list) -> dict:
    """Per threshold: precision, silent rate, weights, and the same in
    each split half. `nodes` carry spread, unstable and count. Pure."""
    out = {}
    halves = (nodes[0::2], nodes[1::2])
    for t in THRESHOLDS:
        entry = {}
        for name, pop in (("all", nodes), ("half0", halves[0]), ("half1", halves[1])):
            p, pw = _cell(pop, lambda n: n["spread"] < t)
            s, sw = _cell(pop, lambda n: n["spread"] >= t)
            entry[name] = {"precision": p, "fires_weight": pw, "silent_rate": s,
                           "silent_weight": sw}
        out[t] = entry
    return out


def _qualifies(cell) -> bool:
    p, s = cell["precision"], cell["silent_rate"]
    return (p is not None and s is not None and p >= MIN_PRECISION
            and p - s >= MIN_SEPARATION)


def verdict(summary: dict):
    """(threshold or None, text) - the pre-registered rule, mechanically."""
    passing = [t for t in THRESHOLDS
               if all(_qualifies(summary[t][k]) for k in ("all", "half0", "half1"))]
    if not passing:
        return None, "NULL: no threshold meets the bar; no gate ships"
    t = max(passing)
    return t, f"ADOPT spread < {t}"


def main(argv=None) -> int:                              # pragma: no cover
    """Solve the extra seeds, measure every hand-store path, apply the rule."""
    import numpy as np
    from collections import Counter
    from api import config as cfg, solving
    from bench import hand_db
    from bench.real_replay import request_for
    from poker_solver.game_tree import DecisionNode, GameConfig
    from poker_solver.solver import solve_preflop

    args = list(argv if argv is not None else sys.argv[1:])
    fold_axis = "--fold" in args
    args = [a for a in args if a != "--fold"]
    out_path = (args or ["node_spread_rows.json"])[0]
    seeds = FOLD_SEEDS if fold_axis else SEEDS
    table = cfg.MULTIWAY_TABLE_CONFIGS[6]
    config = GameConfig(positions=table["positions"], stack_bb=100.0)
    equity = solving._get_multiway_equity_cache(cfg.MULTIWAY_PREFLOP_HANDS)
    solves = {}
    for seed in seeds:
        solves[seed] = solve_preflop(config=config, hands=cfg.MULTIWAY_PREFLOP_HANDS,
                                     equity_cache=equity, iterations=table["iterations"],
                                     seed=seed, floor_regret=table.get("floor_regret"))
        print(f"solved seed {seed}", flush=True)
    hands = [str(h) for h in solves[1].hands]
    weights = [h.combo_count for h in solves[1].hands]

    paths = Counter()
    db = hand_db.connect()
    for hand in hand_db.query(db, "source = 'pluribus'"):
        acts = [a for a in hand.streets() if a.kind != "show"]
        for i, a in enumerate(acts):
            if a.street != "preflop":
                continue
            body, _, _ = request_for(hand, acts, i, 100.0, hand.hole_cards,
                                     post=lambda b: (500, {}))
            if body is not None:
                paths[tuple(body["preflop_action_path"])] += 1

    def rows_at(result, path):
        node = result.root
        for kind in path:
            action = next((a for a in node.legal_actions if a.kind == kind), None)
            if action is None:
                return None
            node = node.children[action]
        if not isinstance(node, DecisionNode):
            return None
        strat = result.strategy_at(node)
        keys = sorted(strat[hands[0]])
        return keys, np.array([[strat[h].get(k, 0.0) for k in keys] for h in hands])

    nodes = []
    for path, count in sorted(paths.items()):
        got = rows_at(solves[1], path)
        if got is None or np.ptp(got[1]) < 1e-9:
            continue                      # the on-demand trainer owns these
        keys, base = got
        others = [rows_at(solves[s], path)[1] for s in seeds[1:]]
        if fold_axis:
            if "fold" not in keys:
                continue                  # no fold axis at a checked option
            tvd = float(np.mean([fold_tvd(base, o, keys.index("fold"), weights)
                                 for o in others]))
            unstable = tvd > UNSTABLE_FOLD
        else:
            tvd = float(np.mean([row_tvd(base, o, weights) for o in others]))
            unstable = tvd > UNSTABLE_TVD
        nodes.append({"path": list(path), "count": count, "spread": spread(base, weights),
                      "seed_tvd": tvd, "unstable": unstable})
    json.dump(nodes, open(out_path, "w"))
    summary = summarise(nodes)
    print(json.dumps({str(k): v for k, v in summary.items()}, indent=1))
    print(verdict(summary)[1])
    return 0


if __name__ == "__main__":                                # pragma: no cover
    sys.exit(main())
