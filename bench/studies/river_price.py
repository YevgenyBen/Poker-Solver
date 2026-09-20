"""What does the river's disagreement COST, in big blinds? (audit R6, M296)

The 2026-09-20 audit graded the river against the independent solver in
percent of pot: 0.325% opening (A) and 0.985% facing a bet (B). This
project's own thesis says that is the wrong unit - "the measure is
expected value in big blinds per hand, not distance from a reference
strategy" - so the same rows are priced in chips here. No new solves:
the 41 references from that arm already carry a regret in bb per row.

**PRE-REGISTERED READING RULE (fixed before the figures were computed):**

1. Keep the audit's own rows: the reference reaches the row at least 5%
   of the time and our row sits on its support (`off_support <= 0.5`,
   M258 - at a pure node regret prices a sliver and means nothing).
2. Report BOTH the raw regret and the figure NET of the reference's own
   slack, because a solved reference is not exact either (M259's rule).
3. Report the MEDIAN beside the mean. M183 measured this project's cost
   as tail-shaped - the median decision costs almost nothing - and a
   mean alone would read as a typical case.
4. Weight by how often a player actually meets the cell, measured over
   the hand store's real postflop decisions (M188: weight before quoting
   a share).
5. The result is a per-DECISION claim in the cell, and a contribution to
   bb per 100 postflop decisions overall. It is NOT comparable with
   M183's 4.7 bb/100: that is distance from a fuller solve of our own
   model, this is distance from an independent one, on one street.

    python -m bench.studies.river_price rows.json
"""
from __future__ import annotations

import json
import statistics
import sys

MIN_REACH = 0.05
MAX_OFF_SUPPORT = 0.5
#: Share of real postflop decisions, measured over the hand store's clean
#: hands (113,614 hands, 291,347 postflop decisions) before this was run.
EXPOSURE = {"opening": 0.1083, "facing": 0.0496}


def keep(rows: list) -> list:
    """The audit's own inclusion rule, applied to raw rows. Pure."""
    return [r for r in rows
            if r.get("reach_fraction", 0.0) >= MIN_REACH
            and r.get("off_support", 0.0) <= MAX_OFF_SUPPORT]


def cell(rows: list) -> dict:
    """Raw and net regret for one cell, per decision and per 100. Pure."""
    if not rows:
        return {"n": 0}
    regret = [r["regret_bb"] for r in rows]
    net = [r["regret_bb"] - r["slack_bb"] for r in rows]
    return {
        "n": len(rows),
        "bb": statistics.mean(regret),
        "bb_median": statistics.median(regret),
        "bb_net": statistics.mean(net),
        "per_100": 100 * statistics.mean(regret),
        "per_100_net": 100 * statistics.mean(net),
        "worst_bb": max(regret),
    }


def summarise(rows: list, exposure: dict = None) -> dict:
    """Per cell, and the exposure-weighted contribution. Pure."""
    exposure = EXPOSURE if exposure is None else exposure
    kept = keep(rows)
    out = {kind: cell([r for r in kept if r["kind"] == kind]) for kind in ("opening", "facing")}
    weighted = sum(out[kind]["bb"] * exposure[kind] for kind in out if out[kind].get("n"))
    weighted_net = sum(out[kind]["bb_net"] * exposure[kind] for kind in out if out[kind].get("n"))
    out["weighted"] = {
        "share_of_postflop_decisions": sum(exposure[k] for k in out if out[k].get("n")),
        "per_100_postflop_decisions": 100 * weighted,
        "per_100_postflop_decisions_net": 100 * weighted_net,
    }
    return out


def main(argv=None) -> int:                              # pragma: no cover
    import pathlib
    args = argv if argv is not None else sys.argv[1:]
    source = pathlib.Path(args[0]) if args else (
        pathlib.Path(__file__).resolve().parents[2] / "tests" / "data"
        / "river_price_m296.json")
    rows = json.loads(source.read_text())
    print(json.dumps(summarise(rows), indent=1))
    return 0


if __name__ == "__main__":                                # pragma: no cover
    sys.exit(main())
