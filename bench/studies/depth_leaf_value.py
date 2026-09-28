"""Does a CORRECT leaf value improve the turn's advice? (the depth-limited
/ value-network question, asked without chaining)

This is the arm every earlier attempt could not build. The engine values a
turn terminal at `equity * pot - invested` - the hand treated as ending at
showdown with no further betting - and that approximation IS street
isolation. M247 measured what a correct value would be worth (sd 1.75-1.86
bb, 12% of pot, not noise, and no cheap feature predicts it) and left the
route open because an exact leaf cost 400x the budget.

**Why the obvious arm cannot answer it.** `solve_flop_turn` on a four-card
board plays the river out already. M223 measured it WORSE (+0.1713, 2.79
sigma) and a converged chain running to 0.997 aggression. But a chained
solve changes TWO things: it plays the street out AND it crosses a chance
node, where F45's dead-pot offset stops cancelling (M161: 0.97 of strategy
difference, dtype-independent). A probe run for this question at 60 matched
iterations reproduced M223's direction and could not separate the two
causes - and it also could not afford the shipped budget, because the
chained turn's marginal iteration is 12.4s.

**What this does instead.** `cfr`'s `leaf_value_fn` seam replaces the
showdown terminal's value inside a SINGLE-street solve. Nothing crosses a
chance node, so F45 cannot arise, and the solve is as cheap as the shipped
one - so **both arms run at the shipped 250 iterations**, which no chained
comparison has ever managed.

**Arms**, one knob apart, same tree, same ranges, same budget:
- **P** shipped: `equity * pot` at every showdown leaf.
- **D** depth: each distinct leaf situation valued by solving its RIVER
  out (`bench.leaf_values`), computed once from the turn's entering
  ranges.

**PRE-REGISTERED READING RULE (fixed before any spot was solved):**

1. Both arms at `cfg.TURN_STANDALONE_ITERATIONS`. There is no precision
   confound to control here, which is the point (M197).
2. The leaf value is computed ONCE per distinct situation, from the turn's
   ENTERING ranges. The ranges that actually reach a leaf depend on the
   turn strategy being solved; "computed once" cuts that circularity and a
   value net would face the same choice. Stated, not hidden.
3. River values average over EVERY river card. Sampling 12 of 48 was
   measured and REFUSED: signal 1.4126 against sampling noise 0.8027, a
   ratio of 1.8 where the rule required 5.0 - and noise in a leaf value
   perturbs the solve, so it would have biased the study TOWARD "depth is
   alive". Cutting the river's iterations 4x was refused too, at 4.9, and
   saved only 1.11x anyway - which is what showed the cost is the equity
   TABLE rather than CFR (M176's inversion one street over), so each
   river board's table is built once and reused across leaf situations.
   Each hand pair is averaged over the rivers it is LIVE on: a combo
   holding the river card cannot exist there, and importing the engine's
   0.5-equity convention for it would be a fiction.
4. Depth MOVES the answer iff the median |aggression_D - aggression_P| is
   at least `MOVE_LEVEL` (0.05 - inherited: M304's CLOSE_LEVEL and M200's
   "9 of 16 under 0.05").
5. Depth is BETTER iff the card-blind LIFT (M262's control: each arm
   scored against its OWN range-weighted average row, so hedging earns
   nothing) is higher for D at >= 2 sigma; WORSE at <= -2 sigma; a null
   otherwise.
6. Ranges must be the real derived ones. A UNIFORM equity table turns one
   ULP into 0.5 of strategy (measured, `tests/test_leaf_value_seam.py`),
   so a study on a flat fixture would measure its own fixture.
7. **A null here is a real refutation**, unlike the chained arm's: it is
   confounded by neither F45 nor precision. That is the whole reason the
   seam was built.
8. Scope: heads-up turn OPENING decisions at SPR >= `MIN_SPR` (5.0,
   inherited from `TURN_INDEPENDENT_SPR_MIN`; M199 puts 80% of real
   decisions there, median 9.5). A shallow sample measures the regime
   M222 found has least to differ about, which an earlier probe did by
   accident and got a direction wrong for it.
9. Direction is recorded and decides nothing on its own.

**MODE `facing` (M308).** The opening-decision run above had the real
player choosing check or call on 15 of 16 spots, because a hand's FIRST
turn action IS the street's opening decision - so its agreement axis had
almost nothing to separate and its lift null is underpowered. This mode
takes real decisions where hero FACED A BET instead. M177's rule: those
nodes are constructed explicitly or a study silently measures opening
decisions only, and it is the node type M188/M189 put 74% of all cost at.

The tree is built at the street's OPENING pot and stack and the real bet
is then WALKED, never built at the node's own pot - the tree sizes bets
off the pot it was built with, so building at the post-bet pot models a
much larger bet and scores two different situations against each other
(M177's other rule).

**Its own selection bias, measured and stated.** Over 150 real facing-a-bet
turn decisions with known cards: 42% sit at entering SPR >= 5 (median
3.96, shallower than an opening decision's by construction), and hero's
action mix is 30 raise / 120 call / **0 fold**. Hole cards are known
mainly when a hand reaches showdown, so players who FOLDED are
systematically absent. That lifts the aggressive share from 6% to ~20% -
better, not decisive - and the fold axis is unavailable, which is the axis
M241/M242 chose precisely because it needs no size mapping.

    python -m bench.studies.depth_leaf_value out.json [spots] [river_cards] [facing]
"""
from __future__ import annotations

import json
import random
import statistics
import sys
import time

MIN_SPR = 5.0
MOVE_LEVEL = 0.05
MIN_SIGMA = 2.0
RIVER_CARDS = 48        # sampling was refused: noise was 57% of signal
SPOTS = 16
CARD_SEED = 927


def kind_of(action: str) -> str:
    if action.startswith("fold"):
        return "fold"
    if action.startswith("call") or action.startswith("check"):
        return "call_or_check"
    return "aggressive"


def aggression(row: dict) -> float:
    return sum(p for a, p in row.items() if kind_of(a) == "aggressive")


def mass_on(row: dict, kind: str) -> float:
    return sum(p for a, p in row.items() if kind_of(a) == kind)


def card_blind(strategy: dict, weights: dict, kind: str):
    """What an arm says about the chosen action WITHOUT knowing hero's
    cards - its own range-weighted average row (M262's control). Raw
    agreement rewards hedging; this does not."""
    total = weighted = 0.0
    for key, row in strategy.items():
        w = weights.get(key, 0.0)
        if w <= 0:
            continue
        total += w
        weighted += w * mass_on(row, kind)
    return (weighted / total) if total else None


def summarise(rows: list) -> dict:
    """Rules 4 and 5. Pure, so a test can pin it."""
    kept = [r for r in rows if r.get("agg_D") is not None]
    if not kept:
        return {"n": 0}

    def paired(left, right):
        deltas = [r[left] - r[right] for r in kept
                  if r.get(left) is not None and r.get(right) is not None]
        if len(deltas) < 2:
            return {"n": len(deltas)}
        mean = statistics.fmean(deltas)
        sem = statistics.stdev(deltas) / (len(deltas) ** 0.5)
        return {"n": len(deltas), "mean": mean, "sem": sem,
                "median": statistics.median(deltas),
                "sigma": (mean / sem) if sem else None}

    moves = [r["agg_D"] - r["agg_P"] for r in kept]
    return {
        "n": len(kept),
        "move_median": statistics.median([abs(m) for m in moves]),
        "signed": paired("agg_D", "agg_P"),
        "more_aggressive_on": sum(1 for m in moves if m > 0),
        "lift_D_minus_P": paired("lift_D", "lift_P"),
        "lift_D": paired("lift_D", "zero"),
        "lift_P": paired("lift_P", "zero"),
        "spr_min": min(r["spr"] for r in kept),
        "spr_median": statistics.median([r["spr"] for r in kept]),
        "iterations": kept[0].get("iterations"),
        "river_cards": kept[0].get("river_cards"),
        "leaf_situations_median": statistics.median(
            [r["leaf_situations"] for r in kept]),
    }


def verdict(summary: dict) -> dict:
    """Rules 4, 5 and 7."""
    if not summary.get("n"):
        return {"measured": False}
    moved = summary["move_median"] >= MOVE_LEVEL
    lift = summary["lift_D_minus_P"]
    sigma = lift.get("sigma")
    better = sigma is not None and sigma >= MIN_SIGMA
    worse = sigma is not None and sigma <= -MIN_SIGMA
    return {
        "measured": True,
        "depth_moves_the_answer": bool(moved),
        "depth_is_better": bool(better),
        "depth_is_worse": bool(worse),
        # Rule 7: neither F45 nor precision can explain this one.
        "refutation_is_clean": bool(not better),
        "scope_is_the_real_regime": summary.get("spr_min", 0) >= MIN_SPR,
    }


def main(argv=None) -> int:                              # pragma: no cover
    args = argv if argv is not None else sys.argv[1:]
    out_path = args[0]
    spots = int(args[1]) if len(args) > 1 else SPOTS
    river_cards = int(args[2]) if len(args) > 2 else RIVER_CARDS
    facing_mode = len(args) > 3 and args[3].lower() in ("facing", "1", "true")

    from fastapi.testclient import TestClient
    from api import config as cfg, solving
    from api.main import app
    from api.parallel import parallel_board_equity_table
    from bench import leaf_values
    from bench.hand_db import connect, query
    from bench.real_replay import DEFAULT_WHERE, request_for
    from bench.server_warmup import warm_multiway
    from poker_solver.cards import Card, parse_cards
    from poker_solver.combos import HandCombo
    from poker_solver.game_tree import StreetConfig, build_street_tree
    from poker_solver.solver import DEFAULT_ITERATIONS, solve_flop

    client = TestClient(app)
    warm_multiway(depths=(100.0,), table_sizes=(6,))

    def post(body):
        r = client.post("/advise", json=body)
        return r.status_code, (r.json() if r.status_code == 200 else {})

    db = connect()
    rows, skipped = [], []
    for hand in query(db, f"({DEFAULT_WHERE})"):
        if len(rows) >= spots:
            break
        acts = [a for a in hand.streets() if a.kind != "show"]
        hole = hand.hole_cards
        street_turns = [i for i, a in enumerate(acts) if a.street == "turn"]
        if not street_turns:
            continue
        if facing_mode:
            # A real decision where hero faced a bet (M177), not a
            # constructed one - so the action scored is one a strong
            # player actually took at that node.
            turns = [i for i in street_turns
                     if (acts[i].facing_bb or 0) > 1e-9 and hole.get(acts[i].player)]
        else:
            turns = [i for i in street_turns[:1] if hole.get(acts[i].player)]
        if not turns:
            continue
        index = turns[0]
        if not all(hole.get(a.player) for a in acts[:index + 1]
                   if a.street != "preflop"):
            continue
        try:
            body, _w, _x = request_for(hand, acts, index,
                                       round(hand.row["eff_stack_bb"], 2),
                                       hole, post=post)
        except (KeyError, ValueError) as exc:
            skipped.append(f"{type(exc).__name__}: {exc}")
            continue
        if body is None:
            continue
        turn_path = list(body.get("turn_action_path") or [])
        if bool(turn_path) != facing_mode:
            # Opening mode wants no turn path; facing mode needs one. A
            # mismatch means the arms would read a different node than the
            # player faced, which is M177's rule in reverse.
            continue
        body["hero_cards"] = hole[acts[index].player]
        status, js = post(body)
        if status != 200 or len(js.get("positions") or []) != 2:
            continue
        # The street's OPENING pot and stack, which is what the tree must
        # be built at even when hero acts later on it (M177).
        if facing_mode:
            opening_body, _ow, _ox = request_for(
                hand, acts, street_turns[0], round(hand.row["eff_stack_bb"], 2),
                hole, post=post)
            if opening_body is None or opening_body.get("turn_action_path"):
                continue
            o_status, o_js = post(opening_body)
            if o_status != 200 or len(o_js.get("positions") or []) != 2:
                continue
            pot, stack = o_js["pot"], o_js["max_affordable_bb"]
        else:
            pot, stack = js["pot"], js["max_affordable_bb"]
        if pot <= 0 or stack <= 0 or stack / pot < MIN_SPR:
            continue

        board = tuple(parse_cards(body["board"] + body["turn_card"]))
        hero_combo = HandCombo(*parse_cards(body["hero_cards"]))
        situation = solving._derive_path_situation(
            action_kinds=list(body["preflop_action_path"]),
            stack_bb=body["stack_bb"], board_cards=board,
            iterations=DEFAULT_ITERATIONS, players=body.get("players", 2),
            multiway=False, sibling_endpoint="/advise",
            max_classes_per_position=cfg.TURN_STANDALONE_CLASSES_PER_SIDE,
            path_field_name="preflop_action_path", hero_combo=hero_combo)
        oop, ip = situation.postflop_positions
        hero_range = situation.position_ranges[oop]
        villain_range = situation.position_ranges[ip]
        hero_key = str(hero_combo)
        chosen = kind_of(acts[index].kind)

        common = dict(board=board, hero_range=hero_range,
                      villain_range=villain_range, pot=pot,
                      effective_stack_bb=stack, positions=(oop, ip),
                      raise_sizes=cfg.TURN_STANDALONE_RAISE_SIZES,
                      max_raises=cfg.TURN_STANDALONE_MAX_RAISES,
                      iterations=cfg.TURN_STANDALONE_ITERATIONS)

        # The terminals to value, enumerated off a tree built from the SAME
        # config `solve_flop` will build - the values are keyed by the leaf
        # SITUATION rather than by `id(node)`, so they match across trees
        # (M158's trap, avoided by construction).
        probe = build_street_tree(StreetConfig(
            positions=(oop, ip), pot=pot, stack_bb=stack,
            raise_sizes=cfg.TURN_STANDALONE_RAISE_SIZES,
            max_raises=cfg.TURN_STANDALONE_MAX_RAISES))
        nodes, terminals = [probe], []
        while nodes:
            node = nodes.pop()
            kids = getattr(node, "children", None)
            if kids:
                nodes.extend(kids.values())
            else:
                terminals.append(node)

        used = set(board)
        deck = [Card(r, s) for r in "23456789TJQKA" for s in "cdhs"
                if Card(r, s) not in used]
        rng = random.Random(CARD_SEED)
        rng.shuffle(deck)
        sample = deck[:river_cards]  # 48 = the whole deck

        started = time.time()
        values = leaf_values.turn_leaf_values(
            turn_board=board, hero_range=hero_range, villain_range=villain_range,
            positions=(oop, ip), effective_stack_bb=stack, terminals=terminals,
            raise_sizes=cfg.RIVER_STANDALONE_RAISE_SIZES,
            max_raises=cfg.RIVER_STANDALONE_MAX_RAISES,
            iterations=cfg.RIVER_STANDALONE_ITERATIONS, deck=sample,
            # The dominant cost, and it is the TABLE not CFR: cutting the
            # river's iterations 4x saved 10% of the time (M176's
            # inversion one street over). Each river board's table is
            # built once and reused across leaf situations.
            equity_table_fn=parallel_board_equity_table)
        value_seconds = time.time() - started
        if not values:
            continue

        shipped = solve_flop(equity_table_fn=parallel_board_equity_table, **common)
        deep = solve_flop(
            equity_table_fn=parallel_board_equity_table,
            leaf_value_fn=lambda node: values.get(leaf_values.leaf_key(node, oop)),
            **common)

        arms = {"P": shipped, "D": deep}
        weights = {str(c): w for c, w in hero_range.items()}

        def node_of(result):
            """Hero's node: the root at an opening decision, or the node
            the real bet leads to when hero faced one."""
            if not turn_path:
                return result.root
            _actions, node = solving._resolve_action_path(result.root, turn_path)
            return node
        entry = {"board": body["board"] + body["turn_card"],
                 "hero": body["hero_cards"], "i": index, "pot": pot,
                 "stack": stack, "spr": stack / pot, "chosen": chosen,
                 "iterations": cfg.TURN_STANDALONE_ITERATIONS,
                 "river_cards": river_cards, "leaf_situations": len(values),
                 "value_seconds": value_seconds, "zero": 0.0,
                 "facing": facing_mode, "turn_path": turn_path,
                 "combos": len(set(hero_range) | set(villain_range))}
        try:
            nodes = {name: node_of(result) for name, result in arms.items()}
        except ValueError:
            skipped.append("turn path did not resolve in the solved tree")
            continue
        if any(not hasattr(n, "legal_actions") for n in nodes.values()):
            skipped.append("the turn path ended at a terminal")
            continue
        for name, result in arms.items():
            row = (result.strategy_at(nodes[name]).get(hero_key) or {})
            entry[f"agg_{name}"] = aggression(row)
            entry[f"agree_{name}"] = mass_on(row, chosen)
            blind = card_blind(result.strategy_at(nodes[name]), weights, chosen)
            entry[f"blind_{name}"] = blind
            entry[f"lift_{name}"] = (entry[f"agree_{name}"] - blind
                                     if blind is not None else None)
        rows.append(entry)
        print(f"[{len(rows)}/{spots}] {entry['board']} SPR {entry['spr']:.1f} "
              f"leaves {len(values)} chose {chosen} | agg P {entry['agg_P']:.3f} "
              f"D {entry['agg_D']:.3f} | lift P {entry['lift_P']:+.3f} "
              f"D {entry['lift_D']:+.3f} | values {value_seconds / 60:.1f} min",
              flush=True)
        json.dump(rows, open(out_path, "w"))

    if skipped:
        print(f"skipped {len(skipped)} the harness could not build, "
              f"first: {skipped[0]}", flush=True)
    summary = summarise(rows)
    print(json.dumps(summary, indent=1, default=str), flush=True)
    print(json.dumps(verdict(summary), indent=1), flush=True)
    print("DEPTH LEAF STUDY DONE", flush=True)
    return 0


if __name__ == "__main__":                               # pragma: no cover
    raise SystemExit(main())
