"""Re-measure the standing postflop aggression caveat (audit R7, M292).

`POSTFLOP_AGGRESSION_CAVEAT_REASON` is the most-met disclosure in the
product - it fires on EVERY postflop decision, 23% of real decisions in
the 1,200-hand replay. Its figures are M140/M142's, measured at flop
range cap 26, 30 equity samples and ONE bet size. The flop has shipped at
cap 100 with a 0.33/0.75/2.5 menu since M207/M234, so every number in it
is unverified at the configuration that ships (F59).

**Arms.** The same real flop decisions through `/advise` twice, changing
only the budget, one process each (caps are config constants and appear
in no cache key - M245's trap, where the wrong arm comes back faster):
- `shipped`: as it ships.
- `reference`: `MAX_PATH_QUERY_CLASSES_PER_SIDE` 169 (uncapped),
  `PATH_QUERY_EQUITY_SAMPLES` 200, `PATH_QUERY_ITERATIONS` 2500. An
  uncapped 1,176-combo flop solve costs 13.3s here (M161/M176 made this
  affordable; M140 could not have run it at this width).

Heads-up flop decisions only, which is what M140/M142 measured. Both
arms answer the same request, so the ranges, the pot and the stack are
the product's own (M180's rule).

**Half the spots FACE A BET, by construction.** M177's standing rule: a
postflop study that samples each street's first decision measures only
opening decisions, and the weak-hand clause here is about facing a bet.
The first run of this study did exactly that and its weak-hand cell came
back EMPTY - which would have retired the clause on a population that
could not contain it (M252's failure).

**AMENDMENT (after the discarded first run, before any arm of the real
one).** That run drew 7 open-ended rows out of 120 - too few for a 2
sigma test either way - so the sample is now STRATIFIED: 45 opening
decisions, 45 facing a bet, and 30 spots where hero holds an open-ended
straight draw, out of the same 120. The bars below are unchanged. The
first run's open-ender cell leaned NEGATIVE (-0.2164, 1.47 sigma, i.e.
against the clause), so this widening can only make its death easier to
establish, not harder; it is recorded because the amendment was written
after seeing that lean.

**PRE-REGISTERED READING RULE (fixed before either arm was run):**

1. HEADLINE: aggression = the probability mass on bet/raise/all-in.
   `POSTFLOP_AGGRESSION_ERROR_MEAN` and `_WORST` are replaced by the mean
   and worst |shipped - reference| over every answered row.
2. The OPEN-ENDER clause survives only if open-ended straight draws are
   signed MORE aggressive than the reference at >= 2 sigma over >= 6
   rows. Otherwise it is removed - including its "88% of the time" case.
3. The WEAK-HAND-FACING-A-BET clause survives only if, facing a bet,
   hands in the weakest quarter by strength percentile continue (1 -
   fold) more than the reference at >= 2 sigma over >= 6 rows.
   Otherwise it is removed, including its nine-high case.
4. A clause that survives quotes THIS run's numbers, not M140's.
5. Whatever happens, the disclosure records the configuration it was
   measured at.

    python -m bench.studies.aggression_caveat shipped rows.jsonl
    python -m bench.studies.aggression_caveat reference rows.jsonl
"""
from __future__ import annotations

import json
import math
import statistics
import sys

import os

SPOTS = int(os.environ.get("AGGR_SPOTS", 120))
#: 45 / 45 / 30 of the same 120 (see the AMENDMENT above). The open-ended
#: quota is filled first, since those rows are ~6% of a random sample.
QUOTAS = {"open_ended": 30, "facing": 45, "opening": 45}
#: M297 (audit R5) re-runs this on FRESH spots to replicate one cell, so
#: the sample seed and the quotas are settable - `AGGR_SEED` picks a
#: different population, never a different rule.
if os.environ.get("AGGR_FACING_ONLY"):
    QUOTAS = {"open_ended": 0, "facing": SPOTS, "opening": 0}
SEED = int(os.environ.get("AGGR_SEED", 292))
WEAK_BAND = 0.25
MIN_ROWS = 6
MIN_SIGMA = 2.0
RANKS = "23456789TJQKA"


def aggression(strategy: dict) -> float:
    """Mass on bet/raise/all-in. The action key is `kind:size`."""
    return sum(v for k, v in strategy.items()
               if k.split(":")[0] not in ("fold", "call_or_check"))


def folding(strategy: dict) -> float:
    return sum(v for k, v in strategy.items() if k.split(":")[0] == "fold")


def is_open_ended(hero: str, board: str) -> bool:
    """Four cards to a straight, open at BOTH ends, using both ranks of a
    two-card hand and the board - and not already a straight.

    Deliberately strict: a gutshot and an open-ender behaved differently
    in M140 (gutshots were clean), so lumping them together would measure
    a different claim from the one the copy makes.
    """
    cards = [hero[i:i + 2] for i in (0, 2)] + [board[i:i + 2] for i in range(0, len(board), 2)]
    ranks = {RANKS.index(c[0]) for c in cards}
    if any(all(r + k in ranks for k in range(5)) for r in range(len(RANKS))):
        return False                      # already a straight
    # `low` starts at 0 so 2-3-4-5 counts (an ace or a six completes it)
    # and stops so J-Q-K-A does not (only a ten does - a one-ender).
    for low in range(0, len(RANKS) - 4):
        run = {low, low + 1, low + 2, low + 3}
        if run <= ranks and (low + 4) < len(RANKS):
            # Hero must be part of the draw, or this is the board's straight
            # draw and not the hand's.
            if run & {RANKS.index(hero[0]), RANKS.index(hero[2])}:
                return True
    return False


def _paired(rows, key):
    values = [r[key] for r in rows]
    if len(values) < 2:
        return {"n": len(values), "mean": values[0] if values else None, "sigma": None}
    mean = statistics.mean(values)
    sd = statistics.stdev(values)
    return {"n": len(values), "mean": mean,
            "sigma": (mean / (sd / math.sqrt(len(values)))) if sd else None}


def summarise(rows: list) -> dict:
    """The headline error and the two clause cells. Pure.

    Each row carries `shipped_aggression`, `reference_aggression`,
    `shipped_continue`, `reference_continue`, `open_ended`, `facing`,
    `percentile`.
    """
    for r in rows:
        r["error"] = abs(r["shipped_aggression"] - r["reference_aggression"])
        r["signed"] = r["shipped_aggression"] - r["reference_aggression"]
        r["continue_gap"] = r["shipped_continue"] - r["reference_continue"]
    errors = [r["error"] for r in rows]
    weak_facing = [r for r in rows if r["facing"] and r["percentile"] < WEAK_BAND]
    return {
        "n": len(rows),
        "mean_error": statistics.mean(errors) if errors else None,
        "worst_error": max(errors) if errors else None,
        "open_ended": _paired([r for r in rows if r["open_ended"]], "signed"),
        "weak_facing": _paired(weak_facing, "continue_gap"),
    }


def clause_survives(cell: dict) -> bool:
    """A clause is kept only on its own evidence, at this configuration."""
    return (cell["n"] >= MIN_ROWS and cell["mean"] is not None and cell["mean"] > 0
            and cell["sigma"] is not None and cell["sigma"] >= MIN_SIGMA)


def verdict(summary: dict) -> dict:
    return {"open_ended_clause": clause_survives(summary["open_ended"]),
            "weak_facing_clause": clause_survives(summary["weak_facing"])}


def main(argv=None) -> int:                              # pragma: no cover
    import random
    from fastapi.testclient import TestClient
    from api import config as cfg
    from api.main import app
    from bench import hand_db
    from bench.real_replay import DEFAULT_WHERE, deal, request_for, sample_hands
    from bench.server_warmup import clear_postflop_caches, warm_multiway
    from poker_solver.cards import parse_cards
    from poker_solver.combos import HandCombo
    from poker_solver.hand_strength import strength_percentile

    def percentile_of(hero_cards: str, board_cards: str) -> float:
        """`strength_percentile` takes PARSED cards, not strings - passing
        strings raises on the board's length, which is how the first run
        of this study produced zero rows."""
        return strength_percentile(HandCombo(*parse_cards(hero_cards)),
                                   tuple(parse_cards(board_cards)))


    args = list(argv if argv is not None else sys.argv[1:])
    arm, out_path = args[0], args[1]
    if arm == "reference":
        cfg.MAX_PATH_QUERY_CLASSES_PER_SIDE = 169
        cfg.PATH_QUERY_EQUITY_SAMPLES = 200
        cfg.PATH_QUERY_ITERATIONS = 2500
    client = TestClient(app)
    warm_multiway(depths=(100.0,), table_sizes=(6,))

    def post(body):
        r = client.post("/advise", json=body)
        return r.status_code, (r.json() if r.status_code == 200 else {})

    db = hand_db.connect()
    rng = random.Random(SEED)
    written = 0
    counts = {"facing": 0, "opening": 0, "open_ended": 0}
    skips = {"unrepresentable": 0, "refused": 0, "not_heads_up": 0, "no_quota_match": 0}
    with open(out_path, "w") as fh:
        for hand in sample_hands(db, 4000, SEED, where=DEFAULT_WHERE):
            if written >= SPOTS:
                break
            acts = [a for a in hand.streets() if a.kind != "show"]
            flop = [i for i, a in enumerate(acts) if a.street == "flop"]
            if not flop:
                continue
            facing = [i for i in flop if (acts[i].facing_bb or 0) > 1e-9]
            if all(counts[k] >= QUOTAS[k] for k in QUOTAS):
                break
            # The deal comes FIRST: the open-ended quota is a property of
            # the cards this hand deals its actors, so it cannot be read
            # before they exist.
            cards = deal(hand.board, hand.n_players, rng)
            # Stratified: an open-ended draw counts toward its own quota
            # whichever kind of decision it is, because the clause it
            # tests is about the HAND, not the node.
            index = None
            if counts["open_ended"] < QUOTAS["open_ended"]:
                for i in flop:
                    hero_cards = cards.get(acts[i].player)
                    if hero_cards and is_open_ended(hero_cards, hand.board[0:6]):
                        index = i
                        break
            if index is None and counts["facing"] < QUOTAS["facing"] and facing:
                index = facing[0]
            elif index is None and counts["opening"] < QUOTAS["opening"] and (
                    acts[flop[0]].facing_bb or 0) <= 1e-9:
                index = flop[0]
            if index is None:
                skips["no_quota_match"] += 1
                continue
            body, why, _ = request_for(hand, acts, index, round(hand.row["eff_stack_bb"], 2),
                                       cards, post=post)
            if body is None:
                skips["unrepresentable"] += 1
                continue
            body["hero_cards"] = cards[acts[index].player]
            clear_postflop_caches()
            status, js = post(body)
            hero = (js.get("hero") or {}).get("strategy") if status == 200 else None
            if not hero:
                skips["refused"] += 1
                continue
            if len(js.get("positions") or []) != 2:
                skips["not_heads_up"] += 1
                continue
            board = body["board"]
            fh.write(json.dumps({
                "hand": hand.id, "i": index, "arm": arm, "board": board,
                # Facing a bet is read off the ROW, not the request: folding
                # is only legal against a bet, and `to_call_bb` never leaves
                # the internal dict (the first runs read it and every row
                # came back "opening").
                "hero": body["hero_cards"], "facing": "fold" in hero,
                "percentile": percentile_of(body["hero_cards"], board),
                "open_ended": is_open_ended(body["hero_cards"], board),
                "aggression": aggression(hero), "continue": 1.0 - folding(hero),
                "seconds": js.get("elapsed_seconds"),
            }) + "\n")
            fh.flush()
            written += 1
            counts["facing" if "fold" in hero else "opening"] += 1
            if is_open_ended(body["hero_cards"], board):
                counts["open_ended"] += 1
            if written % 20 == 0:
                print(arm, written, flush=True)
    print("DONE", arm, written, json.dumps(counts), json.dumps(skips), flush=True)
    return 0


if __name__ == "__main__":                                # pragma: no cover
    sys.exit(main())


# -- WHERE THIS STOPPED (M292, paused before any figure shipped) ----------
#
# The study and its rule are complete and tested; the DATA is not in yet.
# Two arms must be run, one process each, before anything is rewritten:
#
#     python -m bench.memory_guard --log ship.guard.json -- \
#         python -m bench.studies.aggression_caveat shipped shipped.jsonl
#     python -m bench.memory_guard --log ref.guard.json -- \
#         python -m bench.studies.aggression_caveat reference reference.jsonl
#
# Roughly 40 minutes and 2 hours respectively, peaking near 8.7 GB.
#
# A FIRST run (opening decisions only, before the sampler was fixed) gave
# mean |error| 0.1569 and worst 0.8549 against M140's shipped 0.1394 and
# 0.8810 - so the headline reproduces at the shipped configuration - and
# the open-ender cell came back at n=7, signed **-0.2164 (-1.47 sigma)**,
# i.e. the REVERSE of the direction the copy claims. That run's weak-hand
# cell was EMPTY, which is why the sampler now builds facing-a-bet spots.
# None of it is quotable: the population was wrong. Re-run both arms.
