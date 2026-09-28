"""`bench/studies/depth_leaf_value.py` - the reading rule for the depth
question asked WITHOUT chaining.

The rule these pin is rule 7: a null here is a real refutation, because
neither of the two things that could explain the chained arm's null can
explain this one. Both arms run at the shipped budget, so precision is
not it (M197), and neither crosses a chance node, so F45's dead-pot
offset is not it (M161).
"""
import pytest

from bench.studies import depth_leaf_value as study


def _row(agg_p=0.30, agg_d=0.30, lift_p=0.05, lift_d=0.05, spr=8.0):
    return {"agg_P": agg_p, "agg_D": agg_d, "lift_P": lift_p, "lift_D": lift_d,
            "spr": spr, "zero": 0.0, "iterations": 250, "river_cards": 12,
            "leaf_situations": 7}


# -- the axes ------------------------------------------------------------

def test_aggression_is_the_non_check_non_fold_mass():
    row = {"fold": 0.1, "call_or_check": 0.3, "raise:0.75": 0.4, "all_in:20.00": 0.2}
    assert study.aggression(row) == pytest.approx(0.6)


@pytest.mark.parametrize("action,kind", [
    ("fold", "fold"), ("call_or_check", "call_or_check"),
    ("check", "call_or_check"), ("raise:0.75", "aggressive"),
    ("all_in:20.00", "aggressive")])
def test_every_action_maps_to_one_of_three_kinds(action, kind):
    """Mapped by KIND so the two arms need no size mapping between them
    (M241's axis choice)."""
    assert study.kind_of(action) == kind


def test_the_card_blind_prior_cannot_be_won_by_hedging():
    """M262's control. Two arms with the SAME prior: the hedging one earns
    nothing for hedging, the decisive-and-right one earns the lot."""
    hedgy = {"a": {"call_or_check": 0.5, "raise:1": 0.5},
             "b": {"call_or_check": 0.5, "raise:1": 0.5}}
    sharp = {"a": {"call_or_check": 0.0, "raise:1": 1.0},
             "b": {"call_or_check": 1.0, "raise:1": 0.0}}
    weights = {"a": 1.0, "b": 1.0}
    assert study.card_blind(hedgy, weights, "aggressive") == pytest.approx(0.5)
    assert study.card_blind(sharp, weights, "aggressive") == pytest.approx(0.5)
    # hero holds "a", which is aggressive in both arms
    assert 0.5 - study.card_blind(hedgy, weights, "aggressive") == pytest.approx(0.0)
    assert 1.0 - study.card_blind(sharp, weights, "aggressive") == pytest.approx(0.5)


def test_a_zero_weight_hand_does_not_vote_in_the_prior():
    strategy = {"a": {"raise:1": 1.0}, "b": {"call_or_check": 1.0}}
    assert study.card_blind(strategy, {"a": 1.0, "b": 0.0}, "aggressive") == 1.0


def test_an_empty_range_has_no_prior_rather_than_a_zero_one():
    assert study.card_blind({}, {}, "aggressive") is None


# -- rule 4: does depth move the answer? --------------------------------

def test_a_movement_over_the_level_counts_as_moving_it():
    rows = [_row(agg_p=0.2, agg_d=0.2 + 0.06 + 0.001 * i) for i in range(8)]
    out = study.verdict(study.summarise(rows))
    assert out["depth_moves_the_answer"] is True


def test_a_movement_inside_the_level_does_not():
    rows = [_row(agg_p=0.2, agg_d=0.2 + 0.01 + 0.001 * i) for i in range(8)]
    out = study.verdict(study.summarise(rows))
    assert out["depth_moves_the_answer"] is False


def test_the_level_is_the_one_this_project_already_calls_close():
    """Inherited rather than invented (M305's rule): M304's CLOSE_LEVEL
    and M200's "9 of 16 under 0.05"."""
    assert study.MOVE_LEVEL == 0.05


# -- rule 5: is depth better? -------------------------------------------

def test_depth_is_better_only_when_the_lift_separates():
    rows = [_row(lift_p=0.02, lift_d=0.30 + 0.001 * i) for i in range(10)]
    out = study.verdict(study.summarise(rows))
    assert out["depth_is_better"] is True
    assert out["depth_is_worse"] is False
    assert out["refutation_is_clean"] is False


def test_depth_is_worse_when_the_lift_separates_the_other_way():
    rows = [_row(lift_p=0.30, lift_d=0.02 + 0.001 * i) for i in range(10)]
    out = study.verdict(study.summarise(rows))
    assert out["depth_is_worse"] is True
    assert out["depth_is_better"] is False


def test_a_lift_that_does_not_separate_is_a_null_not_a_win():
    rows = [_row(lift_p=0.10, lift_d=0.10 + (0.15 if i % 2 else -0.15))
            for i in range(10)]
    out = study.verdict(study.summarise(rows))
    assert out["depth_is_better"] is False and out["depth_is_worse"] is False


# -- rule 7: what makes a null here mean something ----------------------

def test_a_null_is_recorded_as_a_clean_refutation():
    """The whole reason the seam was built. A chained arm's null could be
    precision (M197) or F45's dead-pot offset (M161); here both arms run
    at the shipped budget and neither crosses a chance node, so a null
    cannot be either."""
    rows = [_row(agg_p=0.3, agg_d=0.3 + 0.2, lift_p=0.10, lift_d=0.09)
            for _ in range(10)]
    out = study.verdict(study.summarise(rows))
    assert out["depth_moves_the_answer"] is True, "it changed the answer"
    assert out["depth_is_better"] is False, "and did not improve it"
    assert out["refutation_is_clean"] is True


def test_both_arms_are_declared_at_the_shipped_budget():
    """If the arms ever differ in iterations this study has the chained
    arm's confound back and rule 7 no longer holds."""
    import pathlib
    from api import config as cfg

    source = pathlib.Path(study.__file__).read_text(encoding="utf-8")
    runner = source.split("def main(", 1)[1]
    assert "iterations=cfg.TURN_STANDALONE_ITERATIONS" in runner
    assert runner.count("iterations=cfg.TURN_STANDALONE_ITERATIONS") == 1, (
        "both arms share one `common` dict, so the budget cannot diverge")
    assert cfg.TURN_STANDALONE_ITERATIONS > 0


# -- rule 8: scope -------------------------------------------------------

def test_a_shallow_sample_is_flagged_rather_than_quietly_read():
    """An earlier probe drew SPR 0.2-3.2 and got M223's direction wrong
    for it - M222 measured the turn's disagreement collapsing there."""
    shallow = study.summarise([_row(spr=1.3) for _ in range(6)])
    assert study.verdict(shallow)["scope_is_the_real_regime"] is False
    deep = study.summarise([_row(spr=6.0) for _ in range(6)])
    assert study.verdict(deep)["scope_is_the_real_regime"] is True


def test_the_spr_bound_is_the_one_the_turn_note_already_uses():
    from api import config as cfg
    assert study.MIN_SPR == cfg.TURN_INDEPENDENT_SPR_MIN


# -- the shape of the reading --------------------------------------------

def test_an_unrun_study_reports_as_unmeasured():
    assert study.summarise([]) == {"n": 0}
    assert study.verdict({"n": 0})["measured"] is False


def test_the_direction_is_reported_as_a_count_as_well_as_a_mean():
    """M223 claimed the fuller model "bets everything". A mean can hide a
    split, so the per-spot count is reported beside it."""
    rows = [_row(agg_p=0.2, agg_d=0.9) for _ in range(6)] + [
        _row(agg_p=0.9, agg_d=0.2) for _ in range(2)]
    out = study.summarise(rows)
    assert out["more_aggressive_on"] == 6 and out["n"] == 8


def test_the_leaf_situation_count_is_recorded_with_the_figures():
    """It is what makes the study affordable at all - M247 measured 27
    terminals collapsing to 8, and the cost probe measured 7."""
    out = study.summarise([_row() for _ in range(4)])
    assert out["leaf_situations_median"] == 7
    assert out["river_cards"] == 12


# -- the committed rows --------------------------------------------------

import json
import pathlib

FIXTURE = pathlib.Path(__file__).parent / "data" / "depth_leaf_value_m308.json"


def _recorded():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_sample_is_the_real_regime_at_the_shipped_budget():
    from api import config as cfg

    rows = _recorded()
    assert len(rows) == 16
    assert all(r["spr"] >= study.MIN_SPR for r in rows)
    assert all(r["iterations"] == cfg.TURN_STANDALONE_ITERATIONS for r in rows)
    assert all(r["river_cards"] == 48 for r in rows), "sampling was refused"
    assert all(r["leaf_situations"] == 7 for r in rows), "M247 measured 8"


def test_depth_changes_the_answer_and_does_not_improve_it():
    """The finding. It clears the movement bar and the lift difference is
    a dead null - and because both arms share the shipped budget and
    neither crosses a chance node, the null is explained by neither
    precision (M197) nor F45 (M161)."""
    out = study.verdict(study.summarise(_recorded()))
    assert out["depth_moves_the_answer"] is True
    assert out["depth_is_better"] is False
    assert out["depth_is_worse"] is False
    assert out["refutation_is_clean"] is True
    assert out["scope_is_the_real_regime"] is True


def test_it_is_a_tail_rather_than_a_shift():
    """Half the spots are untouched and five move 0.25-0.44. Quoting the
    median alone would describe neither."""
    rows = _recorded()
    moves = sorted(abs(r["agg_D"] - r["agg_P"]) for r in rows)
    assert sum(1 for m in moves if m >= study.MOVE_LEVEL) == 8
    assert sum(1 for m in moves if m > 0.20) == 5
    assert moves[0] < 0.01, "the quiet end is genuinely quiet"


def test_m223s_direction_does_not_reproduce():
    """"A converged chain bets everything at 0.997" - measured 8 of 16 at
    0.62 sigma with the menu matched and no chance node crossed."""
    out = study.summarise(_recorded())
    assert out["more_aggressive_on"] == 8
    assert abs(out["signed"]["sigma"]) < 2.0


def test_the_populations_action_mix_is_recorded_as_the_limitation_it_is():
    """M252: a benchmark measures the population it generates. A hand's
    FIRST turn action is the street's opening decision, where checking
    dominates - so the agreement axis had almost nothing to separate, and
    the lift null is underpowered rather than decisive."""
    rows = _recorded()
    passive = sum(1 for r in rows if r["chosen"] == "call_or_check")
    assert passive == 15, (
        "if this mix ever balances, the lift null becomes decisive and the "
        "copy about it should be re-derived rather than kept")


# -- the facing-a-bet mode (M308) ---------------------------------------

def test_the_facing_mode_builds_at_the_streets_opening_pot():
    """M177's other rule, and the one that voids a study rather than
    weakening it: the tree sizes bets off the pot it was BUILT with, so
    building at the post-bet pot models a much larger bet and scores two
    different situations against each other. The facing arm therefore
    takes pot and stack from the street's OPENING decision and walks the
    real bet."""
    import pathlib

    source = pathlib.Path(study.__file__).read_text(encoding="utf-8")
    runner = source.split("def main(", 1)[1]
    assert "street_turns[0]" in runner, "the opening decision supplies the pot"
    assert 'pot, stack = o_js["pot"], o_js["max_affordable_bb"]' in runner
    # And the node hero is scored at is the one the bet leads to.
    assert "_resolve_action_path(result.root, turn_path)" in runner


def test_the_two_modes_cannot_read_each_others_nodes():
    """Opening mode wants no turn path and facing mode needs one. A
    mismatch would have the arms answering a different question than the
    player faced - M177's rule in reverse, which is how M301's first pass
    produced 120 rows with zero facing a bet."""
    import pathlib

    runner = pathlib.Path(study.__file__).read_text(
        encoding="utf-8").split("def main(", 1)[1]
    assert "if bool(turn_path) != facing_mode:" in runner


def test_the_facing_populations_bias_is_recorded_in_the_rule():
    """Measured over 150 real facing-a-bet turn decisions: 42% at SPR >= 5,
    and an action mix of 30 raise / 120 call / ZERO fold, because hole
    cards are known mainly when a hand reaches showdown. The fold axis is
    the one M241/M242 chose for needing no size mapping, and it is
    unavailable here - so the mode improves the agreement axis without
    making it decisive, and the docstring says so."""
    assert "0 fold" in study.__doc__
    assert "42%" in study.__doc__
    assert "not decisive" in study.__doc__


# -- the facing-a-bet rows (M308) ----------------------------------------

FACING_FIXTURE = pathlib.Path(__file__).parent / "data" / "depth_leaf_facing_m308.json"


def _facing():
    return json.loads(FACING_FIXTURE.read_text(encoding="utf-8"))


def test_the_facing_sample_is_what_it_claims_to_be():
    from api import config as cfg

    rows = _facing()
    assert len(rows) == 16
    assert all(r["facing"] for r in rows), "every row faced a bet"
    assert all(r["turn_path"] for r in rows), "and reached that node by a real path"
    assert all(r["spr"] >= study.MIN_SPR for r in rows)
    assert all(r["iterations"] == cfg.TURN_STANDALONE_ITERATIONS for r in rows)
    assert all(r["river_cards"] == 48 for r in rows)


def test_depth_moves_the_turn_LESS_facing_a_bet():
    """The finding, and it is the opposite of what M188/M189 predict -
    they put 74% of all cost at 12% of decisions there. Whatever makes
    those nodes expensive, it is not that the turn values its leaves at
    showdown equity."""
    facing = study.summarise(_facing())
    opening = study.summarise(_recorded())
    assert facing["move_median"] < opening["move_median"]
    assert study.verdict(facing)["depth_moves_the_answer"] is False
    assert study.verdict(opening)["depth_moves_the_answer"] is True


def test_it_is_bimodal_so_neither_average_describes_it():
    """Ten spots move under 0.02 - five of those under 0.001 - and five
    move 0.14-0.60. A correct leaf value is irrelevant or transformative
    with almost nothing between.

    Those five are 1.4e-05 to 3.95e-04, NOT zero: they print as 0.000 at
    three decimals and a first version of this test asserted exact zeros
    off the log rather than off the data.
    """
    moves = sorted(abs(r["agg_D"] - r["agg_P"]) for r in _facing())
    assert sum(1 for m in moves if m < 0.02) == 10
    assert sum(1 for m in moves if m < 0.001) == 5
    assert not any(m == 0.0 for m in moves), "negligible is not zero"
    assert sum(1 for m in moves if m > 0.20) == 5


def test_neither_population_shows_depth_improving_the_advice():
    """Both clean: both arms at the shipped budget, neither crossing a
    chance node, so neither precision (M197) nor F45 (M161) explains
    either null."""
    for rows in (_recorded(), _facing()):
        out = study.verdict(study.summarise(rows))
        assert out["depth_is_better"] is False
        assert out["refutation_is_clean"] is True


def test_the_agreement_axis_stayed_underpowered_and_that_is_recorded():
    """M307 predicted this population would settle the lift question. It
    does not: hole cards are known mainly at SHOWDOWN so folders are
    absent, and SPR >= 5 selects spots where hero CALLS. If this mix ever
    balances, the lift null becomes decisive and the copy about it must be
    re-derived rather than kept (M281)."""
    facing = _facing()
    aggressive = sum(1 for r in facing if r["chosen"] == "aggressive")
    assert aggressive == 2, "2 of 16, against the ~20% projected from the scan"
    assert not any(r["chosen"] == "fold" for r in facing), (
        "a fold in this population would mean the store now carries a "
        "folder's cards, and the axis M241/M242 chose becomes available")
