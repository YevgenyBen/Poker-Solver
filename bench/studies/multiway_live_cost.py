"""What does a multiway postflop decision cost as more players stay in?
(audit R6 / F53, M291)

M253 found a six-live flop cost ~11.4 reference units (~7.4s on this
machine's typical state) because range width is capped PER SEAT, so a
six-way pot pays for six seats, and recommended a cap on TOTAL work.
M264 has since raised the multiway postflop budget 4x at three live and
kept 1,000 iterations from four live (`MULTIWAY_WIDE_POT_MIN_LIVE`), so
the numbers are stale. This measures before anything is built.

**Method.** Six-handed, 100bb, the preflop solve warm. For each street
(flop, turn, river) and live count (3-6): `SPOTS` cold decisions (postflop
caches cleared before each), preflop line = folds, one open, everyone
else calls; the street's opening decision; random board and hero cards.
Every request is timed with `bench.reference_units.DriftClock`, and the
response's `positions` must show the intended live count or the spot is
dropped.

**Exposure** (the hand store's clean hands, share of that street's
decisions at each live count; measured before the run):
flop 3: 22.19%, 4: 6.43%, 5: 1.65%, 6: 0.30%; turn 3: 14.54%, 4: 3.29%,
5: 0.59%, 6: 0.12%; river 3: 9.37%, 4: 1.76%, 5: 0.25%, 6: 0.07%.

**PRE-REGISTERED READING RULE (fixed before the run):**

1. The bar is the product's 5 seconds expressed in units at this run's
   MEDIAN reference second: `BAR_UNITS = 5 / median(reference_seconds)`.
2. A cell is OVER when its p90 in units exceeds `BAR_UNITS`.
3. A total-work cap is NEEDED only if an OVER cell carries at least
   `MIN_EXPOSURE` (0.5%) of its street's real decisions. Otherwise R6 is a
   measured NULL: the tail is real, rare, and recorded.
4. Needing a cap does not ship one: its accuracy cost must be measured
   against seed noise (M245's yardstick) in its own milestone.

    python -m bench.studies.multiway_live_cost out.json
"""
from __future__ import annotations

import json
import random
import statistics
import sys

SPOTS = 10
LIVES = (3, 4, 5, 6)
STREETS = ("flop", "turn", "river")
SIZE = 6
STACK = 100.0
MIN_EXPOSURE = 0.005
EXPOSURE = {
    ("flop", 3): 0.2219, ("flop", 4): 0.0643, ("flop", 5): 0.0165, ("flop", 6): 0.0030,
    ("turn", 3): 0.1454, ("turn", 4): 0.0329, ("turn", 5): 0.0059, ("turn", 6): 0.0012,
    ("river", 3): 0.0937, ("river", 4): 0.0176, ("river", 5): 0.0025, ("river", 6): 0.0007,
}
RANKS = "23456789TJQKA"
SUITS = "cdhs"


def preflop_line(live: int, players: int = SIZE) -> list:
    """Folds to an open, then every remaining seat calls: `live` players
    see the flop."""
    folds = players - live
    return ["fold"] * folds + ["raise"] + ["call_or_check"] * (live - 1)


def body_for(street: str, live: int, rng: random.Random) -> dict:
    deck = [r + s for r in RANKS for s in SUITS]
    rng.shuffle(deck)
    body = {"stack_bb": STACK, "players": SIZE, "preflop_action_path": preflop_line(live),
            "hero_cards": deck[5] + deck[6], "board": "".join(deck[0:3])}
    checks = ["call_or_check"] * live
    if street in ("turn", "river"):
        body.update(flop_action_path=list(checks), turn_card=deck[3])
    if street == "river":
        body.update(turn_action_path=list(checks), river_card=deck[4])
    return body


def _p(values, q):
    values = sorted(values)
    return values[int(q * (len(values) - 1))]


def summarise(rows: list) -> dict:
    """Per (street, live): n, p50/p90/max units; plus the bar. Pure."""
    refs = [r["reference_seconds"] for r in rows]
    bar = 5.0 / statistics.median(refs) if refs else None
    cells = {}
    for street in STREETS:
        for live in LIVES:
            units = [r["units"] for r in rows if r["street"] == street and r["live"] == live]
            if not units:
                continue
            cells[f"{street}:{live}"] = {
                "n": len(units), "p50": _p(units, 0.5), "p90": _p(units, 0.9),
                "max": max(units), "over": _p(units, 0.9) > bar,
                "exposure": EXPOSURE[(street, live)]}
    return {"bar_units": bar, "cells": cells}


def verdict(summary: dict):
    """(needed?, the over-bar cells that decide it)."""
    deciding = [name for name, c in summary["cells"].items()
                if c["over"] and c["exposure"] >= MIN_EXPOSURE]
    return bool(deciding), deciding


def main(argv=None) -> int:                              # pragma: no cover
    from fastapi.testclient import TestClient
    from api.main import app
    from bench.reference_units import DriftClock
    from bench.server_warmup import clear_postflop_caches, warm_multiway

    out_path = (argv if argv is not None else sys.argv[1:])[0]
    client = TestClient(app)
    warm_multiway(depths=(STACK,), table_sizes=(SIZE,))
    clock = DriftClock()
    rng = random.Random(291)
    rows = []

    def post(body):
        r = client.post("/advise", json=body)
        return r.status_code, (r.json() if r.status_code == 200 else {"detail": r.text[:200]})

    for street in STREETS:
        for live in LIVES:
            done = 0
            while done < SPOTS:
                body = body_for(street, live, rng)
                clear_postflop_caches()
                (status, js), m = clock.measure(post, body)
                if status != 200 or len(js.get("positions") or []) != live:
                    print("dropped", street, live, status, str(js.get("detail"))[:80], flush=True)
                    continue
                rows.append({"street": street, "live": live, "seconds": m.seconds,
                             "units": m.units, "reference_seconds": m.reference_seconds})
                done += 1
            print(street, live, "done", flush=True)
            json.dump({"rows": rows, "drift": clock.report()}, open(out_path, "w"))
    summary = summarise(rows)
    print(json.dumps(summary, indent=1))
    print(json.dumps(clock.report()))
    needed, deciding = verdict(summary)
    print("CAP NEEDED" if needed else "NULL: no over-bar cell carries 0.5% of real decisions",
          deciding)
    return 0


if __name__ == "__main__":                                # pragma: no cover
    sys.exit(main())
