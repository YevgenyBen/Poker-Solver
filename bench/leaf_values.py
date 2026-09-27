"""What a turn leaf is ACTUALLY worth, by solving the river out.

The engine values a turn terminal at `equity * pot - invested`: the hand
is treated as ending at showdown with no further betting. That
approximation *is* street isolation. M247 measured what a correct value
would be worth - sd 1.75-1.86 bb, 12% of pot, two runs identical to four
decimals so not noise, and **no cheap feature predicts it** (equity
0-2%, equity-swing 0-2%) - and left the route open. `cfr`'s
`leaf_value_fn` seam is where such a value plugs in; this builds one.

**Why not just chain.** `solve_flop_turn` on a four-card board plays the
river out already, and is the arm a depth study reaches for first. It
changes TWO things at once: it plays the street out AND it crosses a
chance node, where F45's dead-pot offset stops cancelling (M161 measured
0.97 of strategy difference, dtype-independent). Values computed here
are injected at terminals of a SINGLE-street solve, so nothing crosses a
chance node and a measurement tests the leaf value alone.

**Why a matrix and not a vector.** A counterfactual value vector depends
on the ranges reaching the leaf, which change every iteration. M247's
proposal is explicitly an estimate computed ONCE, so the object has to
be reach-independent: `V[i, j]` = position A's chips when A holds `i`
and B holds `j`. That is the same shape the equity default has, which is
what makes the two arms comparable.

**Why one walk rather than N^2.** `ev._value` prices one hero hand
against one opponent reach, so a full table would be N^2 walks - the
cost that made turn pricing 30 minutes a spot until M277 cut it. A
single MATRIX-valued walk over the solved tree carries an (N, N) array
through ~45 nodes instead (M160's anatomy), which numpy does in
milliseconds.

**The approximation, stated.** The river is solved against the ranges
ENTERING the turn, not the ranges that actually reach each leaf - those
depend on the turn strategy being solved, which is the circularity
M247's "computed once" cuts. A value net would face the same choice and
usually resolves it the same way.

Instrument only: nothing under `poker_solver/` or `api/` may import this.
"""
from __future__ import annotations

import numpy as np

from poker_solver.board_equity import build_board_equity_table
from poker_solver.chance import ChanceNode
from poker_solver.game_tree import StreetConfig, TerminalNode
from poker_solver.solver import solve_flop


def value_matrix(node, *, position_a: str, position_b: str,
                 equity_table: np.ndarray, strategy_of) -> np.ndarray:
    """Position A's chips from `node` down, per (a_hand, b_hand).

    `strategy_of(node)` returns that node's acting player's averaged
    strategy, shape (num_hands, num_actions).

    The terminal convention is `_terminal_value_vector`'s default,
    deliberately and to the letter - a fold pays `pot - invested_a` or
    `-invested_a`, a showdown pays `equity * pot - invested_a`. Anything
    else here would make the two arms of a depth study differ in their
    ACCOUNTING as well as in their depth, which is exactly the confound
    the seam exists to remove.
    """
    if isinstance(node, ChanceNode):
        branches = list(node.branches.values())
        if not branches:
            raise ValueError("a chance node with no branches has no value")
        # Each branch carries its OWN table, one card richer. Using the
        # parent's is M165: a confident answer to the wrong question.
        return sum(
            value_matrix(b.root, position_a=position_a, position_b=position_b,
                         equity_table=b.equity_table, strategy_of=strategy_of)
            for b in branches) / len(branches)

    if isinstance(node, TerminalNode):
        invested_a = node.invested[position_a]
        n = equity_table.shape[0]
        if position_a in node.folded:
            return np.full((n, n), -invested_a, dtype=np.float64)
        if position_b in node.folded:
            return np.full((n, n), node.pot - invested_a, dtype=np.float64)
        return equity_table * node.pot - invested_a

    strategy = strategy_of(node)
    acting_is_a = node.player_to_act == position_a
    total = None
    for index, (_action, child) in enumerate(node.children.items()):
        child_value = value_matrix(
            child, position_a=position_a, position_b=position_b,
            equity_table=equity_table, strategy_of=strategy_of)
        weight = strategy[:, index]
        # A weights its own ROWS, B weights the COLUMNS - the table is
        # indexed [a_hand, b_hand], so which axis the acting player's
        # probability multiplies is not interchangeable.
        weighted = (weight[:, None] if acting_is_a else weight[None, :]) * child_value
        total = weighted if total is None else total + weighted
    if total is None:
        raise ValueError("a decision node with no children has no value")
    return total


def solved_value(*, board, hero_range, villain_range, pot, effective_stack_bb,
                 positions, raise_sizes, max_raises, iterations,
                 equity_table=None, equity_table_fn=None) -> np.ndarray:
    """Solve one street on `board` and return its value matrix.

    On a RIVER board the equity is exact - the board is complete, so
    there is nothing to sample (M154) - which is why this is the street
    worth valuing exactly and the reason M247 costed it at 1.07s.
    """
    result = solve_flop(
        board=board, hero_range=hero_range, villain_range=villain_range,
        pot=pot, effective_stack_bb=effective_stack_bb, positions=positions,
        raise_sizes=raise_sizes, max_raises=max_raises, iterations=iterations,
        equity_table_fn=equity_table_fn)
    combos = result.hands
    if equity_table is None:
        equity_table = build_board_equity_table(board, combos)
    # **Unconditionally, and that is the point.** A blocked pair's equity
    # is NaN, and `solve_flop` replaces it with 0.5 before solving
    # (M161's docstring). This used to nan_to_num only the table it built
    # ITSELF, so a caller passing a pre-built one - which the per-board
    # cache added to make this affordable does - handed NaNs straight
    # into the walk. NaN regrets make `current_strategy` return the
    # uniform prior, so the depth arm came back as exactly 4/5 aggressive
    # at a five-action node: F43's signature, produced by an optimisation
    # that silently changed the answer.
    equity_table = np.nan_to_num(equity_table, nan=0.5)

    def strategy_of(node):
        table = result.node_data.get(id(node))
        if table is None:
            # Never visited: the uniform prior is what `average_strategy`
            # would return for an all-zero table, so this matches rather
            # than inventing a different fallback (M215's argument).
            return np.full((len(combos), len(node.legal_actions)),
                           1.0 / len(node.legal_actions))
        return table.average_strategy()

    matrix = value_matrix(result.root, position_a=positions[0],
                          position_b=positions[1], equity_table=equity_table,
                          strategy_of=strategy_of)
    # A NaN here reaches a solve and returns the uniform prior rather than
    # an error, so it is refused at the boundary instead.
    if not np.isfinite(matrix).all():
        raise ValueError(
            "the leaf value is not finite - a NaN would reach the solve and "
            "come back as the uniform prior rather than as an error")
    return matrix


def leaf_key(node, position_a: str) -> tuple:
    """What makes two turn leaves the SAME river situation.

    M247 measured 27 showdown terminals collapsing to 8 situations,
    identical across spots - so this is what makes the study affordable
    rather than 27x its cost. Folded leaves never reach here.
    """
    return (round(node.pot, 6),
            tuple(sorted((p, round(v, 6)) for p, v in node.invested.items())))


def turn_leaf_values(*, turn_board, hero_range, villain_range, positions,
                     effective_stack_bb, terminals, raise_sizes, max_raises,
                     iterations, deck, equity_table_fn=None,
                     progress=None) -> dict:
    """`{leaf_key: (N, N) value matrix}` for each distinct turn leaf.

    Each leaf's value is the mean over every river card of that river's
    solved value - the same uniform average over runouts `chance.py` and
    `ev._value` use, so the difference from the shipped arm is the
    BETTING the river now contains, not a different runout weighting.

    **Every river's equity table is built ONCE and reused across leaves,
    which is most of what this study costs.** Measured: cutting the river
    solve's iterations 4x saved 10% of the time, so the cost is the TABLE
    and not CFR - M176's finding one street over, and the inversion M217
    says must be measured rather than assumed. The leaves differ only in
    pot and stack; the BOARD is the same, so a table computed for river
    card X serves all of them. Without this, seven leaf situations over
    48 cards build 336 tables where 48 will do.
    """
    tables: dict = {}

    def table_fn(board, combos, samples, seed=None):
        cached = tables.get(tuple(board))
        if cached is None:
            cached = (equity_table_fn(board, combos, samples, seed=seed)
                      if equity_table_fn is not None
                      else np.nan_to_num(
                          build_board_equity_table(board, combos), nan=0.5))
            tables[tuple(board)] = cached
        return cached

    values, seen = {}, {}
    for terminal in terminals:
        if terminal.folded:
            continue                       # the hand ended; nothing to play out
        key = leaf_key(terminal, positions[0])
        seen.setdefault(key, terminal)
    for index, (key, terminal) in enumerate(sorted(seen.items())):
        behind = effective_stack_bb - max(terminal.invested.values())
        if behind <= 0:
            continue                       # all in: no river betting to solve
        combos = sorted(set(hero_range) | set(villain_range), key=str)
        total = live_count = None
        for card in deck:
            river_board = tuple(turn_board) + (card,)
            matrix = solved_value(
                board=river_board, hero_range=hero_range,
                villain_range=villain_range, pot=terminal.pot,
                effective_stack_bb=behind, positions=positions,
                raise_sizes=raise_sizes, max_raises=max_raises,
                iterations=iterations,
                equity_table=tables.get(river_board),
                equity_table_fn=table_fn)
            # A combo holding the river card cannot exist on that river.
            # The engine's own convention replaces such a pair's equity
            # with 0.5 and solves anyway (`_terminal_value_vector`), which
            # is fine for a turn table where nothing is blocked by a card
            # still to come - but averaging it INTO a leaf value would
            # import that fiction. Each pair is averaged over the rivers
            # it is actually live on instead.
            alive = np.array([card not in combo.cards for combo in combos])
            mask = np.outer(alive, alive).astype(np.float64)
            total = matrix * mask if total is None else total + matrix * mask
            live_count = mask if live_count is None else live_count + mask
        # A hand PAIR holds up to four distinct cards, so a deck of five
        # or more always leaves one river live and a zero means the deck
        # was too small - not that the pair is impossible. Refused by name
        # rather than dividing by zero and seeding a solve with NaN.
        if live_count is None or not (live_count > 0).all():
            raise ValueError(
                f"some hand pair was live on no river card: a deck of "
                f"{len(deck)} is too small, a pair blocks up to 4")
        # **The turn's own investment has to come off.** The default this
        # replaces is `equity * turn_pot - turn_invested_a`, but the river
        # subgame is solved with `pot = turn_pot` and its OWN `invested`
        # restarting at zero - so it accounts for the turn pot as money A
        # can win and never subtracts what A paid to reach the leaf. The
        # offset differs per leaf, so it does not cancel out of regret
        # differences the way F45's does within a street: it just flattens
        # them. Left out, a whole campaign's depth arm came back as the
        # exactly-uniform prior (0.8 = 4/5 aggressive at a five-action
        # node), which is F43's signature, not a finding.
        values[key] = total / live_count - terminal.invested[positions[0]]
        if progress:
            progress(index + 1, len(seen), key, len(per_card))
    return values
