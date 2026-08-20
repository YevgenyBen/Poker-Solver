import numpy as np
import pytest

from poker_solver.cards import Card
from poker_solver.combos import HandCombo
from poker_solver.equity import build_equity_table
from poker_solver.game_tree import GameConfig, build_game_tree
from poker_solver.solver import solve_flop, solve_preflop
from poker_solver.starting_hands import StartingHand
from poker_solver.strategy_format import format_flop_response, format_solve_response

# A tiny hand set + freshly-built (small, fast) equity table — same
# pattern test_solver.py uses, so these tests don't pay for the full
# 169x169 cached table.
_SMALL_HANDS = [StartingHand("A", "A"), StartingHand("K", "K"), StartingHand("7", "2", suited=False)]
_SMALL_EQUITY_TABLE = build_equity_table(hands=_SMALL_HANDS, samples=30)


@pytest.fixture(scope="module")
def preflop_result():
    config = GameConfig(stack_bb=100.0)
    root = build_game_tree(config)
    return solve_preflop(
        config=config, hands=_SMALL_HANDS, equity_table=_SMALL_EQUITY_TABLE, iterations=20
    )


def test_format_solve_response_has_the_expected_shape(preflop_result):
    body = format_solve_response(preflop_result)
    assert body["stack_bb"] == 100.0
    assert body["iterations"] == 20
    assert body["elapsed_seconds"] >= 0.0
    assert body["position"] == "BTN"
    assert body["positions"] == ["BTN", "BB"]
    assert set(body["opening_range"].keys()) == {str(hand) for hand in _SMALL_HANDS}
    assert set(body["trained"].keys()) == {str(hand) for hand in _SMALL_HANDS}


def test_format_solve_response_trained_is_all_true_for_the_exact_solver(preflop_result):
    # The exact HU solver visits every hand at the root exhaustively —
    # nothing should ever read as untrained here.
    body = format_solve_response(preflop_result)
    assert all(body["trained"].values())


def test_format_solve_response_defaults_to_first_to_act(preflop_result):
    default_body = format_solve_response(preflop_result)
    explicit_body = format_solve_response(preflop_result, position="BTN")
    assert default_body["opening_range"] == explicit_body["opening_range"]


def test_format_solve_response_honors_an_explicit_position(preflop_result):
    body = format_solve_response(preflop_result, position="BB")
    assert body["position"] == "BB"


@pytest.fixture(scope="module")
def flop_result():
    board = (Card("7", "h"), Card("2", "d"), Card("9", "c"))
    hero_range = {HandCombo(Card("7", "s"), Card("7", "c")): 1.0}
    villain_range = {HandCombo(Card("A", "h"), Card("K", "h")): 1.0}
    return solve_flop(
        board=board,
        hero_range=hero_range,
        villain_range=villain_range,
        pot=10.0,
        effective_stack_bb=15.0,
        max_raises=1,
        raise_sizes=(),
        iterations=20,
        equity_samples=30,
    )


def test_format_flop_response_has_the_expected_shape(flop_result):
    body = format_flop_response(flop_result, board="7h2d9c")
    assert body["board"] == "7h2d9c"
    assert body["pot"] == pytest.approx(10.0)
    assert body["stack_bb"] == pytest.approx(15.0)
    assert body["iterations"] == 20
    assert body["elapsed_seconds"] >= 0.0
    assert body["position"] == "OOP"
    assert body["positions"] == ["OOP", "IP"]
    assert set(body["strategy"].keys()) == {"7s7c", "AhKh"}
    assert set(body["trained"].keys()) == {"7s7c", "AhKh"}
    # OOP's own real combo (7s7c) is trained — the exact solver visits it
    # exhaustively. AhKh is villain's combo, zero-weight in OOP's own
    # range by construction, so OOP's reach for it is 0 throughout the
    # whole solve and its strategy_sum row never accumulates anything —
    # correctly reads as untrained too, for a different, equally valid
    # reason than MCCFR under-sampling: this hand structurally can never
    # be OOP's, at any iteration count, not just "wasn't sampled yet".
    assert body["trained"]["7s7c"] is True
    assert body["trained"]["AhKh"] is False


def test_format_flop_response_frequencies_sum_to_one(flop_result):
    body = format_flop_response(flop_result, board="7h2d9c")
    for freqs in body["strategy"].values():
        assert not any(np.isnan(freq) for freq in freqs.values())
        assert pytest.approx(sum(freqs.values()), abs=1e-6) == 1.0


def test_format_flop_response_honors_an_explicit_position(flop_result):
    body = format_flop_response(flop_result, board="7h2d9c", position="IP")
    assert body["position"] == "IP"


def test_format_flop_response_echoes_the_caller_supplied_board_string(flop_result):
    # format_flop_response never re-derives the board from `result` —
    # StreetConfig doesn't carry it — so it must faithfully echo back
    # whatever the caller passed, not silently normalize/reorder it.
    body = format_flop_response(flop_result, board="7h2d9c")
    assert body["board"] == "7h2d9c"
