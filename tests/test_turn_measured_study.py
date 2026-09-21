"""`bench/studies/turn_measured.py` - M303's reading rule, audit R3.

The first test here pins a mistake this study made before it produced a
figure, and it is the mistake that voided M222's first run: patching the
CHAINED turn's range cap while the standalone path reads a different
constant. It produced arms that differed, so nothing looked wrong.
"""
import pytest

from bench.studies import turn_measured as study


def _row(shipped=0.5, reference=0.5, percentile=0.5, facing=False):
    return {"shipped_aggression": shipped, "reference_aggression": reference,
            "percentile": percentile, "facing": facing}


# -- the constant this study got wrong first -----------------------------

def test_the_study_patches_the_standalone_turns_own_cap():
    """M155/M222: the constant governing the path you are on is not
    always the one you patched. `MAX_TURN_PATH_QUERY_CLASSES_PER_SIDE`
    is the CHAINED turn's cap and is inert here; the standalone turn
    reads `TURN_STANDALONE_CLASSES_PER_SIDE`."""
    import pathlib
    source = pathlib.Path(study.__file__).read_text(encoding="utf-8")
    runner = source.split("def _run(", 1)[1]
    assert "cfg.TURN_STANDALONE_CLASSES_PER_SIDE = 169" in runner
    assert "cfg.MAX_TURN_PATH_QUERY_CLASSES_PER_SIDE = 169" not in runner


def test_the_shipped_width_is_captured_rather_than_written_back_as_a_literal():
    """Writing 140 back would pin the configuration this audit item
    exists to stop disclosures from pinning."""
    import pathlib
    source = pathlib.Path(study.__file__).read_text(encoding="utf-8")
    runner = source.split("def _run(", 1)[1]
    assert "width = cfg.TURN_STANDALONE_CLASSES_PER_SIDE" in runner
    assert "cfg.TURN_STANDALONE_CLASSES_PER_SIDE = width" in runner


# -- rule 1 and 2: the axis and the headline -----------------------------

def test_the_error_is_the_betting_frequency_gap():
    assert study.error_of(_row(shipped=0.2, reference=0.9)) == pytest.approx(0.7)


def test_an_empty_cell_reports_as_unmeasured():
    assert study.cell([]) == {"n": 0}


def test_the_headline_weights_the_two_node_types_by_occurrence():
    """The turn is 0.1382 opening against 0.0562 facing, so a quota'd
    sample must not read as if a player meets them equally (M188)."""
    rows = ([_row(shipped=0.0, reference=1.0) for _ in range(10)]
            + [_row(facing=True) for _ in range(10)])
    out = study.headline(rows)
    assert out["unweighted_over_threshold"] == pytest.approx(0.5)
    assert out["over_threshold"] > 0.6


def test_the_cells_keep_the_sign_as_well_as_the_size():
    out = study.cell([_row(shipped=0.9, reference=0.1)])
    assert out["signed"] == pytest.approx(0.8)
    assert out["mean"] == pytest.approx(0.8)


# -- rule 3: "more than 0.30 at both ends" -------------------------------

def _band_rows(top_error, bottom_error, n=10):
    rows = []
    for i in range(n):
        rows.append(_row(shipped=0.0, reference=top_error + 0.001 * i, percentile=0.9))
        rows.append(_row(shipped=0.0, reference=bottom_error + 0.001 * i, percentile=0.1))
    return rows


def test_both_ends_holds_when_both_bands_are_over_the_level():
    assert study.both_ends(_band_rows(0.45, 0.40))["holds"] is True


def test_both_ends_fails_when_only_one_band_is_over_the_level():
    """The published figure is about BOTH ends; one end alone is a
    different claim, and naming it would be M166's error."""
    assert study.both_ends(_band_rows(0.45, 0.05))["holds"] is False


def test_both_ends_fails_when_neither_band_is_over_the_level():
    assert study.both_ends(_band_rows(0.05, 0.05))["holds"] is False


def test_both_ends_needs_rows_at_each_end():
    rows = [_row(shipped=0.0, reference=0.9, percentile=0.9) for _ in range(20)]
    assert study.both_ends(rows)["holds"] is False


# -- rule 4: does strength predict the error? ----------------------------

def test_strength_predicting_the_error_is_reported_either_way_round():
    """A note claiming strength carries no signal must fail whichever
    direction the signal runs."""
    strong_worse = study.summarise(_band_rows(0.9, 0.05, n=15))
    assert study.verdict(strong_worse)["strength_predicts"] is True
    strong_better = study.summarise(_band_rows(0.05, 0.9, n=15))
    assert study.verdict(strong_better)["strength_predicts"] is True


def test_a_street_where_strength_carries_nothing_keeps_the_claim():
    rows = _band_rows(0.30, 0.30, n=15)
    assert study.verdict(study.summarise(rows))["strength_predicts"] is False


# -- rule 5: the street stops being unmeasured ---------------------------

def test_measuring_the_street_is_what_answers_the_first_claim():
    """The note says accuracy here "has not been measured". The study
    existing is the refutation - M177 made the same move on the river."""
    assert study.verdict(study.summarise(_band_rows(0.2, 0.2)))["street_is_measured_now"] is True
    assert study.verdict(study.summarise([]))["street_is_measured_now"] is False


# -- the committed rows --------------------------------------------------

import json
import pathlib

FIXTURE = pathlib.Path(__file__).parent / "data" / "turn_measured_m303.jsonl"


def _recorded():
    return [json.loads(line) for line in FIXTURE.read_text().splitlines() if line.strip()]


def test_the_sample_carries_both_node_types_in_equal_measure():
    rows = _recorded()
    assert len(rows) == 120
    facing = sum(1 for r in rows if r["facing"])
    assert facing == 60 and len(rows) - facing == 60


def test_the_shipped_constants_reproduce_from_the_recorded_rows():
    from api import config as cfg

    summary = study.summarise(_recorded())
    assert summary["n"] == cfg.TURN_MEASURED_ROWS
    assert round(summary["headline"]["over_threshold"], 3) == cfg.TURN_MEASURED_OVER_TEN
    assert round(summary["headline"]["cells"]["opening"]["mean"], 4) == cfg.TURN_MEASURED_OPENING_ERROR
    assert round(summary["headline"]["cells"]["facing"]["mean"], 4) == cfg.TURN_MEASURED_FACING_ERROR
    assert round(summary["strength"]["whole"]["mean_left"], 4) == cfg.TURN_MEASURED_STRONG_ERROR
    assert round(summary["strength"]["whole"]["mean_right"], 4) == cfg.TURN_MEASURED_WEAK_ERROR
    assert round(summary["signed"]["mean"], 4) == cfg.TURN_MEASURED_SIGNED


def test_the_published_both_ends_figure_dies_on_the_recorded_rows():
    """M175 said more than 0.30 at BOTH ends. It is 0.18 and 0.05."""
    summary = study.summarise(_recorded())
    assert summary["both_ends"]["holds"] is False
    assert summary["both_ends"]["top"]["mean"] < study.BOTH_ENDS_LEVEL
    assert summary["both_ends"]["bottom"]["mean"] < study.BOTH_ENDS_LEVEL


def test_the_turn_is_the_one_street_where_strength_predicts():
    """M168's rule demonstrated three times: 0.61 sigma on the flop,
    -1.28 on the river, +2.30 here."""
    summary = study.summarise(_recorded())
    assert study.verdict(summary)["strength_predicts"] is True
    assert summary["strength"]["whole"]["sigma"] >= study.MIN_SIGMA
    assert summary["strength"]["whole"]["ratio"] > 2.0


def test_the_halves_of_the_strength_split_are_reported_not_hidden():
    """Neither half clears the bar alone (1.29 and 1.78), which is why
    the copy names the split without overstating it."""
    summary = study.summarise(_recorded())
    halves = summary["strength"]["halves"]
    assert all(h["delta"] > 0 for h in halves), "both halves agree in direction"
    assert all(abs(h["sigma"]) < study.MIN_SIGMA for h in halves)


def test_facing_a_bet_is_the_more_reliable_node_here_too():
    """Third street in three milestones - and the sharpest of them."""
    summary = study.summarise(_recorded())
    assert summary["node_type"]["whole"]["delta"] < 0
    assert abs(summary["node_type"]["whole"]["sigma"]) > 4.0
