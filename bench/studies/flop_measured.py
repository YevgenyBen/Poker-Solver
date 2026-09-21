"""Is the flop's reliability note still true? (audit R3, M300)

`FLOP_MEASURED_NOTE` is the highest-exposure stale disclosure left - it
reaches **12% of real decisions** - and it makes three separate claims:

1. "about one answer in seven was off by more than 0.10 in how often it
   bets or folds" (M180: 8 of 56 spots);
2. "neither how strong your hand is nor whether you are facing a bet
   predicts which answers those are" - a claim that there is NO
   predictor, which M180 made deliberately after M166/M167 asserted one
   from too few spots and had to withdraw it;
3. "the worst case measured was a top pair that a fuller solve bets
   almost always, where this advice checks" - Kc8c on 7h9hKd, reference
   0.9987 against 0.045.

**Why it is stale**: M180 measured at the flop's range cap 140 with ONE
bet size. M207 gave the flop a 0.33/0.75/2.5 menu and M234 took the cap
to 100, so every figure in it describes a tree the product no longer
builds.

**THIS STUDY RUNS NO SOLVES.** M292 and M297 already priced exactly this
comparison at the shipped configuration - real heads-up flop decisions
through `/advise` against an uncapped 169-class solve of the same
request - and their rows are committed. 176 of them, against M180's 56.
Re-reading them answers a different question from the one they were
drawn for, which is legitimate only if the rule is fixed first and what
was already known about the sample is written down:

**KNOWN BEFORE THIS RULE WAS WRITTEN** (all published in M292/M297):
mean |error| 0.1026 and worst 0.9037 over the 116; no hand-type band
separated for M292's own two clauses; the SIGNED facing-a-bet lean was
+0.0376 (2.09 sigma) on the first sample and +0.0181 (0.81) on the
fresh one, pooling to 1.82. **Not known**: any share over 0.10, any
|error| comparison between node types or strength bands, and which row
is the worst.

**PRE-REGISTERED READING RULE (fixed before any figure below was
computed):**

1. ERROR is `|shipped_aggression - reference_aggression|`, the same axis
   M180 measured - how often the advice bets or raises.
2. HEADLINE: the share of decisions with error > `THRESHOLD` (0.10, the
   note's own number), weighted by how often each stratum occurs. The
   sample is stratified by construction - M292 drew 30 open-ended draws
   deliberately, and M297 added 60 facing-a-bet rows - so an unweighted
   share is a share of the quota, not of what a player meets (M188).
   The copy quotes the weighted figure as "about one in N".
3. THE "NO PREDICTOR" CLAIM survives only if NEITHER candidate separates:
   mean error facing a bet against acting first, and mean error in the
   top strength quartile against the rest, each at < `MIN_SIGMA`. If
   either separates AND holds on both halves of a random split, the copy
   must name it - a note claiming nothing predicts the error, when
   something does, hides the one thing a player could act on.
4. THE WORST CASE is re-read from these rows and described as what it
   is. M180's example is a different tree's row and cannot stand.
5. Nulls are results. A stratum with fewer than `MIN_ROWS` rows is
   reported as unmeasured rather than as absent.
6. Every figure is a LOWER BOUND on distance from correct play: both
   arms share the model (M183's standing caveat).

    python -m bench.studies.flop_measured
"""
from __future__ import annotations

import json
import math
import pathlib
import statistics
import sys

THRESHOLD = 0.10
MIN_SIGMA = 2.0
MIN_ROWS = 6
STRONG_QUARTILE = 0.75

#: How often each stratum occurs among real heads-up flop decisions.
#: The open-ended rate is M292's own observation on an unstratified draw
#: (7 of 120); the rest splits by the hand store's flop cells, 0.2378
#: opening against 0.1118 facing.
OPEN_ENDED_RATE = 7 / 120
FACING_SHARE_OF_FLOP = 0.1118 / (0.1118 + 0.2378)

FIXTURES = ("aggression_caveat_m292.json", "aggression_facing_m297.json")


def load(directory: pathlib.Path = None) -> list:
    """Every committed flop row, from both samples. Pure given the files."""
    base = directory or (pathlib.Path(__file__).resolve().parents[2] / "tests" / "data")
    rows = []
    for name in FIXTURES:
        rows.extend(json.loads((base / name).read_text()))
    return rows


def error_of(row: dict) -> float:
    return abs(row["shipped_aggression"] - row["reference_aggression"])


def stratum(row: dict) -> str:
    """Which quota a row was drawn under. Pure.

    Open-ended draws were filled FIRST in both samples, so a row holding
    one belongs to that stratum whichever node type it is - reading it as
    an ordinary facing-a-bet row would count a deliberate oversample as
    if it were the natural rate.
    """
    if row.get("open_ended"):
        return "open_ended"
    return "facing" if row.get("facing") else "opening"


def _share_over(rows: list) -> float | None:
    if not rows:
        return None
    return sum(1 for r in rows if error_of(r) > THRESHOLD) / len(rows)


def headline(rows: list) -> dict:
    """Rule 2: the weighted share of answers off by more than 0.10."""
    by_stratum = {name: [r for r in rows if stratum(r) == name]
                  for name in ("open_ended", "facing", "opening")}
    shares = {name: _share_over(group) for name, group in by_stratum.items()}
    weights = {
        "open_ended": OPEN_ENDED_RATE,
        "facing": (1 - OPEN_ENDED_RATE) * FACING_SHARE_OF_FLOP,
        "opening": (1 - OPEN_ENDED_RATE) * (1 - FACING_SHARE_OF_FLOP),
    }
    live = {name: w for name, w in weights.items() if shares.get(name) is not None}
    total = sum(live.values())
    weighted = (sum(shares[name] * w for name, w in live.items()) / total) if total else None
    errors = [error_of(r) for r in rows]
    return {
        "n": len(rows),
        "share_over_threshold": weighted,
        "one_in": (1 / weighted) if weighted else None,
        "unweighted_share": _share_over(rows),
        "per_stratum": {name: {"n": len(group), "share": shares[name]}
                        for name, group in by_stratum.items()},
        "mean_error": statistics.mean(errors) if errors else None,
        "median_error": statistics.median(errors) if errors else None,
        "worst_error": max(errors) if errors else None,
    }


def _two_sample(left: list, right: list) -> dict:
    """Unpaired: these are different decisions, not two arms of one."""
    if len(left) < 2 or len(right) < 2:
        return {"n_left": len(left), "n_right": len(right), "sigma": None}
    mean_l, mean_r = statistics.mean(left), statistics.mean(right)
    sem = math.sqrt(statistics.stdev(left) ** 2 / len(left)
                    + statistics.stdev(right) ** 2 / len(right))
    return {"n_left": len(left), "n_right": len(right),
            "mean_left": mean_l, "mean_right": mean_r,
            "delta": mean_l - mean_r,
            "sigma": ((mean_l - mean_r) / sem) if sem else None}


def predictors(rows: list, seed: int = 300) -> dict:
    """Rule 3: the two candidates the note says do not predict the error."""
    import random

    def split(group, key):
        return ([error_of(r) for r in group if key(r)],
                [error_of(r) for r in group if not key(r)])

    facing_key = lambda r: bool(r.get("facing"))                      # noqa: E731
    strong_key = lambda r: r["percentile"] >= STRONG_QUARTILE         # noqa: E731

    out = {}
    for name, key in (("facing", facing_key), ("strength", strong_key)):
        left, right = split(rows, key)
        whole = _two_sample(left, right)
        rng = random.Random(seed)
        shuffled = list(rows)
        rng.shuffle(shuffled)
        halves = []
        for part in (shuffled[:len(shuffled) // 2], shuffled[len(shuffled) // 2:]):
            left_h, right_h = split(part, key)
            halves.append(_two_sample(left_h, right_h))
        out[name] = {"whole": whole, "halves": halves}
    return out


def separates(cell: dict) -> bool:
    """A predictor counts only if it separates AND both halves agree."""
    whole = cell["whole"]
    if whole.get("sigma") is None or abs(whole["sigma"]) < MIN_SIGMA:
        return False
    if min(whole["n_left"], whole["n_right"]) < MIN_ROWS:
        return False
    direction = whole["delta"] > 0
    return all(h.get("delta") is not None and (h["delta"] > 0) == direction
               for h in cell["halves"])


def worst_row(rows: list) -> dict:
    """Rule 4: the worst answer in this sample, described as what it is."""
    row = max(rows, key=error_of)
    return {"hero": row["hero"], "board": row["board"],
            "percentile": row["percentile"], "facing": bool(row.get("facing")),
            "shipped_aggression": row["shipped_aggression"],
            "reference_aggression": row["reference_aggression"],
            "error": error_of(row)}


def summarise(rows: list) -> dict:
    return {"headline": headline(rows), "predictors": predictors(rows),
            "worst": worst_row(rows)}


def verdict(summary: dict) -> dict:
    """Rule 3's outcome: does the note's "nothing predicts it" hold?"""
    found = [name for name, cell in summary["predictors"].items() if separates(cell)]
    return {"no_predictor_claim_holds": not found, "predictors_found": found}


def main(argv=None) -> int:                              # pragma: no cover
    rows = load()
    summary = summarise(rows)
    print(json.dumps({**summary, "verdict": verdict(summary)}, indent=1, default=str))
    return 0


if __name__ == "__main__":                                # pragma: no cover
    sys.exit(main())
