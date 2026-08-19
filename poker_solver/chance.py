"""Chance nodes: the machinery that chains one postflop street's betting
round into the next, by dealing a real community card between them.

M11's flop-only tree treated "flop action capped, nobody folded" as an
immediate showdown, valued by averaging over *every* remaining runout
(turn + river) inside one board_equity table — a deliberate stand-in for
"the rest of the hand gets played out," not a real next street. This
module is the first real step past that: `build_chance_node` takes such
a capped flop terminal and turns it into a `ChanceNode` — one branch per
possible next card, each branch holding its own board-specific equity
table and its own turn-street betting-round tree (built via
`game_tree.build_street_tree`, unchanged).

Deliberately NOT part of `game_tree.py`: that module's own docstring
promises to stay card-agnostic ("no dependency on cards/equity concepts
at all"), and a chance node inherently needs to reason about which cards
remain in the deck — the same reason board_equity.py lives apart from
equity.py rather than folded in. `cfr.py` is what actually walks a
`ChanceNode` during solving (see its module docstring) — this module
only builds the structure, it doesn't solve anything.

Approximation, stated precisely (not glossed as "the same as an existing
one" — see the M12 PR for the full writeup): every branch here gets
uniform weight (1 / number of undealt cards), regardless of which cards
either player's range is already holding. `remaining_deck` only excludes
the board, not hole cards, so for any given combo, exactly 2 of the ~47
branches deal a card that combo physically holds; `build_board_equity_table`
correctly marks that branch's row NaN (then 0.5 after `nan_to_num`) for
that combo, but reach weight for that combo still contributes to the
uniform average over that physically-impossible branch. That's a real,
small, precisely-bounded bias (~4.3% of branches per combo, deterministic
given the combo, non-compounding across CFR iterations, nets toward
neutral rather than a wrong extreme) — distinct from, not a restatement
of, the project's existing NaN->0.5 cross-player-blocker precedent
(solve_flop, MultiwayEquityCache), which is about two *different*
players' hands conflicting, not one hand conflicting with the very card
being dealt to decide its own equity. A future fix (per-branch reach
masking + renormalization) is straightforward but out of scope here.
"""

from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from .board_equity import build_board_equity_table
from .cards import Card, remaining_deck
from .game_tree import DecisionNode, StreetConfig, TerminalNode, build_street_tree


@dataclass(frozen=True)
class ChanceBranch:
    """One possible next card, and everything that follows from it.

    `chance_fn` is the reuse hook for chaining *further* streets (e.g. a
    future turn->river milestone): if set, cfr.py will call it on any
    showdown-eligible terminal reached inside `root`'s own subtree,
    exactly the way the flop-level chance_fn produced this branch in the
    first place. M12 always leaves it `None` — a turn-street terminal
    should fall through to `equity_table` (already an average over every
    possible river), not deal a 5th street. See cfr.py's module docstring
    for why this has to be a per-branch field, not just threaded through
    unconditionally: doing that would double-deal a card off the wrong
    board once a branch's own subtree reaches its own showdown terminal.

    M13: `build_chance_node`'s own `chain_to_river` parameter is what
    actually populates this field now (a real turn branch's `chance_fn`,
    when set, deals the river) — this docstring's "future milestone"
    became this one; nothing about the field itself needed to change.
    """

    card: Card
    equity_table: np.ndarray
    root: DecisionNode | TerminalNode
    chance_fn: Optional[Callable[[TerminalNode], "ChanceNode"]] = None


@dataclass(frozen=True)
class ChanceNode:
    """A "deal the next card" point in the joint tree. `branches` maps
    each possible next card to a `ChanceBranch`; every branch is equally
    likely (see this module's docstring for the approximation that
    implies) — there's no explicit probability field, cfr.py just
    averages uniformly over `branches.values()`.
    """

    pot: float
    invested: dict
    branches: dict  # Card -> ChanceBranch


def build_chance_node(
    terminal: TerminalNode,
    board: tuple,
    combos: list,
    positions: tuple,
    effective_stack_bb: float,
    raise_sizes: tuple = (2.5, 3.0, 2.2),
    max_raises: int = 4,
    chain_to_river: bool = False,
) -> ChanceNode:
    """Build the chance node that follows a showdown-eligible `terminal`
    (action capped without a fold on whatever street `terminal` belongs
    to) — one branch per card not already on `board`.

    `combos` is the same combo pool `terminal`'s own tree was solved
    with (the union of both positions' ranges) — every branch's equity
    table is built over that identical pool/ordering, so hand-index
    alignment with the outer solve's reach vectors is preserved all the
    way down. `effective_stack_bb` is the *entering* stack for whatever
    street `terminal` belongs to (same meaning as `solver.solve_flop`'s
    own parameter of that name when called from the flop level) — the
    remaining stack for the *next* street is derived from it here, not
    re-supplied by the caller.

    `chain_to_river` (M13, default False — every M12 call site is
    unaffected): when True, a branch whose own street still has real
    betting left (`remaining_stack > 0`) AND whose own board isn't
    already a complete 5-card river gets its `chance_fn` populated with
    a closure that deals *that* branch's next card the same way — i.e.
    calling this with `chain_to_river=True` on a flop terminal chains
    flop->turn->river, not just flop->turn, since the recursive call
    keeps passing `chain_to_river=True` forward. A branch whose stack is
    already `0` (both players all-in) never gets a populated `chance_fn`
    regardless of `chain_to_river` — see the loop below for why that's
    structural, not a separate check that could drift out of sync.

    Raises ValueError if `terminal` is a fold-out (nothing to deal a
    card for) or if the derived remaining stack would be negative (an
    inconsistent `effective_stack_bb` relative to what `terminal` already
    shows invested).
    """
    if not terminal.is_showdown:
        raise ValueError("build_chance_node needs a showdown-eligible terminal (no fold), got a fold-out")

    remaining_stack = effective_stack_bb - max(terminal.invested.values())
    if remaining_stack < 0:
        raise ValueError(
            f"effective_stack_bb={effective_stack_bb} is less than what's already invested "
            f"({max(terminal.invested.values())}) at this terminal"
        )

    branches = {}
    for card in remaining_deck(board):
        next_board = board + (card,)
        equity_table = build_board_equity_table(next_board, combos)
        equity_table = np.nan_to_num(equity_table, nan=0.5)

        if remaining_stack == 0:
            # Both players are already all-in — no more betting is
            # possible, `terminal` is already a valid showdown leaf for
            # this branch too, only its equity table (one card richer)
            # changes. Deliberately no `chance_fn` here regardless of
            # `chain_to_river`: this branch's equity_table (built for
            # `next_board`, one card richer than `board`) already
            # correctly averages over however many community cards
            # remain via build_board_equity_table's own remaining_needed
            # handling — a further explicit chance dispatch on top of
            # that would double-process this same physical terminal
            # against two inconsistent runout distributions. Putting
            # `chance_fn = None` in this branch of the if/else (rather
            # than as a separate check applied afterward) is what makes
            # that structurally impossible, not just tested-for.
            root = terminal
            chance_fn = None
        else:
            root = build_street_tree(
                StreetConfig(
                    positions=positions,
                    pot=terminal.pot,
                    stack_bb=remaining_stack,
                    raise_sizes=raise_sizes,
                    max_raises=max_raises,
                )
            )
            if chain_to_river and len(next_board) < 5:
                # Default-arg binding (`_b=next_board, _s=remaining_stack`)
                # is required, not stylistic: without it every branch's
                # closure would share the *loop variables* next_board/
                # remaining_stack by reference, so by the time any of
                # them actually got called (lazily, during solving) they'd
                # all see whichever values the loop last left behind —
                # every branch would silently deal its river off the last
                # branch's board instead of its own.
                chance_fn = lambda t, _b=next_board, _s=remaining_stack: build_chance_node(
                    t, board=_b, combos=combos, positions=positions,
                    effective_stack_bb=_s, raise_sizes=raise_sizes, max_raises=max_raises,
                    chain_to_river=True,
                )
            else:
                chance_fn = None

        branches[card] = ChanceBranch(card=card, equity_table=equity_table, root=root, chance_fn=chance_fn)

    return ChanceNode(pot=terminal.pot, invested=dict(terminal.invested), branches=branches)
