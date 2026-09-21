"""`bench/studies/facing_cost.py` - M299's reading rule, audit R3.

Every test here pins one clause of a rule that was fixed before any row
was priced, so a later edit that would have changed the verdict fails
loudly instead of quietly re-reading the same data.
"""
import json
import pathlib

import pytest

from bench.studies import facing_cost as study

FIXTURE = pathlib.Path(__file__).parent / "data" / "facing_cost_m299.jsonl"


def _recorded():
    return [json.loads(line) for line in FIXTURE.read_text().splitlines() if line.strip()]


def _row(street="flop", kind="facing", loss=0.5, percentile=0.7, **extra):
    return {"street": street, "kind": kind, "loss_bb": loss, "percentile": percentile,
            "remapped_mass": 0.0, "reference_exploitability_pct": 0.05, **extra}


# -- rule 1: the two controls -------------------------------------------

def test_a_row_whose_arms_offer_different_actions_is_refused():
    """M202: two menus is two games, not one comparison."""
    assert study.keep([_row(remapped_mass=0.2)]) == []


def test_a_row_whose_reference_did_not_converge_is_refused():
    """M138: an unconverged reference is not a reference."""
    assert study.keep([_row(reference_exploitability_pct=3.0)]) == []


def test_a_clean_row_is_kept():
    assert len(study.keep([_row()])) == 1


def test_refusals_are_counted_rather_than_dropped_silently():
    summary = study.summarise([_row(), _row(remapped_mass=0.5)])
    assert summary["n_priced"] == 2 and summary["n_kept"] == 1 and summary["refused"] == 1


# -- rule 2: the cells ---------------------------------------------------

def test_each_street_and_node_type_is_read_on_its_own():
    summary = study.cells([_row("flop", "facing", 2.0), _row("river", "opening", 0.5)])
    assert summary["flop/facing"]["n"] == 1 and summary["river/opening"]["n"] == 1
    assert summary["turn/facing"]["n"] == 0


def test_an_empty_cell_reports_as_unmeasured_not_as_zero():
    """A null is a result; a cell with no rows must not read as "costs
    nothing", which is what M292's first run nearly published."""
    empty = study.cells([])["flop/facing"]
    assert empty == {"n": 0} and "mean_abs" not in empty


def test_a_cell_reports_the_median_beside_the_mean():
    """M183: the median decision costs almost nothing while the tail
    carries the total, so a mean alone reads as the typical case."""
    cell = study.cells([_row(loss=0.0), _row(loss=0.0), _row(loss=9.0)])["flop/facing"]
    assert cell["median_abs"] == 0.0 and cell["mean_abs"] == pytest.approx(3.0)
    assert cell["over_1bb"] == pytest.approx(1 / 3) and cell["over_5bb"] == pytest.approx(1 / 3)


def test_the_sign_is_kept_as_well_as_the_size():
    """A shipped row can price BETTER against this opponent model, and
    `ev.py`'s contract says to report that rather than hide it."""
    cell = study.cells([_row(loss=-1.0), _row(loss=3.0)])["flop/facing"]
    assert cell["mean_abs"] == pytest.approx(2.0) and cell["mean_signed"] == pytest.approx(1.0)


# -- rule 3: the ratio ---------------------------------------------------

def test_the_pooled_mean_weights_cells_by_how_often_they_occur():
    """M188's own correction: a quota'd sample is balanced and a player
    does not meet a balanced sample. The flop is 2.7x the river's
    exposure, so a costly river cell must not count equally."""
    rows = [_row("flop", "facing", 1.0), _row("river", "facing", 10.0)]
    pooled = study._pooled(rows, "facing")
    assert pooled["unweighted_mean_abs"] == pytest.approx(5.5)
    expected = (1.0 * study.EXPOSURE[("flop", "facing")]
                + 10.0 * study.EXPOSURE[("river", "facing")]) / (
        study.EXPOSURE[("flop", "facing")] + study.EXPOSURE[("river", "facing")])
    assert pooled["mean_abs"] == pytest.approx(expected)
    assert pooled["mean_abs"] < pooled["unweighted_mean_abs"]


def test_the_claimed_ratio_is_kept_only_when_it_is_both_big_and_separable():
    summary = {"ratio": {"ratio": 30.0, "sigma": 4.0}}
    assert study.ratio_claim(summary) == "keep"


def test_a_separable_ratio_below_the_claim_is_quoted_as_measured():
    assert study.ratio_claim({"ratio": {"ratio": 3.0, "sigma": 4.0}}) == "quote_measured"


def test_a_ratio_that_does_not_separate_leaves_the_copy():
    """The one outcome the copy may not survive: a comparison the data
    cannot support."""
    assert study.ratio_claim({"ratio": {"ratio": 40.0, "sigma": 1.2}}) == "remove"
    assert study.ratio_claim({"ratio": {"ratio": None, "sigma": None}}) == "remove"


# -- rule 4: the share ---------------------------------------------------

def test_the_cost_share_is_weighted_by_real_occurrence():
    rows = [_row("flop", "facing", 2.0), _row("flop", "opening", 1.0)]
    share = study.cost_share(rows)
    expected = (2.0 * study.EXPOSURE[("flop", "facing")]) / (
        2.0 * study.EXPOSURE[("flop", "facing")] + 1.0 * study.EXPOSURE[("flop", "opening")])
    assert share["facing_share"] == pytest.approx(expected)


def test_the_share_of_a_sample_with_no_rows_is_unmeasured():
    assert study.cost_share([])["facing_share"] is None


# -- rule 6: the band ----------------------------------------------------

def _band_rows(in_band_loss, out_band_loss, n=12):
    rows = []
    for i in range(n):
        rows.append(_row(loss=in_band_loss + 0.01 * i, percentile=0.7))
        rows.append(_row(loss=out_band_loss + 0.01 * i, percentile=0.2))
    return rows


def test_the_band_survives_when_it_is_large_and_replicates():
    summary = study.summarise(_band_rows(5.0, 0.1))
    assert summary["band"]["whole"]["sigma"] > study.MIN_SIGMA
    assert study.band_survives(summary) is True


def test_the_band_is_withdrawn_when_it_does_not_separate():
    """M166's rule, which this note's own comment invokes."""
    summary = study.summarise(_band_rows(0.5, 0.5))
    assert study.band_survives(summary) is False


def test_the_band_is_withdrawn_when_a_split_half_disagrees():
    """The whole sample can pass while a half points the other way -
    M166 shipped exactly that and M167 withdrew it."""
    summary = study.summarise(_band_rows(5.0, 0.1))
    summary["band"]["halves"][0]["delta"] = -0.2
    assert study.band_survives(summary) is False


def test_the_band_needs_enough_rows_to_be_read_at_all():
    summary = study.summarise([_row(loss=5.0, percentile=0.7),
                               _row(loss=0.1, percentile=0.2),
                               _row(loss=0.1, percentile=0.2)])
    assert summary["band"]["whole"]["n_in"] < study.MIN_ROWS
    assert study.band_survives(summary) is False


def test_the_band_only_reads_decisions_facing_a_bet():
    """It is nested inside the facing-a-bet gate in `api/main.py`, so a
    sample including opening decisions would measure a different note."""
    rows = _band_rows(0.5, 0.5) + [_row("flop", "opening", 50.0, percentile=0.7)]
    assert study.band(rows)["whole"]["n_in"] == 12


def test_the_band_boundaries_are_the_shipped_ones():
    """Re-cutting a band on the sample that found it is the error M166
    made; the study reads the constants the product fires on."""
    from api import config as cfg
    assert (study.BAND_LOW, study.BAND_HIGH) == (cfg.COSTLY_BAND_LOW, cfg.COSTLY_BAND_HIGH)


# -- the slack control ---------------------------------------------------

def test_a_loss_smaller_than_the_references_own_slack_is_not_visible():
    """The control run found the reference's own row beaten by a pure
    fold, so the yardstick has per-hand slack. A loss underneath it says
    nothing about the shipped answer."""
    out = study.yardstick_slack([_row(loss=0.02, reference_slack_bb=0.10)])
    assert out["visible"] == 0.0 and out["mean_slack"] == pytest.approx(0.10)


def test_a_loss_clear_of_the_slack_is_visible():
    out = study.yardstick_slack([_row(loss=1.0, reference_slack_bb=0.05)])
    assert out["visible"] == 1.0


def test_the_slack_is_reported_and_never_subtracted():
    """Saying "net of slack" would get the arithmetic backwards: `loss`
    is already a difference between two rows in the same game, and the
    slack says how steady the row being compared against is. A corrected
    cost figure must not appear here for a later reader to quote."""
    out = study.yardstick_slack([_row(loss=0.02, reference_slack_bb=0.10)])
    assert not any("net" in key for key in out)


def test_the_slack_reading_ignores_rows_that_have_none():
    """The slack is a second pass over the same rows, so a sample can be
    read before it has run - and must then report zero rows rather than
    an invented floor."""
    assert study.yardstick_slack([_row(loss=1.0)]) == {"n": 0}


def test_the_slack_does_not_change_either_verdict():
    """It was added after the verdicts were read; rules 3 and 6 stay on
    the raw metric they were pre-registered against."""
    rows = _band_rows(5.0, 0.1)
    before = study.verdict(study.summarise(rows))
    after = study.verdict(study.summarise(
        [{**r, "reference_slack_bb": 99.0} for r in rows]))
    assert before == after


# -- rule 7: scope -------------------------------------------------------

def test_the_exposure_table_covers_every_heads_up_postflop_cell():
    assert set(study.EXPOSURE) == {(s, k) for s in study.STREETS for k in study.KINDS}


def test_the_exposure_table_records_that_it_is_heads_up_only():
    """Both notes fire multiway too, and this instrument solves two
    positions - so the weights must sum to the share of real postflop
    decisions this can price, not to 1.0."""
    assert sum(study.EXPOSURE.values()) == pytest.approx(study.SCOPE_SHARE, abs=0.001)
    assert study.SCOPE_SHARE == pytest.approx(0.6706, abs=0.001)


class _Act:
    def __init__(self, street, player, kind):
        self.street, self.player, self.kind = street, player, kind


def test_a_pot_that_folds_to_two_on_the_flop_is_not_a_heads_up_pot():
    """The distinction that cost the first run its throughput: three
    players take the flop, one folds, and two are left at the turn - but
    no two-position solve of that preflop path exists, so it cannot be
    priced at all."""
    acts = [_Act("preflop", 0, "fold"), _Act("preflop", 1, "raise"),
            _Act("preflop", 2, "call"), _Act("preflop", 3, "call"),
            _Act("flop", 1, "bet"), _Act("flop", 2, "fold"), _Act("flop", 3, "call"),
            _Act("turn", 1, "bet")]
    assert study.live_after_preflop(acts, 4) == 3


def test_a_pot_heads_up_from_the_flop_is_one_this_can_price():
    acts = [_Act("preflop", 0, "fold"), _Act("preflop", 1, "fold"),
            _Act("preflop", 2, "raise"), _Act("preflop", 3, "call"),
            _Act("flop", 2, "bet")]
    assert study.live_after_preflop(acts, 4) == 2


# -- the shipped figures, re-derived from the committed rows -------------

def test_the_shipped_constants_reproduce_from_the_recorded_rows():
    """M299. The copy's numbers are not remembered, they are re-derived."""
    from api import config as cfg

    summary = study.summarise(_recorded())
    assert summary["n_kept"] == cfg.FACING_A_BET_COST_ROWS
    assert summary["ratio"]["facing"]["n"] == cfg.FACING_A_BET_COST_FACING_ROWS
    assert round(summary["ratio"]["facing"]["mean_abs"], 4) == cfg.FACING_A_BET_COST_FACING_BB
    assert round(summary["ratio"]["opening"]["mean_abs"], 4) == cfg.FACING_A_BET_COST_OPENING_BB
    assert round(summary["ratio"]["ratio"], 2) == cfg.FACING_A_BET_COST_RATIO
    assert round(summary["cost_share"]["facing_share"], 3) == cfg.FACING_A_BET_COST_SHARE
    assert round(summary["ratio"]["facing"]["over_1bb"], 4) == cfg.FACING_A_BET_COST_OVER_1BB


def test_both_controls_passed_on_every_recorded_row():
    """Rule 1. No row was refused, so the sample is not a survivor of a
    filter that could have shaped it."""
    rows = _recorded()
    assert study.keep(rows) == rows
    assert max(abs(r["remapped_mass"]) for r in rows) == 0.0, (
        "the two arms offered the same actions on every row, so nothing was "
        "remapped between two different games (M202)")
    assert max(r["reference_exploitability_pct"] for r in rows) < study.MAX_REFERENCE_PCT


def test_the_claimed_ratio_dies_on_the_recorded_rows():
    """The verdict that rewrote the copy, re-derived rather than recalled:
    separable, and nowhere near 25x."""
    summary = study.summarise(_recorded())
    assert study.ratio_claim(summary) == "quote_measured"
    assert summary["ratio"]["sigma"] >= study.MIN_SIGMA
    assert summary["ratio"]["ratio"] < study.CLAIMED_RATIO / 5


def test_the_band_note_dies_on_the_recorded_rows():
    """The verdict that withdrew `COSTLY_BAND_NOTE`."""
    summary = study.summarise(_recorded())
    assert study.band_survives(summary) is False
    assert summary["band"]["whole"]["sigma"] < study.MIN_SIGMA
    # On the metric the withdrawn copy actually quoted - 44% in band
    # against 4% out of it - there is no separation at all.
    whole = summary["band"]["whole"]
    assert abs(whole["over_1bb_in"] - whole["over_1bb_out"]) < 0.02


def test_the_over_five_big_blind_tail_is_empty():
    """M188 told players 5% of these decisions cost more than five big
    blinds. Not one of 180 does."""
    summary = study.summarise(_recorded())
    assert summary["ratio"]["facing"]["over_5bb"] == 0.0


def test_the_river_cells_sit_inside_the_yardsticks_own_slack():
    """The control that decides how far these figures can be read. The
    flop cells are clear of the reference's own per-hand slack; the
    river's means are not, so the copy quotes a pooled figure and makes
    no per-street claim."""
    summary = study.summarise(_recorded())
    flop = summary["slack"]["flop/facing"]
    river = summary["slack"]["river/facing"]
    assert flop["visible"] > 0.8
    assert river["visible"] < 0.5
