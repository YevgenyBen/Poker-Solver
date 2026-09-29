"""Does the 4x multiway budget order BET SIZES better? (M310)

M309 measured the 4x postflop budget as worth **+0.0751 at 3.59 sigma** at
four live, concentrated on the flop (+0.1004 at 4.33), and it did so
against an outside reference - which capped it at **36 hands**, because
the published six-handed agent reaches a four-way flop only 36 times in
10,000 hands and **never reaches a five-way one**. So three things M309
could not say anything about:

- five- and six-live pots, which `MULTIWAY_WIDE_POT_MIN_LIVE` also gates;
- any stack depth other than 100bb, the only one the reference plays;
- anything at all beyond 36 spots.

This asks the same question with an instrument that needs **no reference**,
so n is unlimited and none of those limits apply.

**THE CRITERION, and it is this project's own.** Facing a SMALLER bet must
fold LESS. M214 validated it against the heads-up exact solver at **6.90
sigma, 17 of 17**, then used it to take the multiway flop budget 200 ->
1,000 when no converged multiway reference existed (F46/M163), and M220
used it again to accept the sized re-raise. M214's own words on what kind
of evidence it is: "Stability evidence would still not justify this;
directional correctness against a known-truth control is different
evidence." It is a CORRECTNESS criterion, not a stability one.

**WHAT IT KNOWS AND WHAT IT DOES NOT.** It knows the SIGN of the ordering
and nothing about its magnitude. If the true fold frequencies are 0.05 and
0.60, an arm reading 0.05/0.60 is right and one reading 0.01/0.95 has a
bigger gap while being worse. M214 read the magnitude as better anyway
(+0.1466 -> +0.3138) and also counted spots right (11/16 -> 13/16). **This
study makes the count primary and the magnitude secondary**, because a
violation is a defect whatever its size:

- **PRIMARY: the violation rate.** A spot is violated when ANY adjacent
  pair of bet sizes folds the wrong way. Lower is unambiguously better.
- **SECONDARY: the gap**, fold(largest) - fold(smallest), reported with
  its sign trusted and its magnitude not.

**PRE-REGISTERED RULE (fixed before any arm was run).**

Population: spots walked off the real preflop tree (M252's
`preflop_walk`), closed with one check per live player (`closing_path` -
not the two that every session before M252 hardcoded), asked through
`/advise` (M174: measure multiway through `/advise`).

Arms, set per request through `solve_iterations` and verified from the
response's own echo, exactly as M309 did:

    S   1,000 iterations - what ships at 4+ live
    W   4,000 iterations - what ships at 3 live

    1. EVERY SPOT MUST OFFER THREE SIZES BELOW THE ALL-IN, read from the
       response's `modelled_bet_sizes` against `max_affordable_bb`, never
       computed here. The menu is `((0.33, 0.75, 2.5), 2.0)`, so a
       2.5x-pot bet needs SPR >= 2.5 or it COLLAPSES INTO THE ALL-IN and
       the third size silently vanishes - which is what a feasibility
       probe saw at SPR 0.48, where `modelled_bet_sizes` came back
       `[22.77, 51.75, 83.5]` with 83.5 equal to `max_affordable_bb`.
       M302's trap: the largest entry IS the all-in and naming it
       `raise:` is a 422. A spot short of three sizes is SKIPPED, and the
       count of skips is reported.
    2. SANITY. The shipped arm must order correctly on a majority of
       spots. M214 measured 13 of 16 right at this budget; if the shipped
       arm cannot manage a majority here the population is not comparable
       and nothing below is read.
    3. THE QUESTION. W's violation rate must be lower than S's, paired
       per spot, at >= 2.0 sigma.
    4. SPLIT-HALF. Any cell clearing rule 3 must hold in both halves at
       >= 1.0 sigma, split on a crc32 digest of the spot's IDENTITY only
       (M306).
    5. CELLS. Report per live count and per stack depth separately. Five
       and six live, and every depth away from 100bb, are what this study
       exists to reach; pooling them away would waste it.
    6. Latency reported, never a veto (M309's rule 6: the prize is a
       target for an offline computation, so its cost is the point).

The 2.0 bar is M291's own `MIN_SIGMA`, inherited rather than chosen here
(M305's rule).

**WHAT A POSITIVE RESULT WOULD AND WOULD NOT ESTABLISH.** It would say
more budget makes the advice more internally correct on one known-truth
axis, in cells no reference can reach. It would NOT say the advice is
closer to strong play there - only M309's instrument speaks to that, and
only at four live and 100bb. The two are complementary and must not be
pooled or quoted as one figure.

    python -m bench.studies.wide_pot_ordering [rows.jsonl]

An instrument: nothing under `poker_solver/` or `api/` imports it.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
import zlib

#: Rules 3. M291's own bar, fixed before this question.
MIN_SIGMA = 2.0
#: Rule 4.
MIN_HALF_SIGMA = 1.0
#: Rule 2 - the shipped arm must order correctly more often than not.
SANITY_MIN_CORRECT = 0.5
#: The two arms, as iteration budgets, identical to M309's.
SHIPPED_ITERATIONS = 1000
WIDE_ITERATIONS = 4000
#: Rule 1: three sized bets must sit strictly below the all-in.
SIZES_NEEDED = 3
#: Fixed so the draw is reproducible and a partial run is still a sample.
DRAW_SEED = 310
#: Six-handed, so five- and six-live pots are reachable at all; M309's
#: table size, which keeps the two studies comparable on that axis.
TABLE_SIZE = 6
#: The depths. 100bb is M309's, and the rest are what it could not reach -
#: real multiway pots sit at 100bb or deeper 88% of the time (M263) and
#: 15.1% sit at 200-260bb (M271), so a study that only ever asked at 100
#: would be answering for a minority of real play.
STACK_DEPTHS = (100.0, 50.0, 200.0)


def fold_mass(row: dict) -> float:
    """How much of a hand's row folds."""
    return sum(p for a, p in row.items() if a.startswith("fold"))


def nameable_sizes(sizes: list, max_affordable: float) -> list:
    """The bet sizes that can be NAMED as a raise, largest excluded.

    The largest `modelled_bet_sizes` entry is the all-in, and naming it
    `raise:` is a 422 (M302). Read from the response, never computed:
    at SPR 0.48 the 2.5x-pot bet collapses into the all-in and the third
    size vanishes without anything saying so.
    """
    return [s for s in (sizes or []) if s < max_affordable - 1e-9]


def violated(folds: list) -> bool:
    """True when ANY adjacent pair folds the wrong way.

    `folds` is ordered by increasing bet size. A bigger bet must not be
    folded to LESS often than a smaller one.
    """
    return any(b < a - 1e-12 for a, b in zip(folds, folds[1:]))


def gap(folds: list):
    """fold(largest) - fold(smallest). Sign trusted, magnitude not."""
    return (folds[-1] - folds[0]) if len(folds) >= 2 else None


def digest(row: dict) -> int:
    """A stable split key from the spot's IDENTITY only (M306)."""
    return zlib.crc32(("%s|%s|%s|%s" % (row["path"], row["board"],
                                        row["hero"], row["stack"]))
                      .encode("utf-8"))


def halves(rows: list) -> tuple:
    left = [r for r in rows if digest(r) % 2 == 0]
    right = [r for r in rows if digest(r) % 2 == 1]
    return left, right


def paired(rows: list, field: str) -> dict:
    """W minus S on `field`, paired per spot. Pure.

    sigma is None when UNDEFINED - under two rows, or no variation at all.
    A zero-variance cell scored 0.0 reads as "measured and null" when the
    truth is "not measured" (M306).
    """
    usable = [r for r in rows
              if r.get("S_" + field) is not None and r.get("W_" + field) is not None]
    deltas = [r["W_" + field] - r["S_" + field] for r in usable]
    out = {"n": len(usable), "sigma": None,
           "S": statistics.mean(r["S_" + field] for r in usable) if usable else None,
           "W": statistics.mean(r["W_" + field] for r in usable) if usable else None,
           "delta": statistics.mean(deltas) if deltas else None,
           "better": sum(1 for d in deltas if d < 0) if field == "violated"
                     else sum(1 for d in deltas if d > 0),
           "worse": sum(1 for d in deltas if d > 0) if field == "violated"
                    else sum(1 for d in deltas if d < 0)}
    if len(deltas) > 1 and statistics.stdev(deltas) > 0:
        out["sigma"] = out["delta"] / (statistics.stdev(deltas) / math.sqrt(len(deltas)))
    return out


def improvement(rows: list) -> dict:
    """Rule 3 on the PRIMARY metric, signed so POSITIVE means better.

    The raw paired delta on `violated` is negative when W violates less,
    so it is flipped here - a sign error in a headline is the one thing a
    reader cannot check.
    """
    out = paired(rows, "violated")
    for key in ("delta", "sigma"):
        if out.get(key) is not None:
            out[key] = -out[key]
    out["S_violation_rate"], out["W_violation_rate"] = out.pop("S"), out.pop("W")
    return out


def summarise(rows: list) -> dict:
    """Every cell the rule reads. Pure, so a test can pin it."""
    out = {"all": improvement(rows), "gap": paired(rows, "gap")}
    left, right = halves(rows)
    out["half_a"], out["half_b"] = improvement(left), improvement(right)
    for live in sorted({r["live"] for r in rows}):
        cell = [r for r in rows if r["live"] == live]
        out["live%d" % live] = improvement(cell)
    for depth in sorted({r["stack"] for r in rows}):
        cell = [r for r in rows if r["stack"] == depth]
        out["stack%g" % depth] = improvement(cell)
    for street in sorted({r["street"] for r in rows}):
        cell = [r for r in rows if r["street"] == street]
        out["street_" + street] = improvement(cell)
    out["counts"] = {
        "spots": len(rows),
        "S_correct": sum(1 for r in rows if not r["S_violated"]),
        "W_correct": sum(1 for r in rows if not r["W_violated"]),
    }
    return out


def _clears(cell: dict, bar: float) -> bool:
    """A cell clears only with a DEFINED sigma at or above the bar."""
    return cell.get("sigma") is not None and cell["sigma"] >= bar


def verdict(summary: dict) -> dict:
    """The pre-registered rule, applied. Pure."""
    counts = summary["counts"]
    spots = counts["spots"]
    shipped_ok = spots > 0 and (counts["S_correct"] / spots) > SANITY_MIN_CORRECT
    if not shipped_ok:
        return {"sanity": "FAILED", "result": "NOT READ",
                "note": "the shipped arm does not order correctly on a "
                        "majority of these spots, so the population is not "
                        "comparable to M214's and nothing below is read"}

    better = _clears(summary["all"], MIN_SIGMA)
    halves_ok = all(_clears(summary[k], MIN_HALF_SIGMA)
                    for k in ("half_a", "half_b"))
    if not better:
        state = "NO IMPROVEMENT"
    elif not halves_ok:
        state = "UNREPLICATED"
    else:
        state = "BETTER"
    return {"sanity": "passed", "result": state,
            "halves": "held" if halves_ok else "did not hold",
            "S_violation_rate": summary["all"]["S_violation_rate"],
            "W_violation_rate": summary["all"]["W_violation_rate"]}


def run(out_path, per_cell: int, minutes: float | None = None) -> int:  # pragma: no cover
    """Drive both arms over walked spots and append rows as they land."""
    import random
    import time
    from fastapi.testclient import TestClient
    from api.main import app
    from bench.server_warmup import warm_multiway
    from bench.spot_population import closing_path, preflop_walk

    client = TestClient(app)

    def post(body):
        r = client.post("/advise", json=body)
        return r.status_code, (r.json() if r.status_code == 200
                               else {"detail": r.text[:200]})

    print("warming %d-max at %s..." % (TABLE_SIZE, list(STACK_DEPTHS)), flush=True)
    warm_multiway(depths=STACK_DEPTHS, table_sizes=(TABLE_SIZE,))

    ranks, suits = "AKQJT98765432", "shdc"
    rng = random.Random(DRAW_SEED)
    started = time.perf_counter()
    done = {}
    handle = open(out_path, "a", encoding="utf-8")
    written = skipped = 0

    while True:
        if minutes is not None and (time.perf_counter() - started) / 60 > minutes:
            print("time budget reached", flush=True)
            break
        if done and all(v >= per_cell for v in done.values()) and len(done) >= 6:
            break
        stack = STACK_DEPTHS[written % len(STACK_DEPTHS)]
        walk = preflop_walk(rng, TABLE_SIZE, stack)
        if walk.closed_path is None or walk.live_players < 4:
            continue
        cell = (walk.live_players, stack)
        done.setdefault(cell, 0)
        if done[cell] >= per_cell:
            continue

        deck = [r + s for r in ranks for s in suits]
        rng.shuffle(deck)
        board, hero = "".join(deck[:3]), "".join(deck[3:5])
        base = {"players": TABLE_SIZE, "stack_bb": stack,
                "preflop_action_path": list(walk.closed_path),
                "board": board, "hero_cards": hero}

        status, js = post(dict(base, solve_iterations=SHIPPED_ITERATIONS))
        if status != 200:
            continue
        sizes = nameable_sizes(js.get("modelled_bet_sizes"),
                               js.get("max_affordable_bb") or 0.0)
        if len(sizes) < SIZES_NEEDED:
            # Rule 1: without three sizes below the all-in there is no
            # ordering to test. Counted, never silently dropped.
            skipped += 1
            continue
        sizes = sizes[:SIZES_NEEDED]

        row = {"path": "/".join(walk.closed_path), "board": board, "hero": hero,
               "stack": stack, "live": walk.live_players, "street": "flop",
               "pot": js["pot"], "sizes": sizes}
        ok = True
        for arm, iters in (("S", SHIPPED_ITERATIONS), ("W", WIDE_ITERATIONS)):
            folds, seconds = [], 0.0
            for size in sizes:
                body = dict(base, solve_iterations=iters,
                            flop_action_path=["raise:%.2f" % size])
                t0 = time.perf_counter()
                st, resp = post(body)
                seconds += time.perf_counter() - t0
                if st != 200:
                    ok = False
                    break
                echoed = resp.get("solve_iterations")
                if echoed != iters:
                    raise SystemExit(
                        "arm %s asked for %d and the response echoed %r - the "
                        "budget is not being honoured" % (arm, iters, echoed))
                hero_row = (resp.get("hero") or {}).get("strategy") or {}
                if not hero_row:
                    ok = False
                    break
                folds.append(fold_mass(hero_row))
            if not ok:
                break
            row[arm + "_folds"] = folds
            row[arm + "_violated"] = 1 if violated(folds) else 0
            row[arm + "_gap"] = gap(folds)
            row[arm + "_iterations"] = iters
            row[arm + "_seconds"] = round(seconds, 3)
        if not ok:
            continue
        handle.write(json.dumps(row) + "\n")
        handle.flush()
        done[cell] += 1
        written += 1
        if written % 10 == 0:
            mins = (time.perf_counter() - started) / 60
            filled = " ".join("%dL%g:%d" % (l, s, n)
                              for (l, s), n in sorted(done.items()))
            print("  %4d spots [%s] skipped=%d %5.1f min  S=%.1fs W=%.1fs"
                  % (written, filled, skipped, mins,
                     row["S_seconds"], row["W_seconds"]), flush=True)
    handle.close()
    print("wrote %d spots to %s (%d skipped for want of three sizes)"
          % (written, out_path, skipped), flush=True)
    return 0


def main(argv=None) -> int:                              # pragma: no cover
    """Read the committed rows and apply the rule, or `--run` the arms."""
    import pathlib
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] == "--run":
        out = args[1]
        per_cell = int(args[2]) if len(args) > 2 else 40
        minutes = float(args[3]) if len(args) > 3 else None
        return run(out, per_cell, minutes)

    source = pathlib.Path(args[0]) if args else (
        pathlib.Path(__file__).resolve().parents[2] / "tests" / "data"
        / "wide_pot_ordering_m310.json")
    text = source.read_text()
    rows = ([json.loads(line) for line in text.splitlines() if line.strip()]
            if source.suffix == ".jsonl" else json.loads(text))
    summary = summarise(rows)
    print(json.dumps(summary, indent=1, default=float))
    print(json.dumps(verdict(summary), indent=1, default=float))
    return 0


if __name__ == "__main__":                                # pragma: no cover
    sys.exit(main())
