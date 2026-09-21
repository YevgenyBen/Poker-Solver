"""`bench/studies/facing_cost.py` - M299's reading rule, audit R3.

Every test here pins one clause of a rule that was fixed before any row
was priced, so a later edit that would have changed the verdict fails
loudly instead of quietly re-reading the same data.
"""
import pytest

from bench.studies import facing_cost as study


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
