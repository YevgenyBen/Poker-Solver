"""Does the 4x postflop budget pay at FOUR OR MORE live? (M309)

`MULTIWAY_WIDE_POT_MIN_LIVE`'s own comment says so in as many words:

    The 4-live accuracy question is UNDERPOWERED, not answered: this
    reverts a cost nobody measured a benefit for.

M264 chose the 4x multiway postflop budget against an outside reference
and measured **+0.107 at 7.86 sigma** over 579 real decisions. M266 then
split that by live count and found **+0.110 at 7.88 sigma with 3 live
(n=541)** against **+0.056 at 1.01 sigma with 4 (n=38)**, and reverted
4+ live to 1,000 iterations because a 4-live flop had gone 2.1 -> 7.0s.
A10 (M269) re-asked it with the professionals' decisions as well and read
**+0.040 at 2.01 sigma (n=260)** - real, and refused on latency (14.3s
median, p90 16.8s).

So the cell is governed by three readings at n=38, n=260 and nothing
else, and the hand store holds **3,433 scorable decisions** there.

**WHY IT IS ASKED NOW.** A learned value network trained on our own
solves buys LATENCY, never knowledge - the stone law forbids training on
the reference, and M194 says shared model error is the whole residual
anyway. So such a net pays off in exactly one situation: we already know
a better answer and cannot serve it in time. This measures whether 4+
live is that situation. It is a precondition, not an implementation: if
the bigger budget buys nothing here, there is nothing for a net to
compress and the route dies cheaply.

The other candidate arm is already DEAD and is not re-run: M264 measured
a four-seed postflop **ensemble** at **+0.002, 0.23 sigma** against this
same reference, and M169 had already refused it on latency with the
worst case never improving at any K.

**PRE-REGISTERED RULE (fixed before any arm was run).**

Population: real postflop decisions from `bench.hand_db` where the
ACTING player's own hole cards are known, live count taken AT THE
DECISION rather than at the flop. Unknown players are dealt random legal
cards; the actor's are real.

Arms, set per request through `solve_iterations` and **verified from the
response's own echo** (M233: read a real response, never trust the
constant), so no config constant moves and M245's trap does not apply:

    S   1,000 iterations - what ships at 4+ live
    W   4,000 iterations - what ships at 3 live

Metric: M262/M264/M291's own - our probability mass on the action KIND
the real player took (aggressive / passive / fold). Kind-grouped because
our menu and a real bet size need not match, which is M241's reason for
choosing an axis that needs no size mapping.

    1. CONTROL FIRST. At 3 live, W - S must clear +2.0 sigma. M264
       measured +0.107 at 7.86 sigma there. If the control fails the
       instrument is broken and NOTHING is concluded at 4+ live.

       **THE CONTROL DOES DOUBLE DUTY, and this was written into the rule
       while it stood at 24 hands and -0.85 sigma - nowhere near
       conclusive either way, so it is not a reading chosen to suit an
       answer.** At 3 live the two arms are exactly M264's own comparison:
       its baseline was 1,000 iterations and its 4x arm 4,000. But M264
       measured that BEFORE `MULTIWAY_POSTFLOP_ACTION_GROUPING` shipped,
       and grouping attacks the same mechanism - it divides a kind's
       regret by how many actions it holds, so a menu of three bet sizes
       stops collecting three actions' positive regret against checking's
       one. M269 measured grouping at a FIXED 4x budget (+0.042, 3.65
       sigma, bets when checked to 0.472 -> 0.410); **nobody has measured
       the budget at a fixed grouping.** So a control failure here would
       not only mean the instrument is unfit: it would mean M264's
       headline no longer reproduces at the shipped configuration, and
       `DEFAULT_MULTIWAY_PATH_QUERY_FLOP_ITERATIONS = 4000` carries a
       justification that has expired - the same family as M292, M299 and
       M300-M306. Either way rule 1 stands as written and nothing is
       concluded at 4+ live from this design.
    2. PRIZE. At 4+ live, W - S must clear +2.0 sigma.
    3. GUARD. The card-blind lift difference must not be -2.0 sigma or
       worse. Raw agreement is not a proper scoring rule and a better
       converged arm is more DECISIVE (M173: mixed rows 37.3% -> 22.1%),
       so an agreement gain must not be decisiveness alone. Where the
       guard cannot be computed the prize is reported PROVISIONAL, never
       passed by default.
    4. SPLIT-HALF. Any cell clearing rule 2 must hold in both halves at
       >= 1.0 sigma, split on a crc32 digest of the decision's IDENTITY
       only - never of what it measured (M306).
    5. AGGRESSION, inherited from M264's own bar: report whether W moves
       bet-when-checked-to toward the reference's 0.194.
    6. Latency is reported and is NEVER a veto. The prize is a target for
       an offline computation, so its cost is the point of the study
       rather than an objection to it.

The 2.0 bar is M291's own `MIN_SIGMA`, fixed before this question was
asked - an inherited bar, which M305 records as the only kind that can
be trusted once the data is already on disk.

**TWO AMENDMENTS, both written after COUNTING the population and before
any 4+ live row was scored** - so neither could be chosen to suit a
result. M287's precedent: an amendment forced by the shape of the data
is legitimate; one forced by an answer is not.

*Amendment 1 - the unit of independence is the HAND, not the decision.*
Pluribus reaches a four-way flop **36 times in 10,000 hands**, and those
36 hands carry 238 scorable decisions - six or seven per hand, sharing
one board, one preflop line and one set of ranges. A per-decision sigma
treats them as 238 independent draws and overstates significance roughly
six-fold. So every cell reports a `hand_sigma` as well, computed by
averaging within a hand first, and **the verdict reads the clustered
one**. Split halves are split BY HAND for the same reason.

*Amendment 2 - the reference is Pluribus, and 2009 online play is a
labelled secondary cell.* The pool holds 779 handhq-2009 decisions
against Pluribus's 238 at 4+ live, and scoring agreement with unselected
2009 online play measures closeness to an average player of that era,
which is not the quantity. M262 established the published six-handed
agent as the reference and M264/M266/M269 all scored on it. A10's "five
professionals" cannot be reconstructed here: the 6-max 4+ live pool holds
**1,797 distinct anonymised players**, the most frequent with 45 hands,
so there is no win-rate selection to be had. handhq rows are therefore
recorded, reported, and **excluded from the verdict**.

**A consequence worth stating in advance**: with 36 effective units the
primary cell may well remain underpowered, and that would be a real
finding rather than a failure - the same structural limit M308 hit from
the other direction. It is also why `MULTIWAY_WIDE_POT_MIN_LIVE`'s own
comment has stood since M266: four-way pots are rare in strong
six-handed play, so the cell cannot be powered from a strong player's
hands however many are collected. What the cell IS common in is loose
real play, where M291 measured the 4-live flop at 6.43% of decisions -
so exposure and correctness have to come from different populations, and
each is named where it is used.

    python -m bench.studies.wide_pot_budget [rows.json]

An instrument: nothing under `poker_solver/` or `api/` imports it.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
import zlib

#: Rules 1 and 2. M291's own bar, fixed before this question.
MIN_SIGMA = 2.0
#: Rule 4.
MIN_HALF_SIGMA = 1.0
#: Rule 3 - the guard refuses only a separable REVERSAL, not any dip.
MAX_GUARD_SIGMA = -2.0
#: Rule 5. What the outside reference bets when checked to (M264).
REFERENCE_BETS_CHECKED_TO = 0.194
#: What A10 (M269) reported at 4+ live, which is the effect this study
#: must be able to SEE for a null to mean anything.
A10_SIGNED_GAP = 0.040
#: Amendment 2: the PRIMARY cell's source - the published six-handed agent
#: M262 established. Everything else is recorded and reported, never read
#: for the verdict.
REFERENCE_SOURCE = "pluribus"
#: The two arms, as iteration budgets.
SHIPPED_ITERATIONS = 1000
WIDE_ITERATIONS = 4000
#: Flop and turn only. The multiway RIVER is capped at 200 iterations for
#: every live count (`_ADVISE_ITERATION_CAPS[("river", True)]` is
#: `(200, 200)`) and `MULTIWAY_WIDE_POT_ITERATIONS` names no river, so
#: there is no second arm to compare there - M266's own comment says the
#: river was unchanged. Scope, established before the run rather than
#: discovered inside it.
STREETS = ("flop", "turn")
#: Six-handed only, which is the population M262/M264/M266/M269 all used.
#: It also removes table size as a confound and holds the warm-up to one
#: table size - 1,017 scorable decisions at 4+ live, against A10's 260.
TABLE_SIZE = 6
#: The one 5bb stack bucket, and it is the REFERENCE's own depth rather
#: than a convenience: the published agent plays 100bb only, so all 549 of
#: its six-handed hands sit in this single bucket while 2009 online play
#: spans 87. Restricting both sources to it removes stack depth as a
#: confound between the primary and secondary cells, and holds the preflop
#: warm-up to one solve instead of 87 cold ones - about four hours that
#: would have been spent entirely on the half excluded from the verdict.
#:
#: **So every figure here is a 100bb statement**, and M274 is why that is
#: said out loud: a flop finding that held at 100bb inverted at 50bb. This
#: study cannot speak to any other depth, and the primary cell has no
#: other depth available by construction.
STACK_BUCKET_BB = 100
#: Fixed so the draw is reproducible and a partial run is still a sample.
DRAW_SEED = 309
#: Targets per (live band, source). The primary 4+ live cell takes
#: EVERYTHING the reference has - there are only ~238 such decisions and
#: they come from 36 hands, so nothing is left on the table. The other
#: cells are capped because they are either a control (3 live, where M264
#: already saw 7.86 sigma at n=541) or a labelled secondary (handhq).
TARGETS = {(4, "pluribus"): 400, (3, "pluribus"): 250,
           (4, "handhq-2009"): 250, (3, "handhq-2009"): 150}


def kind_of(action: str) -> str:
    """Our own action names, grouped to the three kinds the metric uses."""
    if action.startswith("fold"):
        return "fold"
    if action.startswith("call") or action.startswith("check"):
        return "passive"
    return "aggressive"


def real_kind(kind: str) -> str:
    """`bench.hand_db`'s action kinds, grouped the same way.

    Its vocabulary is fold / check / call / bet / raise; ours is fold /
    check / call / raise / all_in. Both collapse to three.
    """
    if kind == "fold":
        return "fold"
    if kind in ("check", "call"):
        return "passive"
    if kind in ("bet", "raise"):
        return "aggressive"
    raise ValueError("not a decision kind: %r" % (kind,))


def mass_on(row: dict, kind: str) -> float:
    """How much of one hand's row sits on a kind."""
    return sum(p for a, p in row.items() if kind_of(a) == kind)


def aggression(row: dict) -> float:
    return mass_on(row, "aggressive")


def card_blind(strategy: dict, weights: dict, kind: str):
    """The arm's range-weighted average row, scored on the same kind.

    M262's control. Raw agreement rewards hedging - a flat row collects
    something whatever happened - and this does not, because it subtracts
    what the arm would have said knowing no cards at all.
    """
    total = weighted = 0.0
    for key, row in (strategy or {}).items():
        w = (weights or {}).get(key, 0.0)
        if w <= 0:
            continue
        total += w
        weighted += w * mass_on(row, kind)
    return (weighted / total) if total else None


def digest(row: dict) -> int:
    """A stable split key from the HAND's identity only.

    Never from anything the decision measured: M306's own rule tests
    caught halves that alternated in draw order, which would have tested
    the generator instead of the finding.

    Keyed on the hand rather than the decision (amendment 1). Six or seven
    decisions can share one board, one preflop line and one set of ranges,
    so splitting per decision puts the SAME spot in both halves and a
    split-half check stops being independent.
    """
    return zlib.crc32(str(row["hand"]).encode("utf-8"))


def halves(rows: list) -> tuple:
    left = [r for r in rows if digest(r) % 2 == 0]
    right = [r for r in rows if digest(r) % 2 == 1]
    return left, right


def is_reference(row: dict) -> bool:
    """Whether a row belongs to the PRIMARY cell (amendment 2).

    The published six-handed agent M262 established and M264/M266/M269
    all scored on. 2009 online play is recorded and reported, never used
    for the verdict.
    """
    return row.get("source") == REFERENCE_SOURCE


def paired(rows: list, left: str = "S", right: str = "W") -> dict:
    """`right` minus `left`, paired on the decision. Pure.

    sigma is None when it is UNDEFINED - fewer than two rows, or a cell
    with no variation at all. M306's rule tests caught a zero-variance
    cell being scored 0.0 sigma, which reads as "measured and null" when
    the truth is "not measured".
    """
    usable = [r for r in rows
              if r.get(left) is not None and r.get(right) is not None]
    deltas = [r[right] - r[left] for r in usable]
    out = {"n": len(usable), "sigma": None,
           left: statistics.mean(r[left] for r in usable) if usable else None,
           right: statistics.mean(r[right] for r in usable) if usable else None,
           "delta": statistics.mean(deltas) if deltas else None,
           "better": sum(1 for d in deltas if d > 0),
           "worse": sum(1 for d in deltas if d < 0)}
    if len(deltas) > 1 and statistics.stdev(deltas) > 0:
        out["sigma"] = out["delta"] / (statistics.stdev(deltas) / math.sqrt(len(deltas)))

    # Amendment 1: the hand is the unit of independence. Average within a
    # hand first, so six decisions sharing one board count once.
    per_hand = {}
    for r in usable:
        per_hand.setdefault(r["hand"], []).append(r[right] - r[left])
    means = [statistics.mean(v) for v in per_hand.values()]
    out["hands"] = len(means)
    out["hand_sigma"] = None
    out["hand_delta"] = statistics.mean(means) if means else None
    if len(means) > 1 and statistics.stdev(means) > 0:
        out["hand_sigma"] = out["hand_delta"] / (
            statistics.stdev(means) / math.sqrt(len(means)))
    return out


def min_detectable(cell: dict, bar: float = MIN_SIGMA):
    """The smallest effect this cell could have cleared the bar with.

    A null is only evidence of absence if the cell could have SEEN the
    effect it is looking for. With 36 clustered units that is not
    guaranteed, so every null here is reported beside the effect size it
    was able to detect - and A10's +0.040 beside it.

    Derived from the cell's own per-hand spread: `bar * sem`, where sem is
    recovered from the reported hand sigma. Returns None when the cell
    carries no defined hand sigma, because then nothing was measured.
    """
    if cell.get("hand_sigma") in (None, 0) or cell.get("hand_delta") is None:
        return None
    sem = abs(cell["hand_delta"]) / abs(cell["hand_sigma"])
    return bar * sem


def guard(rows: list) -> dict:
    """Rule 3: the same comparison on the CARD-BLIND lift.

    Each arm is scored against its own range-weighted average row, so a
    gain that is only extra decisiveness cannot earn anything here.
    Returns `{"n": 0}` when no row carries a blind score - the caller
    must then report PROVISIONAL rather than pass.
    """
    lifted = []
    for r in rows:
        if r.get("S_blind") is None or r.get("W_blind") is None:
            continue
        if r.get("S") is None or r.get("W") is None:
            continue
        lifted.append({"hand": r["hand"], "i": r["i"],
                       "S": r["S"] - r["S_blind"], "W": r["W"] - r["W_blind"]})
    return paired(lifted) if lifted else {"n": 0}


def checked_to_aggression(rows: list, arm: str):
    """Rule 5: how often an arm bets when it is checked to.

    Read only on rows where hero faced nothing, which is what M264's own
    figure describes.
    """
    vals = [r["%s_aggression" % arm] for r in rows
            if not r.get("facing") and r.get("%s_aggression" % arm) is not None]
    return statistics.mean(vals) if vals else None


def summarise(rows: list) -> dict:
    """Every cell the rule reads. Pure, so a test can pin it.

    The PRIMARY cells hold reference rows only (amendment 2); the `_other`
    cells hold everything else and exist to be reported, not read.
    """
    primary = [r for r in rows if is_reference(r)]
    other = [r for r in rows if not is_reference(r)]

    def cells(pool, tag):
        control = [r for r in pool if r.get("live") == 3]
        wide = [r for r in pool if (r.get("live") or 0) >= 4]
        out = {tag + "control3": paired(control),
               tag + "wide4plus": paired(wide),
               tag + "guard4plus": guard(wide)}
        for street in STREETS:
            cell = [r for r in wide if r.get("street") == street]
            if cell:
                out[tag + "wide_" + street] = paired(cell)
        left, right = halves(wide)
        out[tag + "wide_half_a"] = paired(left)
        out[tag + "wide_half_b"] = paired(right)
        return out

    out = dict(cells(primary, ""))
    out.update(cells(other, "other_"))
    out["aggression"] = {
        arm: checked_to_aggression([r for r in primary
                                    if (r.get("live") or 0) >= 4], arm)
        for arm in ("S", "W")}
    out["aggression"]["reference"] = REFERENCE_BETS_CHECKED_TO
    out["counts"] = {"primary": len(primary), "other": len(other),
                     "primary_hands": len({r["hand"] for r in primary}),
                     "forced_hero": sum(1 for r in rows
                                        if r.get("S_in_range") is False),
                     "support_mismatch": sum(1 for r in rows
                                             if r.get("support_matches") is False)}
    return out


def _clears(cell: dict, bar: float, key: str = "hand_sigma") -> bool:
    """A cell clears only with a DEFINED sigma at or above the bar.

    Reads the HAND-clustered sigma by default (amendment 1): six decisions
    sharing one board are one draw, not six.
    """
    return cell.get(key) is not None and cell[key] >= bar


def verdict(summary: dict) -> dict:
    """The pre-registered rule, applied to the PRIMARY cells only. Pure."""
    if not _clears(summary["control3"], MIN_SIGMA):
        return {"control": "FAILED", "prize": "NOT CONCLUDED",
                "note": "the instrument did not reproduce M264's 3-live gain, "
                        "so nothing is concluded at 4+ live"}

    prize = _clears(summary["wide4plus"], MIN_SIGMA)
    g = summary.get("guard4plus") or {"n": 0}
    if g.get("n", 0) == 0:
        guard_state = "unavailable"
    elif g.get("hand_sigma") is not None and g["hand_sigma"] <= MAX_GUARD_SIGMA:
        guard_state = "REVERSED"
    else:
        guard_state = "held"

    halves_ok = all(_clears(summary[k], MIN_HALF_SIGMA)
                    for k in ("wide_half_a", "wide_half_b"))

    if not prize:
        state = "NO PRIZE"
    elif guard_state == "REVERSED":
        state = "ARTIFACT"
    elif guard_state == "unavailable":
        state = "PROVISIONAL"
    elif not halves_ok:
        state = "UNREPLICATED"
    else:
        state = "PRIZE"
    out = {"control": "passed", "prize": state, "guard": guard_state,
           "halves": "held" if halves_ok else "did not hold",
           "hands": summary["wide4plus"].get("hands")}
    if state == "NO PRIZE":
        # A null is only evidence of absence if the cell could have seen
        # the effect. Say what it could have seen, and what A10 reported.
        floor = min_detectable(summary["wide4plus"])
        out["could_have_detected"] = floor
        out["a10_reported"] = A10_SIGNED_GAP
        out["underpowered_for_a10"] = (floor is not None
                                       and floor > A10_SIGNED_GAP)
    return out


def decisions_in(hand, min_live: int = 3) -> list:
    """Every flop/turn decision in one hand whose ACTOR's real cards are
    known, with the live count taken AT THE DECISION.

    Live count is what selects the cell, and `players_to_flop` is the
    wrong field for it: a hand four-handed on the flop can be heads-up by
    the turn. Returns `(index, action, live)` triples alongside the action
    list the walker needs.
    """
    acts = [a for a in hand.streets() if a.kind != "show"]
    cards = {int(k): v for k, v in (hand.hole_cards or {}).items()}
    folded, out = set(), []
    for i, a in enumerate(acts):
        if a.street in STREETS:
            live = hand.n_players - len(folded)
            if live >= min_live and a.player in cards:
                out.append((i, a, live))
        if a.kind == "fold":
            folded.add(a.player)
    return out, acts, cards


def run(out_path, wide_target: int, control_target: int,
        minutes: float | None = None) -> int:            # pragma: no cover
    """Drive both arms over real decisions and append rows as they land.

    Rows are written one at a time: M257's rule, so a run stopped halfway
    holds many decisions rather than one. The decision list is SHUFFLED,
    which is what makes a partial run an unbiased smaller sample.
    """
    import pathlib
    import random
    import time
    from fastapi.testclient import TestClient
    from api import config as cfg
    from api.main import app
    from bench import hand_db
    from bench.real_replay import deal, request_for
    from bench.server_warmup import warm_multiway

    client = TestClient(app)

    def post(body):
        r = client.post("/advise", json=body)
        return r.status_code, (r.json() if r.status_code == 200
                               else {"detail": r.text[:200]})

    print("warming %d-max at the depths production prewarms..." % TABLE_SIZE,
          flush=True)
    warm_multiway(table_sizes=(TABLE_SIZE,))

    db = hand_db.connect()
    print("building the decision list...", flush=True)
    bucket = cfg.MULTIWAY_STACK_BUCKET_BB
    pool = []
    for hand in hand_db.query(
            db, "clean = 1 AND players_to_flop >= 3 AND n_players = ? "
                "AND eff_stack_bb >= ? AND eff_stack_bb < ?",
            (TABLE_SIZE, STACK_BUCKET_BB, STACK_BUCKET_BB + bucket)):
        picks, acts, cards = decisions_in(hand)
        for i, a, live in picks:
            pool.append((hand.id, i, live, a.street, hand.source))
    random.Random(DRAW_SEED).shuffle(pool)
    print("  %d scorable decisions in the pool" % len(pool), flush=True)

    # Quotas are per (band, source): with 779 handhq rows against the
    # reference's 238 at 4+ live, one flat quota would starve the PRIMARY
    # cell with secondary data.
    scale = wide_target / TARGETS[(4, REFERENCE_SOURCE)]
    targets = {k: max(1, int(round(v * scale))) for k, v in TARGETS.items()}
    print("  targets:", {"%d/%s" % k: v for k, v in sorted(targets.items())},
          flush=True)

    started = time.perf_counter()
    done = {k: 0 for k in targets}
    written = 0
    handle = open(out_path, "a", encoding="utf-8")
    for hid, index, live, street, source in pool:
        band = 3 if live == 3 else 4
        cell = (band, source)
        if cell not in targets or done[cell] >= targets[cell]:
            continue
        if all(done[k] >= t for k, t in targets.items()):
            break
        if minutes is not None and (time.perf_counter() - started) / 60 > minutes:
            print("time budget reached", flush=True)
            break

        hand = next(hand_db.query(db, "id = ?", (hid,)))
        picks, acts, real = decisions_in(hand)
        action = acts[index]
        stack = round(hand.row["eff_stack_bb"], 2)
        cards = deal(hand.board, hand.n_players, random.Random(index))
        cards.update(real)                      # real where the log shows them
        try:
            body, why, _ = request_for(hand, acts, index, stack, cards, post)
        except Exception as exc:                            # noqa: BLE001
            print("  walker %s on %s" % (type(exc).__name__, hid[:8]), flush=True)
            continue
        if body is None:
            continue
        body["hero_cards"] = cards[action.player]

        chosen = real_kind(action.kind)
        row = {"hand": hid, "i": index, "street": street, "live": live,
               "players": hand.n_players, "stack": stack,
               "facing": action.facing_bb > 1e-9, "real_kind": chosen,
               "source": hand.source}
        ok = True
        tables = {}
        for arm, iters in (("S", SHIPPED_ITERATIONS), ("W", WIDE_ITERATIONS)):
            t0 = time.perf_counter()
            status, js = post(dict(body, solve_iterations=iters))
            elapsed = time.perf_counter() - t0
            if status != 200:
                ok = False
                break
            echoed = js.get("solve_iterations")
            if echoed != iters:
                # M233: the constant is not the measurement. An arm that
                # was not actually solved at the budget it claims makes
                # every figure below meaningless, so this stops the run.
                raise SystemExit(
                    "arm %s asked for %d and the response echoed %r - the "
                    "budget is not being honoured" % (arm, iters, echoed))
            hero = js.get("hero") or {}
            hero_row = hero.get("strategy") or {}
            table = js.get("strategy") or {}
            if not hero_row:
                ok = False
                break
            tables[arm] = set(table)
            # The blind row is this arm's own average over the combos it
            # modelled. NOT frequency-weighted: M119's rule means combo
            # multiplicity already carries a class's composition, and rule
            # 3 is a PAIRED lift, so a shared weighting error cancels.
            row[arm] = mass_on(hero_row, chosen)
            row[arm + "_blind"] = card_blind(
                table, {k: 1.0 for k in table}, chosen)
            row[arm + "_aggression"] = aggression(hero_row)
            row[arm + "_iterations"] = echoed
            row[arm + "_seconds"] = round(elapsed, 3)
            row[arm + "_in_range"] = hero.get("in_range")
        if not ok:
            continue
        # Both arms must model the SAME range, or their blind rows are
        # averages over different supports and rule 3 compares two things.
        row["support_matches"] = tables["S"] == tables["W"]
        handle.write(json.dumps(row) + "\n")
        handle.flush()
        done[cell] += 1
        written += 1
        if written % 10 == 0:
            mins = (time.perf_counter() - started) / 60
            filled = " ".join("%d/%s:%d" % (b, s[:4], n)
                              for (b, s), n in sorted(done.items()))
            print("  %4d rows [%s] %5.1f min  last %s live=%d S=%.2fs W=%.2fs"
                  % (written, filled, mins, street, live,
                     row["S_seconds"], row["W_seconds"]), flush=True)
    handle.close()
    print("wrote %d rows to %s" % (written, out_path), flush=True)
    return 0


def main(argv=None) -> int:                              # pragma: no cover
    """Read the committed rows and apply the rule, or `--run` the arms."""
    import pathlib
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] == "--run":
        out = args[1]
        wide = int(args[2]) if len(args) > 2 else 400
        control = int(args[3]) if len(args) > 3 else 300
        minutes = float(args[4]) if len(args) > 4 else None
        return run(out, wide, control, minutes)

    source = pathlib.Path(args[0]) if args else (
        pathlib.Path(__file__).resolve().parents[2] / "tests" / "data"
        / "wide_pot_budget_m309.json")
    text = source.read_text()
    rows = ([json.loads(line) for line in text.splitlines() if line.strip()]
            if source.suffix == ".jsonl" else json.loads(text))
    summary = summarise(rows)
    print(json.dumps(summary, indent=1, default=float))
    print(json.dumps(verdict(summary), indent=1))
    return 0


if __name__ == "__main__":                                # pragma: no cover
    sys.exit(main())
