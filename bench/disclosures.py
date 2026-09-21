"""Every figure a player is shown, and where it came from (M285).

**Why this exists.** The 2026-09-18 audit's F58: the eighteen
`test_*_quotes_its_own_measurement` tests pin each warning's copy to a
constant, and nothing pins the constant to reality. Three live failures
of exactly that shape, each passing the full suite while wrong:

- M281 improved the two-live defect and left its copy saying 98%.
- M282 found the replacement figure was taken from a hand-written list.
- M285 found `SIZING_CAVEAT_REASON` still quoting M251's "98% of the time
  (measured over 22 spots)" through BOTH of those corrections - the same
  figure typed into a second string - and its test asserted the literal
  "98%", so correcting the copy would have failed the build. And
  `MULTIWAY_BET_NOTE` said "47%" after M269 measured 41% on the same 579
  decisions and shipped the change.

A figure goes stale in two ways, and this module makes both visible:

1. **A number typed into copy with no constant behind it.** Updating the
   constant cannot reach it. `unsourced_numbers` finds every such number,
   and `tests/test_disclosures.py` fails the build on any that is not
   either rendered from a registered constant or listed in the entry's
   `literals` WITH its source.
2. **A figure measured under a configuration that no longer ships.**
   M232's rule: a warning may not quote a figure taken at a width or a
   population the product does not run. `current=False` records that, and
   `superseded_by` names what shipped afterwards. This is a WORKLIST, not
   a test failure - re-measuring is a study each, and hiding the list
   would be worse than carrying it.

`study` is the repository path of a script that re-derives the figure.
Before M285 every study behind every shipped figure lived in a session
scratchpad under the system temp directory, where the OS may delete it;
`provenance` records the last-known script name where one survived.

Run `python -m bench.disclosures` for the table.

Instrument only: `bench/` is never imported by what ships.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: A number as it appears in copy: 579, 0.27, 47%, 2.4. Not glued to a
#: letter, so "72o" still yields 72 (a hand name, sourced as a literal)
#: but "M251" yields nothing.
NUMBER = re.compile(r"(?<![A-Za-z0-9.])\d+(?:\.\d+)?%?")

ENGINE, REFERENCE, HAND_STORE = "engine", "reference", "hand_store"


@dataclass(frozen=True)
class Disclosure:
    copy: str                        # config name of the user-facing text
    milestone: str                   # where its figures were last measured
    instrument: str                  # ENGINE / REFERENCE / HAND_STORE
    constants: tuple = ()            # config names whose values it quotes
    literals: dict = field(default_factory=dict)   # number -> its source
    current: bool = True             # measured at the configuration that ships?
    superseded_by: str = ""          # what shipped after, if not current
    shown: bool = True               # can it reach a player at all?
    study: str | None = None         # repo path that re-derives it
    provenance: str | None = None    # last-known scratch script


def renderings(value) -> set:
    """Every way a constant's value is written in copy.

    Deliberately TIGHT: a fraction renders as a percentage or its own
    decimals, never as a rounded whole number, so 0.9661 cannot 'explain'
    a stray 1. A loose matcher would source numbers by coincidence and the
    guard would be dead (M214).
    """
    out = set()
    if isinstance(value, bool):
        return out
    if isinstance(value, int):
        # Integers are exact, and several are stored as percentages
        # (`FLOP_UNDER_FOLD_AGREEMENT_PCT = 71`), so "71%" is theirs too.
        return {str(value), f"{value}%"}
    if isinstance(value, float):
        out |= {f"{value:.1f}", f"{value:.2f}", f"{value:.3f}", f"{value:.4f}"}
        if value >= 1:
            out |= {str(round(value)), f"{round(value)}%", repr(value)}
        if 0 <= value <= 1:
            out |= {f"{round(value * 100)}%", f"{value * 100:.1f}%",
                    str(round(value * 100))}
        return out
    if isinstance(value, (tuple, list)):
        for item in value:
            out |= renderings(item)
    return out


def copy_text(cfg, name: str) -> str:
    if "[" in name:
        base, key = name[:-1].split("[")
        return getattr(cfg, base)[int(key)]
    return getattr(cfg, name)


def unsourced_numbers(entry: Disclosure, cfg) -> list:
    """Numbers in the copy that no registered constant renders and no
    literal accounts for."""
    sourced = set(entry.literals)
    for name in entry.constants:
        sourced |= renderings(getattr(cfg, name))
    return [n for n in NUMBER.findall(copy_text(cfg, entry.copy)) if n not in sourced]


_TWO_LIVE = ("PREFLOP_TWO_LIVE_FOUR_BET_CONTINUES", "PREFLOP_TWO_LIVE_FOUR_BET_NODES",
             "PREFLOP_TWO_LIVE_THREE_BET_CONTINUES", "PREFLOP_TWO_LIVE_THREE_BET_NODES",
             "PREFLOP_TWO_LIVE_NODES", "PREFLOP_MANY_LIVE_TRASH_CONTINUES")
_ONE_RAISE = ("PREFLOP_TWO_LIVE_ONE_RAISE_CONTINUES", "PREFLOP_TWO_LIVE_ONE_RAISE_REFERENCE",
              "PREFLOP_TWO_LIVE_ONE_RAISE_DECISIONS")
_TWO_LIVE_PRICE = {
    "72": "hand name (72o), not a measurement",
    "27%": "M251: the equity the call needs - 9.00 owed into a 24.00 pot, read off the tree",
    "23%": "M251: how wide a four-bet range must be for 72o to break even, from our equity table",
    "13%": "M280: the four-bet range the engine models at this node",
}

REGISTRY = (
    # -- current: measured at the configuration that ships ---------------
    Disclosure(
        "PREFLOP_TWO_LIVE_FOUR_BET_REASON", "M282", ENGINE, _TWO_LIVE,
        literals=_TWO_LIVE_PRICE, study="bench/studies/two_live.py",
        provenance="a18_sample.py"),
    Disclosure(
        "PREFLOP_TWO_LIVE_THREE_BET_REASON", "M282", ENGINE, _TWO_LIVE,
        literals={"72": "hand name (72o), not a measurement"},
        study="bench/studies/two_live.py", provenance="a18_sample.py"),
    Disclosure(
        "PREFLOP_TWO_LIVE_REASON", "M282", ENGINE, _TWO_LIVE,
        literals=_TWO_LIVE_PRICE, study="bench/studies/two_live.py",
        provenance="a18_sample.py"),
    Disclosure(
        "PREFLOP_TWO_LIVE_ONE_RAISE_REASON", "M287", HAND_STORE, _ONE_RAISE,
        study="bench/studies/two_live_silent.py"),
    Disclosure(
        "PREFLOP_FOLD_SEED_8_REASON", "M289", ENGINE, ("PREFLOP_FOLD_SEED_MOVE_8",),
        literals={"8": "table size"}, study="bench/studies/preflop_fold_seeds.py"),
    Disclosure(
        "PREFLOP_FOLD_SEED_9_REASON", "M289", ENGINE, ("PREFLOP_FOLD_SEED_MOVE_9",),
        literals={"9": "table size"}, study="bench/studies/preflop_fold_seeds.py"),
    Disclosure(
        "FLOP_UNDER_FOLD_NOTE", "M272", REFERENCE,
        ("FLOP_UNDER_FOLD_ROWS", "FLOP_UNDER_FOLD_GAP",
         "FLOP_UNDER_FOLD_SMALL_BET_GAP", "FLOP_UNDER_FOLD_AGREEMENT_PCT"),
        provenance="a6_flop_facing.py"),
    Disclosure(
        "RIVER_UNDER_FOLD_NOTE", "M259", REFERENCE,
        ("RIVER_UNDER_FOLD_COST_BB", "RIVER_UNDER_FOLD_SILENT_COST_BB",
         "RIVER_UNDER_FOLD_SPOTS", "RIVER_UNDER_FOLD_REFERENCE_FOLDS",
         "RIVER_UNDER_FOLD_WE_FOLD", "RIVER_UNDER_FOLD_UNDER_FOLDED"),
        literals={"2%": "M259: 0.27 bb as a share of the pot (1.79%), rounded"},
        provenance="m259_river_price.py"),
    Disclosure(
        "TURN_SHOVE_NOTE", "M260/M277", REFERENCE,
        ("TURN_SHOVE_ROWS", "TURN_SHOVE_BOARDS", "TURN_SHOVE_REFERENCE_NEVER",
         "TURN_SHOVE_COST_BB", "TURN_SHOVE_COST_PCT_POT"),
        provenance="m260_milestone.py"),
    Disclosure(
        "TURN_INDEPENDENT_NOTE", "M260", REFERENCE,
        ("TURN_INDEPENDENT_GAP_MEDIAN", "TURN_INDEPENDENT_WITHIN_TEN",
         "TURN_INDEPENDENT_ROWS", "TURN_INDEPENDENT_HAND_PICKED_MEDIAN",
         "TURN_INDEPENDENT_GAP_SIGNED", "TURN_INDEPENDENT_SPOTS_BETTING_MORE",
         "TURN_INDEPENDENT_SPOTS"),
        literals={"10": "the agreement threshold, 10 points",
                  "25": "M232: the narrowest range width swept",
                  "140": "M232: the widest range width swept (the shipped turn cap)"},
        provenance="m222_turn.py"),
    Disclosure(
        "LOW_CONFIDENCE_TABLE_SIZES[7]", "M270", ENGINE,
        literals={"7": "table size", "0.44": "M270: AA's jam, seed-to-seed spread at 3,000 iterations"}),
    Disclosure(
        "LOW_CONFIDENCE_TABLE_SIZES[8]", "M270", ENGINE,
        literals={"8": "table size", "0.35": "M270: AA's jam, seed-to-seed spread at 12,000 iterations"}),
    Disclosure(
        "LOW_CONFIDENCE_TABLE_SIZES[9]", "M157", ENGINE,
        literals={"9": "table size", "6": "6-max, the comparison table",
                  "73%": "M157: T7s under the gun folds 0.731 on average at 12,000 iterations",
                  "12%": "M157: the same hand at the old 3,000-iteration arm (0.122)",
                  "0.43": "M157: T7s fold seed-to-seed spread"}),

    # -- not current: something that changes the figure shipped after it -
    Disclosure(
        "MULTIWAY_BET_NOTE", "M285", HAND_STORE,
        ("MULTIWAY_BET_DECISIONS", "MULTIWAY_BET_WE_BET", "MULTIWAY_BET_REFERENCE_BETS",
         "MULTIWAY_BET_WEAK_WE", "MULTIWAY_BET_WEAK_REFERENCE",
         "MULTIWAY_BET_FACING_REFERENCE_RAISES", "MULTIWAY_BET_FACING_REFERENCE_CALLS",
         "MULTIWAY_BET_FACING_REFERENCE_FOLDS"),
        literals={"100": "the stack depth, 100 big blinds"},
        provenance="m262_pluribus.py / m263_arms.py; M285 recomputed it from "
                   "m263_grouped_mean.jsonl (the shipped arm) after M269 left it stale"),
    Disclosure(
        "MULTIWAY_STABLE_REASON", "M267", ENGINE,
        ("MULTIWAY_STABLE_HELD_SPOTS", "MULTIWAY_STABLE_SPOTS", "MULTIWAY_STABLE_FLOP_HELD",
         "MULTIWAY_STABLE_FLOP_SPOTS", "MULTIWAY_STABLE_TURN_HELD", "MULTIWAY_STABLE_TURN_SPOTS",
         "MULTIWAY_STABLE_RIVER_HELD", "MULTIWAY_STABLE_RIVER_SPOTS", "MULTIWAY_STABLE_TVD",
         "MULTIWAY_STABLE_ACTION_CHANGES", "MULTIWAY_STABLE_MAX_TOP_ACTION"),
        current=False,
        superseded_by="M269 grouped action matching changed how the multiway postflop "
                      "solver matches regret; reproducibility was measured before it",
        provenance="m254_predict_instability.py"),
    Disclosure(
        "MULTIWAY_REPRODUCIBILITY_REASON", "M267", ENGINE,
        literals={"50%": "M267: split flop rows change action", "33%": "M267: turn",
                  "49%": "M267: river"},
        current=False,
        superseded_by="M269 grouped action matching (see MULTIWAY_STABLE_REASON)",
        provenance="m254_predict_instability.py"),
    Disclosure(
        "SIZING_CAVEAT_REASON", "M110/M282/M287", ENGINE,
        _TWO_LIVE + _ONE_RAISE,
        literals={"72": "hand name (72o)", "6": "6-max",
                  "0.03": "M72/M139: AA's jam at the shipped 6-max budget and its converged value",
                  "0.92": "M110: AA's jam at 12,000 iterations, worst seed",
                  "15%": "published GTO: opening range under the gun",
                  "45%": "published GTO: opening range on the button"},
        current=False,
        superseded_by="the AA-jam range and the flat positional fold mass were measured at "
                      "12,000 iterations (M110/M111); 6-max ships 3,000. 'A converged solve "
                      "puts it near 0.03' predates F37/M139, which put the converged value at "
                      "0.0. The two-live clause is current (M282) and built from constants"),
    Disclosure(
        "FACING_A_BET_COST_NOTE", "M188", ENGINE,
        literals={"801": "M188: facing spots priced", "86%": "M188: share of all cost",
                  "25": "M188: facing vs opening cost ratio, a floor",
                  "18%": "M188: facing decisions costing over 1 bb",
                  "5%": "M188: facing decisions costing over 5 bb"},
        current=False,
        superseded_by="M190 cap 140, M207 bet menu, M231 river; M192 then measured TOTAL cost "
                      "at the shipped config as no longer separable from zero"),
    # COSTLY_BAND_NOTE was registered here until M299 (audit R3)
    # WITHDREW it. Re-priced at the shipped configuration the 0.55-0.90
    # band is 1.48x at 0.90 sigma with both split halves under the bar,
    # and on its own published metric it does not separate at all. A
    # withdrawn disclosure leaves this registry rather than sitting in it
    # as `shown=False`, which is for copy that still exists.
    Disclosure(
        "RIVER_MEASURED_NOTE", "M177", ENGINE,
        literals={"56": "M177: river spots", "14": "M177: strong-band spots over 0.10",
                  "28": "M177: spots per band", "3": "M177: weak-band spots over 0.10"},
        current=False,
        superseded_by="M231 took the river to cap 60 / 1,000 iterations, which cut its "
                      "over-aggression against an independent solver from +0.19 to +0.05"),
    Disclosure(
        "FLOP_MEASURED_NOTE", "M180", ENGINE, ("FLOP_MEASURED_SPOTS", "FLOP_MEASURED_FAILURES"),
        literals={"0.10": "the error threshold, 0.10"},
        current=False, superseded_by="M207's flop bet menu shipped after M180 measured it"),
    Disclosure(
        "UNMEASURED_STREET_NOTE", "M175", ENGINE,
        literals={"0.30": "M175: turn error at both ends of the strength range"},
        current=False, superseded_by="M179 took the turn cap 26 -> 140; M213 its bet menu"),
    Disclosure(
        "POSTFLOP_AGGRESSION_CAVEAT_REASON", "M292", ENGINE,
        ("POSTFLOP_AGGRESSION_ERROR_MEAN", "POSTFLOP_AGGRESSION_ERROR_WORST",
         "POSTFLOP_AGGRESSION_ERROR_ROWS"),
        literals={"7%": "M292: the worst row's shipped bet frequency (0.0716)",
                  "98%": "M292: the same row's uncapped reference (0.9753)"},
        study="bench/studies/aggression_caveat.py"),
    Disclosure(
        "STREET_ISOLATION_NOTE", "M197-M202", ENGINE,
        ("STREET_ISOLATION_SPR_MIN", "STREET_ISOLATION_MEDIAN_GAP",
         "STREET_ISOLATION_CATEGORICAL", "STREET_ISOLATION_PRODUCTION_AGREE",
         "STREET_ISOLATION_WORST_CATEGORICAL", "STREET_ISOLATION_GAP_MEAN",
         "STREET_ISOLATION_GAP_CI_LOW", "STREET_ISOLATION_GAP_CI_HIGH",
         "STREET_ISOLATION_COST_BB", "STREET_ISOLATION_COST_CI_LOW",
         "STREET_ISOLATION_COST_CI_HIGH", "STREET_ISOLATION_COST_WORST",
         "STREET_ISOLATION_BIAS_LOW", "STREET_ISOLATION_BIAS_HIGH"),
        literals={"100%": "M200: the fuller solve's bet frequency at the categorical spots"},
        current=False, superseded_by="M203-M207's flop bet menu shipped after M202 priced it"),
    Disclosure(
        "DRAWY_BOARD_NOTE", "M221", REFERENCE,
        literals={"28": "M221: flop spots", "3.5": "M221: two-tone median gap, points",
                  "0.6": "M221: rainbow median gap, points",
                  "10": "the disagreement threshold, 10 points",
                  "2.2": "M221: the split in standard deviations"},
        current=False,
        superseded_by="measured at range cap 25 (the reference's width); the flop ships 100, "
                      "and M234 measured cap 25 separably worse (+0.1134, 2.56 sigma)",
        provenance="m221_texture.py"),
    Disclosure(
        "BET_SIZING_COVERAGE_NOTE", "M151", ENGINE,
        literals={"99%": "M151: a top pair checking / a busted draw shoving, with all-in "
                         "the only bet available"},
        current=False,
        superseded_by="measured on the chained river M174 replaced; the note still fires only "
                      "where all-in is the sole size, derived from the response's rows"),

    # -- dormant: cannot reach a player -----------------------------------
    Disclosure(
        "RELIABLE_HAND_NOTE", "M167", ENGINE,
        ("RELIABLE_HAND_ERROR_WORST",),
        literals={"10": "the certification threshold, 10 points",
                  "6": "M167: worst error in the certified band, points"},
        current=False, shown=False,
        superseded_by="M180 withdrew the certificate (CERTIFY_RELIABILITY_ON_STREETS = ())"),
    Disclosure(
        "UNCERTAIN_HAND_NOTE", "M167", ENGINE,
        literals={"10": "the certification threshold, 10 points",
                  "99": "M167: worst error in the uncertified band, points"},
        current=False, shown=False,
        superseded_by="M180 withdrew the certificate (CERTIFY_RELIABILITY_ON_STREETS = ())"),
)


def numeric_copies(cfg) -> list:
    """Every user-facing string in config that quotes a number."""
    names = [n for n in dir(cfg) if n.endswith(("_NOTE", "_REASON"))
             and isinstance(getattr(cfg, n), str) and NUMBER.search(getattr(cfg, n))]
    names += [f"LOW_CONFIDENCE_TABLE_SIZES[{k}]"
              for k, v in cfg.LOW_CONFIDENCE_TABLE_SIZES.items() if NUMBER.search(v)]
    return sorted(names)


def table(cfg) -> str:
    rows = [f"{'disclosure':<36} {'measured':<16} {'instr.':<10} {'current':<8} "
            f"{'shown':<6} study"]
    for e in sorted(REGISTRY, key=lambda e: (not e.shown, e.current, e.copy)):
        rows.append(f"{e.copy:<36} {e.milestone:<16} {e.instrument:<10} "
                    f"{'yes' if e.current else 'NO':<8} {'yes' if e.shown else 'no':<6} "
                    f"{e.study or '-'}")
    shown = [e for e in REGISTRY if e.shown]
    stale = [e for e in shown if not e.current]
    rows.append("")
    rows.append(f"{len(shown)} shown to players; {len(stale)} quote figures measured under a "
                f"configuration that no longer ships; "
                f"{sum(1 for e in shown if e.study)} re-derivable from this repository.")
    return "\n".join(rows)


if __name__ == "__main__":                                # pragma: no cover
    import sys
    sys.path.insert(0, ".")
    from api import config
    print(table(config))
