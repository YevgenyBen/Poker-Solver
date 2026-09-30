"""Can a head start let 1,000 iterations reach the 4,000-iteration answer? (M312)

M311 took the three-live multiway pot apart and removed a third of its
cost as overhead, bit-identically. What remains is CFR iterations - ~61% -
and the only lever left on them is to start somewhere better than
uniform. That is the shape any learned "System 1" stand-in would take: a
fast PROPOSAL that a short solve refines. So before building any model,
this asks whether a proposal CAN do that job here, using proposals whose
quality is known.

**Where it is asked, and why there.** Three live is the one cell with both
a prize and an instrument (M309/M310): the 4,000 budget beats 1,000 there
against strong outside play (+0.0713 at 3.28 sigma, flop +0.0746 at 3.03),
and that instrument - agreement with the published six-handed agent - is
the only yardstick that resolves it. M310 measured internal seed-noise
yardsticks failing at multiway, so no internal distance is used here.

**ARMS**, per real three-live flop decision, all through `/advise` (M174):

    C1000    cold, 1,000 iterations (what 4+ live ships; the cheap arm)
    C4000    cold, 4,000 iterations (what 3 live ships; the target)
    ORACLE0  this spot solved at 4,000 under ANOTHER seed - the donor's own
             answer, as-is
    NBR0     the NEIGHBOUR board's 4,000 answer, as-is - a lookup library
             with no refinement
    WARM_O   1,000 iterations from a head start taken from ORACLE0's solve
    WARM_N   1,000 iterations from a head start taken from NBR0's solve

**The neighbour** is the nearest realistic proposal: the board with its
lowest UNPAIRED card moved one rank (up first, then down), same suit, onto
a rank not already on the board (so no pair is made or broken) and not
one of hero's cards. Same preflop line, stack, hero and action path, so
the ranges and the pot are identical and only the board differs. If this
proposal fails, farther ones do not succeed.

**WHAT A HEAD START KEEPS** (pre-registered, one convention): the donor's
REGRETS, scaled by budget / donor iterations so they count as much as the
refinement, and its STRATEGY SUMS reset to zero. The answer is then built
only from solving THIS spot, so any gain is the head start's. Keeping the
strategy sums too - M158's convention, right there because its donor IS
the same spot - would give a 4,000-iteration donor ~16x the linear
averaging weight of 1,000 refinement iterations and simply return the
donor's answer. Regret matching is scale-invariant, so the scaling does
not change where refinement STARTS, only how fast it can move away.

**PRE-REGISTERED RULE (fixed before any arm was run).** The reference
agent's rows only; hand-clustered sigma; halves split by hand (M309's two
amendments, inherited).

    1. INSTRUMENT. C4000 - C1000 must clear +2.0 sigma - M309's flop
       control reproduced. If not, nothing below is read.
    2. MECHANISM (the load-bearing control). WARM_O - C1000 must clear
       +2.0 sigma AND C4000 - WARM_O must NOT (not separably worse). A
       perfect proposal that cannot lift 1,000 iterations means the
       mechanism does not carry a proposal, and the neighbour is REPORTED
       BUT NOT READ.
    3. THE QUESTION, on WARM_N:
         PRIZE     WARM_N - C1000 >= 2 sigma and C4000 - WARM_N < 2 sigma
         PARTIAL   WARM_N - C1000 >= 2 sigma, still separably below C4000
         NO HELP   WARM_N - C1000 < 2 sigma
    4. GUARD. The card-blind lift of WARM_N - C1000 must not reverse at
       2 sigma (M262/M309: a gain that is only decisiveness earns nothing).
    5. HALVES. PRIZE or PARTIAL must hold in both halves at >= 1.0 sigma.
    6. NBR0 and ORACLE0 are REPORTED against C1000 and C4000: whether a
       library of neighbours could serve directly, and whether another
       seed's answer scores like C4000 (the instrument's own seed floor).

The 2.0 bar is M291's `MIN_SIGMA`, inherited.

**FOLLOW-UP, PRE-REGISTERED AFTER THE PRIZE AND BEFORE THIS ARM RAN.** The
main rule answered "does a head start from the neighbour reach C4000" -
yes - and left open WHY, which decides what would have to be built. If a
head start from ANY board on the same preflop line does as well, the gain
is a generic warm-up and one donor per (line, stack) serves everything;
if only a NEARBY board does, a board library or a learned model is needed.
So a third donor, on the same spots:

    FAR0     a FAR board's 4,000 answer, as-is
    WARM_F   1,000 iterations from a head start taken from FAR0's solve

The far board is three cards drawn uniformly from the deck minus the
target board and hero's cards, seeded by the spot's identity, so it is
reproducible and unrelated to the target's texture.

    GENERIC   WARM_F - C1000 >= 2 sigma AND WARM_N - WARM_F < 2 sigma:
              the neighbour's closeness is not what carries the gain.
    SPECIFIC  otherwise: proximity matters.

**Joining onto the first run is only valid if the solve is deterministic**,
so C1000 is recomputed on every spot and must EQUAL the stored value
exactly; one mismatch voids the join and the follow-up is not read.

    python -m bench.studies.warm_start [rows.json]
    python -m bench.studies.warm_start --run rows.json [limit]
    python -m bench.studies.warm_start --followup rows.json out.json

An instrument: nothing under `poker_solver/` or `api/` imports it.
"""
from __future__ import annotations

import copy
import json
import sys

from bench.studies.wide_pot_budget import (
    card_blind, mass_on, paired, real_kind)

MIN_SIGMA = 2.0
MIN_HALF_SIGMA = 1.0
DONOR_ITERATIONS = 4000
BUDGET = 1000
ORACLE_SEED = 1
ARMS = ("C1000", "C4000", "ORACLE0", "NBR0", "WARM_O", "WARM_N")
RANKS = "23456789TJQKA"


def _rank(card: str) -> int:
    return RANKS.index(card[0])


def neighbour_board(board: str, hero: str):
    """The board with its lowest UNPAIRED card moved one rank, or None.

    Up first, then down; same suit; onto a rank the board does not already
    hold (so no pair is made or broken); never one of hero's cards.
    """
    cards = [board[i:i + 2] for i in range(0, len(board), 2)]
    ranks = [c[0] for c in cards]
    hero_cards = {hero[0:2], hero[2:4]}
    unpaired = sorted((c for c in cards if ranks.count(c[0]) == 1), key=_rank)
    for card in unpaired:
        for step in (1, -1):
            index = _rank(card) + step
            if not 0 <= index < len(RANKS):
                continue
            new_rank = RANKS[index]
            if new_rank in ranks:
                continue
            new_card = new_rank + card[1]
            if new_card in hero_cards:
                continue
            return "".join(new_card if c == card else c for c in cards)
    return None


def far_board(board: str, hero: str, key: str) -> str:
    """Three cards drawn uniformly from the deck minus the target board and
    hero's cards, seeded by `key` so the draw is reproducible."""
    import random as _random
    import zlib
    taken = {board[i:i + 2] for i in range(0, len(board), 2)}
    taken |= {hero[0:2], hero[2:4]}
    deck = [r + s for r in RANKS for s in "cdhs" if r + s not in taken]
    rng = _random.Random(zlib.crc32(key.encode("utf-8")))
    return "".join(rng.sample(deck, 3))


def followup_verdict(rows: list) -> dict:
    """The follow-up rule. Pure. `rows` carry C1000, WARM_N, WARM_F and
    `c1000_matches` - the determinism check that makes the join valid."""
    if not rows:
        return {"result": "NO DATA"}
    if not all(r.get("c1000_matches") for r in rows):
        return {"result": "NOT READ",
                "note": "C1000 did not reproduce exactly, so the follow-up "
                        "cannot be joined onto the first run"}
    primary = [r for r in rows if r.get("source") == "pluribus"]
    far_helps = _clears(paired(primary, "C1000", "WARM_F"), MIN_SIGMA)
    near_better = _clears(paired(primary, "WARM_F", "WARM_N"), MIN_SIGMA)
    return {"result": "GENERIC" if far_helps and not near_better else "SPECIFIC",
            "far_helps": far_helps, "near_separably_better": near_better}


def head_start(by_path: dict, donor_iterations: int = DONOR_ITERATIONS,
               budget: int = BUDGET) -> dict:
    """The pre-registered convention: regrets scaled to count as much as
    the refinement, strategy sums reset. Deep-copied, so the donor is
    never mutated by the solve that grows on top of it."""
    scale = budget / float(donor_iterations)
    out = {}
    for path, table in by_path.items():
        fresh = copy.deepcopy(table)
        fresh.regret_sum = fresh.regret_sum * scale
        fresh.strategy_sum = fresh.strategy_sum * 0.0
        out[path] = fresh
    return out


def _clears(cell: dict, bar: float) -> bool:
    return cell.get("hand_sigma") is not None and cell["hand_sigma"] >= bar


def summarise(rows: list) -> dict:
    """Every comparison the rule reads, on the reference agent's rows."""
    primary = [r for r in rows if r.get("source") == "pluribus"]
    out = {"n": len(primary), "hands": len({r["hand"] for r in primary})}
    for left, right in (("C1000", "C4000"), ("C1000", "WARM_O"),
                        ("WARM_O", "C4000"), ("C1000", "WARM_N"),
                        ("WARM_N", "C4000"), ("C1000", "NBR0"),
                        ("NBR0", "C4000"), ("C4000", "ORACLE0")):
        out["%s->%s" % (left, right)] = paired(primary, left, right)
    lifted = [{"hand": r["hand"], "i": r["i"],
               "C1000": r["C1000"] - r["C1000_blind"],
               "WARM_N": r["WARM_N"] - r["WARM_N_blind"]}
              for r in primary
              if r.get("C1000_blind") is not None and r.get("WARM_N_blind") is not None]
    out["guard"] = paired(lifted, "C1000", "WARM_N") if lifted else {"n": 0}
    from bench.studies.wide_pot_budget import halves
    left, right = halves(primary)
    out["half_a"] = paired(left, "C1000", "WARM_N")
    out["half_b"] = paired(right, "C1000", "WARM_N")
    return out


def verdict(s: dict) -> dict:
    """The pre-registered rule, applied. Pure."""
    if not _clears(s["C1000->C4000"], MIN_SIGMA):
        return {"instrument": "FAILED", "result": "NOT READ"}
    mechanism = (_clears(s["C1000->WARM_O"], MIN_SIGMA)
                 and not _clears(s["WARM_O->C4000"], MIN_SIGMA))
    if not mechanism:
        return {"instrument": "passed", "mechanism": "FAILED",
                "result": "NOT READ",
                "note": "a perfect proposal did not lift 1,000 iterations, so "
                        "the neighbour is reported and not read"}
    helps = _clears(s["C1000->WARM_N"], MIN_SIGMA)
    reaches = not _clears(s["WARM_N->C4000"], MIN_SIGMA)
    guard = s.get("guard") or {"n": 0}
    reversed_ = guard.get("hand_sigma") is not None and guard["hand_sigma"] <= -MIN_SIGMA
    halves_ok = all(_clears(s[k], MIN_HALF_SIGMA) for k in ("half_a", "half_b"))
    if not helps:
        result = "NO HELP"
    elif reversed_:
        result = "ARTIFACT"
    elif not halves_ok:
        result = "UNREPLICATED"
    else:
        result = "PRIZE" if reaches else "PARTIAL"
    return {"instrument": "passed", "mechanism": "passed", "result": result,
            "guard": "REVERSED" if reversed_ else "held",
            "halves": "held" if halves_ok else "did not hold"}


class _Harness:                                           # pragma: no cover
    """One `/advise` client with the seed and the head start bindable onto
    the engine call the production path makes. Shared by `run` and
    `followup`, so the two arms can never be measured two different ways."""

    def __init__(self):
        import random
        import time
        from fastapi.testclient import TestClient
        from api import solving
        from api.main import app
        from bench import hand_db
        from bench.real_replay import deal, request_for
        from bench.server_warmup import clear_postflop_caches, warm_multiway
        from bench.studies.wide_pot_budget import decisions_in
        from poker_solver.warmstart import index_by_path

        self._random, self._time = random, time
        self._deal, self._request_for = deal, request_for
        self._decisions_in, self._clear = decisions_in, clear_postflop_caches
        self._index_by_path = index_by_path
        self._solving = solving
        self.control = {"seed": None, "warm": None, "captured": None}
        self._real = solving.solve_flop_multiway
        control, real = self.control, self._real

        def solve(*a, **kw):
            if control["seed"] is not None:
                kw["seed"] = control["seed"]
            if control["warm"] is not None:
                kw["warm_start"] = control["warm"]
            result = real(*a, **kw)
            control["captured"] = result
            return result

        solving.solve_flop_multiway = solve
        self.client = TestClient(app)
        warm_multiway(depths=(100.0,), table_sizes=(6,), verbose=False)
        self.db = hand_db.connect()
        self._hand_db = hand_db

    def close(self):
        self._solving.solve_flop_multiway = self._real

    def ask(self, body, iterations, seed=None, warm=None):
        self.control.update(seed=seed, warm=warm, captured=None)
        self._clear()            # a warm and a cold solve share a cache key
        t0 = self._time.perf_counter()
        r = self.client.post("/advise", json=dict(body, solve_iterations=iterations))
        elapsed = self._time.perf_counter() - t0
        js = r.json() if r.status_code == 200 else None
        return js, self.control["captured"], elapsed

    def warm_from(self, donor):
        return (list(donor.hands),
                head_start(self._index_by_path(donor.root, donor.node_data)))

    def body(self, hand_id, index):
        hand = next(self._hand_db.query(self.db, "id = ?", (hand_id,)))
        _, acts, real = self._decisions_in(hand)
        cards = self._deal(hand.board, hand.n_players, self._random.Random(index))
        cards.update(real)
        client = self.client
        body, _, _ = self._request_for(
            hand, acts, index, round(hand.row["eff_stack_bb"], 2), cards,
            lambda b: (lambda r: (r.status_code, r.json() if r.status_code == 200 else {}))(
                client.post("/advise", json=b)))
        if body is None:
            return None, None
        body["hero_cards"] = cards[acts[index].player]
        return body, real_kind(acts[index].kind)


def _score(js, kind):
    hero = (js.get("hero") or {}).get("strategy") or {}
    table = js.get("strategy") or {}
    if not hero:
        return None, None
    return mass_on(hero, kind), card_blind(table, {k: 1.0 for k in table}, kind)


def followup(rows_path, out_path) -> int:                 # pragma: no cover
    """The far-board arm, joined onto the first run's rows - valid only if
    C1000 reproduces exactly on every spot."""
    import pathlib
    import time
    rows = json.loads(pathlib.Path(rows_path).read_text())
    h = _Harness()
    out, started = [], time.perf_counter()
    try:
        for row in rows:
            body, kind = h.body(row["hand"], row["i"])
            if body is None or kind != row["real_kind"]:
                continue
            far = far_board(body["board"], body["hero_cards"],
                            "%s:%s" % (row["hand"], row["i"]))
            c1000_js, _, _ = h.ask(body, BUDGET)
            far_js, far_result, far_t = h.ask(dict(body, board=far), DONOR_ITERATIONS)
            if c1000_js is None or far_js is None or far_result is None:
                continue
            warm_js, _, warm_t = h.ask(body, BUDGET, warm=h.warm_from(far_result))
            if warm_js is None:
                continue
            c1000, _ = _score(c1000_js, kind)
            far0, far0_blind = _score(far_js, kind)
            warm_f, warm_f_blind = _score(warm_js, kind)
            if None in (c1000, far0, warm_f):
                continue
            merged = dict(row, far=far, FAR0=far0, FAR0_blind=far0_blind,
                          WARM_F=warm_f, WARM_F_blind=warm_f_blind,
                          WARM_F_seconds=round(warm_t, 3),
                          WARM_F_iterations=warm_js.get("solve_iterations"),
                          c1000_recheck=c1000,
                          c1000_matches=(c1000 == row["C1000"]))
            out.append(merged)
            if len(out) % 10 == 0:
                print("  %3d spots  %.1f min  c1000 reproduced on all so far: %s" % (
                    len(out), (time.perf_counter() - started) / 60,
                    all(r["c1000_matches"] for r in out)), flush=True)
    finally:
        h.close()
    pathlib.Path(out_path).write_text(json.dumps(out, indent=0))
    print("wrote %d spots" % len(out), flush=True)
    return 0


def run(out_path, limit=None) -> int:                     # pragma: no cover
    """Every arm, per real three-live flop decision, through `/advise`."""
    import pathlib
    import random
    import time
    from fastapi.testclient import TestClient
    from api import solving
    from api.main import app
    from bench import hand_db
    from bench.real_replay import deal, request_for
    from bench.server_warmup import clear_postflop_caches, warm_multiway
    from bench.studies.wide_pot_budget import decisions_in
    from poker_solver.warmstart import index_by_path

    control = {"seed": None, "warm": None, "captured": None}
    real_solve = solving.solve_flop_multiway

    def solve(*a, **kw):
        # `/advise` passes no seed and no warm start; binding them onto the
        # engine call the production path makes keeps every other part of
        # that path - ranges, force-inclusion, node training - real (M306).
        if control["seed"] is not None:
            kw["seed"] = control["seed"]
        if control["warm"] is not None:
            kw["warm_start"] = control["warm"]
        result = real_solve(*a, **kw)
        control["captured"] = result
        return result

    solving.solve_flop_multiway = solve
    client = TestClient(app)

    def ask(body, iterations, seed=None, warm=None):
        control.update(seed=seed, warm=warm, captured=None)
        clear_postflop_caches()        # a warm and a cold solve share a cache key
        t0 = time.perf_counter()
        r = client.post("/advise", json=dict(body, solve_iterations=iterations))
        elapsed = time.perf_counter() - t0
        js = r.json() if r.status_code == 200 else None
        return js, control["captured"], elapsed

    def score(js, kind):
        hero = (js.get("hero") or {}).get("strategy") or {}
        table = js.get("strategy") or {}
        if not hero:
            return None, None
        return mass_on(hero, kind), card_blind(table, {k: 1.0 for k in table}, kind)

    warm_multiway(depths=(100.0,), table_sizes=(6,), verbose=False)
    source = (pathlib.Path(__file__).resolve().parents[2] / "tests" / "data"
              / "wide_pot_budget_m309.json")
    picks = [r for r in json.loads(source.read_text())
             if r["source"] == "pluribus" and r["live"] == 3 and r["street"] == "flop"]
    if limit:
        picks = picks[:limit]
    db = hand_db.connect()
    handle = open(out_path, "a", encoding="utf-8")
    written = skipped = 0
    started = time.perf_counter()
    try:
        for row in picks:
            hand = next(hand_db.query(db, "id = ?", (row["hand"],)))
            _, acts, real = decisions_in(hand)
            cards = deal(hand.board, hand.n_players, random.Random(row["i"]))
            cards.update(real)
            body, _, _ = request_for(
                hand, acts, row["i"], round(hand.row["eff_stack_bb"], 2), cards,
                lambda b: (lambda r: (r.status_code, r.json() if r.status_code == 200 else {}))(
                    client.post("/advise", json=b)))
            if body is None:
                skipped += 1
                continue
            body["hero_cards"] = cards[acts[row["i"]].player]
            nbr = neighbour_board(body["board"], body["hero_cards"])
            if nbr is None:
                skipped += 1
                continue
            kind = real_kind(acts[row["i"]].kind)
            out = {"hand": row["hand"], "i": row["i"], "source": "pluribus",
                   "street": "flop", "real_kind": kind,
                   "board": body["board"], "neighbour": nbr}

            arms = {}
            arms["C1000"] = ask(body, BUDGET)
            arms["C4000"] = ask(body, DONOR_ITERATIONS)
            oracle_js, oracle_result, oracle_t = ask(body, DONOR_ITERATIONS, seed=ORACLE_SEED)
            arms["ORACLE0"] = (oracle_js, oracle_result, oracle_t)
            nbr_js, nbr_result, nbr_t = ask(dict(body, board=nbr), DONOR_ITERATIONS)
            arms["NBR0"] = (nbr_js, nbr_result, nbr_t)
            if oracle_result is None or nbr_result is None:
                skipped += 1
                continue
            for name, donor in (("WARM_O", oracle_result), ("WARM_N", nbr_result)):
                warm = (list(donor.hands),
                        head_start(index_by_path(donor.root, donor.node_data)))
                arms[name] = ask(body, BUDGET, warm=warm)

            ok = True
            for name in ARMS:
                js = arms[name][0]
                if js is None or len(js.get("positions") or []) != 3:
                    ok = False
                    break
                value, blind = score(js, kind)
                if value is None:
                    ok = False
                    break
                out[name], out[name + "_blind"] = value, blind
                out[name + "_seconds"] = round(arms[name][2], 3)
                out[name + "_iterations"] = js.get("solve_iterations")
            if not ok:
                skipped += 1
                continue
            handle.write(json.dumps(out) + "\n")
            handle.flush()
            written += 1
            if written % 10 == 0:
                print("  %3d spots  %.1f min  skipped %d" % (
                    written, (time.perf_counter() - started) / 60, skipped), flush=True)
    finally:
        solving.solve_flop_multiway = real_solve
        handle.close()
    print("wrote %d spots (%d skipped)" % (written, skipped), flush=True)
    return 0


def main(argv=None) -> int:                               # pragma: no cover
    import pathlib
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] == "--run":
        return run(args[1], int(args[2]) if len(args) > 2 else None)
    if args and args[0] == "--followup":
        return followup(args[1], args[2])
    source = pathlib.Path(args[0]) if args else (
        pathlib.Path(__file__).resolve().parents[2] / "tests" / "data"
        / "warm_start_m312.json")
    text = source.read_text()
    rows = ([json.loads(l) for l in text.splitlines() if l.strip()]
            if source.suffix == ".jsonl" else json.loads(text))
    s = summarise(rows)
    print(json.dumps(s, indent=1, default=float))
    print(json.dumps(verdict(s), indent=1))
    return 0


if __name__ == "__main__":                                # pragma: no cover
    sys.exit(main())
