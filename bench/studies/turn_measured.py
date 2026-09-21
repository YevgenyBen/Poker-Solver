"""The turn, measured the way the flop and river were (audit R3, M303).

`UNMEASURED_STREET_NOTE` fires on the turn - and only the turn, since
`api/main.py` maps the flop and river to their own notes - and tells a
player three things:

1. "accuracy on this street **has not been measured** against a larger
   solve the way the flop has";
2. "on the turn, where this was tested directly, the advice was off by
   more than **0.30 at BOTH ends** of the strength range";
3. "how strong your hand is **did not predict** which answers were the
   accurate ones".

**Why it is stale.** All three are M175's, over 24 spots at the turn's
range cap 26. M179 took the turn to cap 140 and M213 gave it a
0.33/0.75/2.5 menu, so the tree behind those figures is gone.

**And claim 1 is self-undermining**: the moment the turn IS measured the
same way, the note's own first sentence is false. M177 made exactly this
move on the river, replacing the blanket "not measured" with a measured
note. So this study either produces a turn-measured note or leaves the
street genuinely unmeasured; there is no outcome where the copy stays as
it is.

**Arms.** Real heads-up turn decisions through `/advise` at the shipped
configuration, scored against an uncapped 169-class solve of the same
request - the same comparison M300 ran on the flop and M301's arm B on
the river, so the three streets end up on one axis for the first time.

**Half the sample FACES A BET, by construction.** M177's standing rule,
and M301 broke it three milestones ago: a sample that takes each
street's first decision contains no facing-a-bet node at all. The two
cells are filled by separate passes.

**PRE-REGISTERED READING RULE (fixed before any row was scored):**

1. ERROR is `|shipped_aggression - reference_aggression|` - how often
   the advice bets or raises, M175's own axis.
2. HEADLINE: the mean, the median, the worst and the share over
   `THRESHOLD` (0.10), per node type and pooled by real occurrence.
3. CLAIM 2 ("more than 0.30 at both ends") survives only if BOTH the top
   and bottom strength bands have mean error above `BOTH_ENDS_LEVEL`.
   Either band failing removes the figure; the copy then quotes what was
   measured.
4. CLAIM 3 ("strength does not predict") survives only if the top
   quartile against the rest is BELOW `MIN_SIGMA` - the same test the
   flop passed and the river passed. If strength does separate, the copy
   must name it.
5. CLAIM 1 is decided by the study existing: a measured street gets a
   measured note, and the blanket "not measured" wording goes.
6. Nulls are results. Every figure is a LOWER BOUND - both arms share
   the model (M183's standing caveat) - and the copy says so.

    python -m bench.studies.turn_measured run rows.jsonl
    python -m bench.studies.turn_measured rows.jsonl
"""
from __future__ import annotations

import json
import os
import statistics
import sys

from bench.studies.river_measured import _one_sample, _two_sample, separates

THRESHOLD = 0.10
MIN_SIGMA = 2.0
MIN_ROWS = 6
STRONG_QUARTILE = 0.75
WEAK_QUARTILE = 0.25
#: The level M175 published at both ends of the strength range.
BOTH_ENDS_LEVEL = 0.30

#: Real occurrence among all postflop decisions in pots heads-up from the
#: flop, counted for M299 over the hand store: the turn is 0.1382
#: opening and 0.0562 facing.
EXPOSURE = {"opening": 0.1382, "facing": 0.0562}

SPOTS = int(os.environ.get("TURN_SPOTS", 60))
SEED = int(os.environ.get("TURN_SEED", 303))
FACING_ONLY = bool(os.environ.get("TURN_FACING_ONLY"))


def error_of(row: dict) -> float:
    return abs(row["shipped_aggression"] - row["reference_aggression"])


def cell(rows: list) -> dict:
    """Mean, median, worst and the share over the threshold. Pure."""
    if not rows:
        return {"n": 0}
    errors = [error_of(r) for r in rows]
    return {
        "n": len(rows),
        "mean": statistics.mean(errors),
        "median": statistics.median(errors),
        "worst": max(errors),
        "over_threshold": sum(1 for e in errors if e > THRESHOLD) / len(errors),
        "signed": statistics.mean(
            r["shipped_aggression"] - r["reference_aggression"] for r in rows),
    }


def headline(rows: list) -> dict:
    """Rule 2, with the two cells weighted by how often they occur."""
    cells = {kind: cell([r for r in rows if bool(r.get("facing")) == (kind == "facing")])
             for kind in ("opening", "facing")}
    live = {k: EXPOSURE[k] for k, c in cells.items() if c.get("n")}
    total = sum(live.values())
    weighted = (sum(cells[k]["over_threshold"] * w for k, w in live.items()) / total
                if total else None)
    weighted_mean = (sum(cells[k]["mean"] * w for k, w in live.items()) / total
                     if total else None)
    return {"cells": cells, "n": len(rows),
            "over_threshold": weighted, "mean": weighted_mean,
            "unweighted_over_threshold": (cell(rows) or {}).get("over_threshold")}


def both_ends(rows: list) -> dict:
    """Rule 3: M175's claim that BOTH ends of the strength range are bad."""
    top = [r for r in rows if r["percentile"] >= STRONG_QUARTILE]
    bottom = [r for r in rows if r["percentile"] <= WEAK_QUARTILE]
    return {
        "top": cell(top), "bottom": cell(bottom),
        "level": BOTH_ENDS_LEVEL,
        "holds": bool(len(top) >= MIN_ROWS and len(bottom) >= MIN_ROWS
                      and cell(top)["mean"] > BOTH_ENDS_LEVEL
                      and cell(bottom)["mean"] > BOTH_ENDS_LEVEL),
    }


def strength_split(rows: list, seed: int = SEED) -> dict:
    """Rule 4: does hand strength predict the error here?"""
    import random
    strong = lambda r: r["percentile"] >= STRONG_QUARTILE           # noqa: E731
    whole = _two_sample([error_of(r) for r in rows if strong(r)],
                        [error_of(r) for r in rows if not strong(r)])
    rng = random.Random(seed)
    shuffled = list(rows)
    rng.shuffle(shuffled)
    halves = []
    for part in (shuffled[:len(shuffled) // 2], shuffled[len(shuffled) // 2:]):
        halves.append(_two_sample([error_of(r) for r in part if strong(r)],
                                  [error_of(r) for r in part if not strong(r)]))
    return {"whole": whole, "halves": halves}


def node_split(rows: list, seed: int = SEED) -> dict:
    """The comparison the flop and river both made: facing against opening."""
    import random
    facing = lambda r: bool(r.get("facing"))                        # noqa: E731
    whole = _two_sample([error_of(r) for r in rows if facing(r)],
                        [error_of(r) for r in rows if not facing(r)])
    rng = random.Random(seed)
    shuffled = list(rows)
    rng.shuffle(shuffled)
    halves = []
    for part in (shuffled[:len(shuffled) // 2], shuffled[len(shuffled) // 2:]):
        halves.append(_two_sample([error_of(r) for r in part if facing(r)],
                                  [error_of(r) for r in part if not facing(r)]))
    return {"whole": whole, "halves": halves}


def summarise(rows: list) -> dict:
    return {
        "n": len(rows),
        "headline": headline(rows),
        "both_ends": both_ends(rows),
        "strength": strength_split(rows),
        "node_type": node_split(rows),
        "signed": _one_sample([r["shipped_aggression"] - r["reference_aggression"]
                               for r in rows]),
    }


def verdict(summary: dict) -> dict:
    strength_predicts = separates(summary["strength"]) or separates(
        {"whole": {**summary["strength"]["whole"],
                   "delta": -summary["strength"]["whole"].get("delta", 0.0)},
         "halves": [{**h, "delta": -h.get("delta", 0.0)} if h.get("delta") is not None else h
                    for h in summary["strength"]["halves"]]})
    return {
        # Claim 1 is answered by the study existing.
        "street_is_measured_now": summary["n"] >= MIN_ROWS,
        "both_ends_over_the_level": summary["both_ends"]["holds"],
        "strength_predicts": strength_predicts,
        "node_type_predicts": separates(summary["node_type"]) or separates(
            {"whole": {**summary["node_type"]["whole"],
                       "delta": -summary["node_type"]["whole"].get("delta", 0.0)},
             "halves": [{**h, "delta": -h.get("delta", 0.0)} if h.get("delta") is not None else h
                        for h in summary["node_type"]["halves"]]}),
    }


def main(argv=None) -> int:                              # pragma: no cover
    import pathlib
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] == "run":
        return _run(args[1])
    source = pathlib.Path(args[0]) if args else (
        pathlib.Path(__file__).resolve().parents[2] / "tests" / "data"
        / "turn_measured_m303.jsonl")
    rows = [json.loads(line) for line in source.read_text().splitlines() if line.strip()]
    summary = summarise(rows)
    print(json.dumps({**summary, "verdict": verdict(summary)}, indent=1, default=str))
    return 0


def _run(out_path: str) -> int:                          # pragma: no cover
    """Real turn decisions, shipped against an uncapped solve."""
    import random
    import time

    from fastapi.testclient import TestClient
    from api import config as cfg
    from api.main import app
    from bench import hand_db
    from bench.real_replay import DEFAULT_WHERE, deal, request_for, sample_hands
    from bench.server_warmup import clear_postflop_caches, warm_multiway
    from bench.studies.facing_cost import live_after_preflop
    from poker_solver.cards import parse_cards
    from poker_solver.combos import HandCombo
    from poker_solver.hand_strength import strength_percentile

    def aggression(strategy: dict) -> float:
        return sum(v for k, v in strategy.items()
                   if k.split(":")[0] not in ("fold", "call_or_check"))

    client = TestClient(app)
    warm_multiway(depths=(100.0,), table_sizes=(6,))

    def post(body):
        r = client.post("/advise", json=body)
        return r.status_code, (r.json() if r.status_code == 200 else {})

    db = hand_db.connect()
    rng = random.Random(SEED)
    written = 0
    skips = {"multiway_pot": 0, "no_turn": 0, "unrepresentable": 0,
             "refused": 0, "not_heads_up": 0}
    with open(out_path, "w") as fh:
        for hand in sample_hands(db, 20000, SEED,
                                 where=DEFAULT_WHERE + " AND last_street >= 2"):
            if written >= SPOTS:
                break
            acts = [a for a in hand.streets() if a.kind != "show"]
            if live_after_preflop(acts, hand.n_players) != 2:
                skips["multiway_pot"] += 1
                continue
            turn = [i for i, a in enumerate(acts) if a.street == "turn"]
            if FACING_ONLY:
                # M177, and M301 broke it three milestones ago: a
                # facing-a-bet node has to be constructed, or the sample
                # is every street's first decision and contains none.
                turn = [i for i in turn if (acts[i].facing_bb or 0) > 1e-9]
            if not turn:
                skips["no_turn"] += 1
                continue
            index = turn[0]
            cards = deal(hand.board, hand.n_players, rng)
            body, _why, _worst = request_for(
                hand, acts, index, round(hand.row["eff_stack_bb"], 2), cards, post=post)
            if body is None:
                skips["unrepresentable"] += 1
                continue
            body["hero_cards"] = cards[acts[index].player]
            # **`TURN_STANDALONE_CLASSES_PER_SIDE`, not
            # `MAX_TURN_PATH_QUERY_CLASSES_PER_SIDE`** - the latter is
            # the CHAINED turn's cap (4) and is inert on the standalone
            # path this ships. M222's first run was voided for exactly
            # this and `api/config.py` records it; the first draft of
            # this study made the same mistake, and the arms still
            # differed because the ITERATIONS knob was doing the work.
            # M155's rule: the constant governing the path you are on is
            # not always the one you patched.
            width = cfg.TURN_STANDALONE_CLASSES_PER_SIDE
            iterations = cfg.TURN_STANDALONE_ITERATIONS
            rows = {}
            try:
                for arm in ("shipped", "reference"):
                    if arm == "reference":
                        cfg.TURN_STANDALONE_CLASSES_PER_SIDE = 169
                        cfg.TURN_STANDALONE_ITERATIONS = 2500
                    # M245: a range cap lives in no cache key, so the
                    # wrong arm comes back faster and reads as the right
                    # one. Clear between arms, every time.
                    clear_postflop_caches()
                    status, js = post(body)
                    hero = (js.get("hero") or {}).get("strategy") if status == 200 else None
                    if not hero or len(js.get("positions") or []) != 2:
                        break
                    rows[arm] = (hero, js)
            finally:
                cfg.TURN_STANDALONE_CLASSES_PER_SIDE = width
                cfg.TURN_STANDALONE_ITERATIONS = iterations
            if len(rows) != 2:
                skips["refused" if "shipped" not in rows else "not_heads_up"] += 1
                continue
            shipped_hero, shipped_js = rows["shipped"]
            reference_hero, _ = rows["reference"]
            board = body["board"] + body["turn_card"]
            fh.write(json.dumps({
                "hand": hand.id, "i": index, "board": board, "hero": body["hero_cards"],
                "percentile": strength_percentile(
                    HandCombo(*parse_cards(body["hero_cards"])),
                    tuple(parse_cards(board))),
                "facing": "fold" in shipped_hero,
                "shipped_aggression": aggression(shipped_hero),
                "reference_aggression": aggression(reference_hero),
                "pot": shipped_js.get("pot"), "request": body,
            }) + "\n")
            fh.flush()
            written += 1
            if written % 10 == 0:
                print(written, "of", SPOTS, time.strftime("%H:%M:%S"), flush=True)
    print("DONE", written, json.dumps(skips), flush=True)
    return 0


if __name__ == "__main__":                                # pragma: no cover
    sys.exit(main())
