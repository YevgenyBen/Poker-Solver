"""The control that would have caught F59, as a test.

M256 shipped four controls and all of them were internal. This one asks
the solver what it thinks of its own strategy and checks the walk
against that, which is the only kind of control a wrong INPUT cannot
satisfy by construction.
"""
import pytest

from bench.dump_control import (ControlFailed, MAX_PLAUSIBLE_RATIO, check,
                                range_weighted_slack, require)


def _summary(slack_bb):
    return {"hands": 26, "weighted_mean_slack_bb": slack_bb,
            "median_slack_bb": slack_bb, "max_slack_bb": slack_bb,
            "negative_slacks": 0}


def test_a_walk_that_matches_the_solver_passes():
    ok, detail = check(_summary(0.085), pot=33.0,
                       reported_exploitability_pct=0.256)
    assert ok
    assert detail["ratio"] == pytest.approx(1.0, abs=0.05)


def test_the_real_failure_is_refused():
    """The figures M257 actually measured: 0.711 bb on a 33bb pot where
    the solver reported 0.256%, a ratio of 8.4x."""
    ok, detail = check(_summary(0.711), pot=33.0,
                       reported_exploitability_pct=0.256)
    assert not ok
    assert detail["ratio"] == pytest.approx(8.4, abs=0.1)
    with pytest.raises(ControlFailed, match="overstating"):
        require(_summary(0.711), pot=33.0, reported_exploitability_pct=0.256)


def test_a_walk_UNDER_the_reported_figure_is_fine():
    """The bound is one-sided. A root-only deviation gaining LESS than a
    full best response is exactly what should happen."""
    ok, _ = check(_summary(0.01), pot=33.0, reported_exploitability_pct=0.256)
    assert ok


def test_the_allowance_covers_definitional_slop_and_no_more():
    assert MAX_PLAUSIBLE_RATIO == 4.0
    assert check(_summary(0.085 * 3.9), pot=33.0,
                 reported_exploitability_pct=0.256)[0]
    assert not check(_summary(0.085 * 4.5), pot=33.0,
                     reported_exploitability_pct=0.256)[0]


def test_a_hand_the_board_blocks_is_skipped_not_scored_zero():
    """A hand that cannot be held is not a hand the solver got wrong;
    counting it at zero would dilute the mean toward passing."""
    calls = []

    def walk_for(hero_key):                       # pragma: no cover - not hit
        calls.append(hero_key)
        raise AssertionError("a blocked hand must never be walked")

    got = range_weighted_slack(
        node={"strategy": {"actions": ["CHECK"],
                           "strategy": {"CHECK": [1.0]}}},
        walk_for=walk_for, board=("8h", "6h", "2s"),
        weights={"8h6h": 1.0}, hero_keys=["8h6h"])
    assert got is None
    assert calls == []


def test_a_hand_outside_the_range_is_skipped_too():
    def walk_for(hero_key):                       # pragma: no cover - not hit
        raise AssertionError("a hand the range never held must not be walked")

    assert range_weighted_slack(
        node={}, walk_for=walk_for, board=("8h", "6h", "2s"),
        weights={}, hero_keys=["AcKd"]) is None
