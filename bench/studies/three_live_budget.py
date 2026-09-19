"""Is the three-live postflop budget worth what it costs? (audit R6, M291)

R6 asked for a cap on TOTAL multiway work by live count, because M253
measured a six-live flop at ~11.4 reference units and width is capped per
seat. **Re-measured, the premise is gone**: since M266 gave four or more
live the smaller budget (`MULTIWAY_WIDE_POT_MIN_LIVE`), cost PEAKS at
three live, which is the most common multiway cell there is.

`bench/studies/multiway_live_cost.py` (ten cold six-handed decisions per
cell, timed in reference units, bar = 5s at the run's median reference):

    street  live  p50 units  p90    over 19.0?   real exposure
    flop    3     24.6       25.7   YES          22.19%
    flop    4     12.7       13.3   no            6.43%
    flop    5     17.9       19.6   YES           1.65%
    flop    6     19.3       20.5   YES           0.30%
    turn    3     21.0       23.2   YES          14.54%
    river   any   1.6-3.4     -     no            <10%

So the question became whether the three-live budget (4,000 iterations
on the flop and the standalone turn, M264) can be halved.

**PRE-REGISTERED RULE (fixed before the arms were run).** The same 579
real multiway decisions M264 used, scored the same way - our probability
on the action the published six-handed AI actually took - one fresh
process per arm. ADOPT the half budget only if the paired delta is NOT
worse at 2 sigma, over all decisions AND over the three-live subset.
Latency is not a veto (halving can only be faster); it is reported.

**RESULT: REFUSED.** Halving is separably worse everywhere it moves:

    cell          n    shipped   half     delta      sigma   sec p50
    all           579  0.5814    0.5627   -0.0187    -2.78   5.45 -> 3.05
    live 3        541  0.5861    0.5662   -0.0200    -2.78   5.55 -> 3.06
    flop 3-live   328  0.5754    0.5552   -0.0202    -2.03   6.05 -> 3.39
    turn 3-live   147  0.6436    0.6152   -0.0284    -2.00   5.38 -> 2.89
    live 4         38  0.5135    0.5135   +0.0000     -      unchanged

**So R6 ships nothing, and the finding is the latency itself**: the
three-live flop and turn sit over the five-second bar on 22% and 15% of
real decisions of their street, and that cost buys accuracy that halving
gives back. The next audit grades it (F62).

    python -m bench.studies.three_live_budget rows.json
"""
from __future__ import annotations

import json
import math
import statistics
import sys

MIN_SIGMA = -2.0


def paired(rows: list) -> dict:
    """Shipped and half means, their paired delta and sigma. `rows` carry
    `shipped` and `half` - each arm's probability on the action the
    reference player took. Pure."""
    deltas = [r["half"] - r["shipped"] for r in rows]
    out = {"n": len(rows),
           "shipped": statistics.mean(r["shipped"] for r in rows) if rows else None,
           "half": statistics.mean(r["half"] for r in rows) if rows else None,
           "delta": statistics.mean(deltas) if rows else None, "sigma": None}
    if len(deltas) > 1 and statistics.stdev(deltas) > 0:
        se = statistics.stdev(deltas) / math.sqrt(len(deltas))
        out["sigma"] = out["delta"] / se
    return out


def summarise(rows: list) -> dict:
    """The cells the rule reads, plus each street at three live. Pure."""
    out = {"all": paired(rows), "live3": paired([r for r in rows if r["live"] == 3])}
    for street in ("flop", "turn", "river"):
        cell = [r for r in rows if r["live"] == 3 and r["street"] == street]
        if cell:
            out[f"{street}3"] = paired(cell)
    return out


def verdict(summary: dict):
    """(adopt?, the cells that refuse it) - the pre-registered rule."""
    refusing = [name for name in ("all", "live3")
                if summary[name]["sigma"] is not None and summary[name]["sigma"] <= MIN_SIGMA]
    return not refusing, refusing


def main(argv=None) -> int:                              # pragma: no cover
    """Read the committed rows (or a path given on the command line) and
    apply the rule."""
    import pathlib
    path = (argv if argv is not None else sys.argv[1:])
    source = pathlib.Path(path[0]) if path else (
        pathlib.Path(__file__).resolve().parents[2] / "tests" / "data"
        / "three_live_budget_m291.json")
    rows = json.loads(source.read_text())
    summary = summarise(rows)
    print(json.dumps(summary, indent=1))
    adopt, refusing = verdict(summary)
    print("ADOPT the half budget" if adopt else f"REFUSE: worse at {refusing}")
    return 0


if __name__ == "__main__":                                # pragma: no cover
    sys.exit(main())
