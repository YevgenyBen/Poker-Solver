"""Rule tests for M309's wide-pot budget study.

Written BEFORE the arms were run. M306 recorded why: its own rule tests
caught three defects before a single spot was solved - halves that
alternated in draw order, a zero-variance cell scored 0.0 sigma where it
is undefined, and a local name shadowing the function it came from. The
last of those would have crashed a multi-hour run on its final line.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from bench.studies import wide_pot_budget as study


# --------------------------------------------------------------- kinds

def test_real_kind_covers_every_kind_the_hand_store_emits():
    """`bench.hand_db.replay` emits exactly these five decision kinds."""
    assert study.real_kind("fold") == "fold"
    assert study.real_kind("check") == "passive"
    assert study.real_kind("call") == "passive"
    assert study.real_kind("bet") == "aggressive"
    assert study.real_kind("raise") == "aggressive"


def test_real_kind_refuses_a_kind_it_does_not_know():
    """`show` is not a decision, and a silent default would score it."""
    with pytest.raises(ValueError):
        study.real_kind("show")


def test_kind_of_groups_our_own_action_names():
    assert study.kind_of("fold") == "fold"
    assert study.kind_of("check") == "passive"
    assert study.kind_of("call") == "passive"
    assert study.kind_of("raise:12.50") == "aggressive"
    assert study.kind_of("all_in:97.50") == "aggressive"


def test_the_two_vocabularies_agree_on_every_shared_kind():
    """Ours and the store's must collapse to the SAME three labels, or a
    row is scored on one axis and compared on another."""
    for ours, theirs in (("fold", "fold"), ("check", "check"),
                         ("call", "call"), ("raise:5.0", "raise")):
        assert study.kind_of(ours) == study.real_kind(theirs)


# ------------------------------------------------------------ the row

def test_mass_on_sums_only_the_matching_kind():
    row = {"fold": 0.25, "call": 0.25, "raise:5.0": 0.3, "all_in:90.0": 0.2}
    assert study.mass_on(row, "fold") == pytest.approx(0.25)
    assert study.mass_on(row, "passive") == pytest.approx(0.25)
    assert study.mass_on(row, "aggressive") == pytest.approx(0.5)


def test_aggression_is_the_aggressive_mass():
    row = {"check": 0.4, "raise:5.0": 0.6}
    assert study.aggression(row) == pytest.approx(study.mass_on(row, "aggressive"))


# ------------------------------------------------------- card blindness

def test_card_blind_is_weighted_by_the_range():
    strategy = {"AhAd": {"raise:5.0": 1.0}, "7c2d": {"fold": 1.0}}
    weights = {"AhAd": 3.0, "7c2d": 1.0}
    assert study.card_blind(strategy, weights, "aggressive") == pytest.approx(0.75)


def test_card_blind_ignores_hands_the_range_does_not_hold():
    strategy = {"AhAd": {"raise:5.0": 1.0}, "7c2d": {"raise:5.0": 1.0}}
    weights = {"AhAd": 1.0, "7c2d": 0.0}
    assert study.card_blind(strategy, weights, "aggressive") == pytest.approx(1.0)


def test_card_blind_is_none_when_nothing_carries_weight():
    """None must propagate, so `guard` drops the row instead of scoring a
    zero that reads as a measured null."""
    assert study.card_blind({"AhAd": {"fold": 1.0}}, {}, "fold") is None
    assert study.card_blind({}, {"AhAd": 1.0}, "fold") is None


def test_a_hedging_arm_earns_nothing_on_the_card_blind_axis():
    """The whole reason rule 3 exists. An arm whose every row is the same
    flat mix agrees with WHATEVER happened - and its blind row is that
    same mix, so its lift is exactly zero on every kind."""
    flat = {"fold": 1 / 3, "call": 1 / 3, "raise:5.0": 1 / 3}
    strategy = {"AhAd": dict(flat), "7c2d": dict(flat)}
    weights = {"AhAd": 1.0, "7c2d": 1.0}
    for kind in ("fold", "passive", "aggressive"):
        hero = study.mass_on(strategy["AhAd"], kind)
        blind = study.card_blind(strategy, weights, kind)
        assert hero - blind == pytest.approx(0.0)


# ------------------------------------------------------------- halving

def test_the_split_key_reads_only_the_decisions_identity():
    """M306's defect. A digest over anything MEASURED makes the halves a
    test of the generator rather than of the finding."""
    a = {"hand": "abc", "i": 3, "S": 0.1, "W": 0.9, "live": 4}
    b = {"hand": "abc", "i": 3, "S": 0.9, "W": 0.1, "live": 6}
    assert study.digest(a) == study.digest(b)


def test_the_split_key_keeps_one_hands_decisions_together():
    """Amendment 1. Six decisions can share one board, one preflop line and
    one set of ranges, so splitting per DECISION puts the same spot in both
    halves and the split-half check stops being independent."""
    assert study.digest({"hand": "abc", "i": 1}) == study.digest({"hand": "abc", "i": 2})


def test_the_split_key_separates_two_different_hands():
    assert study.digest({"hand": "abc", "i": 0}) != study.digest({"hand": "xyz", "i": 0})


def test_only_the_reference_source_is_primary():
    """Amendment 2: 2009 online play is recorded and never read."""
    assert study.is_reference({"source": study.REFERENCE_SOURCE}) is True
    assert study.is_reference({"source": "handhq-2009"}) is False
    assert study.is_reference({}) is False


def test_halves_do_not_alternate_in_draw_order():
    """The defect M306 caught: `i % 2` over the draw order puts every
    other DRAW in each half, so the halves differ by the generator's
    sequence and not by the spot."""
    rows = [{"hand": "h%d" % n, "i": 0} for n in range(40)]
    left, right = study.halves(rows)
    positions = [0 if r in left else 1 for r in rows]
    assert positions != [n % 2 for n in range(40)]


def test_halves_are_stable_under_reordering():
    rows = [{"hand": "h%d" % n, "i": n % 3} for n in range(30)]
    left, right = study.halves(rows)
    shuffled = list(reversed(rows))
    left2, right2 = study.halves(shuffled)
    assert {study.digest(r) for r in left} == {study.digest(r) for r in left2}
    assert {study.digest(r) for r in right} == {study.digest(r) for r in right2}


def test_halves_partition_without_loss_or_duplication():
    rows = [{"hand": "h%d" % n, "i": 0} for n in range(50)]
    left, right = study.halves(rows)
    assert len(left) + len(right) == len(rows)
    assert not ({id(r) for r in left} & {id(r) for r in right})


# -------------------------------------------------------------- paired

def test_paired_reports_sigma_none_below_two_rows():
    one = [{"hand": "a", "i": 0, "S": 0.2, "W": 0.5}]
    assert study.paired(one)["sigma"] is None
    assert study.paired(one)["n"] == 1


def test_paired_reports_sigma_none_when_there_is_no_variation():
    """M306's second defect. Every delta identical means sigma is
    UNDEFINED, not 0.0 - and 0.0 reads as "measured and null"."""
    rows = [{"hand": "h%d" % n, "i": 0, "S": 0.2, "W": 0.4} for n in range(8)]
    out = study.paired(rows)
    assert out["delta"] == pytest.approx(0.2)
    assert out["sigma"] is None


def test_paired_skips_a_row_missing_either_arm():
    rows = [{"hand": "a", "i": 0, "S": 0.2, "W": 0.4},
            {"hand": "b", "i": 0, "S": None, "W": 0.9},
            {"hand": "c", "i": 0, "S": 0.1, "W": None}]
    assert study.paired(rows)["n"] == 1


def test_paired_counts_which_way_each_decision_moved():
    rows = [{"hand": "a", "i": 0, "S": 0.1, "W": 0.4},
            {"hand": "b", "i": 0, "S": 0.5, "W": 0.2},
            {"hand": "c", "i": 0, "S": 0.3, "W": 0.3}]
    out = study.paired(rows)
    assert (out["better"], out["worse"]) == (1, 1)


def test_paired_signs_the_delta_as_wide_minus_shipped():
    """A sign error here inverts the finding, so it is pinned."""
    rows = [{"hand": "h%d" % n, "i": 0, "S": 0.10, "W": 0.30 + n * 0.01}
            for n in range(6)]
    assert study.paired(rows)["delta"] > 0


# --------------------------------------------------------------- guard

def test_the_guard_is_unavailable_rather_than_passing_when_blind_is_absent():
    rows = [{"hand": "h%d" % n, "i": 0, "S": 0.2, "W": 0.5} for n in range(6)]
    assert study.guard(rows) == {"n": 0}


def test_the_guard_compares_lifts_and_not_raw_scores():
    """Both arms gain 0.2 of raw agreement and both blind rows gain the
    same 0.2, so the LIFT difference is zero - the artifact rule 3 is
    there to catch."""
    rows = [{"hand": "h%d" % n, "i": 0,
             "S": 0.20 + n * 0.01, "S_blind": 0.10 + n * 0.01,
             "W": 0.40 + n * 0.01, "W_blind": 0.30 + n * 0.01}
            for n in range(8)]
    out = study.guard(rows)
    assert out["n"] == 8
    assert out["delta"] == pytest.approx(0.0, abs=1e-12)


# ------------------------------------------------------------- summary

def _rows(n, live, delta, street="flop", blind=True, spread=0.01):
    """Synthetic rows whose DELTAS vary, so sigma is defined.

    The first draft held the delta exactly constant and every cell came
    back with sigma None - `paired` being right and the fixture wrong.
    Worth keeping as a note: a study fixture must carry variation on the
    axis the rule measures, or it tests only the undefined-sigma branch.
    """
    out = []
    for i in range(n):
        shipped = 0.30 + (i % 5) * spread
        row = {"hand": "h%03d" % i, "i": 0, "live": live, "street": street,
               "source": study.REFERENCE_SOURCE,
               "facing": False, "S": shipped,
               "W": shipped + delta + ((i % 7) - 3) * spread,
               "S_aggression": 0.60, "W_aggression": 0.45}
        if blind:
            row["S_blind"] = 0.20
            row["W_blind"] = 0.20
        out.append(row)
    return out


def test_summarise_separates_three_live_from_four_or_more():
    rows = _rows(20, 3, 0.10) + _rows(14, 5, 0.05)
    out = study.summarise(rows)
    assert out["control3"]["n"] == 20
    assert out["wide4plus"]["n"] == 14


def test_summarise_pools_every_live_count_at_or_above_four():
    rows = _rows(6, 4, 0.05) + _rows(6, 5, 0.05) + _rows(6, 6, 0.05)
    assert study.summarise(rows)["wide4plus"]["n"] == 18


def test_summarise_reads_aggression_only_where_hero_faced_nothing():
    rows = _rows(6, 4, 0.05)
    for r in rows[:3]:
        r["facing"] = True
        r["S_aggression"] = 0.0
    out = study.summarise(rows)
    assert out["aggression"]["S"] == pytest.approx(0.60)
    assert out["aggression"]["reference"] == study.REFERENCE_BETS_CHECKED_TO


# ------------------------------------------------------------- verdict

def test_a_failed_control_concludes_nothing_at_four_live():
    """The load-bearing rule. If the instrument cannot reproduce M264's
    3-live gain then a null at 4+ live is not evidence of anything."""
    rows = _rows(30, 3, 0.0, spread=0.05) + _rows(30, 5, 0.20)
    out = study.verdict(study.summarise(rows))
    assert out["control"] == "FAILED"
    assert out["prize"] == "NOT CONCLUDED"
    assert "nothing is concluded" in out["note"]


def test_a_clean_result_reads_prize():
    rows = _rows(40, 3, 0.12) + _rows(40, 5, 0.10)
    out = study.verdict(study.summarise(rows))
    assert out == {"control": "passed", "prize": "PRIZE", "guard": "held",
                   "halves": "held", "hands": 40}


def test_a_null_at_four_live_reads_no_prize_once_the_control_passed():
    rows = _rows(40, 3, 0.12) + _rows(40, 5, 0.0, spread=0.05)
    out = study.verdict(study.summarise(rows))
    assert out["control"] == "passed"
    assert out["prize"] == "NO PRIZE"


def test_a_missing_guard_reads_provisional_and_never_passes_by_default():
    rows = _rows(40, 3, 0.12) + _rows(40, 5, 0.10, blind=False)
    out = study.verdict(study.summarise(rows))
    assert out["guard"] == "unavailable"
    assert out["prize"] == "PROVISIONAL"


def test_a_reversed_guard_reads_artifact():
    """Raw agreement improves while the card-blind lift separably gets
    WORSE - the decisiveness artifact."""
    rows = _rows(40, 3, 0.12)
    wide = _rows(40, 5, 0.10)
    for i, r in enumerate(wide):
        r["W_blind"] = r["W"] + 0.20 + (i % 3) * 0.001
        r["S_blind"] = r["S"]
    out = study.verdict(study.summarise(rows + wide))
    assert out["guard"] == "REVERSED"
    assert out["prize"] == "ARTIFACT"


def test_a_prize_that_one_half_does_not_carry_reads_unreplicated():
    """The pool must CLEAR while a half does not - so the half is given a
    wide spread rather than a reversal. Reversing a half outright sinks
    the pool too, and the verdict then reads NO PRIZE before the halves
    are ever consulted."""
    rows = _rows(40, 3, 0.12)
    wide = _rows(100, 5, 0.30)
    for n, r in enumerate(study.halves(wide)[0]):
        r["W"] = r["S"] + ((n % 2) * 2 - 1) * 0.30      # mean zero, no signal
    summary = study.summarise(rows + wide)
    assert summary["wide4plus"]["sigma"] >= study.MIN_SIGMA
    assert summary["wide_half_a"]["sigma"] < study.MIN_HALF_SIGMA
    assert study.verdict(summary)["prize"] == "UNREPLICATED"


def test_an_undefined_sigma_never_clears_a_bar():
    assert study._clears({"hand_sigma": None}, study.MIN_SIGMA) is False
    assert study._clears({}, study.MIN_SIGMA) is False
    assert study._clears({"hand_sigma": study.MIN_SIGMA}, study.MIN_SIGMA) is True


def test_the_verdict_reads_the_hand_clustered_sigma_not_the_per_decision_one():
    """Amendment 1's whole point. A cell significant per DECISION and not
    per HAND must not clear - that is the six-fold overstatement."""
    cell = {"sigma": 6.0, "hand_sigma": 0.4}
    assert study._clears(cell, study.MIN_SIGMA) is False
    assert study._clears(cell, study.MIN_SIGMA, key="sigma") is True


def test_paired_clusters_six_decisions_of_one_hand_into_one_draw():
    shared = [{"hand": "one", "i": n, "S": 0.1, "W": 0.9} for n in range(6)]
    out = study.paired(shared)
    assert out["n"] == 6
    assert out["hands"] == 1
    assert out["hand_sigma"] is None          # one draw cannot carry a sigma


# ------------------------------------------------------- the bar itself

def test_the_bar_is_inherited_and_not_chosen_here():
    """M305's rule: once data is on disk, only a bar somebody else fixed
    earlier can be trusted. This one is M291's."""
    from bench.studies import three_live_budget
    assert study.MIN_SIGMA == abs(three_live_budget.MIN_SIGMA) == 2.0


def test_the_arms_are_the_two_budgets_the_product_actually_ships():
    from api import config as cfg
    assert study.SHIPPED_ITERATIONS == cfg.MULTIWAY_WIDE_POT_ITERATIONS["flop"]
    assert study.WIDE_ITERATIONS == cfg.DEFAULT_MULTIWAY_PATH_QUERY_FLOP_ITERATIONS
    assert study.WIDE_ITERATIONS <= cfg.MAX_MULTIWAY_PATH_QUERY_FLOP_ITERATIONS


def test_the_reference_aggression_is_m264s_published_figure():
    assert study.REFERENCE_BETS_CHECKED_TO == 0.194


# ------------------------------------------------------- committed rows

ROWS = (pathlib.Path(__file__).resolve().parent / "data"
        / "wide_pot_budget_m309.json")


@pytest.mark.skipif(not ROWS.exists(), reason="the campaign has not run yet")
def test_the_committed_rows_carry_both_arms_and_a_live_count():
    rows = json.loads(ROWS.read_text())
    assert rows
    for r in rows:
        assert r["live"] >= 3
        assert r["street"] in ("flop", "turn", "river")
        assert r["real_kind"] in ("fold", "passive", "aggressive")
        assert 0.0 <= r["S"] <= 1.0 and 0.0 <= r["W"] <= 1.0


@pytest.mark.skipif(not ROWS.exists(), reason="the campaign has not run yet")
def test_every_committed_row_was_solved_at_the_budget_it_claims():
    """M233's rule, as a test: the response's own echo, not the constant."""
    rows = json.loads(ROWS.read_text())
    for r in rows:
        assert r["S_iterations"] == study.SHIPPED_ITERATIONS
        assert r["W_iterations"] == study.WIDE_ITERATIONS


def test_the_river_is_out_of_scope_because_it_has_no_second_arm():
    """Multiway river is capped at 200 iterations at EVERY live count, so
    there is nothing to compare. Scope fixed before the run, not found
    inside it."""
    from api import config as cfg
    from api.solving import _ADVISE_ITERATION_CAPS
    assert study.STREETS == ("flop", "turn")
    assert _ADVISE_ITERATION_CAPS[("river", True)] == (200, 200)
    assert "river" not in cfg.MULTIWAY_WIDE_POT_ITERATIONS


def test_both_studied_streets_can_actually_reach_both_arms():
    from api.solving import _ADVISE_ITERATION_CAPS
    from api import config as cfg
    for street in study.STREETS:
        _, ceiling = _ADVISE_ITERATION_CAPS[(street, True)]
        assert ceiling >= study.WIDE_ITERATIONS
        assert cfg.MULTIWAY_WIDE_POT_ITERATIONS[street] == study.SHIPPED_ITERATIONS


def test_the_table_size_is_the_one_the_reference_studies_used():
    """M262's reference agent is six-handed, and M264/M266/M269 all scored
    on it. Matching removes table size as a confound."""
    assert study.TABLE_SIZE == 6


def test_the_study_runs_at_the_references_own_stack_depth():
    """Restricting to one bucket is not a convenience: the published agent
    plays 100bb only, so the PRIMARY cell has no other depth available.
    Every figure is therefore a 100bb statement (M274)."""
    from api import config as cfg
    assert study.STACK_BUCKET_BB == 100
    assert study.STACK_BUCKET_BB in cfg.MULTIWAY_PREWARM_STACK_DEPTHS


def test_the_targets_do_not_let_the_secondary_source_starve_the_primary():
    """779 handhq rows against the reference's 238 at 4+ live: one flat
    quota would fill with data the verdict cannot read."""
    assert study.TARGETS[(4, study.REFERENCE_SOURCE)] > study.TARGETS[(4, "handhq-2009")]
    assert set(study.TARGETS) == {(4, "pluribus"), (3, "pluribus"),
                                 (4, "handhq-2009"), (3, "handhq-2009")}


def test_min_detectable_recovers_the_bar_times_the_standard_error():
    """A cell measuring +0.10 at exactly 2 sigma has a sem of 0.05, so the
    smallest effect it could have cleared 2 sigma with is +0.10 itself."""
    cell = {"hand_delta": 0.10, "hand_sigma": 2.0}
    assert study.min_detectable(cell, 2.0) == pytest.approx(0.10)
    assert study.min_detectable({"hand_delta": 0.10, "hand_sigma": 4.0},
                               2.0) == pytest.approx(0.05)


def test_min_detectable_is_none_when_nothing_was_measured():
    assert study.min_detectable({"hand_sigma": None, "hand_delta": 0.1}) is None
    assert study.min_detectable({"hand_sigma": 2.0, "hand_delta": None}) is None
    assert study.min_detectable({}) is None


def test_a_null_says_what_it_could_have_detected():
    """A null is evidence of absence only if the cell could have SEEN the
    effect. So NO PRIZE carries the floor and A10's figure beside it."""
    rows = _rows(40, 3, 0.12) + _rows(40, 5, 0.0, spread=0.05)
    out = study.verdict(study.summarise(rows))
    assert out["prize"] == "NO PRIZE"
    assert out["a10_reported"] == study.A10_SIGNED_GAP
    assert "could_have_detected" in out
    assert "underpowered_for_a10" in out


def test_a_prize_does_not_claim_a_power_floor():
    """The floor only qualifies a null; quoting it beside a positive result
    would invite reading it as a confidence bound, which it is not."""
    out = study.verdict(study.summarise(_rows(40, 3, 0.12) + _rows(40, 5, 0.10)))
    assert out["prize"] == "PRIZE"
    assert "could_have_detected" not in out
