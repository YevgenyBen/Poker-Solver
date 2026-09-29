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

def _folds(violates, top=0.85):
    """Fold masses with plenty of room, violating or not as asked.

    They must be present and non-saturated: `summarise` scores only the
    INFORMATIVE spots, so a fixture without fold lists is excluded whole
    and every cell comes back empty. (The first version of these fixtures
    had exactly that shape, and seven tests failed at once the moment
    saturation was added - the fixtures were wrong, not the rule.)
    """
    return [0.05, 0.02, top] if violates else [0.05, 0.40, top]


def _spot(n, live=4, stack=100.0, s_viol=1, w_viol=0, s_top=0.85, w_top=0.95):
    s, w = _folds(bool(s_viol), s_top), _folds(bool(w_viol), w_top)
    return {"path": "p%03d" % n, "board": "AhKs5d", "hero": "QcQd",
            "stack": stack, "live": live, "street": "flop",
            "S_folds": s, "W_folds": w,
            "S_violated": 1 if s_viol else 0, "W_violated": 1 if w_viol else 0,
            "S_gap": study.gap(s), "W_gap": study.gap(w)}


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
    assert out["counts"] == {"spots": 30, "informative": 30, "saturated": 0,
                             "S_correct": 20, "W_correct": 30,
                             "saturated_by_live": {"4": 0}}


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


# ---------------------------------------------------------- saturation

def _sat(n, s_folds, w_folds, live=4, stack=100.0):
    return {"path": "p%03d" % n, "board": "AhKs5d", "hero": "QcQd",
            "stack": stack, "live": live, "street": "flop",
            "S_folds": s_folds, "W_folds": w_folds,
            "S_violated": 1 if study.violated(s_folds) else 0,
            "W_violated": 1 if study.violated(w_folds) else 0,
            "S_gap": study.gap(s_folds), "W_gap": study.gap(w_folds)}


def test_fold_range_is_the_room_the_ordering_has():
    assert study.fold_range([0.10, 0.40, 0.80]) == pytest.approx(0.70)
    assert study.fold_range([]) == 0.0


def test_a_spot_where_hero_folds_to_everything_is_saturated():
    """M307's flat-fixture trap in a new place: the first smoke drew random
    heroes and returned rows like [0.973, 0.994, 0.988], where the ordering
    is decided in the third decimal and a violation is noise."""
    assert study.saturated(_sat(0, [0.973, 0.994, 0.988], [0.998, 1.0, 0.999]))
    assert study.saturated(_sat(1, [0.958, 0.984, 0.964], [0.999, 0.997, 0.986]))


def test_a_spot_with_a_real_decision_is_not_saturated():
    assert not study.saturated(_sat(0, [0.213, 0.220, 0.956], [0.26, 0.187, 0.877]))


def test_saturation_is_symmetric_between_the_arms():
    """A one-armed exclusion would let it depend on which arm did better."""
    one_flat = _sat(0, [0.50, 0.50, 0.50], [0.10, 0.50, 0.90])
    assert not study.saturated(one_flat)
    other_flat = _sat(1, [0.10, 0.50, 0.90], [0.50, 0.50, 0.50])
    assert not study.saturated(other_flat)


def test_the_saturation_level_is_inherited():
    from bench.studies import multiway_instability
    assert study.SATURATION_MIN_RANGE == multiway_instability.MIN_SEPARATION == 0.10


def test_saturated_spots_are_reported_and_never_scored():
    rows = [_sat(n, [0.99, 0.995, 0.99], [0.999, 1.0, 0.998]) for n in range(6)]
    rows += [_sat(100 + n, [0.10, 0.40, 0.80], [0.05, 0.45, 0.90])
             for n in range(10)]
    out = study.summarise(rows)
    assert out["counts"] == {"spots": 16, "informative": 10, "saturated": 6,
                             "S_correct": 10, "W_correct": 10,
                             "saturated_by_live": {"4": 6}}
    assert out["all"]["n"] == 10


def test_the_sanity_gate_reads_the_informative_spots_only():
    """Otherwise a pile of saturated rows could fail - or rescue - a gate
    that is meant to describe the spots being measured."""
    rows = [_sat(n, [0.99, 0.995, 0.99], [0.999, 1.0, 0.998]) for n in range(50)]
    rows += [_sat(100 + n, [0.10, 0.40, 0.80], [0.05, 0.45, 0.90])
             for n in range(10)]
    out = study.verdict(study.summarise(rows))
    assert out["sanity"] == "passed"


# ------------------------------------------------------- hero selection

def test_the_hero_band_is_inherited_from_where_the_defect_lives():
    """M272/A6 located the flop facing-a-bet defect in middling hands,
    0.40-0.75, and that gate ships as `flop-under-fold`."""
    from api import config as cfg
    assert study.HERO_BAND == (0.40, 0.75)
    assert cfg.FLOP_UNDER_FOLD_MIN_STRENGTH == pytest.approx(study.HERO_BAND[0])
    assert cfg.FLOP_UNDER_FOLD_MAX_STRENGTH == pytest.approx(study.HERO_BAND[1])


def test_the_walk_is_biased_toward_small_pots():
    """The default raise weighting builds big pots, and a big pot is a low
    SPR where the third bet size collapses into the all-in: the first smoke
    skipped 172 of 181 spots for want of it."""
    from bench import spot_population
    assert study.RAISE_WEIGHT < spot_population.DEFAULT_RAISE_WEIGHT


# --------------------------------------------------------- calibration

def _cal(curves, live=4):
    return {"path": "p", "board": "AhKs5d", "hero": "QcQd", "stack": 100.0,
            "live": live, "seeds": [0, 101, 102], "sizes": [4.0, 9.0, 30.0],
            "curves": curves}


def test_calibration_measures_how_far_each_point_moves_on_the_seed():
    rows = [_cal([[0.20, 0.30, 0.40], [0.25, 0.30, 0.40]])]
    cal = study.calibration(rows)
    assert cal["point_max"] == pytest.approx(0.05)
    assert cal["spots"] == 1 and cal["pairs"] == 2


def test_calibration_counts_a_pair_whose_SIGN_reverses():
    """The reading that decides. One seed says folding rises with the bet,
    another says it falls - that pair reports no direction at all."""
    rows = [_cal([[0.20, 0.30, 0.40],      # first pair +0.10
                  [0.30, 0.20, 0.40]])]    # first pair -0.10
    cal = study.calibration(rows)
    assert cal["sign_flips"] == 1
    assert cal["sign_flip_share"] == pytest.approx(0.5)


def test_a_stable_ordering_flips_nothing():
    rows = [_cal([[0.20, 0.30, 0.40], [0.21, 0.32, 0.41], [0.19, 0.29, 0.39]])]
    cal = study.calibration(rows)
    assert cal["sign_flips"] == 0
    assert study.criterion_can_speak(cal) is True


def test_a_sign_that_moves_with_the_seed_disqualifies_the_criterion():
    rows = [_cal([[0.20, 0.30, 0.40], [0.30, 0.20, 0.40]]) for _ in range(4)]
    cal = study.calibration(rows)
    assert cal["sign_flip_share"] > study.MAX_SIGN_FLIP_SHARE
    assert study.criterion_can_speak(cal) is False


def test_the_criterion_cannot_speak_when_nothing_was_calibrated():
    """An empty calibration must not pass by default - that is the one
    failure mode a gate like this may not have."""
    assert study.criterion_can_speak(study.calibration([])) is False
    assert study.criterion_can_speak({}) is False


def test_calibration_ignores_a_row_solved_at_only_one_seed():
    assert study.calibration([_cal([[0.2, 0.3, 0.4]])])["pairs"] == 0


def test_calibration_handles_curves_of_unequal_length():
    """A truncated curve must narrow the comparison, not raise or pad."""
    cal = study.calibration([_cal([[0.2, 0.3, 0.4], [0.2, 0.3]])])
    assert cal["pairs"] == 1


# ------------------------------------------- the committed calibration

CAL = (pathlib.Path(__file__).resolve().parent / "data"
       / "wide_pot_ordering_calibration_m310.json")


@pytest.mark.skipif(not CAL.exists(), reason="the calibration has not run yet")
def test_the_committed_calibration_refuses_the_criterion():
    """M310's finding, pinned. The ordering's sign moves on the traversal
    seed alone, so the arms are not worth running and a future
    configuration change that fixes this will fail this test loudly."""
    rows = json.loads(CAL.read_text())
    cal = study.calibration(rows)
    assert cal["pairs"] >= 30
    assert cal["sign_flip_share"] > study.MAX_SIGN_FLIP_SHARE
    assert study.criterion_can_speak(cal) is False


@pytest.mark.skipif(not CAL.exists(), reason="the calibration has not run yet")
def test_the_seed_movement_is_as_large_as_the_effect_being_looked_for():
    """The 1000 -> 4000 ordering gaps measured 0.01-0.09; if the seed moves
    an adjacent pair by as much, there is nothing to find. M214 compared
    200 -> 1000 and moved the gap +0.167, which is why it worked there."""
    cal = study.calibration(json.loads(CAL.read_text()))
    assert cal["pair_median"] > 0.01
    assert cal["pair_p90"] > 0.05


@pytest.mark.skipif(not CAL.exists(), reason="the calibration has not run yet")
def test_every_calibration_row_varied_only_the_seed():
    rows = json.loads(CAL.read_text())
    for r in rows:
        assert r["seeds"] == list(study.CALIBRATION_SEEDS)
        assert len(r["curves"]) == len(study.CALIBRATION_SEEDS)
        assert len(r["sizes"]) == study.SIZES_NEEDED
        assert r["live"] >= 4
