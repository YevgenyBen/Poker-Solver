"""`bench/studies/preflop_position.py` - M305's reading rule, audit R3.

The rule these pin is rule 3: the gradient is graded against the
SOLVER'S OWN movement between seeds, not against zero. That is the bar
M111 used to withdraw M110's "the button opens tighter than under the
gun", and reusing it is what makes this a re-measurement of the same
claim rather than a new one under a friendlier bar.
"""
import json
import pathlib

import pytest

from bench.studies import preflop_position as study


def _by_seed(**seats):
    """{seed: {seat: open_frequency}} from seat -> per-seed list."""
    seeds = range(len(next(iter(seats.values()))))
    return {s: {seat: values[s] for seat, values in seats.items()} for s in seeds}


# -- rule 1: the axes ----------------------------------------------------

def test_the_opening_frequency_is_the_non_fold_mass_weighted_by_combos():
    """M119: a class's frequency belongs to every one of its combos, so
    a six-combo pair counts six times a one-combo hand does not."""
    rows = {"AA": {"fold": 0.0, "raise:2.50": 1.0}, "72o": {"fold": 1.0}}
    weights = {"AA": 6, "72o": 12}
    assert study.open_frequency(rows, weights) == pytest.approx(6 / 18)


def test_an_empty_range_opens_nothing_rather_than_dividing_by_zero():
    assert study.open_frequency({}, {}) == 0.0


def test_the_all_in_share_is_of_the_non_fold_mass_not_of_the_whole_row():
    """The caveat's claim is about "the split among the NON-FOLD
    actions", so folding may not dilute it."""
    share, mass = study.all_in_share({"fold": 0.5, "raise:2.50": 0.2, "all_in:100.00": 0.3})
    assert share == pytest.approx(0.6) and mass == pytest.approx(0.5)


def test_a_hand_that_only_folds_has_no_split_rather_than_a_zero_one():
    """Counting a pure fold as "all-in 0.0" would let the tightest hands
    vote that the split is perfectly stable."""
    share, mass = study.all_in_share({"fold": 1.0})
    assert share is None and mass == pytest.approx(0.0)


# -- rules 2-4: the gradient against the solver's own noise --------------

def test_a_gradient_inside_the_seed_noise_leaves_the_claim_standing():
    """M111's own case: 1.7 points of widening where one seat moves 2.8
    between seeds is not a positional range chart."""
    by_seed = _by_seed(UTG=[0.20, 0.24, 0.18, 0.22], MP=[0.21, 0.19, 0.23, 0.20],
                       CO=[0.22, 0.25, 0.19, 0.21], BTN=[0.22, 0.25, 0.20, 0.23])
    out = study.position_verdict(by_seed)
    assert out["flat"] is True and out["widens"] is False


def test_a_gradient_clear_of_the_noise_and_agreed_by_every_seed_refutes_it():
    by_seed = _by_seed(UTG=[0.20, 0.21, 0.20, 0.21], MP=[0.26, 0.27, 0.26, 0.27],
                       CO=[0.33, 0.34, 0.33, 0.34], BTN=[0.42, 0.43, 0.42, 0.43])
    out = study.position_verdict(by_seed)
    assert out["flat"] is False and out["widens"] is True
    assert out["gradient"]["mean"] == pytest.approx(0.22)


def test_a_large_gradient_the_seeds_disagree_about_refutes_nothing():
    """Rule 4 needs the sign to hold on every seed. A mean carried by
    one draw is M199's n=8, and M200 is what settling it looks like."""
    by_seed = _by_seed(UTG=[0.20, 0.50], MP=[0.3, 0.3], CO=[0.3, 0.3], BTN=[0.50, 0.20])
    out = study.position_verdict(by_seed)
    assert out["gradient"]["signs_agree"] is False
    assert out["widens"] is False


def test_the_gradient_is_reported_against_the_span_gto_shows():
    """"Widens by 3 points where GTO widens by 30" and "widens" are
    different sentences to a player."""
    by_seed = _by_seed(UTG=[0.20, 0.20], MP=[0.2, 0.2], CO=[0.2, 0.2], BTN=[0.23, 0.23])
    out = study.position_verdict(by_seed)
    assert out["share_of_gto_span"] == pytest.approx(0.03 / 0.30)


def test_one_seed_cannot_grade_the_gradient_at_all():
    """With a single seed there is no noise floor, so rule 3 has no
    yardstick and the study must say so rather than compare with zero."""
    by_seed = _by_seed(UTG=[0.20], MP=[0.3], CO=[0.4], BTN=[0.5])
    assert study.position_verdict(by_seed)["measured"] is False


# -- rules 6-7: the sizing split -----------------------------------------

def _shares(**per_class):
    seeds = range(len(next(iter(per_class.values()))))
    return {s: {name: (values[s], 1.0) for name, values in per_class.items()} for s in seeds}


def test_the_split_movement_is_combo_weighted_across_the_classes():
    shares = _shares(AA=[0.10, 0.90], **{"72o": [0.5, 0.5]})
    out = study.split_move(shares, {"AA": 6, "72o": 12})
    assert out["mean"] == pytest.approx(0.8 * 6 / 18)
    assert out["worst"] == pytest.approx(0.8)


def test_classes_with_no_play_are_left_out_of_the_split_figure():
    """Rule 6: a hand that folds everything has no split, and letting it
    in at zero would make the population look stable by construction."""
    shares = {0: {"AA": (0.1, 1.0), "72o": (None, 0.0)},
              1: {"AA": (0.9, 1.0), "72o": (None, 0.0)}}
    out = study.split_move(shares, {"AA": 6, "72o": 12})
    assert out["n"] == 1 and out["mean"] == pytest.approx(0.8)


def test_a_named_hands_span_is_reported_with_its_ends_not_just_its_width():
    """The copy says "anywhere from X to Y", which is two numbers."""
    out = study.hand_span(_shares(AA=[0.03, 0.92, 0.40]), "AA")
    assert (out["low"], out["high"]) == (0.03, 0.92)
    assert out["span"] == pytest.approx(0.89)


def test_a_hand_absent_from_every_seed_reports_as_unmeasured():
    assert study.hand_span(_shares(AA=[0.1, 0.2]), "KK")["n"] == 0


# -- rule 7: what the copy may quote -------------------------------------

def _record(open_by_seed, shares, weights):
    return {"weights": weights,
            "arms": {"ensemble": {"open": {str(k): v for k, v in open_by_seed.items()},
                                  "all_in": {str(k): {n: list(v) for n, v in cls.items()}
                                             for k, cls in shares.items()}}}}


def test_the_copy_may_not_quote_aa_when_aa_alone_does_not_carry_the_claim():
    """M282's rule in a new place: a figure is carried by the population
    the gate fires on, not by the hand that first found it."""
    by_seed = _by_seed(UTG=[0.2, 0.2], MP=[0.2, 0.2], CO=[0.2, 0.2], BTN=[0.2, 0.2])
    shares = _shares(AA=[0.50, 0.52], KK=[0.10, 0.90])
    out = study.verdict(study.summarise(_record(by_seed, shares, {"AA": 6, "KK": 6})))
    assert out["split_moves_with_seed"] is True
    assert out["copy_may_quote_aa"] is False


def test_the_copy_may_quote_aa_when_aa_moves_as_much_as_it_claims():
    by_seed = _by_seed(UTG=[0.2, 0.2], MP=[0.2, 0.2], CO=[0.2, 0.2], BTN=[0.2, 0.2])
    shares = _shares(AA=[0.03, 0.92], KK=[0.10, 0.90])
    out = study.verdict(study.summarise(_record(by_seed, shares, {"AA": 6, "KK": 6})))
    assert out["copy_may_quote_aa"] is True


def test_a_stable_split_is_a_result_and_not_a_missing_measurement():
    """Rule 9. If the ensemble steadied the split, the study has to be
    able to say so."""
    by_seed = _by_seed(UTG=[0.2, 0.2], MP=[0.2, 0.2], CO=[0.2, 0.2], BTN=[0.2, 0.2])
    shares = _shares(AA=[0.50, 0.51], KK=[0.30, 0.32])
    out = study.verdict(study.summarise(_record(by_seed, shares, {"AA": 6, "KK": 6})))
    assert out["measured"] is True and out["split_moves_with_seed"] is False


def test_an_unrun_study_reports_as_unmeasured():
    assert study.verdict({"arms": {}})["measured"] is False


# -- rule 8: the control attributes, it does not grade -------------------

def test_the_control_arm_is_summarised_but_never_graded():
    by_seed = _by_seed(UTG=[0.2, 0.2], MP=[0.2, 0.2], CO=[0.2, 0.2], BTN=[0.2, 0.2])
    shares = _shares(AA=[0.5, 0.5])
    record = _record(by_seed, shares, {"AA": 6})
    record["arms"]["single"] = dict(record["arms"]["ensemble"])
    summary = study.summarise(record)
    assert set(summary["arms"]) == {"ensemble", "single"}
    assert study.verdict(summary, arm="ensemble")["measured"] is True


def test_the_study_measures_the_configuration_that_actually_ships():
    """The whole point of the item: M110/M111 measured 12,000 iterations
    with one seed, and six-handed ships 3,000 with an ensemble."""
    from api import config as cfg

    assert study.SIZE == 6 and study.STACK == 100.0
    assert study.ENSEMBLE == cfg.MULTIWAY_TABLE_CONFIGS[6]["ensemble"]
    assert study.SEEDS[0] == 1, "the shipped seed is one of the arms"
    assert len(set(study.SEEDS)) >= 3, "rule 3 needs a noise floor"


# -- the clause that needed no new solving -------------------------------

def test_the_fold_axis_is_weighted_by_how_often_a_node_occurs():
    nodes = [{"facing": True, "count": 90, "ensemble_move": 0.1},
             {"facing": True, "count": 10, "ensemble_move": 0.9},
             {"facing": False, "count": 1, "ensemble_move": 0.0}]
    out = study.fold_axis(nodes)
    assert out["facing"] == pytest.approx(0.18)


def test_first_in_is_only_called_sounder_when_it_clears_the_published_gap():
    """M289's own MIN_GAP. Reusing it is what makes this the same claim
    re-read rather than a new one under a kinder bar."""
    close = [{"facing": True, "count": 1, "ensemble_move": 0.10},
             {"facing": False, "count": 1, "ensemble_move": 0.08}]
    assert study.fold_axis(close)["first_in_is_sounder"] is False
    clear = [{"facing": True, "count": 1, "ensemble_move": 0.10},
             {"facing": False, "count": 1, "ensemble_move": 0.02}]
    assert study.fold_axis(clear)["first_in_is_sounder"] is True


def _m290_nodes():
    return json.loads((pathlib.Path(__file__).parent / "data"
                       / "preflop_ensemble_m290.json").read_text())["nodes"]


def test_the_fold_clause_misses_its_own_bar_on_the_arm_that_ships():
    """M289 measured singles and six-handed now ships M290's ensemble.
    The ORDERING survives - facing a raise still moves twice what first
    in does - but the GAP is 0.044 against M289's own 0.05, so at six
    the clause no longer clears the bar its own study set."""
    nodes = _m290_nodes()
    shipped = study.fold_axis(nodes, "ensemble_move")
    assert shipped["facing"] > shipped["first_in"], "the ordering holds"
    assert shipped["gap"] < study.FOLD_MIN_GAP
    assert shipped["first_in_is_sounder"] is False


def test_the_ensemble_is_what_took_it_under_the_bar():
    """On single seeds the same rows clear it comfortably, so this is
    M290's own improvement making its neighbour's disclosure false -
    M281's rule, one milestone later and in the other direction."""
    nodes = _m290_nodes()
    single = study.fold_axis(nodes, "single_move")
    shipped = study.fold_axis(nodes, "ensemble_move")
    assert single["first_in_is_sounder"] is True
    assert shipped["facing"] < single["facing"]


# -- the committed rows --------------------------------------------------

FIXTURE = pathlib.Path(__file__).parent / "data" / "preflop_position_m305.json"


def _recorded():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_recorded_run_is_at_the_shipped_budget_and_ensemble():
    from api import config as cfg

    record = _recorded()
    assert record["iterations"] == cfg.MULTIWAY_TABLE_CONFIGS[6]["iterations"]
    assert record["shipped_ensemble"] == cfg.MULTIWAY_TABLE_CONFIGS[6]["ensemble"]
    assert record["arms"]["ensemble"]["runs"] == record["shipped_ensemble"]
    assert record["arms"]["single"]["runs"] == 1


def test_the_shipped_constants_reproduce_from_the_recorded_rows():
    from api import config as cfg

    entry = study.summarise(_recorded())["arms"]["ensemble"]
    assert round(entry["position"]["gradient"]["mean"], 4) == cfg.PREFLOP_POSITION_GRADIENT
    assert round(entry["position"]["noise"]["mean"], 4) == cfg.PREFLOP_POSITION_SEED_NOISE
    assert round(entry["split"]["mean"], 4) == cfg.PREFLOP_SIZING_SPLIT_MOVE
    assert round(entry["aa"]["low"], 4) == cfg.PREFLOP_SIZING_AA_LOW
    assert round(entry["aa"]["high"], 4) == cfg.PREFLOP_SIZING_AA_HIGH
    for seat, value in cfg.PREFLOP_POSITION_OPENS.items():
        mine = [entry["open"][s][seat] for s in entry["open"]]
        assert round(sum(mine) / len(mine), 4) == value
