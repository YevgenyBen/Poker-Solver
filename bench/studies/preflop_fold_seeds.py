"""How much does the multiway preflop FOLD call move with the solver's
seed, by table size and by whether the player faces a raise?
(audit R9 / F61, M289)

M288 found, at six-handed 100bb only, that 37% of real preflop decisions
sit at nodes where the average hand's fold probability moves by more than
ten points between seeds, concentrated at nodes facing an open (0.15-0.20)
while first-in opens are stable. Players are told the opposite: three
`LOW_CONFIDENCE_TABLE_SIZES` strings say "lean on the fold-or-play call",
and `SIZING_CAVEAT_REASON` calls it "sounder". M282's lesson is that a
six-handed figure is not a table-wide one, so every size is measured.

**Method.** For each multiway table size, the shipped 100bb preflop solve
at the shipped seed (1) and three FRESH seeds (8, 9, 10 - never used by
an earlier study), equity held fixed so only the traversal seed moves.
Every preflop decision in the hand store's clean hands of that size,
walked on OUR 100bb tree, weighted by occurrence. Per node, `fold_move`
is the combo-weighted mean |fold probability difference| from seed 1,
averaged over the fresh seeds. Nodes whose every row is exactly the prior
at seed 1 are excluded - the on-demand trainer answers those. Nodes with
no fold action have no fold axis and are excluded.

Cells: FACING (a raise is on the path and something is owed) and FIRST-IN
(everything else: first to act, or a limped/checked option).

**PRE-REGISTERED READING RULE (fixed before any seed was solved):**

1. Per table size, F = the FACING cell's occurrence-weighted mean
   fold_move, S = the FIRST-IN cell's.
2. The disclosure FIRES at a table size iff F >= `MIN_MOVE` (0.10) and
   F - S >= `MIN_GAP` (0.05), and both hold in each split half (nodes
   alternated in path order).
3. The copy quotes each qualifying size's own F. Sizes that do not
   qualify stay silent, and are recorded.
4. Where it fires, every string that tells the player to lean on the
   fold-or-play call is scoped to first-in decisions.
5. Reduction (seed ensembles, M169's mechanism) is NOT part of this rule.

    python -m bench.studies.preflop_fold_seeds out.json [sizes...]
"""
from __future__ import annotations

import json
import sys

SEEDS = (1, 8, 9, 10)                  # 1 is the shipped seed
SIZES = (3, 4, 5, 6, 7, 8, 9)
STACK = 100.0
MIN_MOVE = 0.10
MIN_GAP = 0.05


def _weighted_mean(nodes):
    weight = sum(n["count"] for n in nodes)
    if not weight:
        return None, 0
    return sum(n["fold_move"] * n["count"] for n in nodes) / weight, weight


def summarise(nodes: list) -> dict:
    """Per table size: F, S and their split halves. `nodes` carry players,
    facing, fold_move and count. Pure."""
    out = {}
    for size in sorted({n["players"] for n in nodes}):
        mine = [n for n in nodes if n["players"] == size]
        entry = {}
        for name, pop in (("all", mine), ("half0", mine[0::2]), ("half1", mine[1::2])):
            f, fw = _weighted_mean([n for n in pop if n["facing"]])
            s, sw = _weighted_mean([n for n in pop if not n["facing"]])
            entry[name] = {"facing": f, "facing_weight": fw, "first_in": s, "first_in_weight": sw}
        out[size] = entry
    return out


def _qualifies(cell) -> bool:
    f, s = cell["facing"], cell["first_in"]
    return f is not None and s is not None and f >= MIN_MOVE and f - s >= MIN_GAP


def verdict(summary: dict) -> dict:
    """{size: F rounded to 3 places} for every size where it fires."""
    return {size: round(entry["all"]["facing"], 3) for size, entry in summary.items()
            if all(_qualifies(entry[k]) for k in ("all", "half0", "half1"))}


def fold_move(base, others, fold_index, weights) -> float:
    """Mean over `others` of the combo-weighted mean |fold difference|."""
    import numpy as np
    base = np.asarray(base, float)
    w = np.asarray(weights, float)
    moves = [float((np.abs(base[:, fold_index] - np.asarray(o, float)[:, fold_index]) * w).sum()
                   / w.sum()) for o in others]
    return float(np.mean(moves))


def main(argv=None) -> int:                              # pragma: no cover
    import gc
    import numpy as np
    from collections import Counter
    from api import config as cfg, solving
    from bench import hand_db
    from bench.real_replay import request_for, DEFAULT_WHERE
    from poker_solver.game_tree import DecisionNode, GameConfig
    from poker_solver.solver import solve_preflop

    args = list(argv if argv is not None else sys.argv[1:])
    out_path = args[0]
    sizes = tuple(int(a) for a in args[1:]) or SIZES
    db = hand_db.connect()
    equity = solving._get_multiway_equity_cache(cfg.MULTIWAY_PREFLOP_HANDS)
    nodes = []
    for size in sizes:
        paths = Counter()
        for hand in hand_db.query(db, f"({DEFAULT_WHERE}) AND n_players = {size}"):
            acts = [a for a in hand.streets() if a.kind != "show"]
            for i, a in enumerate(acts):
                if a.street != "preflop":
                    continue
                body, _, _ = request_for(hand, acts, i, STACK, hand.hole_cards,
                                         post=lambda b: (500, {}))
                if body is not None:
                    paths[tuple(body["preflop_action_path"])] += 1
        table = cfg.MULTIWAY_TABLE_CONFIGS[size]
        config = GameConfig(positions=table["positions"], stack_bb=STACK)
        solves = []
        for seed in SEEDS:
            solves.append(solve_preflop(config=config, hands=cfg.MULTIWAY_PREFLOP_HANDS,
                                        equity_cache=equity, iterations=table["iterations"],
                                        seed=seed, floor_regret=table.get("floor_regret")))
            print(f"{size}-max seed {seed} solved", flush=True)
        hands = [str(h) for h in solves[0].hands]
        weights = [h.combo_count for h in solves[0].hands]

        def locate(result, path):
            node = result.root
            for kind in path:
                action = next((a for a in node.legal_actions if a.kind == kind), None)
                if action is None:
                    return None
                node = node.children[action]
            return node if isinstance(node, DecisionNode) else None

        def rows(result, node):
            strat = result.strategy_at(node)
            keys = sorted(strat[hands[0]])
            return keys, np.array([[strat[h].get(k, 0.0) for k in keys] for h in hands])

        for path, count in sorted(paths.items()):
            nodes_by_seed = [locate(r, path) for r in solves]
            if nodes_by_seed[0] is None:
                continue
            keys, base = rows(solves[0], nodes_by_seed[0])
            if "fold" not in keys or np.ptp(base) < 1e-9:
                continue
            node = nodes_by_seed[0]
            owed = max(node.invested.values()) - node.invested[node.player_to_act]
            others = [rows(r, n)[1] for r, n in zip(solves[1:], nodes_by_seed[1:])]
            nodes.append({"players": size, "path": list(path), "count": count,
                          "facing": bool(node.raises_so_far >= 1 and owed > 1e-9),
                          "raises": node.raises_so_far, "owed": round(owed, 2),
                          "fold_move": fold_move(base, others, keys.index("fold"), weights)})
        del solves
        gc.collect()
        json.dump(nodes, open(out_path, "w"))
        print(json.dumps(summarise([n for n in nodes if n["players"] == size]), indent=1),
              flush=True)
    summary = summarise(nodes)
    print(json.dumps({str(k): v for k, v in summary.items()}, indent=1))
    print("FIRES AT", verdict(summary))
    return 0


if __name__ == "__main__":                                # pragma: no cover
    sys.exit(main())
