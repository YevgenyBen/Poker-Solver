"""The two-live preflop cell the warning is SILENT on (audit R5, M287).

`_is_two_live_multiway_preflop` fired only when the player owed at least
3.0bb (`PREFLOP_TWO_LIVE_RERAISE_MIN_TO_CALL_BB`) - a three-bet or deeper.
Below it - the big blind defending one open, blind against blind - it said
nothing, and nobody had established whether that silence was earned.

**Result (M287): WIDEN.** The control passed on the whole re-raise cell
(+0.447, 16.4 sigma) and the silent weak band continued 0.421 against the
reference's 0.207, +0.214 at 8.70 sigma, halves 5.66 / 6.63. All of it is
the big blind facing a single open (1.5 owed); the blind completion (0.5)
is TIGHTER than the reference. The gate now fires from 1.0bb, and
`shipped_figures` reduces the committed rows to the constants the new copy
quotes.

M253 tried, by asking how wide an opponent must be for a weak hand's call
to break even, and could not settle it: the verdict flipped at a
realisation factor of ~0.87, inside the plausible 0.80-0.90 band. That
method needs an assumption about equity realisation. This one does not:
it asks what a strong outside player ACTUALLY did in the same cell.

**Method.** Every preflop decision the published six-handed poker AI in
the hand store made with exactly two players live and something to call,
split by what OUR tree says is owed at that node - which is what the gate
reads, and not the same as the real bet, since the engine models its own
open size. For each, our probability of continuing against whether that
player continued, by preflop strength band.

**PRE-REGISTERED READING RULE (fixed before any measurement):**

1. The GATED cell (owed >= the gate) is the positive control. It must
   show us continuing MORE than the reference with weak hands - M282's
   finding, reproduced from outside. If it does not, the method is not
   measuring what it claims and nothing below is read.
2. In the SILENT cell, the gate WIDENS only if our weak-band excess over
   the reference is at least 3 sigma AND holds its sign with at least 2
   sigma in both split halves.
3. Otherwise the silence is EARNED and M253's open question is closed -
   not "unresolved", because this instrument does not depend on the
   assumption M253's did.
4. The reference is one strong player's frequencies, not a converged
   solve: a figure here says "further from strong play", never "wrong".

**AMENDMENT (written after COUNTING the population, before any `/advise`
call - no answer of ours had been seen).** Pluribus almost never reaches
a three-bet with a weak hand, so the gated cell holds **5** weak-band
rows of 347, and rule 1 would fail on power alone - reporting "the
instrument is broken" when it is merely unfed. So when the gated weak
band has fewer than `CONTROL_MIN_WEAK` rows, the control reads the WHOLE
gated cell instead (same test: excess > 0 at >= 2 sigma). M282 predicts
that too - it measured the over-continuing at the node, and premiums
continue on both sides. The weak-band control is still reported, so the
amendment is visible in every result.

    python -m bench.studies.two_live_silent
"""
from __future__ import annotations

import json
import statistics
import sys

RANKS = "AKQJT98765432"
#: The weakest quarter of hands by combo-weighted equity against a random
#: hand - the band the defect lives in (72o, 83o, 92o, T2o, 62o are all
#: inside it).
WEAK_BAND = 0.25
#: Below this many weak-band rows the gated cell cannot carry the control
#: on its own (see the AMENDMENT above).
CONTROL_MIN_WEAK = 20


def class_of(cards: str) -> str:
    """'7c2d' -> '72o', 'AhKh' -> 'AKs', 'TsTd' -> 'TT'."""
    r1, s1, r2, s2 = cards[0], cards[1], cards[2], cards[3]
    if RANKS.index(r1) > RANKS.index(r2):
        r1, r2, s1, s2 = r2, r1, s2, s1
    if r1 == r2:
        return r1 + r2
    return r1 + r2 + ("s" if s1 == s2 else "o")


def preflop_percentiles() -> dict:
    """{class: share of all COMBOS it beats}, from the engine's own cached
    169x169 heads-up equity table - equity against a uniformly random hand,
    weighted by how many combos each opponent class has."""
    import numpy as np
    from poker_solver.equity import get_equity_table
    from poker_solver.starting_hands import all_starting_hands

    hands = all_starting_hands()
    table = get_equity_table(hands=hands)
    combos = np.array([h.combo_count for h in hands], dtype=float)
    vs_random = table @ combos / combos.sum()
    order = np.argsort(vs_random)
    below, pct = 0.0, {}
    for i in order:
        pct[str(hands[i])] = (below + combos[i] / 2) / combos.sum()
        below += combos[i]
    return pct


def _owed_in_our_tree(players: int, path: list, stack_bb: float):
    """What OUR tree says the actor owes after `path`, and how many are
    live - the two quantities the gate reads. None if the path leaves it."""
    from poker_solver.game_tree import DecisionNode, GameConfig, build_game_tree
    from api import config as cfg

    node = build_game_tree(GameConfig(
        positions=tuple(cfg.MULTIWAY_TABLE_CONFIGS[players]["positions"]),
        stack_bb=stack_bb))
    for kind in path:
        action = next((a for a in node.legal_actions if a.kind == kind), None)
        if action is None:
            return None
        node = node.children[action]
    if not isinstance(node, DecisionNode):
        return None
    live = sum(1 for p in node.invested if p not in node.folded)
    owed = max(node.invested.values()) - node.invested[node.player_to_act]
    return owed, live


def decisions(db, player: str = "Pluribus", stack_bb: float = 100.0) -> list:
    """Every preflop decision `player` made with two live and something
    owed, as the request our API would receive, plus what they did."""
    from bench import hand_db
    from bench.real_replay import request_for

    out = []
    for hand in hand_db.query(db, "source = 'pluribus'"):
        seats = json.loads(hand.row["players"])
        if player not in seats or hand.n_players < 3:
            continue
        me = seats.index(player)
        acts = [a for a in hand.streets() if a.kind != "show"]
        for i, a in enumerate(acts):
            if a.street != "preflop" or a.player != me:
                continue
            body, why, _ = request_for(hand, acts, i, stack_bb, hand.hole_cards,
                                       post=lambda b: (500, {}))
            if body is None:
                continue
            state = _owed_in_our_tree(hand.n_players, body["preflop_action_path"], stack_bb)
            if state is None or state[1] != 2 or state[0] <= 1e-9:
                continue
            body["hero_cards"] = hand.hole_cards[me]
            out.append({"body": body, "owed": round(state[0], 2),
                        "hand_class": class_of(hand.hole_cards[me]),
                        "reference_continued": a.kind != "fold",
                        "hand": hand.id, "i": i})
    return out


def measure(decs: list, post) -> list:
    rows = []
    for d in decs:
        status, js = post(d["body"])
        if status != 200 or not (js.get("hero") or {}).get("strategy"):
            continue
        row = js["hero"]["strategy"]
        ours = 1.0 - sum(v for k, v in row.items() if k.split(":")[0] == "fold")
        rows.append({k: d[k] for k in ("owed", "hand_class", "reference_continued",
                                       "hand", "i")} | {"ours_continue": ours})
    return rows


def _paired(rows):
    """Mean of (our continue probability - reference continued), its sigma."""
    d = [r["ours_continue"] - (1.0 if r["reference_continued"] else 0.0) for r in rows]
    if len(d) < 2:
        return (statistics.mean(d) if d else None), None, len(d)
    se = statistics.stdev(d) / len(d) ** 0.5
    mean = statistics.mean(d)
    return mean, (mean / se if se else float("inf")), len(d)


def summarise(rows: list, gate_bb: float, percentiles: dict) -> dict:
    """Per cell, the weak band's paired excess and its split halves. Pure."""
    out = {}
    for cell, keep in (("gated", lambda r: r["owed"] >= gate_bb),
                       ("silent", lambda r: r["owed"] < gate_bb)):
        cell_rows = sorted((r for r in rows if keep(r)), key=lambda r: (r["hand"], r["i"]))
        weak = [r for r in cell_rows if percentiles[r["hand_class"]] < WEAK_BAND]
        mean, sigma, n = _paired(weak)
        halves = [_paired(weak[k::2]) for k in (0, 1)]
        all_mean, all_sigma, _ = _paired(cell_rows)
        out[cell] = {
            "n": len(cell_rows), "weak_n": n, "weak_excess": mean, "weak_sigma": sigma,
            "all_excess": all_mean, "all_sigma": all_sigma,
            "half_sigmas": [h[1] for h in halves],
            "ours_weak": statistics.mean(r["ours_continue"] for r in weak) if weak else None,
            "reference_weak": (statistics.mean(1.0 if r["reference_continued"] else 0.0
                                               for r in weak) if weak else None),
        }
    return out


def verdict(summary: dict) -> str:
    """The pre-registered rule, applied mechanically."""
    g, s = summary["gated"], summary["silent"]
    # The AMENDMENT: an unfed weak band hands the control to the whole cell.
    key = "weak" if g["weak_n"] >= CONTROL_MIN_WEAK else "all"
    excess, sigma = g[f"{key}_excess"], g[f"{key}_sigma"]
    if not (excess and excess > 0 and (sigma or 0) >= 2):
        return "CONTROL FAILED: the gated cell does not reproduce M282 from outside"
    halves_hold = all(h is not None and h >= 2 for h in s["half_sigmas"])
    if s["weak_excess"] and s["weak_excess"] > 0 and (s["weak_sigma"] or 0) >= 3 and halves_hold:
        return "WIDEN: the silent cell carries the defect too"
    return "EARNED: the silent cell does not carry the defect"


def shipped_figures(rows: list, gate_bb: float, reraise_bb: float, percentiles: dict) -> dict:
    """The single-raise cell the widened gate added - owed in
    [gate, reraise) - reduced to the constants its copy quotes. Pure."""
    weak = [r for r in rows if gate_bb <= r["owed"] < reraise_bb
            and percentiles[r["hand_class"]] < WEAK_BAND]
    return {
        "PREFLOP_TWO_LIVE_ONE_RAISE_CONTINUES":
            round(statistics.mean(r["ours_continue"] for r in weak), 3),
        "PREFLOP_TWO_LIVE_ONE_RAISE_REFERENCE":
            round(statistics.mean(1.0 if r["reference_continued"] else 0.0 for r in weak), 3),
        "PREFLOP_TWO_LIVE_ONE_RAISE_DECISIONS": len(weak),
    }


def main(argv=None) -> int:                              # pragma: no cover
    from fastapi.testclient import TestClient
    from api import config as cfg
    from api.main import app
    from bench import hand_db

    client = TestClient(app)

    def post(body):
        r = client.post("/advise", json=body)
        return r.status_code, (r.json() if r.status_code == 200 else {})

    rows = measure(decisions(hand_db.connect()), post)
    summary = summarise(rows, cfg.PREFLOP_TWO_LIVE_RERAISE_MIN_TO_CALL_BB, preflop_percentiles())
    print(json.dumps(summary, indent=1))
    print(verdict(summary))
    print(json.dumps(shipped_figures(rows, cfg.PREFLOP_TWO_LIVE_MIN_TO_CALL_BB,
                                     cfg.PREFLOP_TWO_LIVE_RERAISE_MIN_TO_CALL_BB,
                                     preflop_percentiles()), indent=1))
    return 0


if __name__ == "__main__":                                # pragma: no cover
    sys.exit(main())
