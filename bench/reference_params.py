"""Write the independent solver's parameters for a spot of OURS.

Every external comparison in this project - M221, M222, M224, M230,
M232, M236, M241, M242, M256, M260, M277, M296 - needed this file, and
every one of them wrote it in a scratch script that is now gone. M299
kept `bench/price.py` for the same reason on the chips side; this is the
reference side, and the four things it gets right are four things that
have each been got wrong at least once here.

**The pot and stack are the SPOT's own** (M177/M180). A facing-a-bet
reference built at the node's pot rather than the street's OPENING pot
sizes every bet off the wrong number, and scores two different
situations against each other.

**The RANGES come from the production derivation** (M164). A study that
rebuilds them agrees with production until it does not, and then
measures its own reconstruction. They are written as class weights
because that is the format the solver reads, and because `parse_params_
ranges` has to be able to read them back - the DUMP does not carry the
ranges, so a walk over it prices against a uniform villain unless the
params are read beside it (F59/M257).

**The BET MENU is ours, read off the config the request used** (F64,
M257). The reference was once handed `(33, 75)` while `/advise` offered
33/75/250 plus all-in: at a shallow stack the 250 IS the all-in and
nothing shows, while deeper our overbet was scored as a 75% bet.

**The RE-RAISE menu has to match too, one action deeper** (M241). Facing
a 5bb bet the reference could raise to 20bb and we to 9.90bb, which is
M222's void run in miniature - two arms solving different games while
both look right at the root. `RAISE_PCT` is 60 because M233 measured
that as approximately matched to our tree; it is a matched setting, not
a derived one, and anything reading a facing-a-bet node should re-check
it rather than inherit it.

Instrument only: nothing under `poker_solver/` or `api/` may import this.
"""
from __future__ import annotations

import pathlib

#: M233: our own `raise_sizes` past the first entry multiply the PREVIOUS
#: BET, not the pot, so the two menus cannot be matched by reading a
#: number across. 60 is the value M233 measured as approximately matched,
#: and the milestone that found it also found a study sweeping this knob
#: to 0.6 and measuring 5.59 sigma of "improvement" while building raises
#: smaller than the bet they faced.
RAISE_PCT = 60
#: A converged run reaches 0.32-0.50% of pot (M221/M242/M256).
DEFAULT_ACCURACY = 0.5
DEFAULT_ITERATIONS = 400
DEFAULT_THREADS = 8
#: One round is the flop's betting only, and it is what every flop
#: comparison here has used. Two rounds cost 240x the bytes and three
#: cost 4 GB (M255); M298 measured three on a flop as impossible on this
#: machine at any stack, width or thread count.
DEFAULT_DUMP_ROUNDS = 1
STREETS = ("flop", "turn", "river")


def range_line(weights: dict) -> str:
    """`AA:0.9979,KK:0.9865,...`, heaviest first.

    The solver parses two decimals and DROPS anything under ~0.006
    (M260), which is 0.13% of range mass and harmless - except that a
    hero drawn below it vanishes from the dump. Draw heroes above it.
    """
    ordered = sorted(weights.items(), key=lambda kv: (-kv[1], str(kv[0])))
    return ",".join(f"{label}:{weight:.4f}" for label, weight in ordered if weight > 0)


def bet_size_lines(bet_pcts, raise_pct: int = RAISE_PCT) -> list:
    """Our opening menu and re-raise, for both players on every street.

    Written for all three streets even when the dump is one round deep:
    the TREE the solver builds is what the flop strategy is solved
    against, so truncating the menu later would change the game while
    leaving the flop looking identical.
    """
    lines = []
    joined = ",".join(str(int(round(p * 100))) for p in bet_pcts)
    for player in ("oop", "ip"):
        for street in STREETS:
            lines.append(f"set_bet_sizes {player},{street},bet,{joined}")
            lines.append(f"set_bet_sizes {player},{street},raise,{raise_pct}")
            lines.append(f"set_bet_sizes {player},{street},allin")
    return lines


def params_text(*, pot: float, effective_stack: float, board: tuple, oop: dict, ip: dict,
                bet_pcts, dump_path: str, iterations: int = DEFAULT_ITERATIONS,
                accuracy: float = DEFAULT_ACCURACY, threads: int = DEFAULT_THREADS,
                dump_rounds: int = DEFAULT_DUMP_ROUNDS, raise_pct: int = RAISE_PCT,
                allin_threshold: float = 0.67, print_interval: int = 50) -> str:
    """The whole file. `board` is card strings; `oop`/`ip` are class
    label -> weight, and both must be non-empty or the solve is of a
    game one player cannot play."""
    if not oop or not ip:
        raise ValueError("both positions need a range; an empty one solves a different game")
    if not bet_pcts:
        raise ValueError("a menu with no bet leaves check-or-shove, which is not our tree (F40)")
    if effective_stack <= 0 or pot <= 0:
        raise ValueError(f"pot {pot} and stack {effective_stack} must both be positive")
    lines = [
        f"set_pot {pot:g}",
        f"set_effective_stack {effective_stack:g}",
        "set_board " + ",".join(board),
        "set_range_oop " + range_line(oop),
        "set_range_ip " + range_line(ip),
        *bet_size_lines(bet_pcts, raise_pct),
        f"set_allin_threshold {allin_threshold}",
        "build_tree",
        f"set_thread_num {threads}",
        f"set_accuracy {accuracy}",
        f"set_max_iteration {iterations}",
        f"set_print_interval {print_interval}",
        "set_use_isomorphism 1",
        "start_solve",
        f"set_dump_rounds {dump_rounds}",
        f"dump_result {dump_path}",
    ]
    return "\n".join(lines) + "\n"


def write(path, **kwargs) -> pathlib.Path:
    """`params_text` to disk, returning the path written."""
    path = pathlib.Path(path)
    path.write_text(params_text(**kwargs), encoding="utf-8")
    return path


def class_weights(scenario, position) -> dict:
    """One position's derived range as `{label: weight}`.

    Takes the CAPPED scenario `/advise` itself solved from, so the
    reference is handed the range the product actually reasons about -
    not the uncapped derivation, which would be a different game, and
    not a reconstruction (M164).
    """
    return {str(hand): float(weight)
            for hand, weight in scenario.ranges[position].items() if weight > 0}
