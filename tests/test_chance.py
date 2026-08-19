import numpy as np
import pytest

from poker_solver.cards import Card
from poker_solver.chance import ChanceBranch, ChanceNode, build_chance_node
from poker_solver.combos import HandCombo
from poker_solver.game_tree import DecisionNode, TerminalNode

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
