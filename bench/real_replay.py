"""Replay REAL played hands through `/advise` as a defect benchmark (A5).

Synthetic spot populations only reach the lines their generator writes
(M252). This walks hands out of `bench.hand_db` instead: real stacks,
real table sizes, real bet sizes, real lines. Online logs hide the hole
cards, so each player is dealt a random legal hand; the replay measures
whether the product can ANSWER real spots, not whether it agrees with
the player (M262 does that, where the cards are known).

Real bet sizes are mapped onto the sizes the response itself says it
models (`modelled_bet_sizes`), nearest in log space; a real all-in maps
to `max_affordable_bb`. `worst` in each result is the largest ratio
between a real size and the size it was mapped to.

    python -m bench.real_replay --sample 1200 --out rows.jsonl

An instrument: nothing under `poker_solver/` or `api/` imports it.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import time
from typing import Callable

from bench import hand_db
from bench.advise_checks import response_defects

# The preflop model's raise cap; the last raise is forced all in.
PREFLOP_MAX_RAISES = 4
# M270 added 7- and 8-handed and M271 warmed the deep buckets, and this
# instrument was not updated for either - so a replay run with the old
# filter measured coverage over a population chosen to be coverable, and
# would have reported a flattering number. M252's F54 in a new place: the
# benchmark measures the population it generates.
SUPPORTED_TABLE_SIZES = (2, 3, 4, 5, 6, 7, 8, 9)
RANKS, SUITS = "AKQJT98765432", "shdc"
#: Every clean hand at a supported table size. **Deliberately NOT capped
#: at 200bb**: stacks above it were always supported (M271 measured the
#: latency, not a refusal), and excluding them hid 15% of real multiway
#: flops from every replay that has ever run here.
DEFAULT_WHERE = ("clean = 1 AND n_players IN (2,3,4,5,6,7,8,9) "
                 "AND eff_stack_bb >= 2")
#: The honest denominator for a COVERAGE claim: every clean hand,
#: including the ones this engine cannot answer. A coverage figure taken
#: over `DEFAULT_WHERE` is conditional on being answerable already.
ALL_CLEAN_WHERE = "clean = 1 AND eff_stack_bb >= 2"


def deal(board: str, n: int, rng: random.Random) -> dict:
    """A random legal two-card hand for each of `n` players."""
    used = {board[i:i + 2] for i in range(0, len(board), 2)}
    deck = [r + s for r in RANKS for s in SUITS if r + s not in used]
    rng.shuffle(deck)
    return {p: deck[2 * p] + deck[2 * p + 1] for p in range(n)}


def street_fields(board: str, street: str, paths: dict) -> dict:
    """The board and earlier-street fields an `/advise` body needs."""
    out = {"board": board[0:6]}
    if street in ("turn", "river"):
        out.update(flop_action_path=list(paths["flop"]), turn_card=board[6:8])
    if street == "river":
        out.update(turn_action_path=list(paths["turn"]), river_card=board[8:10])
    if paths.get(street):
        out[street + "_action_path"] = list(paths[street])
    return out


def request_for(hand, acts: list, upto: int, stack_bb: float,
                hole_cards: dict, post: Callable) -> tuple:
    """The `/advise` body for the decision before `acts[upto]`.

    Returns `(body, None, worst)`, or `(None, reason, worst)` when the
    line cannot be expressed. `post(body)` must return
    `(status, payload)`; it is called at every real postflop bet, to
    read which sizes the product offers there.
    """
    body = {"stack_bb": stack_bb, "players": hand.n_players}
    pre, raises = [], 0
    paths = {"flop": [], "turn": [], "river": []}
    invested = [0.0] * hand.n_players          # before the current street
    street_to = {}
    ours_prev_to = real_prev_to = None
    worst = 1.0
    current = "preflop"
    for a in acts[:upto]:
        if a.street != current:
            for p, v in street_to.items():
                invested[p] += v
            street_to = {}
            ours_prev_to = real_prev_to = None
            current = a.street
        if a.street == "preflop":
            if a.kind == "fold":
                pre.append("fold")
            elif a.kind in ("check", "call"):
                pre.append("call_or_check")
            else:
                raises += 1
                jam = a.to_bb >= stack_bb - 1e-6 or raises >= PREFLOP_MAX_RAISES
                pre.append("all_in" if jam else "raise")
        else:
            path = paths[a.street]
            if a.kind == "fold":
                path.append("fold")
            elif a.kind in ("check", "call"):
                path.append("call_or_check")
            else:
                node = dict(body, preflop_action_path=list(pre),
                            hero_cards=hole_cards[a.player],
                            **street_fields(hand.board, a.street, paths))
                status, js = post(node)
                if status != 200:
                    return None, "walk %s %d" % (a.street, status), worst
                sizes = sorted(js.get("modelled_bet_sizes") or [])
                cap = js.get("max_affordable_bb")
                if not sizes:
                    return None, "no sizes", worst
                if a.kind == "bet" or ours_prev_to is None:
                    target = a.to_bb / max(a.pot_before_bb, 1e-9) * js["pot"]
                else:
                    target = a.to_bb / max(real_prev_to, 1e-9) * ours_prev_to
                if invested[a.player] + a.to_bb >= stack_bb - 1e-6:
                    pick = cap if cap is not None else sizes[-1]
                else:
                    pick = min(sizes, key=lambda s: abs(math.log(s / max(target, 1e-9))))
                worst = max(worst, pick / max(target, 1e-9), max(target, 1e-9) / pick)
                is_allin = cap is not None and abs(pick - cap) < 1e-6
                path.append("all_in" if is_allin else "raise:%.2f" % pick)
                ours_prev_to, real_prev_to = pick, a.to_bb
        if a.to_bb is not None:
            street_to[a.player] = a.to_bb
    req = dict(body, preflop_action_path=pre)
    if acts[upto].street != "preflop":
        req.update(street_fields(hand.board, acts[upto].street, paths))
    return req, None, worst


def replay_one(hand, rng: random.Random, post: Callable, clock=None) -> dict:
    """Pick one decision in `hand` at random and ask `/advise` about it.

    `clock` (a `bench.reference_units.DriftClock`) adds `units` - the
    request's time over a reference workload timed beside it - to each
    answered row. M286: the 2026-09-18 audit could draw NO latency
    comparison with the audit before it, because wall-clock seconds on
    this machine drift up to 9.7x inside one run (M240); units are what
    make two replays comparable. Off by default, since it adds ~0.28s a
    decision.
    """
    stack = round(hand.row["eff_stack_bb"], 2)
    acts = [a for a in hand.streets() if a.kind != "show"]
    cards = deal(hand.board, hand.n_players, rng)
    i = rng.randrange(len(acts))
    a = acts[i]
    row = {"hand": hand.id, "i": i, "players": hand.n_players, "stack": stack,
           "street": a.street}
    try:
        req, why, worst = request_for(hand, acts, i, stack, cards, post)
    except Exception as exc:                              # noqa: BLE001
        req, why, worst = None, "walker %s" % type(exc).__name__, 1.0
    if req is None:
        return dict(row, outcome="unrepresentable", why=why)
    req["hero_cards"] = cards[a.player]
    if clock is None:
        t0 = time.perf_counter()
        status, js = post(req)
        row["seconds"] = round(time.perf_counter() - t0, 3)
    else:
        (status, js), m = clock.measure(post, req)
        row.update(seconds=round(m.seconds, 3), units=round(m.units, 3),
                   reference_seconds=round(m.reference_seconds, 4))
    if status != 200:
        return dict(row, outcome="refused", why=str(js.get("detail"))[:160])
    return dict(row, outcome="answered", defects=response_defects(js, req),
                live=len(js.get("positions") or []),
                notes=js.get("advisory_notes"),
                confidence=js.get("solver_confidence"),
                size_ratio=round(worst, 3))


def sample_hands(db, sample: int, seed: int, where: str = DEFAULT_WHERE,
                 accept: Callable | None = None):
    """Up to `sample` hands matching `where`, in a seeded random order."""
    rng = random.Random(seed)
    ids = [r[0] for r in db.execute("SELECT id FROM hands WHERE " + where)]
    rng.shuffle(ids)
    taken = 0
    for hid in ids:
        if taken >= sample:
            return
        hand = next(hand_db.query(db, "id = ?", (hid,)))
        if not [a for a in hand.streets() if a.kind != "show"]:
            continue
        if accept is not None and not accept(hand):
            continue
        taken += 1
        yield hand


def replay_to(out: str, hands, rng: random.Random, post: Callable, clock=None):
    """Replay `hands` into `out` as JSON lines, one row per hand.

    With a `clock`, every row carries units and the run's drift report is
    written to `out + ".drift.json"` and returned - the number that says
    whether this run's seconds can be read at all (M240). M286's first
    baseline run parsed `--units` in `main` and never passed the clock on,
    so 1,200 rows came back in seconds alone. The loop lives here so the
    wiring is tested rather than trusted.
    """
    with open(out, "w") as fh:
        for hand in hands:
            fh.write(json.dumps(replay_one(hand, rng, post, clock)) + "\n")
            fh.flush()
    if clock is None:
        return None
    report = clock.report()
    with open(out + ".drift.json", "w") as fh:
        json.dump(report, fh, indent=1)
    return report


def main(argv=None):                                      # pragma: no cover
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=1200)
    parser.add_argument("--seed", type=int, default=5)
    parser.add_argument("--out", required=True)
    parser.add_argument("--where", default=DEFAULT_WHERE)
    parser.add_argument("--tables", default="3,4,5,6,7,8,9",
                        help="multiway table sizes to prewarm")
    parser.add_argument("--depths", default="100,50,20",
                        help="stack depths to prewarm them at")
    parser.add_argument("--units", action="store_true",
                        help="time each request in reference units too (M286), and "
                             "write the run's drift report beside --out")
    args = parser.parse_args(argv)

    from fastapi.testclient import TestClient
    from api.main import app
    from bench.server_warmup import warm_multiway

    client = TestClient(app)
    warm_multiway(depths=tuple(float(d) for d in args.depths.split(",")),
                  table_sizes=tuple(int(t) for t in args.tables.split(",")))

    def post(body):
        r = client.post("/advise", json=body)
        return r.status_code, r.json()

    def not_cold_nine_max(hand):
        # An unwarmed 9-max depth costs ~525s a solve.
        return hand.n_players != 9 or math.floor(hand.row["eff_stack_bb"] / 5) * 5 == 100

    db = hand_db.connect()
    clock = None
    if args.units:
        from bench.reference_units import DriftClock
        clock = DriftClock()
    report = replay_to(args.out, sample_hands(db, args.sample, args.seed, where=args.where,
                                              accept=not_cold_nine_max),
                       random.Random(args.seed), post, clock)
    if report is not None:
        print(json.dumps(report, indent=1))


if __name__ == "__main__":                                # pragma: no cover
    main()
