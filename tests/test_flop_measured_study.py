"""`bench/studies/flop_measured.py` - M300's reading rule, audit R3.

The rule was fixed before any figure was computed, over rows that were
already committed for a different question. These tests pin each clause
so a later edit cannot quietly re-read the same sample to a friendlier
answer.
"""
import pytest

from bench.studies import flop_measured as study


def _row(shipped=0.5, reference=0.5, percentile=0.5, facing=False, open_ended=False, **extra):
    return {"shipped_aggression": shipped, "reference_aggression": reference,
            "percentile": percentile, "facing": facing, "open_ended": open_ended,
            "hero": "AhKh", "board": "2c7d9s", **extra}


# -- rule 1: the axis ----------------------------------------------------

def test_the_error_is_the_betting_frequency_gap():
    """M180's own axis: how often the advice bets or raises."""
    assert study.error_of(_row(shipped=0.2, reference=0.9)) == pytest.approx(0.7)
    assert study.error_of(_row(shipped=0.9, reference=0.2)) == pytest.approx(0.7)


# -- rule 2: the weighted headline ---------------------------------------

def test_an_open_ended_draw_counts_as_its_own_stratum():
    """Both samples filled the open-ended quota FIRST, so such a row is a
    deliberate oversample whichever node type it is. Reading it as an
    ordinary facing row would let a quota set the natural rate."""
    assert study.stratum(_row(open_ended=True, facing=True)) == "open_ended"
    assert study.stratum(_row(facing=True)) == "facing"
    assert study.stratum(_row()) == "opening"


def test_the_headline_weights_the_strata_by_real_occurrence():
    """M188: a quota'd sample is balanced and a player does not meet a
    balanced sample. Opening decisions are twice as common as facing ones
    on the flop, so a clean facing cell must not cancel a bad opening one
    out of the headline."""
    rows = ([_row(shipped=0.0, reference=1.0) for _ in range(10)]          # opening, all bad
            + [_row(facing=True) for _ in range(10)])                      # facing, all clean
    out = study.headline(rows)
    assert out["unweighted_share"] == pytest.approx(0.5)
    assert out["share_over_threshold"] > 0.6, (
        "the weighted share must lean toward the opening stratum, which is "
        "roughly two thirds of real flop decisions")


def test_the_headline_reports_one_in_n_as_well_as_the_share():
    out = study.headline([_row(shipped=0.0, reference=1.0)] + [_row() for _ in range(3)])
    assert out["one_in"] == pytest.approx(1 / out["share_over_threshold"])


def test_a_stratum_with_no_rows_drops_out_rather_than_counting_as_clean():
    """A missing cell must not read as "nothing was wrong there" - that is
    the failure M292's first run nearly published."""
    rows = [_row(shipped=0.0, reference=1.0) for _ in range(4)]
    out = study.headline(rows)
    assert out["per_stratum"]["facing"]["share"] is None
    assert out["share_over_threshold"] == pytest.approx(1.0)


# -- rule 3: the "nothing predicts it" claim -----------------------------

def _predictor_rows(facing_error, opening_error, n=20):
    rows = []
    for i in range(n):
        rows.append(_row(shipped=0.0, reference=facing_error + 0.001 * i, facing=True))
        rows.append(_row(shipped=0.0, reference=opening_error + 0.001 * i))
    return rows


def test_a_predictor_that_separates_and_replicates_is_found():
    summary = study.summarise(_predictor_rows(0.60, 0.05))
    assert study.separates(summary["predictors"]["facing"]) is True
    assert study.verdict(summary)["no_predictor_claim_holds"] is False
    assert study.verdict(summary)["predictors_found"] == ["facing"]


def test_a_predictor_that_does_not_separate_leaves_the_claim_standing():
    summary = study.summarise(_predictor_rows(0.30, 0.30))
    assert study.separates(summary["predictors"]["facing"]) is False
    assert study.verdict(summary)["no_predictor_claim_holds"] is True


def test_a_predictor_whose_halves_disagree_in_direction_is_refused():
    """M166/M167: the whole sample can separate while a half points the
    other way, and this note exists because M180 refused to claim one."""
    summary = study.summarise(_predictor_rows(0.60, 0.05))
    summary["predictors"]["facing"]["halves"][0]["delta"] = -1.0
    assert study.separates(summary["predictors"]["facing"]) is False


def test_a_predictor_needs_rows_on_both_sides():
    summary = study.summarise([_row(shipped=0.0, reference=0.9) for _ in range(20)])
    assert study.separates(summary["predictors"]["facing"]) is False


# -- rule 4: the worst case ----------------------------------------------

def test_the_worst_case_is_re_read_rather_than_remembered():
    """M180's example came off a tree the product no longer builds."""
    rows = [_row(shipped=0.1, reference=0.2),
            _row(shipped=0.05, reference=0.95, percentile=0.08, hero="9s3h")]
    worst = study.worst_row(rows)
    assert worst["hero"] == "9s3h" and worst["error"] == pytest.approx(0.90)
    assert worst["percentile"] == pytest.approx(0.08)


# -- the committed sample ------------------------------------------------

def test_the_study_reads_both_committed_samples():
    rows = study.load()
    assert len(rows) == 176, "116 from M292 plus 60 fresh facing rows from M297"


def test_the_shipped_constants_reproduce_from_the_committed_rows():
    from api import config as cfg

    summary = study.summarise(study.load())
    assert summary["headline"]["n"] == cfg.FLOP_MEASURED_ROWS
    assert round(summary["headline"]["share_over_threshold"], 3) == cfg.FLOP_MEASURED_SHARE_OVER_TEN
    assert round(summary["predictors"]["facing"]["whole"]["mean_right"],
                 4) == cfg.FLOP_MEASURED_OPENING_ERROR
    assert round(summary["predictors"]["facing"]["whole"]["mean_left"],
                 4) == cfg.FLOP_MEASURED_FACING_ERROR
    assert round(summary["headline"]["worst_error"], 4) == cfg.FLOP_MEASURED_WORST_ERROR


def test_the_note_understated_itself_and_no_longer_does():
    """The finding that forced the rewrite. M180 published one answer in
    seven off by more than 0.10; at the shipped configuration it is
    roughly twice that - and a warning understating its own defect is the
    one failure mode M232 says a warning may not have."""
    summary = study.summarise(study.load())
    assert summary["headline"]["share_over_threshold"] > 2 * (8 / 56)


def test_facing_a_bet_predicts_the_error_and_strength_still_does_not():
    """Both halves of M180's "nothing predicts it" claim, re-derived."""
    summary = study.summarise(study.load())
    assert study.separates(summary["predictors"]["facing"]) is True
    assert study.separates(summary["predictors"]["strength"]) is False
    # And the direction is the opposite of what the cost notes describe:
    # acting first carries the LARGER frequency error.
    assert summary["predictors"]["facing"]["whole"]["delta"] < 0
