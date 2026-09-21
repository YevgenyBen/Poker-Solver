"""Where does the cost of this advice actually sit, today? (audit R3, M299)

`FACING_A_BET_COST_NOTE` and `COSTLY_BAND_NOTE` are the product's two
sharpest claims about its own weakness. Between them they tell a player
that a decision facing a bet costs **at least 25 times** one where they
act first, carries **86% of everything the advice costs**, and that
holding a hand between the 55th and 90th percentile there is where 74%
of the cost lives. They fire on **8.7%** and **3.4%** of real decisions.

**Every one of those numbers is M188/M189's, and both predate the bet
menu.** M203-M213 gave this engine a 0.33/0.75/2.5 menu on all three
streets; before that the smallest bet it could model was 2.5x the pot,
so a player facing a half-pot bet was answered at a 2.5x-pot node.
M209/M213 priced that at up to **1.85 bb on one decision**, and it was a
facing-a-bet defect by construction - the old engine's fold frequency
was IDENTICAL against a third-pot bet and an overbet, because it only
ever read one node. So the headline split these two notes publish may be
measuring a capability gap that no longer exists.

**Arms.** Real heads-up postflop decisions, replayed through `/advise`
at the shipped configuration, each priced in big blinds by
`bench/price.py` against an uncapped, more-converged solve of the same
request. Distance from a fuller solve of our OWN model, which is what
M183/M188 measured and what makes every figure here a LOWER BOUND.

**PRE-REGISTERED READING RULE (fixed before any row was priced):**

1. A row is kept only if the reference offers the same actions the
   shipped answer does (`remapped_mass <= MAX_REMAPPED`, M202) and the
   reference itself converged (`exploitability <= MAX_REFERENCE_PCT` of
   pot, M138's rule that an unconverged reference is not one). Refusals
   are counted and reported, never dropped silently.
2. CELLS: mean |loss|, signed mean, median, and the share over 1 bb and
   over 5 bb, per street x node type. A null is a result.
3. THE RATIO ("at least 25 times"): mean |loss| facing a bet over mean
   |loss| acting first, each pooled with cells weighted by real
   occurrence. The copy keeps "at least 25 times" only if the ratio is
   >= 25 AND facing separates from opening at >= 2 sigma. If it
   separates below 25, the copy quotes the measured ratio. If it does
   not separate, the comparison leaves the copy.
4. THE SHARE ("about 86%"): facing cells' exposure-weighted cost over
   every cell's, quoted as measured (M188: weight before quoting).
5. THE TAIL ("18% over a big blind, 5% over five, the median costs
   almost nothing"): recomputed over facing rows, quoted as measured.
6. THE BAND: `COSTLY_BAND_NOTE` survives only if, among facing rows,
   the 0.55-0.90 band's mean |loss| exceeds the out-of-band facing mean
   at >= 2 sigma AND both halves of a random split agree in direction.
   Otherwise the note is WITHDRAWN - M166's rule, which that note's own
   comment invokes. The boundaries are NOT re-optimised on this data;
   re-cutting a band on the sample that found it is the error itself.
7. SCOPE: this instrument solves two positions, so it measures pots
   that were HEADS-UP FROM THE FLOP - **67.1% of real postflop
   decisions**. Both notes also fire multiway, and F46/M163 says there
   is no converged multiway reference to price against, so that part of
   the gate stays unmeasured and the copy says so.
8. Every figure is a LOWER BOUND: both arms share the model, so an error
   they share cancels (M202 put the shared part at ~0.1 bb on the flop).

**HOW THE SAMPLE IS DRAWN (a throughput choice, not a rule).** Each of
the six cells is filled by its own pass over the hand store, restricted
to hands that reach that street (`last_street`), because building one
request costs an `/advise` call at every postflop bet on the line - so
hunting a river decision among hands that ended on the flop costs real
solver time and yields nothing. Within a cell the draw is still every
real decision of that kind, in the store's own sampled order, and the
cells are re-weighted by real occurrence afterwards. It changes which
hands are looked at first, never which rows qualify.

**AMENDMENT (before the run that produced the published rows).** Rule 7
first said "heads-up decisions, 76.2%", counting any decision with two
players left - which includes a pot that started three-handed and folded
to two on the flop. This instrument cannot price one: a preflop path
leaving three live has no two-position solve, and
`_derive_path_situation` refuses it by name. The scope is therefore pots
that were heads-up FROM THE FLOP, and the cell weights are recounted
over that population (67.1% of real postflop decisions, from 195,366 of
291,347). **Forced by a refusal, not by a result** - six rows had been
priced when it was written, all of them small (0.003 to 0.027 bb) and
none bearing on any verdict, and they were DISCARDED rather than kept.
Nothing else in the rule moved.

    python -m bench.studies.facing_cost rows.jsonl
"""
from __future__ import annotations

import json
import math
import os
import statistics
import sys

#: Real occurrence of each cell among ALL real postflop decisions,
#: counted over the hand store's clean hands - 195,366 of 291,347
#: decisions sit in a pot that was heads-up from the flop, and the six
#: cells below are that 67.06%. The remainder is multiway, which this
#: instrument cannot price (F46/M163: no converged multiway reference).
#: The denominator is EVERY postflop decision on purpose, so a share
#: quoted from these weights is a share of what a player actually meets.
EXPOSURE = {
    ("flop", "opening"): 0.2378, ("flop", "facing"): 0.1118,
    ("turn", "opening"): 0.1382, ("turn", "facing"): 0.0562,
    ("river", "opening"): 0.0895, ("river", "facing"): 0.0371,
}
#: What the six cells cover, for the copy to state its own scope.
SCOPE_SHARE = 0.6706
STREETS = ("flop", "turn", "river")
KINDS = ("opening", "facing")

#: Rule 1's two controls.
MAX_REMAPPED = 0.01
MAX_REFERENCE_PCT = 1.0

#: Rule 6's band, as it ships. Not re-optimised here.
BAND_LOW = 0.55
BAND_HIGH = 0.90
MIN_SIGMA = 2.0
MIN_ROWS = 6

#: Rule 3's bar, as the copy states it.
CLAIMED_RATIO = 25.0

#: Rows per cell. Facing-a-bet cells carry the claims being re-measured
#: and, per M187, essentially all of the uncertainty, so they get the
#: larger share. Settable so a run can be sized to the machine; the
#: RULE above is not settable.
QUOTAS = {(street, kind): int(os.environ.get("COST_" + kind.upper(),
                                             60 if kind == "facing" else 25))
          for street in ("flop", "turn", "river") for kind in ("opening", "facing")}
SEED = int(os.environ.get("COST_SEED", 299))
#: `last_street` for a hand that reaches each street: 1 flop, 2 turn,
#: 3 river. A cell is hunted only among hands that get that far.
MIN_LAST_STREET = {"flop": 1, "turn": 2, "river": 3}


def live_after_preflop(acts: list, n_players: int) -> int:
    """How many players take the flop.

    Pure. **Not "how many are left at this decision"** - that is the
    distinction this function exists for. A pot that starts three-handed
    and folds to two on the flop has two players at the turn, and cannot
    be priced here at all: `_derive_path_situation` refuses a preflop
    path leaving three live, because there is no two-position solve of
    it. Production answers those through its multiway machinery.

    Reading it from the hand is free; reading it from the product's
    answer costs an `/advise` walk per hand, which is what the first run
    of this study paid before discarding them.
    """
    return n_players - len({a.player for a in acts
                            if a.street == "preflop" and a.kind == "fold"})


def keep(rows: list) -> list:
    """Rule 1. Pure."""
    return [r for r in rows
            if abs(r.get("remapped_mass", 0.0)) <= MAX_REMAPPED
            and r.get("reference_exploitability_pct", 0.0) <= MAX_REFERENCE_PCT]


def net_of_slack(rows: list) -> dict:
    """How much of the measured loss the instrument can actually see.

    **Added AFTER the verdicts were read, and it changes none of them**
    - rules 3 and 6 are computed on the raw |loss| they were written
    against, and this is reported beside them. It exists because the
    control run found the reference's own row beatable by a pure fold on
    two spots of three: the yardstick has per-hand slack, and a median
    loss of 0.039 bb means nothing if the yardstick's own floor is that
    size. M259 measured its figure net of exactly this, and M296
    published raw and net side by side.

    `visible` is the share of rows whose loss exceeds the reference's own
    best deviation at that node - the rows where something real is being
    measured rather than the reference's own convergence error.
    """
    have = [r for r in rows if r.get("reference_slack_bb") is not None]
    if not have:
        return {"n": 0}
    net = [abs(r["loss_bb"]) - r["reference_slack_bb"] for r in have]
    return {
        "n": len(have),
        "mean_slack": statistics.mean(r["reference_slack_bb"] for r in have),
        "median_slack": statistics.median(r["reference_slack_bb"] for r in have),
        "mean_net": statistics.mean(net),
        "median_net": statistics.median(net),
        "visible": sum(1 for v in net if v > 0) / len(net),
    }


def _stats(values: list) -> dict:
    if not values:
        return {"n": 0}
    absolute = [abs(v) for v in values]
    return {
        "n": len(values),
        "mean_abs": statistics.mean(absolute),
        "mean_signed": statistics.mean(values),
        "median_abs": statistics.median(absolute),
        "worst": max(absolute),
        "over_1bb": sum(1 for v in absolute if v > 1.0) / len(values),
        "over_5bb": sum(1 for v in absolute if v > 5.0) / len(values),
        "sem": (statistics.stdev(absolute) / math.sqrt(len(absolute))) if len(absolute) > 1 else None,
    }


def cells(rows: list) -> dict:
    """Rule 2. Pure."""
    return {f"{street}/{kind}": _stats([r["loss_bb"] for r in rows
                                        if r["street"] == street and r["kind"] == kind])
            for street in STREETS for kind in KINDS}


def _pooled(rows: list, kind: str) -> dict:
    """A node type's mean |loss|, with its cells weighted by real occurrence.

    A quota'd sample is balanced by construction and a player does not
    meet a balanced sample, so the pooled mean re-weights each street's
    cell by how often it actually occurs (M188's correction to M187).
    """
    per_street = {street: [abs(r["loss_bb"]) for r in rows
                           if r["street"] == street and r["kind"] == kind]
                  for street in STREETS}
    weights = {street: EXPOSURE[(street, kind)] for street in STREETS if per_street[street]}
    total = sum(weights.values())
    if not total:
        return {"n": 0}
    mean = sum(statistics.mean(per_street[s]) * w for s, w in weights.items()) / total
    flat = [v for s in weights for v in per_street[s]]
    return {"n": len(flat), "mean_abs": mean,
            "unweighted_mean_abs": statistics.mean(flat),
            "sem": (statistics.stdev(flat) / math.sqrt(len(flat))) if len(flat) > 1 else None,
            "median_abs": statistics.median(flat),
            "over_1bb": sum(1 for v in flat if v > 1.0) / len(flat),
            "over_5bb": sum(1 for v in flat if v > 5.0) / len(flat)}


def ratio(rows: list) -> dict:
    """Rule 3. Pure. `sigma` compares the two pools unpaired."""
    facing, opening = _pooled(rows, "facing"), _pooled(rows, "opening")
    if not facing.get("n") or not opening.get("n"):
        return {"facing": facing, "opening": opening, "ratio": None, "sigma": None}
    sem = math.sqrt((facing["sem"] or 0.0) ** 2 + (opening["sem"] or 0.0) ** 2)
    delta = facing["mean_abs"] - opening["mean_abs"]
    return {
        "facing": facing, "opening": opening,
        "ratio": (facing["mean_abs"] / opening["mean_abs"]) if opening["mean_abs"] else None,
        "delta": delta,
        "sigma": (delta / sem) if sem else None,
    }


def cost_share(rows: list) -> dict:
    """Rule 4: facing a bet's share of everything the advice costs. Pure."""
    weighted = {}
    for street in STREETS:
        for kind in KINDS:
            values = [abs(r["loss_bb"]) for r in rows
                      if r["street"] == street and r["kind"] == kind]
            if values:
                weighted[(street, kind)] = statistics.mean(values) * EXPOSURE[(street, kind)]
    total = sum(weighted.values())
    if not total:
        return {"facing_share": None}
    facing = sum(v for k, v in weighted.items() if k[1] == "facing")
    return {
        "facing_share": facing / total,
        "per_cell_share": {f"{s}/{k}": v / total for (s, k), v in weighted.items()},
        "bb_per_100_postflop_decisions": 100 * total,
        "share_of_postflop_decisions": sum(
            EXPOSURE[k] for k in weighted),
    }


def _split(values_a: list, values_b: list) -> dict:
    """In-band against out-of-band, unpaired - they are different rows."""
    if len(values_a) < 2 or len(values_b) < 2:
        return {"n_in": len(values_a), "n_out": len(values_b), "sigma": None}
    mean_a, mean_b = statistics.mean(values_a), statistics.mean(values_b)
    sem = math.sqrt(statistics.stdev(values_a) ** 2 / len(values_a)
                    + statistics.stdev(values_b) ** 2 / len(values_b))
    return {"n_in": len(values_a), "n_out": len(values_b),
            "mean_in": mean_a, "mean_out": mean_b,
            "lift": (mean_a / mean_b) if mean_b else None,
            "delta": mean_a - mean_b,
            "sigma": ((mean_a - mean_b) / sem) if sem else None,
            "over_1bb_in": sum(1 for v in values_a if v > 1.0) / len(values_a),
            "over_1bb_out": sum(1 for v in values_b if v > 1.0) / len(values_b)}


def band(rows: list, seed: int = SEED) -> dict:
    """Rule 6, including the split-half replication. Pure given `seed`."""
    import random
    facing = [r for r in rows if r["kind"] == "facing" and r.get("percentile") is not None]
    in_band = [abs(r["loss_bb"]) for r in facing if BAND_LOW <= r["percentile"] < BAND_HIGH]
    out_band = [abs(r["loss_bb"]) for r in facing if not BAND_LOW <= r["percentile"] < BAND_HIGH]
    whole = _split(in_band, out_band)
    rng = random.Random(seed)
    shuffled = list(facing)
    rng.shuffle(shuffled)
    halves = []
    for part in (shuffled[:len(shuffled) // 2], shuffled[len(shuffled) // 2:]):
        halves.append(_split(
            [abs(r["loss_bb"]) for r in part if BAND_LOW <= r["percentile"] < BAND_HIGH],
            [abs(r["loss_bb"]) for r in part if not BAND_LOW <= r["percentile"] < BAND_HIGH]))
    weak = [abs(r["loss_bb"]) for r in facing if r["percentile"] < BAND_LOW]
    strong = [abs(r["loss_bb"]) for r in facing if r["percentile"] >= BAND_HIGH]
    return {
        "whole": whole, "halves": halves,
        "over_1bb_weak": (sum(1 for v in weak if v > 1.0) / len(weak)) if weak else None,
        "over_1bb_strong": (sum(1 for v in strong if v > 1.0) / len(strong)) if strong else None,
        "n_weak": len(weak), "n_strong": len(strong),
        "share_of_postflop_decisions": (
            len(in_band) / len(facing) * sum(EXPOSURE[(s, "facing")] for s in STREETS)
            if facing else None),
    }


def band_survives(summary: dict) -> bool:
    """Rule 6's verdict. Both the whole sample and both halves must agree."""
    whole = summary["band"]["whole"]
    if whole.get("n_in", 0) < MIN_ROWS or whole.get("sigma") is None:
        return False
    if not (whole["sigma"] >= MIN_SIGMA and whole["delta"] > 0):
        return False
    return all(h.get("delta") is not None and h["delta"] > 0 for h in summary["band"]["halves"])


def ratio_claim(summary: dict) -> str:
    """Rule 3's verdict: which of the three outcomes the copy takes."""
    measured = summary["ratio"]
    if measured.get("sigma") is None or measured["sigma"] < MIN_SIGMA:
        return "remove"
    if measured["ratio"] is not None and measured["ratio"] >= CLAIMED_RATIO:
        return "keep"
    return "quote_measured"


def summarise(rows: list) -> dict:
    kept = keep(rows)
    return {
        "n_priced": len(rows), "n_kept": len(kept),
        "refused": len(rows) - len(kept),
        "cells": cells(kept),
        "ratio": ratio(kept),
        "cost_share": cost_share(kept),
        "band": band(kept),
        "slack": {
            "all": net_of_slack(kept),
            **{kind: net_of_slack([r for r in kept if r["kind"] == kind])
               for kind in KINDS},
        },
    }


def verdict(summary: dict) -> dict:
    return {"ratio_claim": ratio_claim(summary),
            "band_note_survives": band_survives(summary)}


def main(argv=None) -> int:                              # pragma: no cover
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] == "run":
        return _run(args[1])
    if args and args[0] == "reprice":
        return _reprice(args[1], args[2])
    import pathlib
    source = pathlib.Path(args[0]) if args else (
        pathlib.Path(__file__).resolve().parents[2] / "tests" / "data" / "facing_cost_m299.jsonl")
    rows = [json.loads(line) for line in source.read_text().splitlines() if line.strip()]
    summary = summarise(rows)
    print(json.dumps({**summary, "verdict": verdict(summary)}, indent=1, default=str))
    return 0


# -- WHERE THIS STOPPED (M299, paused with the verdicts read) ------------
#
# The 255 rows are committed at `tests/data/facing_cost_m299.jsonl` and
# every verdict above is derived from them:
#
#   ratio_claim        "quote_measured" - facing 0.1544 bb against
#                      opening 0.0529, a ratio of 2.92 at 2.79 sigma,
#                      where the copy claims "at least 25 times"
#   band_note_survives False - in-band 0.2370 against 0.1607 is 1.48x at
#                      0.90 sigma, and both halves miss the bar
#   facing share       56.2% of weighted cost, where the copy says 86%
#   tail               3.3% of facing rows over 1 bb (copy: 18%) and
#                      0 of 180 over 5 bb (copy: 5%)
#
# WHAT IS NOT DONE: the slack pass. `reprice` adds `reference_slack_bb`
# to every row - the reference's own best deviation at that node - and
# it had finished 2 of 255 when this was paused. It is needed before any
# absolute figure reaches a player, because the control run found the
# reference's own row beaten by a pure fold on two spots of three, so
# the yardstick's per-hand slack may be the size of the median loss
# (0.039 bb) this would otherwise quote. It does NOT move the two
# verdicts, which are ratios read on the raw metric they were
# pre-registered against.
#
#     python -m bench.memory_guard --log reprice.guard.json -- \
#         python -m bench.studies.facing_cost reprice \
#             tests/data/facing_cost_m299.jsonl rows_with_slack.jsonl
#
# Roughly an hour, ~8 GB peak. It is also a determinism control: a
# stored request must re-price to its own recorded loss exactly, and the
# 3-row smoke did (drifted 0).
#
# THEN: rewrite both disclosures against these numbers, register the
# constants, update `bench/disclosures.py` (both currently `current=
# False`), and re-run the suite.


def _reprice(in_path: str, out_path: str) -> int:        # pragma: no cover
    """Re-price every stored row from its own request, adding the slack.

    Two things at once, and the second is why it is worth an hour:

    * it adds `reference_slack_bb`, the reference's own best deviation
      at the node, which is the floor beneath every loss here - a
      measurement smaller than its own yardstick's error is not a
      measurement (M259/M296's net figure);
    * it is a DETERMINISM control. The reference solve is seeded, so
      re-pricing a stored request must reproduce its loss exactly. A row
      that does not is a row whose request did not capture everything
      the price depended on, and it is reported rather than quietly
      overwritten.
    """
    import json as _json

    from api import config as cfg
    from api import solving
    from api.main import app
    from bench import price
    from fastapi.testclient import TestClient

    TestClient(app)                                       # wire the app up
    rows = [_json.loads(line) for line in open(in_path) if line.strip()]
    drift = []
    with open(out_path, "w") as fh:
        for index, row in enumerate(rows, 1):
            priced = price.price_request(
                body=row["request"], street=row["street"],
                shipped_strategy=dict(zip(row["actions"], row["shipped_row"])),
                entry_pot=row["entry_pot"], entry_stack=row["entry_stack"],
                solving=solving, cfg=cfg)
            delta = abs(priced.loss_bb - row["loss_bb"])
            if delta > 1e-9:
                drift.append({"hand": row["hand"], "i": row["i"], "delta": delta})
            fh.write(_json.dumps({**row,
                                  "reference_slack_bb": priced.reference_slack_bb,
                                  "repriced_loss_bb": priced.loss_bb}) + "\n")
            fh.flush()
            if index % 20 == 0:
                print(index, "of", len(rows), "drifted", len(drift), flush=True)
    print("DONE repriced", len(rows), "drifted", len(drift),
          _json.dumps(drift[:5]), flush=True)
    return 0


def _run(out_path: str) -> int:                          # pragma: no cover
    """Price real heads-up postflop decisions, one JSON row each."""
    import random
    import time

    from fastapi.testclient import TestClient
    from api import config as cfg
    from api import solving
    from api.main import app
    from bench import hand_db, price
    from bench.real_replay import DEFAULT_WHERE, deal, request_for, sample_hands
    from bench.server_warmup import clear_postflop_caches, warm_multiway
    from poker_solver.cards import parse_cards
    from poker_solver.combos import HandCombo
    from poker_solver.hand_strength import strength_percentile

    client = TestClient(app)
    warm_multiway(depths=(100.0,), table_sizes=(6,))

    def post(body):
        r = client.post("/advise", json=body)
        return r.status_code, (r.json() if r.status_code == 200 else {})

    db = hand_db.connect()
    counts = {key: 0 for key in QUOTAS}
    skips = {"unrepresentable": 0, "refused": 0, "not_heads_up": 0,
             "multiway_pot": 0, "no_decision": 0, "no_opening": 0, "priced": 0}
    written = 0
    with open(out_path, "w") as fh:
        # One pass per cell, over the hands that reach that street.
        for street in STREETS:
            for kind in KINDS:
                rng = random.Random(SEED)
                where = DEFAULT_WHERE + " AND last_street >= %d" % MIN_LAST_STREET[street]
                for hand in sample_hands(db, 20000, SEED, where=where):
                    if counts[(street, kind)] >= QUOTAS[(street, kind)]:
                        break
                    acts = [a for a in hand.streets() if a.kind != "show"]
                    # Rule 7: only a pot that was heads-up from the flop
                    # can be priced by a two-position solve. Checked here,
                    # for free, rather than after building a request.
                    if live_after_preflop(acts, hand.n_players) != 2:
                        skips["multiway_pot"] += 1
                        continue
                    wanted = [i for i, a in enumerate(acts)
                              if a.street == street
                              and (("facing" if (a.facing_bb or 0) > 1e-9 else "opening") == kind)]
                    if not wanted:
                        skips["no_decision"] += 1
                        continue
                    index = wanted[0]
                    cards = deal(hand.board, hand.n_players, rng)
                    body, _why, _worst = request_for(
                        hand, acts, index, round(hand.row["eff_stack_bb"], 2), cards, post=post)
                    if body is None:
                        skips["unrepresentable"] += 1
                        continue
                    body["hero_cards"] = cards[acts[index].player]
                    clear_postflop_caches()
                    asked = time.time()
                    status, js = post(body)
                    hero = (js.get("hero") or {}).get("strategy") if status == 200 else None
                    if not hero:
                        skips["refused"] += 1
                        continue
                    if len(js.get("positions") or []) != 2:
                        skips["not_heads_up"] += 1
                        continue
                    # M177: the reference is built at the STREET's opening
                    # pot and entry stack, never the node's - a tree built
                    # at the post-bet pot sizes its bets off it and models
                    # a different game. Both come from the product's own
                    # answer at that decision.
                    opening_body = {k: v for k, v in body.items()
                                    if k != street + "_action_path"}
                    status_open, js_open = post(opening_body)
                    if status_open != 200:
                        skips["no_opening"] += 1
                        continue
                    started = time.time()
                    try:
                        priced = price.price_request(
                            body=body, street=street, shipped_strategy=hero,
                            entry_pot=js_open["pot"], entry_stack=js_open["max_affordable_bb"],
                            solving=solving, cfg=cfg)
                    except Exception as exc:              # noqa: BLE001 - reported, not hidden
                        skips["priced"] += 1
                        print("PRICE FAILED", street, kind, type(exc).__name__, exc, flush=True)
                        continue
                    hero_combo = HandCombo(*parse_cards(body["hero_cards"]))
                    board = price.street_board(body, street)
                    fh.write(json.dumps({
                        "hand": hand.id, "i": index, "street": street, "kind": kind,
                        "board": "".join(str(c) for c in board), "hero": body["hero_cards"],
                        "percentile": strength_percentile(hero_combo, board),
                        "loss_bb": priced.loss_bb, "value_spread_bb": priced.value_spread_bb,
                        "remapped_mass": priced.remapped_mass,
                        "reference_exploitability_pct": priced.reference_exploitability_pct,
                        "actions": priced.actions, "shipped_row": priced.shipped_row,
                        "reference_row": priced.reference_row,
                        "entry_pot": js_open["pot"], "node_pot": js["pot"],
                        # The request itself, so a committed row can be
                        # re-priced rather than only re-read - which is
                        # what lets the control below score the
                        # reference against itself months later.
                        "request": body,
                        "entry_stack": js_open["max_affordable_bb"],
                        "ask_seconds": started - asked,
                        "seconds": time.time() - started,
                        "stage_seconds": priced.stage_seconds,
                    }) + "\n")
                    fh.flush()
                    counts[(street, kind)] += 1
                    written += 1
                    print("  %d %s/%s %.0fs" % (written, street, kind, time.time() - asked),
                          flush=True)
    print("DONE", written, json.dumps({f"{s}/{k}": v for (s, k), v in counts.items()}),
          json.dumps(skips), flush=True)
    return 0


if __name__ == "__main__":                                # pragma: no cover
    sys.exit(main())
