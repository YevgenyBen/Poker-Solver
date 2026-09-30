"""The multiway warm-start seam (M312).

`mccfr_solve(initial_node_data=)` and `solve_flop_multiway(warm_start=)`
mirror the exact solver's M158 seam. Off by default, so an unused seam
must change nothing; given a donor, it must actually be used; and the two
things it refuses - a wrong-shaped table, and a warm start combined with
an ensemble - must be refused by name rather than guessed at.
"""
from __future__ import annotations

import copy

import numpy as np
import pytest

from poker_solver.cards import Card
from poker_solver.combos import HandCombo
from poker_solver.solver import solve_flop_multiway
from poker_solver.warmstart import index_by_path

BOARD = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
RANGES = {
    "OOP": {HandCombo(Card("7", "s"), Card("7", "c")): 1.0,
            HandCombo(Card("K", "s"), Card("Q", "d")): 1.0},
    "MID": {HandCombo(Card("A", "h"), Card("A", "d")): 1.0,
            HandCombo(Card("3", "h"), Card("2", "h")): 1.0},
    "IP": {HandCombo(Card("9", "d"), Card("8", "d")): 1.0,
           HandCombo(Card("Q", "c"), Card("5", "c")): 1.0},
}


def _kwargs(**over):
    kw = dict(board=BOARD, position_ranges=RANGES, pot=9.0,
              effective_stack_bb=15.0, positions=("OOP", "MID", "IP"),
              raise_sizes=(), max_raises=1, iterations=60,
              equity_samples=50, equity_seed=7, seed=3)
    kw.update(over)
    return kw


def _warm(result):
    """A donor's tables in the seam's `(cached_hands, by_path)` shape."""
    return (list(result.hands),
            copy.deepcopy(index_by_path(result.root, result.node_data)))


def test_an_unused_seam_changes_nothing():
    """`warm_start=None` is the default, so it must be the old behaviour."""
    a = solve_flop_multiway(**_kwargs())
    b = solve_flop_multiway(**_kwargs(warm_start=None))
    assert a.opening_range() == b.opening_range()


def test_a_warm_start_with_zero_iterations_returns_the_donor():
    """With nothing run on top, the grafted tables ARE the answer - the
    plainest proof that the seam delivers what it is handed."""
    donor = solve_flop_multiway(**_kwargs(iterations=200))
    warm = solve_flop_multiway(**_kwargs(iterations=0, warm_start=_warm(donor)))
    assert warm.opening_range() == donor.opening_range()


def test_a_warm_start_changes_a_short_solve():
    """At a small budget, starting from a converged donor must land
    somewhere a cold solve of that budget does not - otherwise the seam
    is inert."""
    donor = solve_flop_multiway(**_kwargs(iterations=400, seed=11))
    cold = solve_flop_multiway(**_kwargs(iterations=5))
    warm = solve_flop_multiway(**_kwargs(iterations=5, warm_start=_warm(donor)))
    assert warm.opening_range() != cold.opening_range()


def test_the_seam_refuses_a_table_shaped_for_another_pool():
    from poker_solver.cfr import InfoSetTable, mccfr_solve
    donor = solve_flop_multiway(**_kwargs(iterations=20))
    bad = {key: InfoSetTable(regret_sum=np.zeros((1, t.regret_sum.shape[1])),
                             strategy_sum=np.zeros((1, t.regret_sum.shape[1])),
                             last_regret=None, last_strategy=None)
           for key, t in donor.node_data.items()}
    # Postflop combos carry no `combo_weight`, so every position needs an
    # explicit reach - as `solve_flop_multiway` always supplies.
    reach = {p: np.ones(len(donor.hands)) for p in ("OOP", "MID", "IP")}
    with pytest.raises(ValueError, match="hand rows"):
        mccfr_solve(donor.root, donor.hands, ("OOP", "MID", "IP"),
                    equity_cache=None, iterations=1, initial_reach=reach,
                    initial_node_data=bad)


def test_a_warm_start_cannot_be_combined_with_an_ensemble():
    donor = solve_flop_multiway(**_kwargs(iterations=20))
    with pytest.raises(ValueError, match="ensemble"):
        solve_flop_multiway(**_kwargs(ensemble=2, warm_start=_warm(donor)))


def test_a_hand_the_donor_lacked_starts_cold():
    """A donor solved without one of this pool's hands hands it nothing:
    that row is zero, as in a cold solve, while shared hands keep theirs."""
    smaller = {**RANGES, "IP": {HandCombo(Card("9", "d"), Card("8", "d")): 1.0}}
    donor = solve_flop_multiway(**_kwargs(position_ranges=smaller, iterations=100))
    from poker_solver.warmstart import graft_node_data
    target = solve_flop_multiway(**_kwargs(iterations=0))
    cached_hands, by_path = _warm(donor)
    grafted = graft_node_data(target.root, by_path, cached_hands, list(target.hands))
    missing = target.hands.index(HandCombo(Card("Q", "c"), Card("5", "c")))
    kept = target.hands.index(HandCombo(Card("A", "h"), Card("A", "d")))
    root_table = grafted[id(target.root)]
    assert not root_table.strategy_sum[missing].any()
    assert root_table.strategy_sum[kept].any() or root_table.regret_sum[kept].any()


def test_the_donors_own_tables_are_not_mutated_by_the_graft():
    """`graft_node_data` builds fresh arrays, so solving on top of a warm
    start cannot corrupt the donor a study goes on to score."""
    donor = solve_flop_multiway(**_kwargs(iterations=100))
    before = {k: t.regret_sum.copy() for k, t in donor.node_data.items()}
    solve_flop_multiway(**_kwargs(iterations=20,
                                  warm_start=(list(donor.hands),
                                              index_by_path(donor.root, donor.node_data))))
    for key, table in donor.node_data.items():
        assert np.array_equal(table.regret_sum, before[key])
