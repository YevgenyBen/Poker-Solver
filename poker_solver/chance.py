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
from .multiway_board_equity import DEFAULT_NWAY_BOARD_EQUITY_SAMPLES
from .multiway_board_equity import DEFAULT_SEED as DEFAULT_NWAY_EQUITY_SEED
from .multiway_board_equity import NwayBoardEquityCache


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
    equity_table_cache: dict | None = None,
    equity_batch_fn=None,
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

    # M129 (B). The equity tables for this node's branches are built
    # FIRST, as a batch, so a caller can compute them in parallel.
    #
    # They are the single largest cost in a turn or river solve — a turn
    # node builds ~49 of them, a river solve ~2,400 — and each is a pure
    # function of (next_board, combos), independent of every other. The
    # 24 cores this typically runs on were doing nothing.
    #
    # Injected rather than implemented here: `poker_solver/` is a plain
    # library with no runtime infrastructure (enforced by
    # tests/test_package_boundary.py), and a process pool is exactly the
    # kind of thing that belongs to whoever owns the request. Default
    # None keeps the original sequential behaviour byte for byte.
    upcoming = [(card, board + (card,)) for card in remaining_deck(board)]
    wanted = []
    for _, next_board in upcoming:
        key = (next_board, tuple(combos))
        if equity_table_cache is None or equity_table_cache.get(key) is None:
            if next_board not in wanted:
                wanted.append(next_board)

    if wanted:
        if equity_batch_fn is not None and len(wanted) > 1:
            built = equity_batch_fn(wanted, combos)
        else:
            built = [np.nan_to_num(build_board_equity_table(b, combos), nan=0.5)
                     for b in wanted]
        prebuilt = dict(zip(wanted, built))
    else:
        prebuilt = {}

    branches = {}
    for card in remaining_deck(board):
        next_board = board + (card,)
        # M55: memoized across chance nodes. A branch's equity table is a
        # pure function of (next_board, combos) — it does NOT depend on
        # which `terminal` this chance node hangs off, since `terminal`
        # only ever influences the branch's TREE (via remaining_stack),
        # never its equity. A flop tree has several showdown-eligible
        # terminals and each was independently rebuilding all ~46-49 of
        # these identical tables: measured at exactly 7.00x redundancy on
        # a real /solve_turn_from_path query (343 builds, 49 distinct
        # inputs), against a bottleneck profiling put at ~74% of that
        # endpoint's total time. Correct by construction, not an
        # approximation — the same pure function, the same arguments.
        cache_key = (next_board, tuple(combos))
        cached = None if equity_table_cache is None else equity_table_cache.get(cache_key)
        if cached is None:
            equity_table = prebuilt[next_board]
            if equity_table_cache is not None:
                equity_table_cache[cache_key] = equity_table
        else:
            equity_table = cached

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
                    chain_to_river=True, equity_table_cache=equity_table_cache,
                    equity_batch_fn=equity_batch_fn,
                )
            else:
                chance_fn = None

        branches[card] = ChanceBranch(card=card, equity_table=equity_table, root=root, chance_fn=chance_fn)

    return ChanceNode(pot=terminal.pot, invested=dict(terminal.invested), branches=branches)


@dataclass(frozen=True)
class SampledChanceBranch:
    """One already-chosen next card, and everything that follows from it —
    the MCCFR-appropriate sibling of `ChanceBranch`/`ChanceNode` (M32),
    deliberately NOT reusing either: `ChanceBranch.equity_table` is a
    precomputed NxN array built for the exact 2-player solver's own
    pairwise value representation, and `ChanceNode` eagerly builds EVERY
    possible card's branch upfront — both wrong shapes for MCCFR, which
    only ever needs ONE sampled card's own subtree, evaluated via an N-way,
    lazily-memoized-per-opponent-tuple equity *cache* (M30's
    `NwayBoardEquityCache`), not a fixed table. See
    `build_mccfr_chance_branch`'s own docstring for how this gets built,
    and `cfr.py`'s module docstring for how it gets sampled/walked.

    `board` has no `ChanceBranch` analog — needed because, unlike the
    exact solver (which never tracks board content at all; a `ChanceBranch`
    is only ever reached via its own parent `ChanceNode`), MCCFR's own
    recursion needs to know the *current* board to sample the *next*
    branch's card when recursing into this branch's own subtree.

    `chance_fn`, always `None` as of M32 (one hop only — flop->turn, not
    chained further): the direct structural analog of `ChanceBranch.
    chance_fn`'s own M12-before-M13 history. A future turn->river
    milestone would populate this the same way M13 populated
    `ChanceBranch.chance_fn`, with no other change needed here.
    """

    card: Card
    board: tuple
    equity_cache: NwayBoardEquityCache
    root: DecisionNode | TerminalNode
    chance_fn: Optional[Callable[[TerminalNode, Card], "SampledChanceBranch"]] = None


def build_mccfr_chance_branch(
    terminal: TerminalNode,
    card: Card,
    board: tuple,
    combos: list,
    positions: tuple,
    effective_stack_bb: float,
    raise_sizes: tuple = (2.5, 3.0, 2.2),
    max_raises: int = 4,
    equity_samples: int = DEFAULT_NWAY_BOARD_EQUITY_SAMPLES,
    equity_seed: int = DEFAULT_NWAY_EQUITY_SEED,
    chain_to_river: bool = False,
) -> SampledChanceBranch:
    """Build the ONE next-street subtree that follows a showdown-eligible
    `terminal` when `card` — already chosen by the caller — is dealt.

    The MCCFR-native sibling of `build_chance_node`: that function eagerly
    builds every possible next card's branch (correct for the exact
    solver, which visits the whole tree exhaustively every iteration
    anyway, so building all branches once and reusing them is the right
    tradeoff there — see M12/M13's own measured costs, ~183s for 50
    iterations at a modest combo pool). MCCFR is fundamentally a
    per-iteration SAMPLING method — reusing `build_chance_node` here would
    force building 44-49 N-way equity caches to serve ONE sampled card per
    iteration, defeating MCCFR's entire reason to exist. This function
    builds exactly the one branch the caller already decided on.

    `card` is a parameter, not sampled inside this function: the sampling
    decision (which card, via `mccfr_solve`'s own seeded `rng`, for the
    same "same seed -> same result" determinism story `mccfr_solve`'s own
    docstring already promises) belongs in `cfr.py` — this module stays a
    pure "given these exact inputs, build this structure" builder,
    matching its own module docstring's existing promise ("this module
    only builds the structure, it doesn't solve anything"). It also lets
    `cfr.py` check its own `chance_data` memoization *before* paying this
    function's construction cost, which a card-sampling-inside-here design
    couldn't support.

    `combos`/`positions`/`effective_stack_bb`/`raise_sizes`/`max_raises`
    mean exactly what they mean on `build_chance_node` (same combo pool
    the calling tree was solved with; the *entering* stack for whichever
    street `terminal` belongs to). `equity_samples`/`equity_seed` size the
    new branch's own `NwayBoardEquityCache` — both default to
    `multiway_board_equity.py`'s own module defaults, matching
    `solver.py`'s existing `from .board_equity import DEFAULT_SEED as
    DEFAULT_EQUITY_SEED`-style aliasing precedent. Note (measured during
    M32's own design, not assumed): every branch this milestone builds
    operates on a 4-card board (flop's 3 + this one dealt card), so
    `remaining_needed` is always 1 inside `NwayBoardEquityCache`'s own
    equity computation — the *exact*-enumeration path
    (`board_equity.py`'s own established `remaining_needed<=1` shortcut,
    mirrored by `multiway_board_equity.py`), not Monte Carlo sampling — so
    `equity_samples` is inert for this milestone's actual usage.

    Raises `ValueError` if `terminal` is a fold-out (nothing to deal a
    card for), if `card` is already on `board` (cfr.py's own sampling
    guarantees this by construction, but this fails loudly rather than
    silently building a board with a duplicate card), or if the derived
    remaining stack would be negative (an inconsistent `effective_stack_bb`
    relative to what `terminal` already shows invested).

    `chain_to_river` (M39, default `False` — every M32 call site
    unaffected): when `True`, a branch whose own street still has real
    betting left (`remaining_stack > 0`) *and* whose own board isn't
    already a complete 5-card river gets its own `chance_fn` populated
    with a closure that deals *that* branch's next card the same way —
    passing `chain_to_river=True` on a flop terminal therefore chains
    flop->turn->river, not just flop->turn, since the recursive call
    keeps forwarding the flag. Mirrors `build_chance_node`'s own
    identical M13 parameter/semantics exactly, but the closure here
    needs none of that function's own default-argument late-binding
    guard (`_b=next_board, _s=remaining_stack`): `build_chance_node`
    builds many branches in one shared loop, so its closures could
    otherwise all capture the *last* iteration's loop variables by
    reference; `build_mccfr_chance_branch` builds exactly one branch per
    call, so `next_board`/`remaining_stack`/etc. are already this call's
    own locals — there is no loop to share variables across, and no
    shared state a later call could retroactively corrupt.
    """
    if not terminal.is_showdown:
        raise ValueError("build_mccfr_chance_branch needs a showdown-eligible terminal (no fold), got a fold-out")
    if card in board:
        raise ValueError(f"card {card} is already on board {board}")

    remaining_stack = effective_stack_bb - max(terminal.invested.values())
    if remaining_stack < 0:
        raise ValueError(
            f"effective_stack_bb={effective_stack_bb} is less than what's already invested "
            f"({max(terminal.invested.values())}) at this terminal"
        )

    next_board = board + (card,)
    equity_cache = NwayBoardEquityCache(next_board, combos, samples=equity_samples, seed=equity_seed)

    # The plain live-position filter, NOT game_tree.postflop_action_order:
    # `positions` here is a StreetConfig-shaped tuple, already
    # postflop-native (first entry already acts first, by that config's
    # own construction) — there is nothing to re-derive. postflop_action_
    # order exists specifically to convert a *preflop* GameConfig.positions
    # tuple into postflop order; applying it to an already-postflop tuple
    # produces the WRONG order (confirmed by direct execution during M32's
    # own design: postflop_action_order(("OOP","MID","IP"), live=("OOP",
    # "IP")) returns ('IP','OOP'), not the correct ('OOP','IP')) — a
    # natural-looking wrong answer for a future reader reaching for the
    # "obvious" helper, recorded here so nobody re-makes that mistake.
    live_positions = tuple(p for p in positions if p not in terminal.folded)

    if remaining_stack == 0:
        # Both/all remaining players are already all-in — no more betting
        # is possible, `terminal` is already a valid showdown leaf for this
        # branch too, only its equity source (one card richer) changes.
        # Deliberately no chance_fn here: this branch's equity_cache
        # (scoped to next_board) already correctly resolves however many
        # community cards remain via NwayBoardEquityCache's own
        # remaining_needed handling — a further chance dispatch on top of
        # that would double-process this same physical terminal. Putting
        # chance_fn = None in this branch of the if/else (rather than a
        # separate check applied afterward) is what makes that
        # structurally impossible, not just tested-for — mirrors
        # build_chance_node's own identical placement/reasoning exactly.
        root = terminal
        chance_fn = None
    else:
        root = build_street_tree(
            StreetConfig(
                positions=live_positions,
                pot=terminal.pot,
                stack_bb=remaining_stack,
                raise_sizes=raise_sizes,
                max_raises=max_raises,
            )
        )
        if chain_to_river and len(next_board) < 5:
            # No default-argument late-binding trick needed here (unlike
            # build_chance_node's own M13 closure) — see this function's
            # own docstring for why: next_board/live_positions/etc. are
            # already this call's own locals, not shared loop state.
            chance_fn = lambda t, c: build_mccfr_chance_branch(
                t, card=c, board=next_board, combos=combos, positions=live_positions,
                effective_stack_bb=effective_stack_bb, raise_sizes=raise_sizes, max_raises=max_raises,
                equity_samples=equity_samples, equity_seed=equity_seed, chain_to_river=True,
            )
        else:
            chance_fn = None  # M32 scope default: one hop only (flop->turn)

    return SampledChanceBranch(card=card, board=next_board, equity_cache=equity_cache, root=root, chance_fn=chance_fn)
