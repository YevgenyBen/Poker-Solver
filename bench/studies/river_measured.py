"""Is the river still "worse for strong hands"? (audit R3, M301)

`RIVER_MEASURED_NOTE` tells a player four things, all M177's:

1. reliability on the river **is worse for strong hands** - the top
   quarter by strength was wrong more than three times as often as weak
   hands (14 of 28 against 3 of 28);
2. it **errs in one direction** - it puts chips in more often than a
   fuller solve does;
3. so a recommendation to commit your stack with a strong-but-not-
   unbeatable hand, **especially facing a bet**, deserves suspicion;
4. **weak-hand advice on this street measured accurate.**

**Why it is stale.** M177 measured at the river's range cap 26 with no
bet size at all beyond all-in. M213 gave the river a 0.33/0.75/2.5 menu
and M231 took it to cap 60 at 1,000 iterations - and M231 measured that
change cutting the river's over-aggression against an independent solver
from **+0.1909 to +0.0463**. So claim 2, the one the copy leans on, was
already known to have shrunk by four fifths before this study ran.

**Two arms, because the claims need different instruments.**

* **A - the strength split, with no new solving.** M296 priced 492 river
  rows against an INDEPENDENT solver (41 references, the 2026-09-20
  audit's R6) and committed them. Those rows carry hero, the board and a
  regret in big blinds, so the split can be re-read directly - and
  against an outside reference, which is better evidence than M177 had.
* **B - the direction, which needs a fresh run.** Nothing committed
  carries a SIGNED aggression gap on the river, so claim 2 needs real
  river decisions through `/advise` scored against an uncapped solve of
  the same request.

**AMENDMENT (after arm B's first pass, before any of its figures were
read as a verdict).** That pass drew each hand's FIRST river decision
and came back with 120 rows of which **zero faced a bet** - M177's own
standing rule failing inside the study that re-measures M177, and
M252's failure in a new place: a benchmark measures the population it
generates. The rows are kept as the OPENING cell, and the facing cell is
a second pass with `RIVER_FACING_ONLY`. Claim 3 was therefore UNMEASURED
rather than refuted by that pass, and nothing in the rule changed - only
the population it is read over.

**KNOWN BEFORE THIS RULE WAS WRITTEN**: M231's +0.1909 -> +0.0463;
M236's pooled river gap +0.0526 at 2.28 sigma; M296's cell means (3.01
bb per 100 opening decisions, 11.05 facing). **Not known**: anything
split by hand strength at the shipped configuration.

**PRE-REGISTERED READING RULE (fixed before either arm was read):**

1. STRENGTH (claims 1 and 4): over M296's kept rows, the top quartile by
   strength percentile against the rest, on `regret_bb`. The claim
   survives only if strong exceeds weak at >= `MIN_SIGMA` AND both
   halves of a random split agree in direction. `tvd` is reported beside
   it as the axis closest to M177's own, and the copy's "three times as
   often" is re-derived as the ratio of the shares exceeding
   `TVD_THRESHOLD`.
2. DIRECTION (claim 2): over arm B's rows, the signed
   `shipped - reference` aggression. It survives only if it is POSITIVE
   at >= `MIN_SIGMA` with both split halves agreeing. A shrunken but
   real direction is quoted at its measured size; a null removes the
   sentence.
3. FACING A BET (claim 3): the node-type split on the same rows. The
   word "especially" survives only if facing separates at >= `MIN_SIGMA`.
4. Claim 4 stands or falls with claim 1: "weak-hand advice measured
   accurate" is a comparative statement, and without the split it is an
   absolute claim this study does not support.
5. Nulls are results, and each figure records WHICH instrument produced
   it. Arm A is an independent solver and arm B a fuller solve of our
   own model; they measure different things and must never be pooled.

    python -m bench.studies.river_measured             # arm A
    python -m bench.studies.river_measured run out.jsonl   # arm B
"""
from __future__ import annotations

import json
import math
import pathlib
import statistics
import sys

MIN_SIGMA = 2.0
MIN_ROWS = 6
STRONG_QUARTILE = 0.75
TVD_THRESHOLD = 0.10

#: M296's own inclusion rule, re-applied here rather than re-invented.
MIN_REACH = 0.05
MAX_OFF_SUPPORT = 0.5

FIXTURE_A = "river_price_m296.json"
SPOTS_B = int(__import__("os").environ.get("RIVER_SPOTS", 120))
SEED_B = int(__import__("os").environ.get("RIVER_SEED", 301))
#: Which river decision to build. **The first run of arm B left this at
#: "opening" by accident and produced 120 rows of which ZERO faced a
#: bet** - which is M177's own standing rule failing inside the study
#: that re-measures M177: a postflop sample that takes each street's
#: first decision contains no facing-a-bet node at all, and the
#: "especially facing a bet" clause cannot be read from it. Those rows
#: are kept as the opening cell; the facing cell is a separate pass.
FACING_ONLY_B = bool(__import__("os").environ.get("RIVER_FACING_ONLY"))


def board_of(tag: str) -> str:
    """`river_3bet_7h2s3s7c3h` -> `7h2s3s7c3h`. Pure.

    The board is in the tag because M296 keyed its references by spot
    name; reading it back is what lets a later question ask about hand
    strength without re-solving anything.
    """
    cards = tag.rsplit("_", 1)[-1]
    if len(cards) != 10:
        raise ValueError(f"{tag!r} does not end in a five-card board")
    return cards


def keep_a(rows: list) -> list:
    """M296's inclusion rule: the reference reaches the row, and our row
    sits on its support (M258 - at a pure node regret prices a sliver)."""
    return [r for r in rows
            if r.get("reach_fraction", 0.0) >= MIN_REACH
            and r.get("off_support", 0.0) <= MAX_OFF_SUPPORT]


def _two_sample(left: list, right: list) -> dict:
    if len(left) < 2 or len(right) < 2:
        return {"n_left": len(left), "n_right": len(right), "sigma": None}
    mean_l, mean_r = statistics.mean(left), statistics.mean(right)
    sem = math.sqrt(statistics.stdev(left) ** 2 / len(left)
                    + statistics.stdev(right) ** 2 / len(right))
    return {"n_left": len(left), "n_right": len(right),
            "mean_left": mean_l, "mean_right": mean_r,
            "delta": mean_l - mean_r,
            "ratio": (mean_l / mean_r) if mean_r else None,
            "sigma": ((mean_l - mean_r) / sem) if sem else None}


def _halves(rows: list, key, value, seed: int) -> list:
    import random
    rng = random.Random(seed)
    shuffled = list(rows)
    rng.shuffle(shuffled)
    out = []
    for part in (shuffled[:len(shuffled) // 2], shuffled[len(shuffled) // 2:]):
        out.append(_two_sample([value(r) for r in part if key(r)],
                               [value(r) for r in part if not key(r)]))
    return out


def strength_split(rows: list, seed: int = SEED_B) -> dict:
    """Rule 1. Rows must already carry `percentile`. Pure given the seed."""
    strong = lambda r: r["percentile"] >= STRONG_QUARTILE           # noqa: E731
    out = {}
    for axis in ("regret_bb", "tvd"):
        have = [r for r in rows if r.get(axis) is not None]
        out[axis] = {"whole": _two_sample([r[axis] for r in have if strong(r)],
                                          [r[axis] for r in have if not strong(r)]),
                     "halves": _halves(have, strong, lambda r, a=axis: r[a], seed)}
    over = [r for r in rows if r.get("tvd") is not None]
    strong_rows = [r for r in over if strong(r)]
    weak_rows = [r for r in over if not strong(r)]
    share = lambda group: (sum(1 for r in group if r["tvd"] > TVD_THRESHOLD) / len(group)
                           if group else None)                      # noqa: E731
    out["over_threshold"] = {
        "strong": share(strong_rows), "weak": share(weak_rows),
        "n_strong": len(strong_rows), "n_weak": len(weak_rows),
        "times_as_often": ((share(strong_rows) / share(weak_rows))
                           if share(weak_rows) else None),
    }
    return out


def separates(cell: dict, positive: bool = True) -> bool:
    """Separable AND replicated on both halves, in one direction."""
    whole = cell["whole"]
    if whole.get("sigma") is None or abs(whole["sigma"]) < MIN_SIGMA:
        return False
    if min(whole["n_left"], whole["n_right"]) < MIN_ROWS:
        return False
    if positive and whole["delta"] <= 0:
        return False
    direction = whole["delta"] > 0
    return all(h.get("delta") is not None and (h["delta"] > 0) == direction
               for h in cell["halves"])


def _one_sample(values: list) -> dict:
    """Mean and sigma of a SIGNED quantity against zero."""
    if len(values) < 2:
        return {"n": len(values), "mean": values[0] if values else None, "sigma": None}
    mean = statistics.mean(values)
    sem = statistics.stdev(values) / math.sqrt(len(values))
    return {"n": len(values), "mean": mean, "sigma": (mean / sem) if sem else None}


def _signed_halves(rows: list, value, seed: int) -> list:
    """Split the sample in two and read each half's own signed mean.

    Rule 2 asks whether the DIRECTION replicates, which is a one-sample
    question - the first implementation reached for `_halves`, the
    two-sample helper rule 1 and 3 use, and produced a degenerate split
    with every row on one side and no sigma at all. Fixed to match the
    rule as written; the rule itself did not move.
    """
    import random
    rng = random.Random(seed)
    shuffled = list(rows)
    rng.shuffle(shuffled)
    return [_one_sample([value(r) for r in part])
            for part in (shuffled[:len(shuffled) // 2], shuffled[len(shuffled) // 2:])]


def direction_split(rows: list, seed: int = SEED_B) -> dict:
    """Rules 2 and 3, over arm B's rows."""
    signed_of = lambda r: r["shipped_aggression"] - r["reference_aggression"]   # noqa: E731
    facing = lambda r: bool(r.get("facing"))                        # noqa: E731
    error = lambda r: abs(signed_of(r))                             # noqa: E731
    whole = _one_sample([signed_of(r) for r in rows])
    return {
        "n": len(rows),
        "signed": whole["mean"],
        "sigma": whole["sigma"],
        "halves": _signed_halves(rows, signed_of, seed),
        "per_kind": {
            "facing": _one_sample([signed_of(r) for r in rows if facing(r)]),
            "opening": _one_sample([signed_of(r) for r in rows if not facing(r)]),
        },
        "facing": {"whole": _two_sample([error(r) for r in rows if facing(r)],
                                        [error(r) for r in rows if not facing(r)]),
                   "halves": _halves(rows, facing, error, seed)},
    }


def summarise(rows: list) -> dict:
    """Read a set of rows as whichever arm produced them.

    The two arms carry different columns - arm A a regret priced against
    an independent solver, arm B a signed aggression gap against a
    fuller solve of our own model - and they must never be pooled, so
    this dispatches rather than merging. It is also the entry point
    `bench/disclosures.py` requires of a registered study.
    """
    if not rows:
        return {"arm": None, "n": 0}
    if "shipped_aggression" in rows[0]:
        summary = direction_split(rows)
        return {"arm": "B", **summary, "verdict": verdict_b(summary)}
    summary = strength_split(rows)
    return {"arm": "A", "n": len(rows), **summary, "verdict": verdict_a(summary)}


def verdict_a(summary: dict) -> dict:
    """Claims 1 and 4."""
    holds = separates(summary["regret_bb"])
    return {"strong_hands_worse": holds, "weak_hands_accurate_claim": holds}


def verdict_b(summary: dict) -> dict:
    """Claims 2 and 3."""
    replicates = all(half.get("mean") is not None and half["mean"] > 0
                     for half in summary["halves"])
    return {
        "errs_in_one_direction": bool(
            summary["sigma"] is not None and summary["sigma"] >= MIN_SIGMA
            and summary["signed"] > 0 and replicates),
        "especially_facing_a_bet": separates(summary["facing"]),
    }


def load_a(directory: pathlib.Path = None) -> list:      # pragma: no cover
    """M296's rows, with a strength percentile added to each."""
    from poker_solver.cards import parse_cards
    from poker_solver.combos import HandCombo
    from poker_solver.hand_strength import strength_percentile

    base = directory or (pathlib.Path(__file__).resolve().parents[2] / "tests" / "data")
    rows = keep_a(json.loads((base / FIXTURE_A).read_text()))
    for row in rows:
        board = tuple(parse_cards(board_of(row["tag"])))
        row["percentile"] = strength_percentile(
            HandCombo(*parse_cards(row["hero"])), board)
    return rows


def main(argv=None) -> int:                              # pragma: no cover
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] == "run":
        return _run(args[1])
    if args:
        rows = [json.loads(line) for line in open(args[0]) if line.strip()]
    else:
        rows = load_a()
    print(json.dumps(summarise(rows), indent=1, default=str))
    return 0


def _run(out_path: str) -> int:                          # pragma: no cover
    """Arm B: real river decisions, shipped against an uncapped solve."""
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
    rng = random.Random(SEED_B)
    written = 0
    skips = {"multiway_pot": 0, "no_river": 0, "unrepresentable": 0,
             "refused": 0, "not_heads_up": 0}
    with open(out_path, "w") as fh:
        for hand in sample_hands(db, 20000, SEED_B,
                                 where=DEFAULT_WHERE + " AND last_street >= 3"):
            if written >= SPOTS_B:
                break
            acts = [a for a in hand.streets() if a.kind != "show"]
            if live_after_preflop(acts, hand.n_players) != 2:
                skips["multiway_pot"] += 1
                continue
            river = [i for i, a in enumerate(acts) if a.street == "river"]
            if FACING_ONLY_B:
                # M177: a facing-a-bet node has to be CONSTRUCTED. Taking
                # the street's first decision yields an opening one every
                # time, which is how this arm's first run came back with
                # 120 rows and no facing cell.
                river = [i for i in river if (acts[i].facing_bb or 0) > 1e-9]
            if not river:
                skips["no_river"] += 1
                continue
            index = river[0]
            cards = deal(hand.board, hand.n_players, rng)
            body, _why, _worst = request_for(
                hand, acts, index, round(hand.row["eff_stack_bb"], 2), cards, post=post)
            if body is None:
                skips["unrepresentable"] += 1
                continue
            body["hero_cards"] = cards[acts[index].player]
            # Captured, never hardcoded: a study that writes the shipped
            # width back as a literal pins the very configuration this
            # audit item exists to stop disclosures from pinning.
            shipped_width = cfg.RIVER_STANDALONE_CLASSES_PER_SIDE
            shipped_iterations = cfg.RIVER_STANDALONE_ITERATIONS
            rows = {}
            try:
                for arm in ("shipped", "reference"):
                    if arm == "reference":
                        cfg.RIVER_STANDALONE_CLASSES_PER_SIDE = 169
                        cfg.RIVER_STANDALONE_ITERATIONS = 2500
                    # M245's trap: a range cap lives in no cache key, so
                    # the wrong arm comes back faster and reads as the
                    # right one. Clear between arms, every time.
                    clear_postflop_caches()
                    status, js = post(body)
                    hero = (js.get("hero") or {}).get("strategy") if status == 200 else None
                    if not hero or len(js.get("positions") or []) != 2:
                        break
                    rows[arm] = (hero, js)
            finally:
                cfg.RIVER_STANDALONE_CLASSES_PER_SIDE = shipped_width
                cfg.RIVER_STANDALONE_ITERATIONS = shipped_iterations
            if len(rows) != 2:
                skips["refused" if "shipped" not in rows else "not_heads_up"] += 1
                continue
            shipped_hero, shipped_js = rows["shipped"]
            reference_hero, _reference_js = rows["reference"]
            board = body["board"] + body["turn_card"] + body["river_card"]
            fh.write(json.dumps({
                "hand": hand.id, "i": index, "board": board, "hero": body["hero_cards"],
                "percentile": strength_percentile(
                    HandCombo(*parse_cards(body["hero_cards"])),
                    tuple(parse_cards(board))),
                "facing": "fold" in shipped_hero,
                "shipped_aggression": aggression(shipped_hero),
                "reference_aggression": aggression(reference_hero),
                "pot": shipped_js.get("pot"),
            }) + "\n")
            fh.flush()
            written += 1
            if written % 10 == 0:
                print(written, "of", SPOTS_B, time.strftime("%H:%M:%S"), flush=True)
    print("DONE", written, json.dumps(skips), flush=True)
    return 0


if __name__ == "__main__":                                # pragma: no cover
    sys.exit(main())
