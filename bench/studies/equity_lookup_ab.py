"""The vectorised equity lookup, end to end: same advice, and how much faster? (M311)

M309 and M310 left the three-live multiway pot as the one place a fast
stand-in could both pay AND be checked. Before building any stand-in, the
anatomy of a real three-live flop request was measured, by exclusive wall
time per stage over 10 cold requests at the shipped 4,000 iterations:

    stage                              share    median
    CFR iterations (excluding equity)  60.9%    3.48s
    equity lookups (inside the loop)   35.7%    2.06s
    runout ranking                      2.7%    0.16s
    everything else                     0.4%    0.02s

and the equity lookups split into a HIT path of 0.03s (~2us over ~14,000
calls) and a MISS path of 2.21s (~2,200 misses at ~1.0ms, each walking
~140 candidate hands in a Python loop). So the lever was not a stand-in at
all: it was overhead inside the existing solve, removable with no change
to any answer.

**How that was established, because the first instrument lied.** cProfile
reported ZERO solve time for a 10-second request: `/advise` runs its work
through `run_in_threadpool`, and cProfile profiles only the thread it was
enabled in. It would also have overstated CFR's share, since its per-call
overhead inflates deeply recursive Python. The anatomy above uses wall
timers wrapped where each function is LOOKED UP, with a thread-local stack
for EXCLUSIVE time - the equity lookups run inside the CFR loop and would
otherwise count twice. Every spot's stages sum to its wall time.

**RULE (fixed before the arms ran).** The change ships only if `/advise`
is BIT-IDENTICAL on every request - every row of every strategy table -
because a one-ULP equity difference can move an MCCFR solve at a near-tie
(M74). Speed is then reported, not gated; an interleaved A/B in one process
with the arm order alternating per spot, which is M70's rule for a speed
claim this machine can support (M240).

    python -m bench.studies.equity_lookup_ab [rows.json]
    python -m bench.studies.equity_lookup_ab --run rows.json

An instrument: nothing under `poker_solver/` or `api/` imports it.
"""
from __future__ import annotations

import json
import statistics
import sys

#: The bar M291 measured the three-live cells against.
LATENCY_BAR_SECONDS = 5.0


def summarise(rows: list) -> dict:
    """Identity over every row, and each street's speed. Pure."""
    out = {"requests": len(rows),
           "identical": sum(1 for r in rows if r["identical"]),
           "rows_compared": sum(r["rows"] for r in rows)}
    for street in sorted({r["street"] for r in rows}):
        cell = [r for r in rows if r["street"] == street]
        out[street] = {
            "n": len(cell),
            "reference_median": statistics.median(r["reference"] for r in cell),
            "vectorised_median": statistics.median(r["vectorised"] for r in cell),
            "speedup_median": statistics.median(r["reference"] / r["vectorised"]
                                                for r in cell),
            "speedup_min": min(r["reference"] / r["vectorised"] for r in cell),
            "over_bar_reference": sum(r["reference"] > LATENCY_BAR_SECONDS
                                      for r in cell),
            "over_bar_vectorised": sum(r["vectorised"] > LATENCY_BAR_SECONDS
                                       for r in cell),
        }
    return out


def verdict(summary: dict) -> str:
    """Ship only on EVERY request bit-identical. Pure."""
    if summary["requests"] == 0:
        return "NO DATA"
    if summary["identical"] != summary["requests"]:
        return "REFUSED: %d of %d requests changed" % (
            summary["requests"] - summary["identical"], summary["requests"])
    return "SHIP: bit-identical on all %d requests" % summary["requests"]


def run(out_path) -> int:                                 # pragma: no cover
    """The interleaved A/B over real three-live flop and turn decisions."""
    import random
    import time
    from fastapi.testclient import TestClient
    from api.main import app
    from bench import hand_db
    from bench.real_replay import deal, request_for
    from bench.server_warmup import clear_postflop_caches, warm_multiway
    from bench.studies.wide_pot_budget import decisions_in
    import poker_solver.multiway_board_equity as mbe

    client = TestClient(app)

    def post(body):
        r = client.post("/advise", json=body)
        return r.status_code, (r.json() if r.status_code == 200 else {})

    def ask(body, vectorised):
        mbe.VECTORISED_EQUITY_LOOKUP = vectorised
        clear_postflop_caches()
        t0 = time.perf_counter()
        status, js = post(body)
        return status, js, time.perf_counter() - t0

    warm_multiway(depths=(100.0,), table_sizes=(6,), verbose=False)
    import pathlib
    source = (pathlib.Path(__file__).resolve().parents[2] / "tests" / "data"
              / "wide_pot_budget_m309.json")
    picks = [r for r in json.loads(source.read_text())
             if r["source"] == "pluribus" and r["live"] == 3
             and r["street"] in ("flop", "turn")]
    random.Random(3110).shuffle(picks)
    db = hand_db.connect()
    want, got, rows = {"flop": 16, "turn": 10}, {"flop": 0, "turn": 0}, []
    try:
        for n, row in enumerate(picks):
            if got[row["street"]] >= want[row["street"]]:
                continue
            hand = next(hand_db.query(db, "id = ?", (row["hand"],)))
            _, acts, real = decisions_in(hand)
            cards = deal(hand.board, hand.n_players, random.Random(row["i"]))
            cards.update(real)
            body, _, _ = request_for(hand, acts, row["i"],
                                     round(hand.row["eff_stack_bb"], 2), cards, post)
            if body is None:
                continue
            body["hero_cards"] = cards[acts[row["i"]].player]
            order = (True, False) if n % 2 == 0 else (False, True)
            res = {v: ask(body, v) for v in order}
            (sf, jf, tf), (sr, jr, tr) = res[True], res[False]
            if sf != 200 or sr != 200 or len(jf.get("positions") or []) != 3:
                continue
            identical = (jf.get("strategy") == jr.get("strategy")
                         and (jf.get("hero") or {}).get("strategy")
                         == (jr.get("hero") or {}).get("strategy"))
            got[row["street"]] += 1
            rows.append({"hand": row["hand"], "i": row["i"], "street": row["street"],
                         "reference": round(tr, 3), "vectorised": round(tf, 3),
                         "identical": identical,
                         "rows": len(jf.get("strategy") or {}),
                         "iterations": jf.get("solve_iterations")})
            if all(got[k] >= want[k] for k in want):
                break
    finally:
        mbe.VECTORISED_EQUITY_LOOKUP = True
    pathlib.Path(out_path).write_text(json.dumps(rows, indent=0))
    return 0


def main(argv=None) -> int:                               # pragma: no cover
    import pathlib
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] == "--run":
        return run(args[1])
    source = pathlib.Path(args[0]) if args else (
        pathlib.Path(__file__).resolve().parents[2] / "tests" / "data"
        / "equity_lookup_ab_m311.json")
    summary = summarise(json.loads(source.read_text()))
    print(json.dumps(summary, indent=1))
    print(verdict(summary))
    return 0


if __name__ == "__main__":                                # pragma: no cover
    sys.exit(main())
