"""Generate the spots a benchmark asks about, by walking the real tree.

**Why this is a module and not a line in a script.** Every play session
this project has run drew its preflop action from a hardcoded list whose
deepest entry held a SINGLE raise:

    rng.choice([[], [], ["raise"], ["fold"] * (players - 2) + ["raise"]])

So no session ever asked for preflop advice at a 3-bet node, let alone a
4-bet or a pot folded down to two. M251's defect lives exactly there —
a two-live multiway pot telling 72o to call a 4-bet 97% of the time —
and was invisible to the instrument by construction, not by bad luck.

Worse, the harness *had* a guard for that defect: it flagged trash
folding under 0.02 while facing action. The observed value is 0.0269.
**A threshold set just below the defect, over a population that could
not reach it.** F38's lesson one layer up: a benchmark measures the
population it generates, and nothing else.

So paths are walked off the real `game_tree` here. That makes them legal
by construction — no path can be assembled that the API would refuse —
and lets the walk reach whatever depth the tree actually offers.

Deliberately NOT in `poker_solver/` or `api/`: this is an instrument, it
imports the engine, and nothing shipped may import it (the same
placement `bench.reference_units` has, for the same reason).
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from poker_solver.game_tree import (
    DecisionNode,
    GameConfig,
    TerminalNode,
    build_game_tree,
)

#: Seat names per table size, matching `api.config`'s own preflop specs.
SEATS = {
    2: ("BTN", "BB"),
    3: ("BTN", "SB", "BB"),
    6: ("UTG", "MP", "CO", "BTN", "SB", "BB"),
    9: ("UTG", "UTG1", "UTG2", "MP", "MP1", "CO", "BTN", "SB", "BB"),
}

#: How much more often the walk takes a raise than a call. Above 1.0 on
#: purpose: an unweighted walk folds out early and almost never reaches a
#: 4-bet, which is the population this module exists to produce. It
#: biases WHICH spots are generated, never what the engine answers.
DEFAULT_RAISE_WEIGHT = 3.0

_TREES: dict = {}


def _tree(players: int, stack_bb: float):
    """A cached preflop tree. `build_game_tree` builds children lazily,
    so this materialises only the paths actually walked."""
    key = (players, stack_bb)
    if key not in _TREES:
        if players not in SEATS:
            raise ValueError(f"no seat names for a {players}-player table")
        _TREES[key] = build_game_tree(
            GameConfig(positions=SEATS[players], stack_bb=stack_bb)
        )
    return _TREES[key]


@dataclass(frozen=True)
class PreflopWalk:
    """One random legal walk through a preflop tree.

    `decision_paths` holds the action-kind path to EVERY decision node
    the walk passed, so a caller wanting "a preflop decision" can pick
    one at whatever depth it likes rather than hoping a random stopping
    rule lands on one.

    `closed_path` is the whole walk when it ended in a terminal with two
    or more players live — the precondition for dealing a board. It is
    None when the hand folded out, which is not a postflop spot.
    """

    decision_paths: tuple
    closed_path: tuple | None
    live_players: int


def preflop_walk(rng: random.Random, players: int, stack_bb: float,
                 raise_weight: float = DEFAULT_RAISE_WEIGHT,
                 max_actions: int = 9) -> PreflopWalk:
    """Walk one legal preflop line, recording every decision node on it.

    All-in is excluded from the walk: it ends the betting immediately and
    would collapse most lines before they reach any depth. A caller
    wanting all-in spots should ask for them directly.
    """
    node = _tree(players, stack_bb)
    kinds: list = []
    decisions: list = []
    while isinstance(node, DecisionNode) and len(kinds) < max_actions:
        decisions.append(tuple(kinds))
        legal = [a for a in node.legal_actions if a.kind != "all_in"]
        if not legal:
            break
        weights = [raise_weight if a.kind == "raise" else 1.0 for a in legal]
        action = rng.choices(legal, weights=weights, k=1)[0]
        kinds.append(action.kind)
        node = node.children[action]
    live = sum(1 for p in SEATS[players] if p not in node.folded)
    closed = isinstance(node, TerminalNode) and live >= 2
    return PreflopWalk(
        decision_paths=tuple(decisions),
        closed_path=tuple(kinds) if closed else None,
        live_players=live,
    )


def closing_path(live_players: int) -> list:
    """A postflop action path that CLOSES a street's betting.

    One check per live player — **not the two that every session in this
    project hardcoded.** That worked only because those sessions almost
    always left two players live; a three-handed board left the street
    open and the API correctly refused to deal the next card, so multiway
    turn and river spots were quietly dropped rather than measured.
    """
    if live_players < 2:
        raise ValueError(f"a street needs at least 2 live players, got {live_players}")
    return ["call_or_check"] * live_players
