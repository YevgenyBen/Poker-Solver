"""Does a seed ENSEMBLE steady the six-handed preflop fold call, without
moving it away from strong play? (audit R10 / F61, M290)

M289 disclosed that, facing a raise at six-handed, the average hand's
fold probability moves 0.150 between solver seeds against 0.067 first in.
M169 built a seed ensemble for the multiway flop and refused it for
REQUEST latency (0.8s -> 3.2s). A preflop solve is precomputed and
cached, so here the cost lands on warm-up time and on a cold first ask,
and the question is only whether it works.

**Arms (K = 4, fixed in advance).** 6-max, 100bb, equity fixed so only
the traversal seed moves (F61's noise):
- singles at seed 1 (shipped) and fresh seeds 11, 12, 13;
- ensembles of four at seeds 1-4 and at 21-24, 31-34, 41-44.

**Stability.** Per node, the combo-weighted mean |fold difference| of the
seed-1 arm from each of its three fresh counterparts, averaged, and
weighted by how often the node occurs in the hand store's clean
six-handed hands - M289's measurement, repeated for both arms.

**Outside.** Every preflop decision the published six-handed AI in the
hand store made at a node with a fold option: our probability on ITS
fold/continue choice, for its hand's class, read from the cold solve
(the on-demand trainer's nodes - every row exactly the prior in either
arm - are excluded, as in M288/M289). Paired per decision, ensemble
minus single.

**PRE-REGISTERED READING RULE (fixed before any ensemble was solved):**

1. STABILITY: R = the ensemble arm's facing-a-raise fold move over the
   single arm's must be <= `MAX_RATIO` (0.70) - pooled and in both split
   halves.
2. OUTSIDE: the paired agreement delta must not be WORSE at 2 sigma or
   more, on facing-a-raise decisions and on all decisions.
3. COVERAGE: at least `MIN_WARM_SHARE` (95%) of real six-handed hands
   must sit at depths the prewarm or background warmer covers, since an
   unwarmed bucket's first ask costs K times as much.
4. ADOPT K=4 at six-handed iff 1-3 all hold. The fold-seed note at six
   is then re-judged on the ENSEMBLE arm with M289's own rule (facing >=
   0.10 and >= 0.05 over first-in, both halves), and quotes the new
   figure or stops firing.
5. Other table sizes are NOT adopted from this: M168.

**RESULT (M290).** Facing-a-raise fold move 0.136 (singles) -> 0.079
(ensembles), R = 0.58 (halves 0.61 / 0.57); agreement with the outside
player's own choices +0.0012 at 0.60 sigma over 4,607 facing decisions
(+0.0006, 0.59 sigma over all 9,859). **REFUSED on coverage alone: 91.6%
of real six-handed hands at warmed depths.** Coverage is a property of
the warm lists, not a measurement, so eleven six-handed buckets were
added to `MULTIWAY_DISK_WARM` (96.2%) and the rule re-applied to the
SAME recorded rows adopts. Both verdicts re-derive in tests from
`tests/data/preflop_ensemble_m290.json`. On the ensemble arm the
six-handed fold-seed note stops firing (facing 0.079 < M289's 0.10).

**The rule's cost premise was wrong by 2x, and it is recorded.** Rule 3
assumed an unwarmed first ask would cost K (4) times as much. Measured,
it costs ~8.5x: a cold 135bb six-handed first ask took 170.6s against
~20s for the single solve (M286), because each extra seed deals hands
whose equity the lazy cache has not sampled yet. The startup warm at
100bb went to 239.5s. The rule still passes as written; the cost lands
on the ~4% of real six-handed hands at unwarmed depths, and on a fresh
server until the warmers finish.

    python -m bench.studies.preflop_ensemble out.json
"""
from __future__ import annotations

import json
import math
import statistics
import sys

K = 4
SINGLE_SEEDS = (1, 11, 12, 13)
ENSEMBLE_SEEDS = (1, 21, 31, 41)       # each the first of K consecutive
MAX_RATIO = 0.70
MIN_WARM_SHARE = 0.95
SIZE = 6
STACK = 100.0


def _weighted(nodes, key):
    weight = sum(n["count"] for n in nodes)
    return (sum(n[key] * n["count"] for n in nodes) / weight) if weight else None


def stability(nodes: list) -> dict:
    """Per arm, the facing and first-in weighted fold move; and the ratio,
    pooled and per split half. Pure."""
    out = {}
    for name, pop in (("all", nodes), ("half0", nodes[0::2]), ("half1", nodes[1::2])):
        facing = [n for n in pop if n["facing"]]
        first = [n for n in pop if not n["facing"]]
        cell = {arm: {"facing": _weighted(facing, f"{arm}_move"),
                      "first_in": _weighted(first, f"{arm}_move")}
                for arm in ("single", "ensemble")}
        s, e = cell["single"]["facing"], cell["ensemble"]["facing"]
        cell["ratio"] = (e / s) if s else None
        out[name] = cell
    return out


def paired(deltas: list):
    """(mean, sigma, n) of per-decision deltas."""
    if len(deltas) < 2:
        return None, None, len(deltas)
    mean = statistics.mean(deltas)
    se = statistics.stdev(deltas) / math.sqrt(len(deltas))
    return mean, (mean / se if se else math.inf), len(deltas)


def outside(decisions: list) -> dict:
    """Paired agreement delta, facing-a-raise and all. Pure."""
    def cell(rows):
        mean, sigma, n = paired([r["ensemble_agree"] - r["single_agree"] for r in rows])
        return {"delta": mean, "sigma": sigma, "n": n}
    return {"facing": cell([d for d in decisions if d["facing"]]), "all": cell(decisions)}


def verdict(stab: dict, out: dict, warm_share: float):
    """(adopt?, reasons) - the pre-registered rule, mechanically."""
    reasons = []
    ratios = [stab[k]["ratio"] for k in ("all", "half0", "half1")]
    if not all(r is not None and r <= MAX_RATIO for r in ratios):
        reasons.append(f"STABILITY fails: ratios {ratios}")
    for name in ("facing", "all"):
        sigma = out[name]["sigma"]
        if sigma is not None and sigma <= -2:
            reasons.append(f"OUTSIDE fails on {name}: {sigma:.2f} sigma worse")
    if warm_share < MIN_WARM_SHARE:
        reasons.append(f"COVERAGE fails: {warm_share:.3f} of hands at warmed depths")
    return not reasons, reasons


def note_after(stab: dict):
    """M289's rule re-applied to the ensemble arm: the figure to quote, or
    None if the note stops firing at six-handed."""
    for name in ("all", "half0", "half1"):
        e = stab[name]["ensemble"]
        if e["facing"] is None or e["first_in"] is None:
            return None
        if not (e["facing"] >= 0.10 and e["facing"] - e["first_in"] >= 0.05):
            return None
    return round(stab["all"]["ensemble"]["facing"], 3)


def warm_share(stacks: list, warmed_buckets: set, bucket_bb: float) -> float:
    """Share of `stacks` whose FLOORED bucket is in `warmed_buckets`."""
    if not stacks:
        return 0.0
    hit = sum(1 for s in stacks if math.floor(s / bucket_bb) * bucket_bb in warmed_buckets)
    return hit / len(stacks)


def main(argv=None) -> int:                              # pragma: no cover
    import numpy as np
    from collections import Counter
    from api import config as cfg, solving
    from bench import hand_db
    from bench.real_replay import request_for, DEFAULT_WHERE
    from bench.studies.preflop_fold_seeds import fold_move
    from bench.studies.two_live_silent import class_of
    from poker_solver.game_tree import DecisionNode, GameConfig
    from poker_solver.solver import solve_preflop

    out_path = (argv if argv is not None else sys.argv[1:])[0]
    table = cfg.MULTIWAY_TABLE_CONFIGS[SIZE]
    config = GameConfig(positions=table["positions"], stack_bb=STACK)
    equity = solving._get_multiway_equity_cache(cfg.MULTIWAY_PREFLOP_HANDS)

    def solve(seed, runs):
        result = solve_preflop(config=config, hands=cfg.MULTIWAY_PREFLOP_HANDS,
                               equity_cache=equity, iterations=table["iterations"],
                               seed=seed, floor_regret=table.get("floor_regret"),
                               ensemble=runs)
        print(f"solved seed {seed} x{runs}", flush=True)
        return result

    singles = [solve(s, 1) for s in SINGLE_SEEDS]
    ensembles = [solve(s, K) for s in ENSEMBLE_SEEDS]
    hands = [str(h) for h in singles[0].hands]
    weights = [h.combo_count for h in singles[0].hands]

    def locate(result, path):
        node = result.root
        for kind in path:
            action = next((a for a in node.legal_actions if a.kind == kind), None)
            if action is None:
                return None
            node = node.children[action]
        return node if isinstance(node, DecisionNode) else None

    def rows(result, path):
        node = locate(result, path)
        if node is None:
            return None, None, None
        strat = result.strategy_at(node)
        keys = sorted(strat[hands[0]])
        return node, keys, np.array([[strat[h].get(k, 0.0) for k in keys] for h in hands])

    db = hand_db.connect()
    paths, stacks = Counter(), []
    reference = []
    for hand in hand_db.query(db, f"({DEFAULT_WHERE}) AND n_players = {SIZE}"):
        stacks.append(hand.row["eff_stack_bb"])
        acts = [a for a in hand.streets() if a.kind != "show"]
        seats = json.loads(hand.row["players"])
        for i, a in enumerate(acts):
            if a.street != "preflop":
                continue
            body, _, _ = request_for(hand, acts, i, STACK, hand.hole_cards,
                                     post=lambda b: (500, {}))
            if body is None:
                continue
            path = tuple(body["preflop_action_path"])
            paths[path] += 1
            if hand.row["source"] == "pluribus" and seats[a.player] == "Pluribus":
                reference.append((path, class_of(hand.hole_cards[a.player]), a.kind == "fold"))

    nodes, cache = [], {}
    for path, count in sorted(paths.items()):
        node, keys, s_base = rows(singles[0], path)
        _, _, e_base = rows(ensembles[0], path)
        if node is None or "fold" not in keys or np.ptp(s_base) < 1e-9 or np.ptp(e_base) < 1e-9:
            continue
        f = keys.index("fold")
        owed = max(node.invested.values()) - node.invested[node.player_to_act]
        facing = bool(node.raises_so_far >= 1 and owed > 1e-9)
        s_move = fold_move(s_base, [rows(r, path)[2] for r in singles[1:]], f, weights)
        e_move = fold_move(e_base, [rows(r, path)[2] for r in ensembles[1:]], f, weights)
        nodes.append({"path": list(path), "count": count, "facing": facing,
                      "single_move": s_move, "ensemble_move": e_move})
        cache[path] = (facing, {h: s_base[k, f] for k, h in enumerate(hands)},
                       {h: e_base[k, f] for k, h in enumerate(hands)})

    decisions = []
    for path, cls, folded in reference:
        if path not in cache:
            continue
        facing, s_fold, e_fold = cache[path]
        agree = (lambda p: p) if folded else (lambda p: 1.0 - p)
        decisions.append({"facing": facing, "single_agree": float(agree(s_fold[cls])),
                          "ensemble_agree": float(agree(e_fold[cls]))})

    bucket = cfg.MULTIWAY_STACK_BUCKET_BB
    warmed = {float(d) for d in cfg.MULTIWAY_PREWARM_STACK_DEPTHS}
    warmed |= {float(d) for p, d in cfg.MULTIWAY_BACKGROUND_WARM if p == SIZE}
    # M284: a disk-warmed bucket's first ask is a file read, not a solve.
    warmed |= {float(d) for p, d in cfg.MULTIWAY_DISK_WARM if p == SIZE}
    share = warm_share(stacks, warmed, bucket)

    stab, out = stability(nodes), outside(decisions)
    adopt, reasons = verdict(stab, out, share)
    json.dump({"nodes": nodes, "decisions": decisions, "warm_share": share}, open(out_path, "w"))
    print(json.dumps({"stability": stab, "outside": out, "warm_share": share}, indent=1))
    print("ADOPT" if adopt else "REFUSE", reasons, "| note at six after:", note_after(stab))
    return 0


if __name__ == "__main__":                                # pragma: no cover
    sys.exit(main())
