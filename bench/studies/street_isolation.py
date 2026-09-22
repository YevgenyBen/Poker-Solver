"""What does solving one street at a time cost now? (audit R3, M304)

`STREET_ISOLATION_NOTE` is the highest-exposure stale disclosure left -
**11% of real decisions** - and it is the most quantitative thing this
product tells a player. It says the flop solve models this street's
betting and averages the turn in as a runout, and then quotes, from
M197-M202:

1. "on more than half the spots the two differed by under 5 percentage
   points, and the typical difference was about 4" (median 0.0382);
2. "**3 of 16 disagreed CATEGORICALLY** - the fuller solve bet 97% to
   100% where this one bet under a quarter";
3. "the advice here is the LESS aggressive by about **18 percentage
   points**, though the measurement is only sharp enough to place that
   between 1 and 36" (mean 0.1848, CI 0.0074-0.3623);
4. "priced in chips... about **a tenth of a big blind** per decision,
   four tenths at its worst, and not one of the 16 spots cost a full
   blind" (0.1097 bb, worst 0.4123).

**Why it is stale.** Every one of those was measured before M203-M207
gave the flop its 0.33/0.75/2.5 menu - the registry's own reason - and
M234 then took the flop's range cap to 100. Both arms of M199/M200's
comparison were single-bet-size trees.

**THREE ARMS, because M197's confound is the trap here.** M195/M196 ran
the chained arm at 20 iterations against a flop-only arm at the shipped
250 and so confounded DEPTH with PRECISION. The precision control is
nearly free, so it runs:

* `shipped` - `solve_flop` at the shipped settings. What a player gets.
* `shipped_converged` - `solve_flop` at the chained arm's iteration
  count. The precision control.
* `chained` - `solve_flop_turn`, same board, same ranges, same pot,
  same stack, same seed, same menu. Only the DEPTH differs.

The depth effect is `chained - shipped_converged`; the distance from
what a player actually reads is `chained - shipped`.

**COST, measured before the campaign was scoped** (M257's rule, and
M199's own campaign was re-scoped on its first measurement). One spot at
898 combos and SPR 15.7, with `parallel_equity_batch` building the turn
branches:

| | seconds |
|---|---|
| shipped `solve_flop` | 2.2 |
| chained at 20 iterations | 561.5 |
| chained at 60 iterations | 705.8 |

So the fixed cost is **~490s of turn equity tables** and the marginal
iteration is **3.6s** - the chained solve is NOT linear in iterations
(M198), and 400 iterations, the budget M199/M200 used, costs about **32
minutes a spot** rather than the 79 M199 measured with one bet size.

**SPR IS BOUNDED ABOVE, and that is a scope statement.** M257 measured
reference cost scaling with SPR rather than width (65x from 2.5 to
19.5), and the first probe drew a LIMPED pot at SPR 74.5 whose
20-iteration chained solve had not finished in fifteen minutes. The note
fires from SPR 5; this measures 5 to 20, which is where M199/M200
measured (16.2) and where M199 put the median real decision (9.5). Above
20 the gap stays unmeasured and the copy says so.

**PRE-REGISTERED READING RULE (fixed before any spot was solved):**

1. AGGRESSION is hero's mass on bet/raise/all-in at the flop decision,
   read from each arm's own strategy at the root.
2. HEADLINE: the signed gap `chained - shipped`, its mean, its 95%
   interval and its median. The copy's "about 18 points, between 1 and
   36" is replaced by whatever this run measures.
3. CATEGORICAL disagreements are spots where the two arms differ by at
   least `CATEGORICAL_LEVEL` (0.5). The copy's "3 of 16" is replaced by
   the measured count, and by none if there are none.
4. "More than half differed by under 5 points" is re-derived as the
   share under `CLOSE_LEVEL` (0.05) and the median.
5. THE PRECISION CONTROL decides what may be attributed to depth: if
   `shipped_converged - shipped` is itself larger than half the depth
   gap, the copy may not call the gap a depth effect at all.
6. PRICED IN CHIPS on the chained tree with `poker_solver/ev.py`, which
   prices a multi-street tree when handed the solve's own `chance_data`
   (M201/M202). The copy's "a tenth of a big blind" is replaced by the
   measured mean and worst.
7. Nulls are results. Every figure is the gap to a fuller model of the
   same kind, NOT the distance to correct play (M183's standing
   caveat), and the copy keeps saying so.

    python -m bench.studies.street_isolation run rows.jsonl
    python -m bench.studies.street_isolation rows.jsonl
"""
from __future__ import annotations

import json
import math
import os
import statistics
import sys

CATEGORICAL_LEVEL = 0.5
CLOSE_LEVEL = 0.05
MIN_ROWS = 6
SPR_MAX = 20.0
#: M199/M200's budget, so the headline is comparable with the figures it
#: replaces. The probe puts it at ~32 minutes a spot.
CHAINED_ITERATIONS = int(os.environ.get("ISO_ITERATIONS", 400))
#: A cheaper budget for the convergence control M197 requires.
CONTROL_ITERATIONS = int(os.environ.get("ISO_CONTROL_ITERATIONS", 150))
SPOTS = int(os.environ.get("ISO_SPOTS", 16))
SEED = int(os.environ.get("ISO_SEED", 304))


def gap_of(row: dict) -> float:
    """Signed: positive means the CHAINED solve is more aggressive."""
    return row["chained_aggression"] - row["shipped_aggression"]


def depth_gap_of(row: dict) -> float:
    """The gap with precision held fixed - rule 5's numerator."""
    return row["chained_aggression"] - row["shipped_converged_aggression"]


def _interval(values: list) -> dict:
    if len(values) < 2:
        return {"n": len(values), "mean": values[0] if values else None, "sem": None}
    mean = statistics.mean(values)
    sem = statistics.stdev(values) / math.sqrt(len(values))
    return {"n": len(values), "mean": mean, "sem": sem,
            "ci_low": mean - 1.96 * sem, "ci_high": mean + 1.96 * sem,
            "sigma": (mean / sem) if sem else None}


def headline(rows: list) -> dict:
    """Rules 2, 3 and 4."""
    if not rows:
        return {"n": 0}
    gaps = [gap_of(r) for r in rows]
    absolute = [abs(g) for g in gaps]
    categorical = [r for r in rows if abs(gap_of(r)) >= CATEGORICAL_LEVEL]
    return {
        "n": len(rows),
        "signed": _interval(gaps),
        "median_abs": statistics.median(absolute),
        "share_close": sum(1 for a in absolute if a < CLOSE_LEVEL) / len(absolute),
        "categorical": len(categorical),
        "categorical_worst": max(absolute),
        "chained_more_aggressive_on": sum(1 for g in gaps if g > 0),
    }


def precision_control(rows: list) -> dict:
    """Rule 5. M197: a chained arm at one budget against a shipped arm at
    another confounds depth with precision, and M195/M196 did exactly
    that. Here the precision arm is nearly free, so it runs."""
    if not rows:
        return {"n": 0}
    precision = _interval([r["shipped_converged_aggression"] - r["shipped_aggression"]
                           for r in rows])
    depth = _interval([depth_gap_of(r) for r in rows])
    ratio = (abs(precision["mean"]) / abs(depth["mean"])
             if depth.get("mean") else None)
    return {"precision": precision, "depth": depth,
            "precision_over_depth": ratio,
            # Rule 5's bar: precision must not carry half the gap.
            "depth_is_the_story": bool(ratio is not None and ratio < 0.5)}


def price(rows: list) -> dict:
    """Rule 6, over the rows that carry a price."""
    priced = [r["cost_bb"] for r in rows if r.get("cost_bb") is not None]
    if not priced:
        return {"n": 0}
    return {"n": len(priced), "mean": statistics.mean(priced),
            "median": statistics.median(priced), "worst": max(priced),
            "over_one_bb": sum(1 for c in priced if c > 1.0),
            **{k: v for k, v in _interval(priced).items() if k.startswith("ci")}}


def summarise(rows: list) -> dict:
    return {"headline": headline(rows), "control": precision_control(rows),
            "price": price(rows),
            "spr": {"min": min((r["spr"] for r in rows), default=None),
                    "max": max((r["spr"] for r in rows), default=None)}}


def verdict(summary: dict) -> dict:
    head = summary["headline"]
    if not head.get("n"):
        return {"measured": False}
    signed = head["signed"]
    return {
        "measured": head["n"] >= MIN_ROWS,
        "direction_separates": bool(signed.get("sigma") is not None
                                    and abs(signed["sigma"]) >= 2.0),
        "chained_is_more_aggressive": bool(signed.get("mean", 0) > 0),
        "has_categorical_disagreements": head["categorical"] > 0,
        "depth_is_the_story": summary["control"].get("depth_is_the_story", False),
    }


def main(argv=None) -> int:                              # pragma: no cover
    import pathlib
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] == "run":
        return _run(args[1])
    source = pathlib.Path(args[0]) if args else (
        pathlib.Path(__file__).resolve().parents[2] / "tests" / "data"
        / "street_isolation_m304.jsonl")
    rows = [json.loads(line) for line in source.read_text().splitlines() if line.strip()]
    summary = summarise(rows)
    print(json.dumps({**summary, "verdict": verdict(summary)}, indent=1, default=str))
    return 0


def _run(out_path: str) -> int:                          # pragma: no cover
    """Solve each spot three ways and price the depth gap in chips."""
    import random
    import time

    import numpy as np
    from fastapi.testclient import TestClient

    from api import config as cfg
    from api import solving
    from api.main import app
    from api.parallel import parallel_board_equity_table, parallel_equity_batch
    from bench import hand_db, price as price_tools
    from bench.real_replay import DEFAULT_WHERE, deal, request_for, sample_hands
    from bench.studies.facing_cost import live_after_preflop
    from bench.server_warmup import warm_multiway
    from poker_solver.cards import parse_cards
    from poker_solver.combos import HandCombo
    from poker_solver.ev import action_values, ev_loss
    from poker_solver.solver import DEFAULT_ITERATIONS, solve_flop, solve_flop_turn

    def aggression(row: dict) -> float:
        return sum(v for k, v in row.items()
                   if k.split(":")[0] not in ("fold", "call_or_check"))

    client = TestClient(app)
    warm_multiway(depths=(100.0,), table_sizes=(6,))

    def post(body):
        r = client.post("/advise", json=body)
        return r.status_code, (r.json() if r.status_code == 200 else {})

    db = hand_db.connect()
    rng = random.Random(SEED)
    written = 0
    skips = {"multiway_pot": 0, "no_opening_flop": 0, "unrepresentable": 0,
             "refused": 0, "out_of_spr_band": 0}
    with open(out_path, "w") as fh:
        for hand in sample_hands(db, 20000, SEED, where=DEFAULT_WHERE):
            if written >= SPOTS:
                break
            acts = [a for a in hand.streets() if a.kind != "show"]
            if live_after_preflop(acts, hand.n_players) != 2:
                skips["multiway_pot"] += 1
                continue
            flop = [i for i, a in enumerate(acts)
                    if a.street == "flop" and (a.facing_bb or 0) <= 1e-9]
            if not flop:
                skips["no_opening_flop"] += 1
                continue
            index = flop[0]
            cards = deal(hand.board, hand.n_players, rng)
            body, _why, _worst = request_for(
                hand, acts, index, round(hand.row["eff_stack_bb"], 2), cards, post=post)
            if body is None:
                skips["unrepresentable"] += 1
                continue
            body["hero_cards"] = cards[acts[index].player]
            status, js = post(body)
            if status != 200 or len(js.get("positions") or []) != 2:
                skips["refused"] += 1
                continue
            pot, stack = js["pot"], js["max_affordable_bb"]
            spr = stack / max(pot, 1e-9)
            if not (cfg.STREET_ISOLATION_SPR_MIN <= spr <= SPR_MAX):
                skips["out_of_spr_band"] += 1
                continue

            hero_combo = HandCombo(*parse_cards(body["hero_cards"]))
            situation = solving._derive_path_situation(
                action_kinds=list(body["preflop_action_path"]),
                stack_bb=body["stack_bb"],
                board_cards=tuple(parse_cards(body["board"])),
                iterations=DEFAULT_ITERATIONS, players=body.get("players", 2),
                multiway=False, sibling_endpoint="/advise",
                max_classes_per_position=cfg.MAX_PATH_QUERY_CLASSES_PER_SIDE,
                path_field_name="preflop_action_path", hero_combo=hero_combo)
            oop, ip = situation.postflop_positions
            board = tuple(parse_cards(body["board"]))
            common = dict(board=board, hero_range=situation.position_ranges[oop],
                          villain_range=situation.position_ranges[ip], pot=pot,
                          effective_stack_bb=stack, positions=(oop, ip),
                          raise_sizes=cfg.FLOP_RAISE_SIZES,
                          max_raises=cfg.FLOP_MAX_RAISES)
            started = time.time()
            shipped = solve_flop(**common, iterations=cfg.PATH_QUERY_ITERATIONS,
                                 equity_samples=cfg.PATH_QUERY_EQUITY_SAMPLES,
                                 equity_table_fn=parallel_board_equity_table)
            converged = solve_flop(**common, iterations=CHAINED_ITERATIONS,
                                   equity_samples=cfg.PATH_QUERY_EQUITY_SAMPLES,
                                   equity_table_fn=parallel_board_equity_table)
            flop_seconds = time.time() - started
            started = time.time()
            # `parallel_equity_batch` is the `equity_batch_fn` written for
            # a map over BOARDS, which is what the turn branches are.
            # Without it one 20-iteration solve did not finish in ten
            # minutes; with it the same solve is 561s.
            chained = solve_flop_turn(**common, iterations=CHAINED_ITERATIONS,
                                      equity_batch_fn=parallel_equity_batch)
            chained_seconds = time.time() - started

            hero_key = str(hero_combo)
            rows_by_arm = {
                "shipped": shipped.strategy_at(shipped.root).get(hero_key),
                "shipped_converged": converged.strategy_at(converged.root).get(hero_key),
                "chained": chained.strategy_at(chained.root).get(hero_key),
            }
            if any(row is None for row in rows_by_arm.values()):
                skips["refused"] += 1
                continue

            # Rule 6: price the disagreement on the CHAINED tree, which is
            # the fuller model, handing `ev.py` the solve's own
            # `chance_data` so the turn is played out rather than
            # collapsed to an averaged equity number (M201/M202).
            cost = None
            try:
                table = np.nan_to_num(
                    parallel_board_equity_table(board, list(chained.hands),
                                                cfg.PATH_QUERY_EQUITY_SAMPLES),
                    nan=0.5).astype(np.float32)
                hands = list(chained.hands)
                strategy_fn = price_tools.strategy_fn_for(chained)
                hero_index = hands.index(hero_combo)
                opp_reach = price_tools.opponent_weights(
                    situation.position_ranges, ip, hands, hero_combo)
                values = action_values(
                    chained.root, hero_position=oop, hero_index=hero_index,
                    hero_is_a=True, equity_table=table, opp_reach=opp_reach,
                    strategy_fn=strategy_fn, chance_data=chained.chance_data)
                actions = list(chained.root.legal_actions)
                labels = [str(a) for a in actions]
                shipped_row = np.array([rows_by_arm["shipped"].get(a, 0.0) for a in labels])
                chained_row = np.array([rows_by_arm["chained"].get(a, 0.0) for a in labels])
                cost = ev_loss(shipped_row, chained_row, values, actions)["loss_bb"]
            except Exception as exc:                      # noqa: BLE001 - reported
                print("PRICE FAILED", type(exc).__name__, exc, flush=True)

            fh.write(json.dumps({
                "hand": hand.id, "i": index, "board": body["board"],
                "hero": body["hero_cards"], "pot": pot, "stack": stack, "spr": spr,
                "combos": len(hands) if cost is not None else None,
                "shipped_aggression": aggression(rows_by_arm["shipped"]),
                "shipped_converged_aggression": aggression(rows_by_arm["shipped_converged"]),
                "chained_aggression": aggression(rows_by_arm["chained"]),
                "cost_bb": cost,
                "iterations": CHAINED_ITERATIONS,
                "flop_seconds": flop_seconds, "chained_seconds": chained_seconds,
                "request": body,
            }) + "\n")
            fh.flush()
            written += 1
            print(" ", written, "of", SPOTS, body["board"], "SPR %.1f" % spr,
                  "%.0fs" % chained_seconds, time.strftime("%H:%M:%S"), flush=True)
    print("DONE", written, json.dumps(skips), flush=True)
    return 0


if __name__ == "__main__":                                # pragma: no cover
    sys.exit(main())
