"""Is multiway postflop advice still irreproducible, and does the split-row
gate still separate, AFTER M269's grouped action matching?
(the 2026-09-20 audit's R3, M306)

`MULTIWAY_REPRODUCIBILITY_REASON` and `MULTIWAY_STABLE_REASON` are the
last pair of stale disclosures with a measurable claim, and between them
they are the **most-met disclosed defect in this product** - M252 put the
exposure at one decision in five, several times anything else here.

Both were measured by M254 and re-measured by A4c/M267 at M264's budget.
**M269 then shipped `MULTIWAY_POSTFLOP_ACTION_GROUPING = "mean"`**, which
changes how the sampled solver matches regret across action KINDS - and
M264 had already located the defect in exactly that place ("the problem
is in how the solver treats three similar bet sizes"). So the change most
likely to move seed-to-seed agreement landed after the figures were
taken, and nobody re-ran them. The original study was a scratch file and
is gone (`m254_predict_instability.py`), which is the other half of why
this is being rebuilt rather than re-run.

**Method.** Six-handed, 100bb, the preflop solve warm. Preflop lines are
WALKED off the real tree (`bench.spot_population.preflop_walk`, M252) so
three-bet pots can appear, and kept when they close with three or more
live. Each spot is asked through `/advise` (M174: multiway is measured
through the endpoint, never an isolated solve) at the shipped solver seed
and then at `len(SEEDS) - 1` fresh ones, with `clear_postflop_caches()`
between arms - **postflop only**, because clearing everything re-pays a
cold preflop solve no player meets and clearing nothing serves the
previous arm's answer (M245/M252).

Both node types are drawn. M177's rule is that facing-a-bet nodes must be
CONSTRUCTED explicitly or a study silently measures opening decisions
only, and M301 broke that rule inside a study re-measuring M177.

**PRE-REGISTERED READING RULE (fixed before any spot was solved). It is
M254's own rule, so this is a re-measurement of the same claim and not a
new one under a friendlier bar:**

1. The predictor is read from the SHIPPED answer alone - hero's top
   action mass against `cfg.MULTIWAY_STABLE_MAX_TOP_ACTION` (0.90). An
   average over seeds is information no request has.
2. A spot's `changed` is the share of fresh seeds whose top action
   differs from the shipped one; its `tvd` is the mean total variation
   distance from the shipped row.
3. The GATE SURVIVES iff the split cell's `changed` exceeds the decisive
   cell's by at least `MIN_SEPARATION` (0.10) at `MIN_SIGMA` (2.0) or
   better, **and in both split halves**. M254 demanded the halves and
   M166 is why. The halves come from a stable digest of each spot, NOT
   from alternating draw order: this generator cycles street and node
   type by index, so alternation would put a whole street in one half
   and test the generator instead of the gate - caught by the rule's own
   test before the run.
4. Both branches quote PER STREET. M254's first draft led with the
   firing cell's pooled average, which is river-weighted, and M245's own
   guard failed the build over it.
5. The quiet branch quotes COUNTS where a cell is small: at 22 of 22 a
   percentage reads "0%" and claims more than 22 spots support.
6. If the gate does NOT survive, the graded pair collapses to one
   blanket note and that is the result - a null is a result.
7. Confidence stays LOW on both branches whatever this measures.
   Multiway has no converged reference of any kind (F46/M163), so "high"
   is not available to claim, and M245 exists because it was.

    python -m bench.studies.multiway_instability out.json [spots]
"""
from __future__ import annotations

import json
import math
import random
import sys
import zlib

SIZE = 6
STACK = 100.0
SEEDS = (0, 101, 102, 103)     # 0 is what `/advise` runs; the rest are fresh
STREETS = ("flop", "turn", "river")
MIN_SEPARATION = 0.10
MIN_SIGMA = 2.0
RANKS = "23456789TJQKA"
SUITS = "cdhs"


# -- the axes ------------------------------------------------------------

def top_action(row: dict):
    """The action a player would follow, and its mass."""
    if not row:
        return None, 0.0
    action = max(row, key=lambda k: (row[k], k))
    return action, row[action]


def tvd(left: dict, right: dict) -> float:
    keys = set(left) | set(right)
    return 0.5 * sum(abs(left.get(k, 0.0) - right.get(k, 0.0)) for k in keys)


def spot_row(shipped: dict, others: list) -> dict:
    """Rule 1 and 2, for one spot. `shipped` and `others` are hero rows."""
    action, mass = top_action(shipped)
    changes = [1.0 if top_action(row)[0] != action else 0.0 for row in others]
    return {"top_action": action, "top_mass": mass,
            "changed": sum(changes) / len(changes) if changes else None,
            "tvd": sum(tvd(shipped, row) for row in others) / len(others) if others else None,
            "n_others": len(others)}


def is_split(row: dict, threshold) -> bool:
    """Rule 1's gate, read off the shipped row only."""
    return row["top_mass"] < threshold


# -- rule 3: does the gate separate? -------------------------------------

def _cell(rows: list) -> dict:
    kept = [r for r in rows if r.get("changed") is not None]
    if not kept:
        return {"n": 0}
    changed = [r["changed"] for r in kept]
    mean = sum(changed) / len(changed)
    var = sum((c - mean) ** 2 for c in changed) / (len(changed) - 1) if len(changed) > 1 else 0.0
    return {"n": len(kept), "changed": mean,
            "sem": math.sqrt(var / len(changed)) if len(changed) else 0.0,
            "tvd": sum(r["tvd"] for r in kept) / len(kept),
            "held": sum(1 for r in kept if r["changed"] == 0.0)}


def separation(rows: list, threshold) -> dict:
    """The split cell against the decisive one, with its own sigma."""
    split = _cell([r for r in rows if is_split(r, threshold)])
    decisive = _cell([r for r in rows if not is_split(r, threshold)])
    if not split.get("n") or not decisive.get("n"):
        return {"measured": False, "split": split, "decisive": decisive}
    delta = split["changed"] - decisive["changed"]
    sem = math.sqrt(split["sem"] ** 2 + decisive["sem"] ** 2)
    # A cell with no within-cell variation has sem 0 and an UNDEFINED
    # sigma. At these sample sizes that is a small-sample artifact, not
    # infinite precision, so it is reported as undefined rather than as
    # `inf` (which would pass rule 3 on a degenerate cell) or as 0.0
    # (which would silently read as "did not separate").
    return {"measured": True, "split": split, "decisive": decisive, "delta": delta,
            "sem": sem, "sigma": (delta / sem) if sem else None}


def halves(rows: list) -> tuple:
    """Rule 3's two halves, split on a STABLE DIGEST of each spot rather
    than on its position in the list.

    M254 alternated in draw order and that is unsafe here: this study's
    generator cycles street by `drawn % 3` and node type by `drawn % 2`,
    so taking every other row can put one street - or every facing-a-bet
    node - entirely in one half, and the halves then test the generator
    instead of the gate. Caught by the rule's own test before the run.
    `hash()` is salted per process, so the digest is explicit.
    """
    def side(row):
        # The spot's IDENTITY only. Keying on anything the spot measured
        # - its top mass, its change rate - would let the split depend on
        # the answer, which is the halves grading themselves.
        key = "|".join(str(row.get(k)) for k in
                       ("board", "hero", "street", "facing", "preflop"))
        return zlib.crc32(key.encode()) & 1
    return ([r for r in rows if side(r) == 0], [r for r in rows if side(r) == 1])


def summarise(rows: list, threshold=None) -> dict:
    """Rule 3 pooled and in both halves, rule 4 per street. Pure."""
    if threshold is None:
        from api import config as cfg
        threshold = cfg.MULTIWAY_STABLE_MAX_TOP_ACTION
    left, right = halves(rows)
    out = {"n": len(rows), "threshold": threshold,
           "whole": separation(rows, threshold),
           "halves": [separation(left, threshold), separation(right, threshold)],
           "streets": {}}
    for street in STREETS:
        mine = [r for r in rows if r.get("street") == street]
        out["streets"][street] = {
            "split": _cell([r for r in mine if is_split(r, threshold)]),
            "decisive": _cell([r for r in mine if not is_split(r, threshold)])}
    out["nodes"] = {name: separation([r for r in rows if r.get("facing") is facing], threshold)
                    for name, facing in (("opening", False), ("facing", True))}
    return out


def verdict(summary: dict) -> dict:
    """Rules 3 and 6."""
    whole = summary["whole"]
    if not summary["n"] or not whole.get("measured"):
        return {"measured": False}
    def clears(cell):
        return (cell.get("measured") and cell["sigma"] is not None
                and cell["delta"] >= MIN_SEPARATION and cell["sigma"] >= MIN_SIGMA)

    survives = bool(clears(whole) and all(clears(h) for h in summary["halves"]))
    return {"measured": True, "gate_survives": survives,
            "delta": whole["delta"], "sigma": whole["sigma"],
            "halves_agree": all(h.get("measured") and h["delta"] >= MIN_SEPARATION
                                for h in summary["halves"]),
            # Rule 7 is not re-decidable by this study and is stated, not measured.
            "confidence_stays_low": True}


# -- the population ------------------------------------------------------

def body_for(line: list, street: str, live: int, facing: bool, rng: random.Random) -> dict:
    """One `/advise` body. `line` is a walked preflop path that closed."""
    deck = [r + s for r in RANKS for s in SUITS]
    rng.shuffle(deck)
    body = {"stack_bb": STACK, "players": SIZE, "preflop_action_path": list(line),
            "hero_cards": deck[5] + deck[6], "board": "".join(deck[0:3])}
    closing = ["call_or_check"] * live
    if street in ("turn", "river"):
        body.update(flop_action_path=list(closing), turn_card=deck[3])
    if street == "river":
        body.update(turn_action_path=list(closing), river_card=deck[4])
    if facing:
        # M177: a facing-a-bet node has to be CONSTRUCTED. One player bets
        # and hero answers it, so hero is the second actor on the street.
        body[f"{street}_action_path"] = ["raise"]
    return body


def main(argv=None) -> int:                              # pragma: no cover
    from fastapi.testclient import TestClient
    from api import config as cfg, solving
    from api.main import app
    from bench.server_warmup import clear_postflop_caches, warm_multiway
    from bench.spot_population import preflop_walk
    from poker_solver.solver import solve_flop_multiway

    args = argv if argv is not None else sys.argv[1:]
    out_path = args[0]
    target = int(args[1]) if len(args) > 1 else 120

    client = TestClient(app)
    warm_multiway(depths=(STACK,), table_sizes=(SIZE,))
    rng = random.Random(306)

    def post(body):
        r = client.post("/advise", json=body)
        return r.status_code, (r.json() if r.status_code == 200 else {"detail": r.text[:160]})

    def hero_row(js):
        hero = js.get("hero") or {}
        return hero.get("strategy") or {}

    # `/advise` never passes a seed, so the only way to move it is to bind
    # one onto the engine call the production path makes. Everything else
    # - ranges, force-inclusion, on-demand training - stays on that path.
    def bind_seed(seed):
        def solve(*a, **kw):
            kw["seed"] = seed
            return solve_flop_multiway(*a, **kw)
        solving.solve_flop_multiway = solve

    spots, drawn = [], 0
    while len(spots) < target and drawn < target * 40:
        drawn += 1
        walk = preflop_walk(rng, SIZE, STACK)
        if walk.closed_path is None or walk.live_players < 3:
            continue
        street = STREETS[drawn % len(STREETS)]
        facing = bool(drawn % 2)
        body = body_for(list(walk.closed_path), street, walk.live_players, facing, rng)
        rows, positions = [], None
        for seed in SEEDS:
            bind_seed(seed)
            clear_postflop_caches()
            status, js = post(body)
            if status != 200:
                rows = []
                break
            if positions is None:
                positions = js.get("positions") or []
            row = hero_row(js)
            if not row:
                rows = []
                break
            rows.append(row)
        if len(rows) != len(SEEDS) or len(positions) < 3:
            continue
        entry = spot_row(rows[0], rows[1:])
        entry.update(street=street, facing=facing, live=len(positions),
                     board=body["board"], hero=body["hero_cards"],
                     preflop=list(walk.closed_path))
        spots.append(entry)
        if len(spots) % 5 == 0:
            json.dump({"spots": spots, "seeds": list(SEEDS)}, open(out_path, "w"))
            print(f"{len(spots)}/{target} drawn={drawn} last={street}"
                  f"{'/facing' if facing else ''} top={entry['top_mass']:.3f}"
                  f" changed={entry['changed']:.2f}", flush=True)
    solving.solve_flop_multiway = solve_flop_multiway
    json.dump({"spots": spots, "seeds": list(SEEDS)}, open(out_path, "w"))

    summary = summarise(spots, cfg.MULTIWAY_STABLE_MAX_TOP_ACTION)
    print(json.dumps({k: v for k, v in summary.items() if k != "spots"},
                     indent=1, default=str))
    print(json.dumps(verdict(summary), indent=1))
    return 0


if __name__ == "__main__":                               # pragma: no cover
    raise SystemExit(main())
