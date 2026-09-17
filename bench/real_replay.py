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
SUPPORTED_TABLE_SIZES = (2, 3, 6, 9)
RANKS, SUITS = "AKQJT98765432", "shdc"
DEFAULT_WHERE = ("clean = 1 AND n_players IN (2,3,6,9) "
                 "AND eff_stack_bb <= 200 AND eff_stack_bb >= 2")


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


def replay_one(hand, rng: random.Random, post: Callable) -> dict:
    """Pick one decision in `hand` at random and ask `/advise` about it."""
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
    t0 = time.perf_counter()
    status, js = post(req)
    row["seconds"] = round(time.perf_counter() - t0, 3)
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


def main(argv=None):                                      # pragma: no cover
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=1200)
    parser.add_argument("--seed", type=int, default=5)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    from fastapi.testclient import TestClient
    from api.main import app
    from bench.server_warmup import warm_multiway

    client = TestClient(app)
    warm_multiway(depths=(100.0, 50.0, 20.0), table_sizes=(3, 6, 9))

    def post(body):
        r = client.post("/advise", json=body)
        return r.status_code, r.json()

    def not_cold_nine_max(hand):
        # An unwarmed 9-max depth costs ~525s a solve.
        return hand.n_players != 9 or math.floor(hand.row["eff_stack_bb"] / 5) * 5 == 100

    db = hand_db.connect()
    rng = random.Random(args.seed)
    with open(args.out, "w") as fh:
        for hand in sample_hands(db, args.sample, args.seed, accept=not_cold_nine_max):
            fh.write(json.dumps(replay_one(hand, rng, post)) + "\n")
            fh.flush()


if __name__ == "__main__":                                # pragma: no cover
    main()
