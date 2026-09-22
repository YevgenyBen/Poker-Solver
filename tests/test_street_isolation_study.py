"""`bench/studies/street_isolation.py` - M304's reading rule, audit R3.

The rule this pins is the one M195/M196 got wrong and M197 caught: a
chained arm at one iteration budget against a flop-only arm at another
confounds DEPTH with PRECISION. Here the precision arm runs too, and
rule 5 decides what may be attributed to depth at all.
"""
import pytest

from bench.studies import street_isolation as study


def _row(shipped=0.10, converged=0.12, chained=0.30, cost=0.05, spr=10.0):
    return {"shipped_aggression": shipped, "shipped_converged_aggression": converged,
            "chained_aggression": chained, "cost_bb": cost, "spr": spr}


# -- rule 1 and 2: the gap -----------------------------------------------

def test_the_gap_is_signed_toward_the_chained_solve():
    """Positive means the fuller model bets MORE, which is the direction
    M195/M196 measured and the copy states."""
    assert study.gap_of(_row(shipped=0.1, chained=0.3)) == pytest.approx(0.2)
    assert study.gap_of(_row(shipped=0.5, chained=0.1)) == pytest.approx(-0.4)


def test_the_depth_gap_holds_precision_fixed():
    """Rule 5's numerator: chained against the flop solve at the SAME
    iteration count, not against the shipped one."""
    row = _row(shipped=0.0, converged=0.2, chained=0.5)
    assert study.depth_gap_of(row) == pytest.approx(0.3)


def test_the_headline_reports_an_interval_not_just_a_mean():
    """The copy says "somewhere between 1 and 36", so the interval is
    part of the claim and has to be re-derived with it."""
    rows = [_row(shipped=0.0, chained=0.2 + 0.01 * i) for i in range(20)]
    out = study.headline(rows)
    assert out["signed"]["ci_low"] < out["signed"]["mean"] < out["signed"]["ci_high"]


def test_an_empty_sample_reports_as_unmeasured():
    assert study.headline([]) == {"n": 0}
    assert study.verdict(study.summarise([]))["measured"] is False


# -- rule 3: categorical disagreements -----------------------------------

def test_a_categorical_disagreement_is_counted_at_the_published_shape():
    """M200's three spots had the fuller solve betting 97-100% where the
    shipped one bet under a quarter - a gap of at least 0.5."""
    rows = [_row(shipped=0.20, chained=0.98), _row(shipped=0.3, chained=0.4)]
    assert study.headline(rows)["categorical"] == 1


def test_no_categorical_disagreements_is_a_result_not_a_blank():
    rows = [_row(shipped=0.3, chained=0.4) for _ in range(10)]
    out = study.headline(rows)
    assert out["categorical"] == 0
    assert study.verdict(study.summarise(rows))["has_categorical_disagreements"] is False


def test_the_close_share_and_the_median_are_both_reported():
    """"More than half differed by under 5 points, and the typical
    difference was about 4" is two claims, not one."""
    rows = [_row(shipped=0.0, chained=0.01) for _ in range(6)] + [
        _row(shipped=0.0, chained=0.9) for _ in range(4)]
    out = study.headline(rows)
    assert out["share_close"] == pytest.approx(0.6)
    assert out["median_abs"] == pytest.approx(0.01)


# -- rule 5: the confound M197 caught ------------------------------------

def test_depth_carries_the_story_when_precision_barely_moves():
    rows = [_row(shipped=0.10, converged=0.11, chained=0.50 + 0.001 * i)
            for i in range(12)]
    control = study.precision_control(rows)
    assert control["precision_over_depth"] < 0.5
    assert control["depth_is_the_story"] is True


def test_depth_may_not_be_credited_when_precision_carries_half_of_it():
    """M195/M196 ran the chained arm at 20 iterations against a shipped
    arm at 250 and called the difference depth. If the precision arm
    moves as much, the copy may not."""
    rows = [_row(shipped=0.10, converged=0.40, chained=0.50 + 0.001 * i)
            for i in range(12)]
    control = study.precision_control(rows)
    assert control["precision_over_depth"] > 0.5
    assert control["depth_is_the_story"] is False
    assert study.verdict(study.summarise(rows))["depth_is_the_story"] is False


def test_the_control_reports_both_components_rather_than_only_the_verdict():
    rows = [_row() for _ in range(8)]
    control = study.precision_control(rows)
    assert control["precision"]["n"] == 8 and control["depth"]["n"] == 8


# -- rule 6: the price ---------------------------------------------------

def test_the_price_reports_the_worst_and_the_over_a_blind_count():
    """The copy says "not one of the 16 spots cost a full blind", which
    is a count and has to be re-derived as one."""
    rows = [_row(cost=0.05), _row(cost=0.4), _row(cost=1.4)]
    out = study.price(rows)
    assert out["worst"] == pytest.approx(1.4)
    assert out["over_one_bb"] == 1


def test_rows_without_a_price_do_not_silently_become_zero():
    rows = [_row(cost=None), _row(cost=0.2)]
    assert study.price(rows)["n"] == 1


# -- rule 7: scope -------------------------------------------------------

def test_the_spr_band_is_recorded_with_the_figures():
    """The note fires from SPR 5 and this measures 5 to 20, so the copy
    has to say where the measurement stops - M257: cost scales with SPR,
    and the first probe drew a limped pot at SPR 74.5."""
    rows = [_row(spr=6.0), _row(spr=18.0)]
    out = study.summarise(rows)
    assert out["spr"]["min"] == 6.0 and out["spr"]["max"] == 18.0
    assert study.SPR_MAX == 20.0


def test_the_chained_budget_matches_the_figures_it_replaces():
    """M199/M200 measured at 400 iterations; quoting a new number from a
    different budget would not be a re-measurement of the same claim."""
    assert study.CHAINED_ITERATIONS == 400
    assert study.CONTROL_ITERATIONS < study.CHAINED_ITERATIONS
