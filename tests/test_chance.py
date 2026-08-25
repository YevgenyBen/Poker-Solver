import numpy as np
import pytest

from poker_solver.cards import Card
from poker_solver.chance import ChanceBranch, ChanceNode, SampledChanceBranch, build_chance_node, build_mccfr_chance_branch
from poker_solver.combos import HandCombo
from poker_solver.game_tree import DecisionNode, TerminalNode, walk
from poker_solver.multiway_board_equity import NwayBoardEquityCache

_BOARD = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
_COMBOS = [
    HandCombo(Card("A", "h"), Card("A", "d")),
    HandCombo(Card("K", "h"), Card("K", "d")),
]
_POSITIONS = ("OOP", "IP")


def _showdown_terminal(pot=10.0, invested=5.0):
    return TerminalNode(pot=pot, invested={"OOP": invested, "IP": invested}, folded=frozenset())


def _fold_out_terminal():
    return TerminalNode(pot=10.0, invested={"OOP": 2.0, "IP": 8.0}, folded=frozenset({"OOP"}))


def test_build_chance_node_rejects_a_fold_out_terminal():
    with pytest.raises(ValueError):
        build_chance_node(
            _fold_out_terminal(), board=_BOARD, combos=_COMBOS, positions=_POSITIONS, effective_stack_bb=15.0
        )


def test_build_chance_node_produces_one_branch_per_card_not_on_the_board():
    node = build_chance_node(
        _showdown_terminal(), board=_BOARD, combos=_COMBOS, positions=_POSITIONS, effective_stack_bb=15.0
    )
    assert len(node.branches) == 52 - len(_BOARD)
    assert all(card not in _BOARD for card in node.branches)


def test_build_chance_node_branch_equity_table_shape_matches_combos():
    node = build_chance_node(
        _showdown_terminal(), board=_BOARD, combos=_COMBOS, positions=_POSITIONS, effective_stack_bb=15.0
    )
    for branch in node.branches.values():
        assert branch.equity_table.shape == (len(_COMBOS), len(_COMBOS))
        assert not np.any(np.isnan(branch.equity_table))  # nan_to_num already applied


def test_build_chance_node_branch_root_is_a_turn_street_decision_node_when_stack_remains():
    terminal = _showdown_terminal(pot=10.0, invested=5.0)
    node = build_chance_node(
        terminal, board=_BOARD, combos=_COMBOS, positions=_POSITIONS, effective_stack_bb=15.0
    )
    any_branch = next(iter(node.branches.values()))
    assert isinstance(any_branch.root, DecisionNode)
    assert any_branch.root.player_to_act == "OOP"
    assert any_branch.root.pot == pytest.approx(10.0)
    assert any_branch.root.invested == {"OOP": 0.0, "IP": 0.0}


def test_build_chance_node_reuses_the_terminal_when_no_stack_remains():
    # Both players already all-in at 15bb — no more betting is possible.
    terminal = _showdown_terminal(pot=30.0, invested=15.0)
    node = build_chance_node(
        terminal, board=_BOARD, combos=_COMBOS, positions=_POSITIONS, effective_stack_bb=15.0
    )
    for branch in node.branches.values():
        assert branch.root is terminal


def test_build_chance_node_rejects_negative_remaining_stack():
    terminal = _showdown_terminal(pot=30.0, invested=20.0)  # invested more than the stack allows
    with pytest.raises(ValueError):
        build_chance_node(
            terminal, board=_BOARD, combos=_COMBOS, positions=_POSITIONS, effective_stack_bb=15.0
        )


def test_build_chance_node_every_branch_chance_fn_is_none():
    # M12's explicit scope: no auto-chaining to a river street yet — a
    # regression guard against silently reintroducing that.
    node = build_chance_node(
        _showdown_terminal(), board=_BOARD, combos=_COMBOS, positions=_POSITIONS, effective_stack_bb=15.0
    )
    assert all(branch.chance_fn is None for branch in node.branches.values())


def test_build_chance_node_is_deterministic_across_calls():
    kwargs = dict(
        terminal=_showdown_terminal(), board=_BOARD, combos=_COMBOS, positions=_POSITIONS, effective_stack_bb=15.0
    )
    node_1 = build_chance_node(**kwargs)
    node_2 = build_chance_node(**kwargs)
    for card, branch_1 in node_1.branches.items():
        branch_2 = node_2.branches[card]
        assert np.array_equal(branch_1.equity_table, branch_2.equity_table)


def test_chance_node_pot_and_invested_carry_over_from_the_terminal():
    terminal = _showdown_terminal(pot=12.5, invested=6.25)
    node = build_chance_node(
        terminal, board=_BOARD, combos=_COMBOS, positions=_POSITIONS, effective_stack_bb=15.0
    )
    assert node.pot == pytest.approx(12.5)
    assert node.invested == {"OOP": 6.25, "IP": 6.25}


# ---------------------------------------------------------------------------
# M13: chain_to_river — a real turn branch's own chance_fn, when set, deals
# the river. See chance.py's module docstring for the full design and the
# correctness reason an all-in-already branch must never get one.
# ---------------------------------------------------------------------------


def test_build_chance_node_chain_to_river_defaults_to_false():
    # Byte-for-byte the same as omitting the kwarg entirely — the M12
    # backward-compat guarantee, restated for the new parameter.
    node_omitted = build_chance_node(
        _showdown_terminal(), board=_BOARD, combos=_COMBOS, positions=_POSITIONS, effective_stack_bb=15.0
    )
    node_explicit_false = build_chance_node(
        _showdown_terminal(), board=_BOARD, combos=_COMBOS, positions=_POSITIONS,
        effective_stack_bb=15.0, chain_to_river=False,
    )
    assert all(branch.chance_fn is None for branch in node_omitted.branches.values())
    assert all(branch.chance_fn is None for branch in node_explicit_false.branches.values())


def test_build_chance_node_chain_to_river_populates_chance_fn_when_a_real_tree_remains():
    terminal = _showdown_terminal(pot=10.0, invested=5.0)  # 10bb behind at a 15bb stack
    node = build_chance_node(
        terminal, board=_BOARD, combos=_COMBOS, positions=_POSITIONS,
        effective_stack_bb=15.0, chain_to_river=True,
    )
    for branch in node.branches.values():
        assert isinstance(branch.root, DecisionNode)
        assert callable(branch.chance_fn)


def test_build_chance_node_chain_to_river_never_populates_chance_fn_for_a_reused_terminal_branch():
    # The direct regression guard for the correctness pitfall: an
    # all-in-already branch's own equity table already averages over
    # every remaining community card, so it must never also get a
    # chance_fn — enforced structurally (same if/else that decides
    # `root = terminal`), not by a separate check that could drift.
    terminal = _showdown_terminal(pot=30.0, invested=15.0)  # both already all-in at 15bb
    node = build_chance_node(
        terminal, board=_BOARD, combos=_COMBOS, positions=_POSITIONS,
        effective_stack_bb=15.0, chain_to_river=True,
    )
    for branch in node.branches.values():
        assert branch.root is terminal
        assert branch.chance_fn is None


def test_build_chance_node_chain_to_river_river_branch_chance_fn_is_none_even_with_stack_remaining():
    # Isolates the "board already complete" half of the guard from the
    # "no stack left" half above: plenty of stack remains, but the board
    # passed in is already a turn board (4 cards) — every branch this
    # produces is a complete 5-card river board, so there's nothing left
    # to chain regardless of how much stack is behind.
    turn_board = _BOARD + (Card("K", "s"),)
    terminal = _showdown_terminal(pot=10.0, invested=5.0)
    node = build_chance_node(
        terminal, board=turn_board, combos=_COMBOS, positions=_POSITIONS,
        effective_stack_bb=15.0, chain_to_river=True,
    )
    for branch in node.branches.values():
        assert isinstance(branch.root, DecisionNode)  # real stack remained
        assert branch.chance_fn is None  # but nothing left to deal


def test_build_chance_node_chain_to_river_closure_is_correctly_scoped_per_branch():
    # Regression guard for the late-binding closure bug the _b=/_s=
    # default-arg trick prevents: two different branches' own chance_fn
    # closures must deal from their *own* board, not whichever board the
    # loop happened to leave behind last.
    terminal = _showdown_terminal(pot=10.0, invested=5.0)
    node = build_chance_node(
        terminal, board=_BOARD, combos=_COMBOS, positions=_POSITIONS,
        effective_stack_bb=15.0, chain_to_river=True,
    )
    branch_iter = iter(node.branches.values())
    branch_a = next(branch_iter)
    branch_b = next(branch_iter)

    # Each branch's own root is itself a showdown-eligible terminal we
    # can hand straight back to that branch's own chance_fn — mirroring
    # how cfr.py actually drives this (calling chance_fn on a real
    # showdown terminal reached inside the branch's own subtree).
    stub_terminal_a = TerminalNode(pot=branch_a.root.pot, invested={"OOP": 5.0, "IP": 5.0}, folded=frozenset())
    stub_terminal_b = TerminalNode(pot=branch_b.root.pot, invested={"OOP": 5.0, "IP": 5.0}, folded=frozenset())

    river_node_a = branch_a.chance_fn(stub_terminal_a)
    river_node_b = branch_b.chance_fn(stub_terminal_b)

    # Branch A's own dealt card must be excluded from its river deck (and
    # vice versa) — if the closures shared the loop's last board instead
    # of their own, both would exclude the *same* (wrong) card.
    assert branch_a.card not in river_node_a.branches
    assert branch_b.card not in river_node_b.branches
    assert branch_b.card in river_node_a.branches  # A's river deck still has B's card available
    assert branch_a.card in river_node_b.branches  # and vice versa


def test_build_chance_node_chain_to_river_is_deterministic_across_calls():
    kwargs = dict(
        terminal=_showdown_terminal(pot=10.0, invested=5.0), board=_BOARD, combos=_COMBOS,
        positions=_POSITIONS, effective_stack_bb=15.0, chain_to_river=True,
    )
    node_1 = build_chance_node(**kwargs)
    node_2 = build_chance_node(**kwargs)
    any_card = next(iter(node_1.branches))
    river_terminal = TerminalNode(pot=node_1.branches[any_card].root.pot, invested={"OOP": 5.0, "IP": 5.0}, folded=frozenset())
    river_1 = node_1.branches[any_card].chance_fn(river_terminal)
    river_2 = node_2.branches[any_card].chance_fn(river_terminal)
    for card, branch_1 in river_1.branches.items():
        assert np.array_equal(branch_1.equity_table, river_2.branches[card].equity_table)


# ---------------------------------------------------------------------------
# M32: build_mccfr_chance_branch — the MCCFR-native sibling of
# build_chance_node, one already-chosen card at a time (lazy, not eager —
# see the function's own docstring for why reusing build_chance_node here
# would defeat MCCFR's entire reason to exist).
# ---------------------------------------------------------------------------

_A_CARD = Card("5", "s")  # not on _BOARD, used as "the card cfr.py chose"

_THREE_POSITIONS = ("OOP", "MID", "IP")


def _three_way_showdown_terminal_mid_folded(pot=15.0, invested=5.0):
    return TerminalNode(
        pot=pot, invested={"OOP": invested, "MID": invested, "IP": invested}, folded=frozenset({"MID"})
    )


def test_build_mccfr_chance_branch_rejects_a_fold_out_terminal():
    with pytest.raises(ValueError):
        build_mccfr_chance_branch(
            _fold_out_terminal(), card=_A_CARD, board=_BOARD, combos=_COMBOS,
            positions=_POSITIONS, effective_stack_bb=15.0,
        )


def test_build_mccfr_chance_branch_rejects_a_card_already_on_the_board():
    with pytest.raises(ValueError):
        build_mccfr_chance_branch(
            _showdown_terminal(), card=_BOARD[0], board=_BOARD, combos=_COMBOS,
            positions=_POSITIONS, effective_stack_bb=15.0,
        )


def test_build_mccfr_chance_branch_rejects_negative_remaining_stack():
    terminal = _showdown_terminal(pot=30.0, invested=20.0)  # invested more than the stack allows
    with pytest.raises(ValueError):
        build_mccfr_chance_branch(
            terminal, card=_A_CARD, board=_BOARD, combos=_COMBOS, positions=_POSITIONS, effective_stack_bb=15.0,
        )


def test_build_mccfr_chance_branch_root_is_a_turn_street_decision_node_when_stack_remains():
    terminal = _showdown_terminal(pot=10.0, invested=5.0)
    branch = build_mccfr_chance_branch(
        terminal, card=_A_CARD, board=_BOARD, combos=_COMBOS, positions=_POSITIONS, effective_stack_bb=15.0,
    )
    assert isinstance(branch.root, DecisionNode)
    assert branch.root.player_to_act == "OOP"
    assert branch.root.pot == pytest.approx(10.0)
    assert branch.root.invested == {"OOP": 0.0, "IP": 0.0}
    assert branch.card == _A_CARD
    assert branch.board == _BOARD + (_A_CARD,)


def test_build_mccfr_chance_branch_reuses_the_terminal_when_no_stack_remains():
    terminal = _showdown_terminal(pot=30.0, invested=15.0)  # both already all-in at 15bb
    branch = build_mccfr_chance_branch(
        terminal, card=_A_CARD, board=_BOARD, combos=_COMBOS, positions=_POSITIONS, effective_stack_bb=15.0,
    )
    assert branch.root is terminal
    assert branch.chance_fn is None


def test_build_mccfr_chance_branch_every_branch_chance_fn_is_none():
    # M32's explicit one-hop scope: no auto-chaining to a river street yet
    # — a regression guard against silently reintroducing that.
    terminal = _showdown_terminal(pot=10.0, invested=5.0)
    branch = build_mccfr_chance_branch(
        terminal, card=_A_CARD, board=_BOARD, combos=_COMBOS, positions=_POSITIONS, effective_stack_bb=15.0,
    )
    assert branch.chance_fn is None


def test_build_mccfr_chance_branch_equity_cache_is_scoped_to_the_next_board():
    terminal = _showdown_terminal()
    branch = build_mccfr_chance_branch(
        terminal, card=_A_CARD, board=_BOARD, combos=_COMBOS, positions=_POSITIONS, effective_stack_bb=15.0,
    )
    assert isinstance(branch.equity_cache, NwayBoardEquityCache)
    assert branch.equity_cache.board == _BOARD + (_A_CARD,)
    aa, kk = _COMBOS
    vector = branch.equity_cache.traverser_equity_vector((kk,))
    assert 0.0 <= vector[0] <= 1.0
    assert not np.isnan(vector[0])  # AA vs KK, no card conflicts — a real, computable value


def test_build_mccfr_chance_branch_is_deterministic_across_calls():
    kwargs = dict(
        terminal=_showdown_terminal(), card=_A_CARD, board=_BOARD, combos=_COMBOS,
        positions=_POSITIONS, effective_stack_bb=15.0,
    )
    branch_1 = build_mccfr_chance_branch(**kwargs)
    branch_2 = build_mccfr_chance_branch(**kwargs)
    aa, kk = _COMBOS
    vector_1 = branch_1.equity_cache.traverser_equity_vector((kk,))
    vector_2 = branch_2.equity_cache.traverser_equity_vector((kk,))
    assert np.array_equal(vector_1, vector_2, equal_nan=True)


def test_build_mccfr_chance_branch_preserves_relative_order_of_live_positions():
    # The direct regression test for the real bug caught during M32's own
    # design: game_tree.postflop_action_order is for converting a
    # *preflop* GameConfig.positions tuple into postflop order — applying
    # it here (to an already-postflop-native StreetConfig-shaped tuple)
    # would silently produce the WRONG order. A plain filter is correct.
    # Confirmed by direct execution during design that the wrong approach
    # returns ('IP', 'OOP') here, not ('OOP', 'IP') — this test is
    # specifically shaped to fail under that mistake.
    terminal = _three_way_showdown_terminal_mid_folded()
    branch = build_mccfr_chance_branch(
        terminal, card=_A_CARD, board=_BOARD, combos=_COMBOS,
        positions=_THREE_POSITIONS, effective_stack_bb=15.0,
    )
    assert isinstance(branch.root, DecisionNode)
    assert branch.root.player_to_act == "OOP"


def test_build_mccfr_chance_branch_excludes_the_folded_position_entirely():
    terminal = _three_way_showdown_terminal_mid_folded()
    branch = build_mccfr_chance_branch(
        terminal, card=_A_CARD, board=_BOARD, combos=_COMBOS,
        positions=_THREE_POSITIONS, effective_stack_bb=15.0,
    )
    for node in walk(branch.root):
        if isinstance(node, DecisionNode):
            assert node.player_to_act != "MID"
            assert "MID" not in node.invested
            assert "MID" not in node.folded
        else:
            assert "MID" not in node.invested
            assert "MID" not in node.folded


# ---------------------------------------------------------------------------
# M39: chain_to_river — build_mccfr_chance_branch's own second-hop flag,
# mirroring build_chance_node's identical M13 parameter/semantics. Unlike
# build_chance_node's own closure (which needs the _b=/_s= default-
# argument trick to avoid several branches sharing one loop's last
# values by reference), this function builds exactly one branch per
# call, so next_board/etc. are already this call's own locals — no loop
# to share variables across. See the function's own docstring.
# ---------------------------------------------------------------------------

_ANOTHER_CARD = Card("3", "c")  # not on _BOARD or _A_CARD's own resulting board


def test_build_mccfr_chance_branch_chain_to_river_defaults_to_false():
    # Byte-for-byte the same as omitting the kwarg entirely — the M32
    # backward-compat guarantee, restated for the new parameter.
    terminal = _showdown_terminal(pot=10.0, invested=5.0)
    kwargs = dict(
        terminal=terminal, card=_A_CARD, board=_BOARD, combos=_COMBOS,
        positions=_POSITIONS, effective_stack_bb=15.0,
    )
    branch_omitted = build_mccfr_chance_branch(**kwargs)
    branch_explicit_false = build_mccfr_chance_branch(**kwargs, chain_to_river=False)
    assert branch_omitted.chance_fn is None
    assert branch_explicit_false.chance_fn is None


def test_build_mccfr_chance_branch_chain_to_river_populates_chance_fn_when_a_real_tree_remains():
    terminal = _showdown_terminal(pot=10.0, invested=5.0)  # 10bb behind at a 15bb stack
    branch = build_mccfr_chance_branch(
        terminal, card=_A_CARD, board=_BOARD, combos=_COMBOS,
        positions=_POSITIONS, effective_stack_bb=15.0, chain_to_river=True,
    )
    assert isinstance(branch.root, DecisionNode)
    assert callable(branch.chance_fn)


def test_build_mccfr_chance_branch_chain_to_river_never_populates_chance_fn_for_a_reused_terminal_branch():
    # The direct regression guard for the correctness pitfall M13 already
    # named for build_chance_node: an all-in-already branch's own equity
    # source already correctly averages over however many community
    # cards remain, so it must never also get a chance_fn — enforced
    # structurally (same if/else that decides root = terminal), not by a
    # separate check that could drift.
    terminal = _showdown_terminal(pot=30.0, invested=15.0)  # both already all-in at 15bb
    branch = build_mccfr_chance_branch(
        terminal, card=_A_CARD, board=_BOARD, combos=_COMBOS,
        positions=_POSITIONS, effective_stack_bb=15.0, chain_to_river=True,
    )
    assert branch.root is terminal
    assert branch.chance_fn is None


def test_build_mccfr_chance_branch_chain_to_river_river_branch_chance_fn_is_none_even_with_stack_remaining():
    # Isolates the "board already complete" half of the guard from the
    # "no stack left" half above: plenty of stack remains, but the board
    # passed in is already a turn board (4 cards) — the branch this
    # produces is a complete 5-card river board, so there's nothing left
    # to chain regardless of how much stack is behind.
    turn_board = _BOARD + (Card("K", "s"),)
    terminal = _showdown_terminal(pot=10.0, invested=5.0)
    branch = build_mccfr_chance_branch(
        terminal, card=_A_CARD, board=turn_board, combos=_COMBOS,
        positions=_POSITIONS, effective_stack_bb=15.0, chain_to_river=True,
    )
    assert isinstance(branch.root, DecisionNode)  # real stack remained
    assert branch.chance_fn is None  # but nothing left to deal


def test_build_mccfr_chance_branch_chain_to_river_second_hop_reaches_a_complete_river_board():
    turn_terminal = _showdown_terminal(pot=10.0, invested=5.0)
    turn_branch = build_mccfr_chance_branch(
        turn_terminal, card=_A_CARD, board=_BOARD, combos=_COMBOS,
        positions=_POSITIONS, effective_stack_bb=15.0, chain_to_river=True,
    )
    assert len(turn_branch.board) == 4
    assert callable(turn_branch.chance_fn)

    # Mirrors how cfr.py's own _mccfr_recurse actually drives this: a
    # real showdown terminal reached inside the turn branch's own
    # subtree, handed straight back to that branch's own chance_fn.
    river_showdown = TerminalNode(pot=turn_branch.root.pot, invested={"OOP": 5.0, "IP": 5.0}, folded=frozenset())
    river_branch = turn_branch.chance_fn(river_showdown, _ANOTHER_CARD)

    assert len(river_branch.board) == 5
    assert river_branch.chance_fn is None  # nothing left to deal — the last possible hop


def test_build_mccfr_chance_branch_chain_to_river_is_deterministic_across_calls():
    kwargs = dict(
        terminal=_showdown_terminal(pot=10.0, invested=5.0), card=_A_CARD, board=_BOARD, combos=_COMBOS,
        positions=_POSITIONS, effective_stack_bb=15.0, chain_to_river=True,
    )
    branch_1 = build_mccfr_chance_branch(**kwargs)
    branch_2 = build_mccfr_chance_branch(**kwargs)
    aa, kk = _COMBOS
    vector_1 = branch_1.equity_cache.traverser_equity_vector((kk,))
    vector_2 = branch_2.equity_cache.traverser_equity_vector((kk,))
    assert np.array_equal(vector_1, vector_2, equal_nan=True)


# ---------------------------------------------------------------------------
# M55: equity-table memoization across chance nodes. A branch's equity
# table is a pure function of (next_board, combos) — it does NOT depend
# on which terminal the chance node hangs off, since the terminal only
# influences the branch's TREE (via remaining_stack). Measured at exactly
# 7.00x redundancy on a real /solve_turn_from_path query before this.
# ---------------------------------------------------------------------------


def test_build_chance_node_without_a_cache_rebuilds_tables_for_each_terminal(monkeypatch):
    import poker_solver.chance as chance_module

    calls = []
    original = chance_module.build_board_equity_table
    monkeypatch.setattr(
        chance_module, "build_board_equity_table",
        lambda board, combos, *a, **k: (calls.append(board), original(board, combos, *a, **k))[1],
    )
    for _ in range(2):
        build_chance_node(
            _showdown_terminal(), board=_BOARD, combos=_COMBOS,
            positions=_POSITIONS, effective_stack_bb=15.0,
        )
    # Two independent nodes, no shared cache -> every table built twice.
    assert len(calls) == 2 * len(set(calls))


def test_build_chance_node_shares_equity_tables_across_terminals_via_the_cache(monkeypatch):
    import poker_solver.chance as chance_module

    calls = []
    original = chance_module.build_board_equity_table
    monkeypatch.setattr(
        chance_module, "build_board_equity_table",
        lambda board, combos, *a, **k: (calls.append(board), original(board, combos, *a, **k))[1],
    )
    cache: dict = {}
    # Two DIFFERENT terminals (different invested -> different remaining
    # stack -> genuinely different trees), same board and combo pool.
    first = build_chance_node(
        _showdown_terminal(invested=5.0), board=_BOARD, combos=_COMBOS,
        positions=_POSITIONS, effective_stack_bb=15.0, equity_table_cache=cache,
    )
    after_first = len(calls)
    second = build_chance_node(
        _showdown_terminal(invested=7.0), board=_BOARD, combos=_COMBOS,
        positions=_POSITIONS, effective_stack_bb=15.0, equity_table_cache=cache,
    )
    # The second node built ZERO new tables — every one was a cache hit.
    assert len(calls) == after_first
    assert len(cache) == after_first

    # ...and the tables really are shared, not merely equal: the branches
    # hold the same array objects.
    for card in first.branches:
        assert second.branches[card].equity_table is first.branches[card].equity_table
    # The trees, meanwhile, are genuinely different — proving the cache
    # shares only what's terminal-independent.
    assert first.branches[next(iter(first.branches))].root is not second.branches[
        next(iter(second.branches))
    ].root


def test_chance_node_equity_tables_are_identical_with_and_without_the_cache():
    # The correctness claim behind M55: memoizing changes nothing, because
    # it's the same pure function called with the same arguments.
    uncached = build_chance_node(
        _showdown_terminal(), board=_BOARD, combos=_COMBOS,
        positions=_POSITIONS, effective_stack_bb=15.0,
    )
    cached = build_chance_node(
        _showdown_terminal(), board=_BOARD, combos=_COMBOS,
        positions=_POSITIONS, effective_stack_bb=15.0, equity_table_cache={},
    )
    assert set(uncached.branches) == set(cached.branches)
    for card in uncached.branches:
        assert np.array_equal(
            uncached.branches[card].equity_table, cached.branches[card].equity_table
        )


@pytest.mark.parametrize("board_text,stack", [
    ("2h 6d 9c", 20.0), ("As Ks Qs", 20.0), ("7h 7d 2c", 5.0), ("Td 9d 8c", 5.0),
])
def test_a_chance_node_deals_exactly_the_undealt_deck(board_text, stack):
    """M121 (audit round 13). The structural contract of a chance node,
    checked at every showdown terminal of whole street trees.

    A branch's probability is implicit — `cfr._solve_recurse` averages
    `sum(branch_values) / len(branch_values)` — so uniformity follows
    from the branch SET being right, which makes that set the thing
    worth pinning. 24 chance nodes across four boards and two stack
    depths, zero violations.
    """
    from poker_solver.cards import remaining_deck
    from poker_solver.game_tree import StreetConfig, build_street_tree

    board = tuple(Card.from_str(token) for token in board_text.split())
    deck = [Card.from_str(r + s) for r in "23456789TJQKA" for s in "cdhs"]
    rest = [card for card in deck if card not in set(board)]
    combos = [HandCombo(rest[i], rest[i + 1]) for i in range(0, 12, 2)]

    config = StreetConfig(positions=_POSITIONS, pot=6.0, stack_bb=stack,
                          raise_sizes=(), max_raises=1)
    checked = 0
    for node in walk(build_street_tree(config)):
        if not isinstance(node, TerminalNode) or not node.is_showdown:
            continue
        chance = build_chance_node(node, board, combos, _POSITIONS,
                                   effective_stack_bb=stack, raise_sizes=(), max_raises=1)
        if chance is None:
            continue
        checked += 1
        assert set(chance.branches) == set(remaining_deck(board))
        assert not (set(chance.branches) & set(board)), "dealt a card already on the board"
        for card, branch in chance.branches.items():
            assert branch.card == card, "a branch is filed under a different card"
            assert np.asarray(branch.equity_table).shape == (len(combos), len(combos))
    assert checked, "this board/stack produced no chance nodes to check"


def test_the_impossible_branch_bias_is_bounded_and_always_toward_neutral():
    """M121. This module's docstring documents an approximation from
    M12: `remaining_deck` excludes only the board, so for any combo
    exactly 2 branches deal a card that combo physically holds, and that
    combo's equity there becomes 0.5 rather than being masked out.

    The note asserted the bias "nets toward neutral rather than a wrong
    extreme" and gave no number. Both halves are measured here:

      * exactly **2 of 49** branches per combo are impossible (4.08%);
      * every combo's biased equity stays on the same side of 0.5 as the
        masked one and moves strictly closer to it — a compression, not
        a directional error;
      * mean |bias| 0.0053, **max 0.0131** equity, signed mean ~1e-05
        across the pool.

    That ceiling is why the deferred fix (per-branch reach masking) is
    still deferred: it is an order of magnitude below the flop-level
    terminal-pricing distortion M99 measured at ~5pp per street and
    deliberately chose not to surface. If this bound is ever measured
    materially higher, that reasoning no longer holds.
    """
    import random

    from poker_solver.cards import remaining_deck
    from poker_solver.game_tree import StreetConfig, build_street_tree

    board = (Card("2", "h"), Card("6", "d"), Card("9", "c"))
    rng = random.Random(7)
    deck = [Card.from_str(r + s) for r in "23456789TJQKA" for s in "cdhs"]
    rest = [card for card in deck if card not in set(board)]
    combos, used = [], set()
    while len(combos) < 12:
        a, b = rng.sample(rest, 2)
        if a in used or b in used:
            continue
        combos.append(HandCombo(a, b))
        used |= {a, b}

    config = StreetConfig(positions=_POSITIONS, pot=6.0, stack_bb=20.0,
                          raise_sizes=(), max_raises=1)
    terminal = next(node for node in walk(build_street_tree(config))
                    if isinstance(node, TerminalNode) and node.is_showdown)
    chance = build_chance_node(terminal, board, combos, _POSITIONS,
                               effective_stack_bb=20.0, raise_sizes=(), max_raises=1)
    legal = list(remaining_deck(board))
    assert len(legal) == 49

    biases = []
    for index, combo in enumerate(combos):
        held = {combo.card_a, combo.card_b}
        assert sum(1 for card in legal if card in held) == 2

        per_branch = np.array([np.nanmean(np.asarray(chance.branches[card].equity_table)[index])
                               for card in legal])
        keep = np.array([card not in held for card in legal])
        as_solved = float(np.mean(per_branch))
        masked = float(np.mean(per_branch[keep]))

        assert (as_solved - 0.5) * (masked - 0.5) >= 0, (
            f"{combo}: the bias flipped which side of neutral the hand is on"
        )
        assert abs(as_solved - 0.5) <= abs(masked - 0.5) + 1e-9, (
            f"{combo}: the bias pushed AWAY from neutral"
        )
        biases.append(as_solved - masked)

    assert max(abs(b) for b in biases) < 0.02, (
        f"the documented approximation is larger than recorded: {max(abs(b) for b in biases):.4f}"
    )


def test_an_injected_equity_batch_builds_the_same_node_as_sequential():
    """M129 (B). `build_chance_node` builds its branches' equity tables
    as a batch first, so a caller can compute them in parallel — a turn
    node builds ~49 of them and a river solve ~2,400, each a pure
    function of (board, combos) and independent of the rest.

    The contract that matters: injecting a mapper must not change the
    node. A caller that parallelises and one that does not must get the
    same answer, or the advice depends on the host's core count.
    """
    from poker_solver.game_tree import StreetConfig, build_street_tree

    board = (Card("2", "h"), Card("6", "d"), Card("9", "c"))
    deck = [Card.from_str(r + s) for r in "23456789TJQKA" for s in "cdhs"]
    rest = [card for card in deck if card not in set(board)]
    combos = [HandCombo(rest[i], rest[i + 1]) for i in range(0, 8, 2)]

    config = StreetConfig(positions=_POSITIONS, pot=6.0, stack_bb=20.0,
                          raise_sizes=(), max_raises=1)
    terminal = next(node for node in walk(build_street_tree(config))
                    if isinstance(node, TerminalNode) and node.is_showdown)

    def build(batch_fn):
        return build_chance_node(terminal, board, combos, _POSITIONS,
                                 effective_stack_bb=20.0, raise_sizes=(), max_raises=1,
                                 equity_batch_fn=batch_fn)

    seen = {}

    def spy(boards, pool):
        """A mapper that does exactly what the sequential path does, and
        records that it was actually consulted — otherwise this test
        could pass while the hook was silently ignored."""
        from poker_solver.board_equity import build_board_equity_table
        seen["boards"] = len(boards)
        return [np.nan_to_num(build_board_equity_table(b, pool), nan=0.5) for b in boards]

    sequential = build(None)
    injected = build(spy)

    assert seen.get("boards"), "the injected mapper was never called"
    assert seen["boards"] == len(sequential.branches)
    assert set(sequential.branches) == set(injected.branches)
    for card, branch in sequential.branches.items():
        assert np.allclose(branch.equity_table,
                           injected.branches[card].equity_table, equal_nan=True), card
