import numpy as np
import pytest

from poker_solver.equity import build_equity_table
from poker_solver.game_tree import GameConfig
from poker_solver.solver import DEFAULT_ITERATIONS, format_opening_range_grid, solve_preflop
from poker_solver.starting_hands import StartingHand

# A tiny hand set + freshly-built (small, fast) equity table, so these
# structural tests never touch the slow-to-build-the-first-time full
# 169x169 cached table.
_SMALL_HANDS = [StartingHand("A", "A"), StartingHand("K", "K"), StartingHand("7", "2", suited=False)]
_SMALL_EQUITY_TABLE = build_equity_table(hands=_SMALL_HANDS, samples=30)


# ---------------------------------------------------------------------------
# Structural correctness (small config + small hand set: fast, no
# dependency on the full 169x169 cached equity table).
# ---------------------------------------------------------------------------


def test_opening_range_covers_every_hand():
    config = GameConfig(raise_sizes=(), max_raises=1)
    result = solve_preflop(iterations=20, config=config, hands=_SMALL_HANDS, equity_table=_SMALL_EQUITY_TABLE)
    opening = result.opening_range()
    assert set(opening.keys()) == {str(hand) for hand in _SMALL_HANDS}


def test_opening_range_frequencies_sum_to_one():
    config = GameConfig(raise_sizes=(), max_raises=1)
    result = solve_preflop(iterations=20, config=config, hands=_SMALL_HANDS, equity_table=_SMALL_EQUITY_TABLE)
    opening = result.opening_range()
    for freqs in opening.values():
        assert freqs
        assert not any(np.isnan(freq) for freq in freqs.values())
        assert pytest.approx(sum(freqs.values()), abs=1e-9) == 1.0


def test_solve_preflop_records_elapsed_time_and_iterations():
    config = GameConfig(raise_sizes=(), max_raises=1)
    result = solve_preflop(iterations=17, config=config, hands=_SMALL_HANDS, equity_table=_SMALL_EQUITY_TABLE)
    assert result.iterations == 17
    assert result.elapsed_seconds >= 0.0


def test_custom_config_overrides_stack_bb_default():
    config = GameConfig(stack_bb=40.0, small_blind=0.5, big_blind=1.0, raise_sizes=(), max_raises=1)
    result = solve_preflop(iterations=10, config=config, hands=_SMALL_HANDS, equity_table=_SMALL_EQUITY_TABLE)
    assert result.config.stack_bb == 40.0


def test_format_opening_range_grid_runs_without_error():
    config = GameConfig(raise_sizes=(), max_raises=1)
    result = solve_preflop(iterations=10, config=config, hands=_SMALL_HANDS, equity_table=_SMALL_EQUITY_TABLE)
    text = format_opening_range_grid(result)
    assert "BTN opening range" in text
    assert "AA" in text


# ---------------------------------------------------------------------------
# Directional GTO sanity checks against known heads-up preflop intuition.
# Full 169-hand solve, backed by the real (cached) preflop equity table —
# the equity table build is a one-time cost per machine (see equity.py);
# once cached, this is fast.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def deep_stack_result():
    config = GameConfig()  # default: 100bb, standard blinds, max_raises=4
    return solve_preflop(iterations=DEFAULT_ITERATIONS, config=config)


def test_btn_opens_very_wide_at_100bb(deep_stack_result):
    opening = deep_stack_result.opening_range()
    non_fold_count = sum(1 for freqs in opening.values() if freqs.get("fold", 0.0) < 0.5)
    # BTN is the sole opener in heads-up with no one else to act first —
    # deep-stacked BTN should be opening (raising or at least not
    # folding) the clear majority of hands.
    assert non_fold_count > len(opening) * 0.6


def test_premium_hands_almost_never_fold(deep_stack_result):
    opening = deep_stack_result.opening_range()
    for label in ["AA", "KK", "AKs"]:
        assert opening[label]["fold"] < 0.05


def test_weakest_hands_fold_far_more_than_premium_hands(deep_stack_result):
    opening = deep_stack_result.opening_range()
    assert opening["72o"]["fold"] > opening["AA"]["fold"]
    assert opening["32o"]["fold"] > opening["AKs"]["fold"]
