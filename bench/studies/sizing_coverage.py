"""What does having no bet size actually cost now? (audit R3, M302)

`BET_SIZING_COVERAGE_NOTE` tells a player that where all-in is the only
way to put money in, the PLAY is distorted and not just the size, and it
quotes two cases: a top pair that should bet about a third of the time
**checks 99%**, and a busted draw that should bet a third of the pot
**moves all in 99%**.

**Why it is stale.** Those came from M151, measured on the CHAINED river
at `FLOP_TO_RIVER_RAISE_SIZES = ()` - a street with no modelled bet size
at all. M174 made the river standalone and M213 gave it a
0.33/0.75/2.5 menu, so that configuration does not exist any more.

**And the note's population changed with it.** M260 narrowed the gate:
at an opening decision where even the smallest bet on the menu is
already the whole stack, the real game offers nothing smaller either, so
the note stays silent (189 of 303 firings). What is left fires almost
entirely FACING A BET, where the tree's single re-raise multiple does
not fit the stack - a limit of the model, which is what the note is
about. Its exposure is **0.7%** of real decisions, the lowest of the
audit's stale list.

**So the study is in two parts, and they answer different questions.**

* **`run` - HOW OFTEN it fires**, over real hands at short stacks, each
  decision the one facing the biggest bet relative to the stack. This is
  the honest rate and it is the only part that may be quoted as
  exposure.
* **`construct` - WHAT IT DOES when it fires**, over spots built to sit
  inside the gate: a short stack facing the largest bet the product
  itself models. Sampling real decisions for this does not work - 50
  short-stacked candidates produced **zero** firings - so the behaviour
  is measured on constructed spots and labelled as such. M252's rule
  cuts both ways here: a benchmark measures the population it
  generates, so a constructed population may describe the behaviour and
  may NOT describe how often a player meets it.

**Arms.** The same request twice, changing only the street's re-raise
multiple:

* `shipped` - as it ships.
* `sized` - the street's re-raise multiple lowered to `SMALLER_RERAISE`
  so that a sized raise FITS behind a short stack. **M233's trap lives
  here**: after the first entry, `raise_sizes` multiplies the PREVIOUS
  BET, not the pot, and a value <= 1.0 builds an illegal raise that
  every legality invariant happily accepts. 1.5 is a raise to one and a
  half times the bet faced - the smallest legal step this tree can
  express.

**PRE-REGISTERED READING RULE (fixed before either arm was run):**

1. A row counts only if the note FIRES on the shipped arm and the sized
   arm actually offers the new action. Rows are reported either way.
2. HEADLINE: the change in hero's all-in frequency when a size becomes
   available, and the mass hero puts on the new size. The note's claim
   that the play is distorted survives only if the all-in frequency
   falls at >= `MIN_SIGMA` over >= `MIN_ROWS` rows.
3. THE TWO NAMED CASES (a top pair checking 99%, a busted draw shoving
   99%) are re-read from this run's rows: each survives only if some row
   reproduces it within `EXAMPLE_TOLERANCE`. Otherwise the copy names
   whatever the measured extremes are, or names none.
4. The DIRECTION claim - "in both directions", value hands checking too
   much AND bluffs shoving too much - needs both halves. Split by hand
   strength at `STRONG_QUARTILE`: strong hands must check MORE on the
   shipped arm, weak hands must shove MORE, each at >= `MIN_SIGMA`.
   Whichever half fails leaves the copy.
5. Nulls are results, and the copy records the configuration.

    python -m bench.studies.sizing_coverage run rows.jsonl
    python -m bench.studies.sizing_coverage rows.jsonl
"""
from __future__ import annotations

import json
import math
import os
import statistics
import sys

MIN_SIGMA = 2.0
MIN_ROWS = 6
STRONG_QUARTILE = 0.75
EXAMPLE_TOLERANCE = 0.10
#: A raise to 1.5x the bet faced. See M233 in the docstring above - this
#: is a multiple of the PREVIOUS BET, and anything <= 1.0 is illegal.
SMALLER_RERAISE = 1.5
SPOTS = int(os.environ.get("SIZING_SPOTS", 40))
SEED = int(os.environ.get("SIZING_SEED", 302))
#: Heroes drawn per (board, stack) in the constructed arm.
HEROES_PER_SPOT = int(os.environ.get("SIZING_HEROES", 5))


def all_in_mass(strategy: dict) -> float:
    return sum(v for k, v in strategy.items() if k.startswith("all_in"))


def check_mass(strategy: dict) -> float:
    return sum(v for k, v in strategy.items() if k.split(":")[0] == "call_or_check")


def sized_mass(strategy: dict) -> float:
    """Mass on any SIZED raise - the action the shipped tree cannot offer."""
    return sum(v for k, v in strategy.items() if k.startswith("raise:"))


def smaller_reraise_menu(sizes: tuple, multiple: float = None) -> tuple:
    """`sizes` with the SECOND entry replaced - hero's own raise.

    `raise_sizes` is (opening menu, second raise, third raise, ...), and
    after the first entry each one multiplies the PREVIOUS BET rather
    than the pot (M233). Hero facing villain's opening bet is making
    raise number TWO, so that is the entry that decides whether a sized
    raise fits. Replacing the LAST entry instead - the third raise -
    left 40 of 50 constructed spots with no size offered, because the
    knob being turned was not the one the node reads.
    """
    multiple = SMALLER_RERAISE if multiple is None else multiple
    if len(sizes) < 2:
        return tuple(sizes) + (multiple,)
    return (sizes[0], multiple) + tuple(sizes[2:])


def usable(rows: list) -> list:
    """Rule 1: the note fired, and the sized arm really offered a size."""
    return [r for r in rows if r.get("note_fired") and r.get("sized_offered")]


def _paired(values: list) -> dict:
    if len(values) < 2:
        return {"n": len(values), "mean": values[0] if values else None, "sigma": None}
    mean = statistics.mean(values)
    sem = statistics.stdev(values) / math.sqrt(len(values))
    return {"n": len(values), "mean": mean, "sigma": (mean / sem) if sem else None}


def headline(rows: list) -> dict:
    """Rule 2. Paired: the same request, one knob apart."""
    kept = usable(rows)
    return {
        "n": len(kept),
        "all_in_change": _paired([r["sized_all_in"] - r["shipped_all_in"] for r in kept]),
        "check_change": _paired([r["sized_check"] - r["shipped_check"] for r in kept]),
        "new_size_used": _paired([r["sized_sized"] for r in kept]),
        "shipped_all_in": statistics.mean([r["shipped_all_in"] for r in kept]) if kept else None,
        "sized_all_in": statistics.mean([r["sized_all_in"] for r in kept]) if kept else None,
    }


def named_cases(rows: list) -> dict:
    """Rule 3: does any row reproduce either published example?"""
    kept = usable(rows)
    strong_checks = [r for r in kept
                     if r["percentile"] >= STRONG_QUARTILE
                     and r["shipped_check"] >= 1.0 - EXAMPLE_TOLERANCE
                     and r["sized_check"] < 1.0 - EXAMPLE_TOLERANCE]
    weak_shoves = [r for r in kept
                   if r["percentile"] < STRONG_QUARTILE
                   and r["shipped_all_in"] >= 1.0 - EXAMPLE_TOLERANCE
                   and r["sized_all_in"] < 1.0 - EXAMPLE_TOLERANCE]
    return {
        "strong_hand_checks_almost_always": len(strong_checks),
        "weak_hand_shoves_almost_always": len(weak_shoves),
        "worst_check": max((r["shipped_check"] for r in kept), default=None),
        "worst_all_in": max((r["shipped_all_in"] for r in kept), default=None),
    }


def direction(rows: list) -> dict:
    """Rule 4: both halves, or the "in both directions" wording goes."""
    kept = usable(rows)
    strong = [r for r in kept if r["percentile"] >= STRONG_QUARTILE]
    weak = [r for r in kept if r["percentile"] < STRONG_QUARTILE]
    return {
        # Strong hands: does the shipped arm CHECK more than the sized one?
        "strong_over_checks": _paired([r["shipped_check"] - r["sized_check"] for r in strong]),
        # Weak hands: does the shipped arm SHOVE more?
        "weak_over_shoves": _paired([r["shipped_all_in"] - r["sized_all_in"] for r in weak]),
    }


def _holds(cell: dict) -> bool:
    return bool(cell.get("n", 0) >= MIN_ROWS and cell.get("mean") is not None
                and cell["mean"] > 0 and cell.get("sigma") is not None
                and cell["sigma"] >= MIN_SIGMA)


def summarise(rows: list) -> dict:
    return {"n_priced": len(rows), "n_usable": len(usable(rows)),
            "headline": headline(rows), "named_cases": named_cases(rows),
            "direction": direction(rows)}


def verdict(summary: dict) -> dict:
    change = summary["headline"]["all_in_change"]
    distorts = bool(change.get("n", 0) >= MIN_ROWS and change.get("sigma") is not None
                    and abs(change["sigma"]) >= MIN_SIGMA and change["mean"] < 0)
    return {
        "play_is_distorted": distorts,
        "strong_hands_over_check": _holds(summary["direction"]["strong_over_checks"]),
        "weak_hands_over_shove": _holds(summary["direction"]["weak_over_shoves"]),
        "both_directions": (_holds(summary["direction"]["strong_over_checks"])
                            and _holds(summary["direction"]["weak_over_shoves"])),
        "named_cases_reproduce": (
            summary["named_cases"]["strong_hand_checks_almost_always"] > 0
            and summary["named_cases"]["weak_hand_shoves_almost_always"] > 0),
    }


def main(argv=None) -> int:                              # pragma: no cover
    import pathlib
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] == "run":
        return _run(args[1])
    if args and args[0] == "construct":
        return _construct(args[1])
    source = pathlib.Path(args[0]) if args else (
        pathlib.Path(__file__).resolve().parents[2] / "tests" / "data"
        / "sizing_coverage_m302.jsonl")
    rows = [json.loads(line) for line in source.read_text().splitlines() if line.strip()]
    summary = summarise(rows)
    print(json.dumps({**summary, "verdict": verdict(summary)}, indent=1, default=str))
    return 0


def _run(out_path: str) -> int:                          # pragma: no cover
    """Seek real low-SPR facing-a-bet nodes where the note fires."""
    import random
    import time

    from fastapi.testclient import TestClient
    from api import config as cfg
    from api.main import app
    from bench import hand_db
    from bench.real_replay import DEFAULT_WHERE, deal, request_for, sample_hands
    from bench.server_warmup import clear_postflop_caches, warm_multiway
    from bench.studies.facing_cost import live_after_preflop
    from poker_solver.cards import parse_cards
    from poker_solver.combos import HandCombo
    from poker_solver.hand_strength import strength_percentile

    street_sizes = {
        "flop": ("FLOP_RAISE_SIZES", "FLOP_MAX_RAISES"),
        "turn": ("TURN_STANDALONE_RAISE_SIZES", "TURN_STANDALONE_MAX_RAISES"),
        "river": ("RIVER_STANDALONE_RAISE_SIZES", "RIVER_STANDALONE_MAX_RAISES"),
    }

    client = TestClient(app)
    warm_multiway(depths=(100.0,), table_sizes=(6,))

    def post(body):
        r = client.post("/advise", json=body)
        return r.status_code, (r.json() if r.status_code == 200 else {})

    db = hand_db.connect()
    rng = random.Random(SEED)
    written = 0
    looked = 0
    skips = {"multiway_pot": 0, "no_facing": 0, "unrepresentable": 0,
             "refused": 0, "note_silent": 0, "no_size_offered": 0}
    # Short stacks are where a sized re-raise stops fitting, which is the
    # only place this note still fires. Drawn from real hands, not
    # invented - M263's rule about benchmark stacks.
    where = DEFAULT_WHERE + " AND eff_stack_bb <= 40"
    with open(out_path, "w") as fh:
        for hand in sample_hands(db, 20000, SEED, where=where):
            if written >= SPOTS:
                break
            acts = [a for a in hand.streets() if a.kind != "show"]
            if live_after_preflop(acts, hand.n_players) != 2:
                skips["multiway_pot"] += 1
                continue
            facing = [i for i, a in enumerate(acts)
                      if a.street in street_sizes and (a.facing_bb or 0) > 1e-9]
            if not facing:
                skips["no_facing"] += 1
                continue
            # The note fires where a sized RE-RAISE stops fitting, so the
            # decision to ask about is the one facing the biggest bet
            # relative to the stack - not the street's first, which
            # usually has a full stack behind it and a re-raise that fits.
            # Taking `facing[0]` found zero firings in seventeen minutes.
            index = max(facing, key=lambda i: (acts[i].facing_bb or 0.0)
                        / max(hand.row["eff_stack_bb"], 1e-9))
            street = acts[index].street
            cards = deal(hand.board, hand.n_players, rng)
            body, _why, _worst = request_for(
                hand, acts, index, round(hand.row["eff_stack_bb"], 2), cards, post=post)
            if body is None:
                skips["unrepresentable"] += 1
                continue
            body["hero_cards"] = cards[acts[index].player]
            looked += 1
            clear_postflop_caches()
            status, js = post(body)
            hero = (js.get("hero") or {}).get("strategy") if status == 200 else None
            if not hero or len(js.get("positions") or []) != 2:
                skips["refused"] += 1
                continue
            if "bet-sizing-coverage" not in set(js.get("advisory_notes") or []):
                skips["note_silent"] += 1
                if looked % 10 == 0:
                    print("  looked at", looked, json.dumps(skips), flush=True)
                continue
            # The sized arm: the same request with a re-raise multiple
            # that fits. Captured and restored, never hardcoded.
            name, _max_name = street_sizes[street]
            original = getattr(cfg, name)
            try:
                setattr(cfg, name, smaller_reraise_menu(original))
                clear_postflop_caches()
                status_b, js_b = post(body)
            finally:
                setattr(cfg, name, original)
            sized_hero = (js_b.get("hero") or {}).get("strategy") if status_b == 200 else None
            if not sized_hero:
                skips["refused"] += 1
                continue
            offered = sized_mass(sized_hero) > 0.0 or any(
                a.startswith("raise:") for a in sized_hero)
            if not offered:
                skips["no_size_offered"] += 1
                continue
            board = body["board"] + body.get("turn_card", "") + body.get("river_card", "")
            fh.write(json.dumps({
                "hand": hand.id, "i": index, "street": street, "board": board,
                "hero": body["hero_cards"],
                "percentile": strength_percentile(
                    HandCombo(*parse_cards(body["hero_cards"])),
                    tuple(parse_cards(board))),
                "note_fired": True, "sized_offered": True,
                "shipped_all_in": all_in_mass(hero), "sized_all_in": all_in_mass(sized_hero),
                "shipped_check": check_mass(hero), "sized_check": check_mass(sized_hero),
                "sized_sized": sized_mass(sized_hero),
                "pot": js.get("pot"), "stack": js.get("max_affordable_bb"),
                "request": body,
            }) + "\n")
            fh.flush()
            written += 1
            print(" ", written, street, time.strftime("%H:%M:%S"), flush=True)
    print("DONE", written, "of", looked, "looked at", json.dumps(skips), flush=True)
    return 0




#: Boards for the constructed arm, drawn from real flops in the hand
#: store's own distribution rather than invented - dry, wet, paired and
#: connected, so the behaviour is not read off one texture (M221's split
#: is what happens when it is).
CONSTRUCTED_BOARDS = (
    "Kd7c2h", "9c8c5h", "AsKd4c", "7h7d2s", "Qs9h3d",
    "Jc9c8d", "As2s2d", "Td6h4c", "KhQhJs", "5c4d3h",
)
#: Stacks short enough that a re-raise stops fitting. Real short stacks
#: exist (the store holds 21,109 heads-up hands and many at 20-40bb);
#: these are the depths, not a claim about frequency.
CONSTRUCTED_STACKS = (12.0, 16.0, 20.0, 25.0, 30.0)


def _construct(out_path: str) -> int:                    # pragma: no cover
    """Build spots INSIDE the gate and measure what the missing size does."""
    import itertools
    import json as _json
    import random

    from fastapi.testclient import TestClient
    from api import config as cfg
    from api.main import app
    from bench.server_warmup import clear_postflop_caches
    from poker_solver.cards import parse_cards
    from poker_solver.combos import HandCombo
    from poker_solver.hand_strength import strength_percentile

    street_sizes = {"flop": "FLOP_RAISE_SIZES",
                    "turn": "TURN_STANDALONE_RAISE_SIZES",
                    "river": "RIVER_STANDALONE_RAISE_SIZES"}
    client = TestClient(app)

    def post(body):
        r = client.post("/advise", json=body)
        return r.status_code, (r.json() if r.status_code == 200 else {})

    rng = random.Random(SEED)
    deck = [rank + suit for rank in "23456789TJQKA" for suit in "cdhs"]
    written = 0
    skips = {"refused": 0, "note_silent": 0, "no_size_offered": 0, "no_bet": 0}
    with open(out_path, "w") as fh:
        for board, stack, which in itertools.product(
                CONSTRUCTED_BOARDS, CONSTRUCTED_STACKS, range(HEROES_PER_SPOT)):
            if written >= SPOTS:
                break
            used = {board[i:i + 2] for i in range(0, len(board), 2)}
            # A fresh hero per pass, so the sample spans the strength
            # range rather than one draw per board - rule 4 splits on it.
            free = [c for c in deck if c not in used]
            rng.shuffle(free)
            hero_cards = free[0] + free[1]
            base = {"stack_bb": stack, "players": 2, "board": board,
                    "preflop_action_path": ["raise", "call_or_check"],
                    "hero_cards": hero_cards}
            # Villain's bet is drawn from the product's OWN modelled
            # sizes (M209's rule), biggest first, because the gate opens
            # when a re-raise over it no longer fits.
            clear_postflop_caches()
            status, opening = post(base)
            # The LARGEST modelled size is the all-in itself, and naming
            # it as `raise:` is a 422 - the tree calls that action
            # `all_in`. Villain's bet is therefore the largest size
            # strictly below the stack, which is also the one most likely
            # to leave a re-raise unaffordable. Picking `sizes[0]` refused
            # 50 of 50 constructed spots.
            cap = opening.get("max_affordable_bb")
            sizes = sorted((s for s in (opening.get("modelled_bet_sizes") or [])
                            if cap is None or s < cap - 1e-9), reverse=True)
            if status != 200 or not sizes:
                skips["no_bet"] += 1
                continue
            body = dict(base, flop_action_path=["raise:%.2f" % sizes[0]])
            clear_postflop_caches()
            status, js = post(body)
            hero_row = (js.get("hero") or {}).get("strategy") if status == 200 else None
            if not hero_row:
                skips["refused"] += 1
                continue
            if "bet-sizing-coverage" not in set(js.get("advisory_notes") or []):
                skips["note_silent"] += 1
                continue
            name = street_sizes["flop"]
            original = getattr(cfg, name)
            try:
                setattr(cfg, name, smaller_reraise_menu(original))
                clear_postflop_caches()
                status_b, js_b = post(body)
            finally:
                setattr(cfg, name, original)
            sized_row = (js_b.get("hero") or {}).get("strategy") if status_b == 200 else None
            if not sized_row:
                skips["refused"] += 1
                continue
            if not any(a.startswith("raise:") for a in sized_row):
                skips["no_size_offered"] += 1
                continue
            fh.write(_json.dumps({
                "constructed": True, "street": "flop", "board": board,
                "hero": hero_cards, "stack": stack,
                "percentile": strength_percentile(
                    HandCombo(*parse_cards(hero_cards)), tuple(parse_cards(board))),
                "note_fired": True, "sized_offered": True,
                "shipped_all_in": all_in_mass(hero_row),
                "sized_all_in": all_in_mass(sized_row),
                "shipped_check": check_mass(hero_row),
                "sized_check": check_mass(sized_row),
                "sized_sized": sized_mass(sized_row),
                "pot": js.get("pot"), "bet_faced": sizes[0],
                "request": body,
            }) + "\n")
            fh.flush()
            written += 1
            print(" ", written, board, stack, flush=True)
    print("DONE constructed", written, _json.dumps(skips), flush=True)
    return 0


if __name__ == "__main__":                                # pragma: no cover
    sys.exit(main())
