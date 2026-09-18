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


def test_the_bound_is_epsilon_only_at_the_root():
    """M259. Exploitability bounds the reach-weighted SUM of regrets, so
    a node reached with probability p may carry up to epsilon / p. The
    same slack that fails at the root passes at a node reached a tenth of
    the time."""
    at_root, _ = check(_summary(0.711), pot=33.0,
                       reported_exploitability_pct=0.256)
    deep, detail = check(_summary(0.711), pot=33.0,
                         reported_exploitability_pct=0.256, reach=0.1)
    assert not at_root
    assert deep
    assert detail["allowed_pct_of_pot"] == pytest.approx(2.56)


def test_reach_defaults_to_the_root_so_old_callers_are_unchanged():
    ok_default, d1 = check(_summary(0.085), pot=33.0,
                           reported_exploitability_pct=0.256)
    ok_root, d2 = check(_summary(0.085), pot=33.0,
                        reported_exploitability_pct=0.256, reach=1.0)
    assert ok_default == ok_root
    assert d1["ratio"] == pytest.approx(d2["ratio"])


def test_an_impossible_reach_is_refused():
    for bad in (0.0, -0.1, 1.5):
        with pytest.raises(ValueError, match="reach"):
            check(_summary(0.1), pot=33.0, reported_exploitability_pct=0.256,
                  reach=bad)


def test_a_reference_is_judged_on_its_own_best_response_too():
    """A13 (M276). The solver's reported figure stops describing the
    strategy it dumps once the tree is deep: on one four-bet flop spot it
    fell 38x between 100 and 700 iterations while the dumped strategy's
    own best-response gain barely moved (1.68% -> 1.46% of pot). So a
    reference is also gated on what the DUMP says about itself."""
    from bench.dump_control import (MAX_REFERENCE_BR_PCT,
                                    reference_is_precise_enough)

    ok, pct = reference_is_precise_enough(0.48, 33.0)      # the four-bet flop
    assert not ok and pct == pytest.approx(1.4545, abs=1e-3)
    ok, pct = reference_is_precise_enough(0.059, 15.0)     # a river reference
    assert ok and pct == pytest.approx(0.3933, abs=1e-3)
    assert MAX_REFERENCE_BR_PCT == 1.0
    with pytest.raises(ValueError):
        reference_is_precise_enough(0.1, 0.0)
