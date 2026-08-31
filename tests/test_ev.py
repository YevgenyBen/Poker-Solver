"""EV loss: pricing a frequency error in chips."""
import numpy as np
import pytest

from poker_solver.ev import action_values, ev_loss, strategy_ev
from poker_solver.game_tree import StreetConfig, build_street_tree


def _tree(raise_sizes=(2.0,), max_raises=2, pot=10.0, stack=20.0):
    # game_tree requires exactly max_raises - 1 sizes, so one size means
    # max_raises=2 — a bet and the all-in that can follow it.
    return build_street_tree(StreetConfig(
        positions=("OOP", "IP"), pot=pot, stack_bb=stack,
        raise_sizes=raise_sizes, max_raises=max_raises))


def _uniform(_node):
    return None          # strategy_fn returning None means "uniform"


def test_a_hand_that_always_wins_prefers_putting_money_in():
    """The sanity floor: with 100% equity, every action that grows the pot
    must be worth more than checking it down."""
    root = _tree()
    equity = np.array([[1.0]])
    values = action_values(
        root, hero_position=root.player_to_act, hero_index=0, hero_is_a=True,
        equity_table=equity, opp_reach=np.array([1.0]), strategy_fn=_uniform)
    assert values, "no legal actions"
    check = next((v for a, v in values.items() if a.kind == "call_or_check"), None)
    bets = [v for a, v in values.items() if a.kind in ("raise", "all_in")]
    assert check is not None and bets
    assert max(bets) > check, (
        f"a hand with 100% equity should profit from betting: {values}")


def _never_folds(node):
    """An opponent who always takes call_or_check — no fold equity."""
    actions = node.legal_actions
    row = np.zeros(len(actions))
    for i, action in enumerate(actions):
        if action.kind == "call_or_check":
            row[i] = 1.0
    if not row.any():
        row[:] = 1.0 / len(actions)
    return np.tile(row, (1, 1))


def test_a_hand_that_never_wins_prefers_not_to_build_a_pot_it_cannot_win():
    """Against an opponent who NEVER FOLDS, 0% equity must prefer checking.

    The uniform opponent is the wrong control here and the first version of
    this test used it: a uniform opponent folds to the all-in half the
    time, and that fold equity exactly offsets having no showdown equity —
    both actions priced at -5.0, and the code was right. Bluffing is only
    unprofitable when nobody folds.
    """
    root = _tree()
    values = action_values(
        root, hero_position=root.player_to_act, hero_index=0, hero_is_a=True,
        equity_table=np.array([[0.0]]), opp_reach=np.array([1.0]),
        strategy_fn=_never_folds)
    check = next(v for a, v in values.items() if a.kind == "call_or_check")
    bets = [v for a, v in values.items() if a.kind in ("raise", "all_in")]
    assert max(bets) < check, (
        f"0% equity with no fold equity should not build a pot: {values}")


def test_the_second_position_is_priced_in_its_OWN_chips():
    """`equity_table[a, b]` is position A's equity, so B's is one minus the
    transposed entry. Getting that backwards prices hero's hand as the
    opponent's — and it is invisible at 50% equity, so the fixture is
    deliberately lopsided.
    """
    root = _tree()
    equity = np.array([[0.9]])          # A wins 90%, so B wins 10%
    common = dict(equity_table=equity, opp_reach=np.array([1.0]),
                  strategy_fn=_uniform, hero_index=0)
    as_a = action_values(root, hero_position="OOP", hero_is_a=True, **common)
    as_b = action_values(root, hero_position="IP", hero_is_a=False, **common)
    bet_a = max(v for a, v in as_a.items() if a.kind in ("raise", "all_in"))
    bet_b = max(v for a, v in as_b.items() if a.kind in ("raise", "all_in"))
    assert bet_a > bet_b, (
        "the 90% side must price a bet higher than the 10% side; equal or "
        f"inverted means the equity axis is transposed ({bet_a} vs {bet_b})")


def test_loss_is_zero_when_the_two_strategies_agree():
    root = _tree()
    values = action_values(
        root, hero_position=root.player_to_act, hero_index=0, hero_is_a=True,
        equity_table=np.array([[0.6]]), opp_reach=np.array([1.0]),
        strategy_fn=_uniform)
    actions = root.legal_actions
    row = np.full(len(actions), 1.0 / len(actions))
    out = ev_loss(row, row, values, actions)
    assert out["loss_bb"] == pytest.approx(0.0, abs=1e-12)


def test_a_big_frequency_error_between_EQUAL_actions_costs_nothing():
    """The property this whole module exists for.

    A frequency distance of 1.0 — the largest possible — costs exactly
    zero when the actions it moves between are worth the same. Solvers mix
    precisely when actions are near-indifferent, so that is where large
    frequency errors actually live, and it is why frequency distance is a
    convergence measure rather than a quality one.
    """
    actions = ["a", "b"]
    values = {"a": 1.25, "b": 1.25}
    out = ev_loss(np.array([1.0, 0.0]), np.array([0.0, 1.0]), values, actions)
    assert out["loss_bb"] == pytest.approx(0.0)
    assert out["value_spread_bb"] == pytest.approx(0.0)

    # The same frequency error where the actions differ costs the spread.
    values = {"a": 0.0, "b": 2.0}
    out = ev_loss(np.array([1.0, 0.0]), np.array([0.0, 1.0]), values, actions)
    assert out["loss_bb"] == pytest.approx(2.0)
    assert out["value_spread_bb"] == pytest.approx(2.0)


def test_loss_is_signed_so_the_shipped_row_can_price_better():
    """A metric that can only find fault is not measuring."""
    actions = ["a", "b"]
    values = {"a": 3.0, "b": 0.0}
    out = ev_loss(np.array([1.0, 0.0]), np.array([0.0, 1.0]), values, actions)
    assert out["loss_bb"] < 0
    assert out["best_action"] == "a"


def test_loss_ignores_a_constant_offset_in_the_payoffs():
    """`cfr.py` values the second position as minus the first's, offsetting
    it by the dead pot (F45). This module computes the true payoff instead
    — but the loss must not depend on that choice, since a constant across
    hero's actions cancels from a difference of two expectations.
    """
    actions = ["a", "b"]
    shipped, reference = np.array([0.8, 0.2]), np.array([0.1, 0.9])
    base = {"a": 1.0, "b": 2.5}
    shifted = {k: v + 7.5 for k, v in base.items()}
    assert (ev_loss(shipped, reference, base, actions)["loss_bb"]
            == pytest.approx(ev_loss(shipped, reference, shifted, actions)["loss_bb"]))


def test_strategy_ev_weights_by_the_row():
    values = {"a": 1.0, "b": 3.0}
    assert strategy_ev(np.array([0.5, 0.5]), values, ["a", "b"]) == pytest.approx(2.0)
    assert strategy_ev(np.array([0.0, 1.0]), values, ["a", "b"]) == pytest.approx(3.0)
