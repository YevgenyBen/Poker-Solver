"""Rule tests for M310's bet-size ordering study.

Written BEFORE the arms were run, for the reason M306 recorded: its own
rule tests caught three defects before a single spot was solved, one of
which would have crashed a multi-hour run on its last line.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from bench.studies import wide_pot_ordering as study


# --------------------------------------------------------- the criterion

def test_a_correctly_ordered_spot_is_not_violated():
    """Facing a bigger bet, folding must not DECREASE."""
    assert study.violated([0.10, 0.40, 0.80]) is False
    assert study.violated([0.10, 0.10, 0.10]) is False       # flat is legal


def test_any_adjacent_pair_going_the_wrong_way_is_a_violation():
    assert study.violated([0.10, 0.05, 0.80]) is True        # first pair
    assert study.violated([0.10, 0.40, 0.20]) is True        # second pair


def test_a_violation_does_not_need_the_endpoints_to_disagree():
    """The whole reason the count is primary and the gap secondary: a spot
    can have a healthy end-to-end gap and still be wrong in the middle."""
    folds = [0.10, 0.05, 0.90]
    assert study.gap(folds) == pytest.approx(0.80)
    assert study.violated(folds) is True


def test_the_gap_is_largest_minus_smallest():
    assert study.gap([0.10, 0.40, 0.80]) == pytest.approx(0.70)
    assert study.gap([0.5]) is None


def test_fold_mass_sums_only_the_fold_actions():
    row = {"fold": 0.3, "call": 0.2, "raise:5.0": 0.4, "all_in:90.0": 0.1}
    assert study.fold_mass(row) == pytest.approx(0.3)


# ------------------------------------------------ rule 1: the size menu

def test_the_all_in_is_excluded_from_the_nameable_sizes():
    """M302: the largest `modelled_bet_sizes` entry IS the all-in, and
    naming it `raise:` is a 422."""
    assert study.nameable_sizes([22.77, 51.75, 83.5], 83.5) == [22.77, 51.75]


def test_a_shallow_spot_loses_its_third_size_and_must_be_skipped():
    """Measured at SPR 0.48: the 2.5x-pot bet collapses into the all-in and
    the menu comes back with only two nameable sizes. Such a spot has no
    three-point ordering to test."""
    sizes = study.nameable_sizes([22.77, 51.75, 83.5], 83.5)
    assert len(sizes) < study.SIZES_NEEDED


def test_a_deep_spot_keeps_three_sizes():
    assert study.nameable_sizes([6.6, 15.0, 50.0, 97.5], 97.5) == [6.6, 15.0, 50.0]


def test_the_size_filter_depends_only_on_what_the_response_carries():
    """M233's rule: the filter is a comparison against the response's own
    `max_affordable_bb`, so the SAME size list yields different answers
    purely because the response says the all-in is somewhere else. No pot
    or stack arithmetic of its own.

    (A first version of this test grepped the function's source for "pot"
    and "stack" and failed on its own docstring. A source-text assertion
    about intent is brittle; a behavioural one is not.)
    """
    sizes = [10.0, 25.0, 60.0, 97.5]
    assert study.nameable_sizes(sizes, 97.5) == [10.0, 25.0, 60.0]
    assert study.nameable_sizes(sizes, 60.0) == [10.0, 25.0]
    assert study.nameable_sizes(sizes, 10.0) == []


def test_a_size_equal_to_the_all_in_is_excluded_not_kept():
    """The boundary is where M302's 422 lives, so it is pinned."""
    assert study.nameable_sizes([22.77, 83.5], 83.5) == [22.77]
    assert study.nameable_sizes([], 97.5) == []
    assert study.nameable_sizes(None, 97.5) == []


# ------------------------------------------------------------- halving

def test_the_split_key_reads_only_the_spots_identity():
    a = dict(path="r/c/c", board="AhKs5d", hero="QcQd", stack=100.0,
             S_violated=1, W_violated=0)
    b = dict(path="r/c/c", board="AhKs5d", hero="QcQd", stack=100.0,
             S_violated=0, W_violated=1)
    assert study.digest(a) == study.digest(b)


def test_the_split_key_separates_two_different_spots():
    base = dict(path="r/c/c", board="AhKs5d", hero="QcQd", stack=100.0)
    assert study.digest(base) != study.digest(dict(base, board="2c3d4h"))
    assert study.digest(base) != study.digest(dict(base, stack=50.0))


def test_halves_do_not_alternate_in_draw_order():
    rows = [dict(path="p%d" % n, board="b", hero="h", stack=100.0)
            for n in range(40)]
    left, _ = study.halves(rows)
    positions = [0 if r in left else 1 for r in rows]
    assert positions != [n % 2 for n in range(40)]


def test_halves_partition_without_loss():
    rows = [dict(path="p%d" % n, board="b", hero="h", stack=100.0)
            for n in range(50)]
    left, right = study.halves(rows)
    assert len(left) + len(right) == 50


# -------------------------------------------------------------- paired

def _spot(n, live=4, stack=100.0, s_viol=1, w_viol=0, s_gap=0.10, w_gap=0.30):
    return {"path": "p%03d" % n, "board": "AhKs5d", "hero": "QcQd",
            "stack": stack, "live": live, "street": "flop",
            "S_violated": s_viol, "W_violated": w_viol,
            "S_gap": s_gap, "W_gap": w_gap}


def test_paired_reports_sigma_none_when_nothing_varies():
    rows = [_spot(n) for n in range(8)]
    out = study.paired(rows, "violated")
    assert out["delta"] == pytest.approx(-1.0)
    assert out["sigma"] is None


def test_paired_reports_sigma_none_below_two_rows():
    assert study.paired([_spot(0)], "violated")["sigma"] is None


def test_paired_skips_a_row_missing_either_arm():
    rows = [_spot(0), dict(_spot(1), S_gap=None), dict(_spot(2), W_gap=None)]
    assert study.paired(rows, "gap")["n"] == 1


def test_better_and_worse_are_counted_in_the_right_direction_for_violations():
    """On `violated`, LOWER is better - so a spot W fixes must count as
    better, not worse. Getting this backwards inverts the headline."""
    rows = [_spot(0, s_viol=1, w_viol=0),      # W fixed it
            _spot(1, s_viol=0, w_viol=1),      # W broke it
            _spot(2, s_viol=0, w_viol=0)]      # neither
    out = study.paired(rows, "violated")
    assert (out["better"], out["worse"]) == (1, 1)


def test_improvement_flips_the_sign_so_positive_means_better():
    """The raw delta on `violated` is negative when W violates less. A
    headline that reads negative for an improvement is the one error a
    reader cannot catch."""
    rows = [_spot(n, s_viol=1 if n % 2 else 0, w_viol=0) for n in range(20)]
    raw = study.paired(rows, "violated")
    out = study.improvement(rows)
    assert raw["delta"] < 0
    assert out["delta"] == pytest.approx(-raw["delta"])
    assert out["sigma"] > 0


def test_improvement_renames_the_arms_as_violation_rates():
    out = study.improvement([_spot(n, s_viol=1, w_viol=0) for n in range(4)])
    assert out["S_violation_rate"] == pytest.approx(1.0)
    assert out["W_violation_rate"] == pytest.approx(0.0)
    assert "S" not in out and "W" not in out


# ------------------------------------------------------------- summary

def _mixed(n_better, n_same, live=4, stack=100.0):
    rows = [_spot(n, live=live, stack=stack, s_viol=1, w_viol=0)
            for n in range(n_better)]
    rows += [_spot(1000 + n, live=live, stack=stack, s_viol=0, w_viol=0)
             for n in range(n_same)]
    return rows


def test_summarise_reports_each_live_count_separately():
    rows = _mixed(10, 20, live=4) + _mixed(6, 14, live=5) + _mixed(2, 8, live=6)
    out = study.summarise(rows)
    assert out["live4"]["n"] == 30
    assert out["live5"]["n"] == 20
    assert out["live6"]["n"] == 10


def test_summarise_reports_each_stack_depth_separately():
    """Rule 5. Five and six live, and every depth away from 100bb, are what
    this study exists to reach - pooling them away would waste it."""
    rows = _mixed(8, 12, stack=100.0) + _mixed(8, 12, stack=50.0)
    out = study.summarise(rows)
    assert out["stack100"]["n"] == 20
    assert out["stack50"]["n"] == 20


def test_summarise_counts_how_many_spots_each_arm_gets_right():
    rows = _mixed(10, 20)
    out = study.summarise(rows)
    assert out["counts"] == {"spots": 30, "S_correct": 20, "W_correct": 30}


# ------------------------------------------------------------- verdict

def test_the_sanity_gate_refuses_a_population_the_shipped_arm_cannot_order():
    """Rule 2. M214 measured 13 of 16 right at this budget; if the shipped
    arm cannot manage a majority here, the population is not comparable and
    nothing is read."""
    rows = [_spot(n, s_viol=1, w_viol=0) for n in range(30)]
    out = study.verdict(study.summarise(rows))
    assert out["sanity"] == "FAILED"
    assert out["result"] == "NOT READ"
    assert "not comparable" in out["note"]


def test_a_clean_improvement_reads_better():
    rows = _mixed(10, 30)
    out = study.verdict(study.summarise(rows))
    assert out["sanity"] == "passed"
    assert out["result"] == "BETTER"
    assert out["halves"] == "held"


def test_no_movement_reads_no_improvement():
    rows = [_spot(n, s_viol=0, w_viol=0) for n in range(40)]
    out = study.verdict(study.summarise(rows))
    assert out["sanity"] == "passed"
    assert out["result"] == "NO IMPROVEMENT"


def test_an_improvement_one_half_does_not_carry_reads_unreplicated():
    rows = _mixed(14, 40)
    for r in study.halves(rows)[0]:
        r["W_violated"] = r["S_violated"]        # that half shows nothing
    summary = study.summarise(rows)
    assert summary["all"]["sigma"] >= study.MIN_SIGMA
    assert study.verdict(summary)["result"] == "UNREPLICATED"


def test_w_being_worse_never_reads_as_an_improvement():
    rows = [_spot(n, s_viol=0, w_viol=1 if n % 3 else 0) for n in range(40)]
    out = study.verdict(study.summarise(rows))
    # the shipped arm is perfect here, so sanity passes and W only breaks things
    assert out["sanity"] == "passed"
    assert out["result"] == "NO IMPROVEMENT"


def test_an_undefined_sigma_never_clears_a_bar():
    assert study._clears({"sigma": None}, study.MIN_SIGMA) is False
    assert study._clears({}, study.MIN_SIGMA) is False
    assert study._clears({"sigma": study.MIN_SIGMA}, study.MIN_SIGMA) is True


# -------------------------------------------------- the bar and the arms

def test_the_bar_is_inherited_and_not_chosen_here():
    from bench.studies import three_live_budget
    assert study.MIN_SIGMA == abs(three_live_budget.MIN_SIGMA) == 2.0


def test_the_arms_are_the_same_two_budgets_m309_compared():
    from bench.studies import wide_pot_budget
    from api import config as cfg
    assert study.SHIPPED_ITERATIONS == wide_pot_budget.SHIPPED_ITERATIONS
    assert study.WIDE_ITERATIONS == wide_pot_budget.WIDE_ITERATIONS
    assert study.SHIPPED_ITERATIONS == cfg.MULTIWAY_WIDE_POT_ITERATIONS["flop"]
    assert study.WIDE_ITERATIONS == cfg.DEFAULT_MULTIWAY_PATH_QUERY_FLOP_ITERATIONS


def test_the_menu_this_study_depends_on_still_has_three_sizes():
    """If `MULTIWAY_FLOP_RAISE_SIZES` ever stops holding three sized bets,
    rule 1 can never be satisfied and this study is silently unrunnable."""
    from api import config as cfg
    first = cfg.MULTIWAY_FLOP_RAISE_SIZES[0]
    assert isinstance(first, tuple)
    assert len(first) == study.SIZES_NEEDED


def test_the_depths_reach_past_the_one_m309_could_measure():
    """M309 was 100bb only because the reference plays no other depth. Real
    multiway pots sit at 100bb or deeper 88% of the time (M263) and 15.1%
    at 200-260bb (M271)."""
    assert 100.0 in study.STACK_DEPTHS
    assert len(study.STACK_DEPTHS) > 1
    assert max(study.STACK_DEPTHS) >= 200.0


# ------------------------------------------------------- committed rows

ROWS = (pathlib.Path(__file__).resolve().parent / "data"
        / "wide_pot_ordering_m310.json")


@pytest.mark.skipif(not ROWS.exists(), reason="the campaign has not run yet")
def test_every_committed_spot_offered_exactly_three_sizes():
    rows = json.loads(ROWS.read_text())
    assert rows
    for r in rows:
        assert len(r["sizes"]) == study.SIZES_NEEDED
        assert len(r["S_folds"]) == study.SIZES_NEEDED
        assert sorted(r["sizes"]) == r["sizes"]


@pytest.mark.skipif(not ROWS.exists(), reason="the campaign has not run yet")
def test_every_committed_spot_was_solved_at_the_budget_it_claims():
    rows = json.loads(ROWS.read_text())
    for r in rows:
        assert r["S_iterations"] == study.SHIPPED_ITERATIONS
        assert r["W_iterations"] == study.WIDE_ITERATIONS


@pytest.mark.skipif(not ROWS.exists(), reason="the campaign has not run yet")
def test_the_committed_rows_reach_the_cells_m309_could_not():
    """The whole point of a reference-free instrument: five- and six-live
    pots, and depths the reference never plays."""
    rows = json.loads(ROWS.read_text())
    assert {r["live"] for r in rows} >= {4, 5}
    assert len({r["stack"] for r in rows}) > 1
