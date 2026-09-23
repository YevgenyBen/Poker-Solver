"""`bench/studies/multiway_instability.py` - M306's reading rule, audit R3.

The rule these pin is M254's, reused rather than rewritten: the gate is
read off the SHIPPED answer alone, and it must separate in both split
halves. M254 demanded the halves because M166 asserted a split from 27
spots and M167 withdrew it.
"""
import json
import pathlib

import pytest

from bench.studies import multiway_instability as study


_COUNTER = iter(range(10_000))


def _row(top=0.95, changed=0.0, tvd=0.02, street="flop", facing=False, board=None):
    # Distinct boards, so the digest-based halves see distinct spots
    # rather than collapsing every row onto one side.
    return {"top_mass": top, "changed": changed, "tvd": tvd,
            "street": street, "facing": facing, "top_action": "call_or_check",
            "board": board if board is not None else f"b{next(_COUNTER)}", "hero": "AhKh"}


# -- rule 1: the predictor comes off the shipped answer only -------------

def test_the_top_action_is_the_one_a_player_would_follow():
    action, mass = study.top_action({"fold": 0.1, "call_or_check": 0.6, "all_in:97.50": 0.3})
    assert action == "call_or_check" and mass == pytest.approx(0.6)


def test_an_empty_row_has_no_top_action_rather_than_a_default_one():
    assert study.top_action({}) == (None, 0.0)


def test_a_tie_resolves_the_same_way_every_time():
    """A uniform row is the normal case here (M149/F43), so the tie-break
    must not make a spot look unstable because two seeds broke a tie
    differently."""
    row = {"fold": 0.5, "call_or_check": 0.5}
    assert study.top_action(row) == study.top_action(dict(reversed(list(row.items()))))


def test_the_gate_reads_the_shipped_rows_mass_and_nothing_else():
    assert study.is_split(_row(top=0.89), 0.90) is True
    assert study.is_split(_row(top=0.90), 0.90) is False


# -- rule 2: what one spot contributes -----------------------------------

def test_a_spot_that_keeps_its_action_under_every_seed_changed_zero():
    shipped = {"fold": 0.2, "call_or_check": 0.8}
    out = study.spot_row(shipped, [{"fold": 0.3, "call_or_check": 0.7},
                                   {"fold": 0.1, "call_or_check": 0.9}])
    assert out["changed"] == 0.0 and out["top_action"] == "call_or_check"


def test_the_change_rate_is_a_share_of_the_seeds_not_a_flag():
    """Two of three seeds flipping is not the same news as three of
    three, and a boolean would lose it."""
    shipped = {"fold": 0.6, "call_or_check": 0.4}
    out = study.spot_row(shipped, [{"fold": 0.4, "call_or_check": 0.6},
                                   {"fold": 0.3, "call_or_check": 0.7},
                                   {"fold": 0.8, "call_or_check": 0.2}])
    assert out["changed"] == pytest.approx(2 / 3)


def test_total_variation_distance_is_half_the_absolute_difference():
    assert study.tvd({"a": 1.0}, {"b": 1.0}) == pytest.approx(1.0)
    assert study.tvd({"a": 0.5, "b": 0.5}, {"a": 0.5, "b": 0.5}) == 0.0


def test_an_action_missing_from_one_row_counts_as_zero_there():
    assert study.tvd({"a": 0.8, "b": 0.2}, {"a": 1.0}) == pytest.approx(0.2)


def test_a_spot_with_no_other_seeds_reports_nothing_rather_than_zero():
    out = study.spot_row({"a": 1.0}, [])
    assert out["changed"] is None and out["tvd"] is None


# -- rule 3: the gate has to separate, and in both halves ----------------

def _population(split_changed, decisive_changed, n=40):
    rows = []
    for i in range(n):
        rows.append(_row(top=0.5, changed=split_changed[i % len(split_changed)]))
        rows.append(_row(top=0.99, changed=decisive_changed[i % len(decisive_changed)]))
    return rows


def test_a_gate_that_separates_everywhere_survives():
    summary = study.summarise(_population([1.0, 0.667], [0.0, 0.0]), 0.90)
    out = study.verdict(summary)
    assert out["gate_survives"] is True and out["sigma"] >= study.MIN_SIGMA


def test_a_gate_that_separates_by_too_little_does_not_survive():
    """Rule 3 is a separation AND a sigma. A tiny gap measured very
    precisely is not a reason to tell one player 45% and another 0."""
    summary = study.summarise(_population([0.06, 0.04], [0.01, 0.0]), 0.90)
    out = study.verdict(summary)
    assert out["sigma"] > study.MIN_SIGMA, "precise"
    assert out["delta"] < study.MIN_SEPARATION
    assert out["gate_survives"] is False


def test_a_cell_with_no_variation_reports_an_undefined_sigma():
    """sem 0 at this sample size is a small-sample artifact, not
    infinite precision. Calling it `inf` would pass rule 3 on a
    degenerate cell; calling it 0.0 would read as "did not separate"."""
    summary = study.summarise(_population([1.0], [0.0]), 0.90)
    assert summary["whole"]["sigma"] is None
    assert study.verdict(summary)["gate_survives"] is False


def test_a_gate_carried_by_one_half_does_not_survive():
    """M166 asserted a split from 27 spots and M167 withdrew it at 44.

    Built by putting every separating spot on ONE side of the digest, so
    the pooled figure looks strong and a half does not.
    """
    rows = []
    for i in range(200):
        board = f"half{i}"
        strong = bool(study.halves([_row(board=board)])[0])
        rows.append(_row(top=0.5, changed=1.0 if strong else 0.02, board=board))
        rows.append(_row(top=0.99, changed=0.01 if strong else 0.0, board=board))
    summary = study.summarise(rows, 0.90)
    assert summary["whole"]["delta"] > study.MIN_SEPARATION
    assert study.verdict(summary)["gate_survives"] is False


def test_the_halves_do_not_come_from_draw_order():
    """The flaw this study's own rule test caught before the run: the
    generator cycles street by `drawn % 3` and node type by `drawn % 2`,
    so alternating rows tests the generator, not the gate."""
    rows = [_row(street=study.STREETS[i % 3], facing=bool(i % 2)) for i in range(60)]
    left, right = study.halves(rows)
    for side in (left, right):
        assert {r["street"] for r in side} == set(study.STREETS)
        assert {r["facing"] for r in side} == {True, False}


def test_the_halves_are_stable_across_processes():
    """`hash()` is salted per run, so a digest-based split has to be an
    explicit one or the committed figures would not re-derive."""
    rows = [_row(board=f"stable{i}") for i in range(40)]
    assert [r["board"] for r in study.halves(rows)[0]] == [
        r["board"] for r in study.halves(list(rows))[0]]
    assert study.halves(rows)[0] and study.halves(rows)[1]


def test_a_population_with_only_split_rows_cannot_grade_the_gate():
    """With nothing on the quiet side there is no contrast to measure,
    and reporting the firing cell alone would read as one."""
    summary = study.summarise([_row(top=0.5, changed=1.0) for _ in range(20)], 0.90)
    assert summary["whole"]["measured"] is False
    assert study.verdict(summary)["measured"] is False


def test_an_unrun_study_reports_as_unmeasured():
    assert study.verdict(study.summarise([], 0.90))["measured"] is False


# -- rule 4: per street, never pooled ------------------------------------

def test_every_street_is_reported_separately():
    """M254's first draft led with the firing cell's pooled average,
    which is river-weighted, and M245's own guard failed the build."""
    rows = ([_row(top=0.5, changed=1.0, street="river") for _ in range(10)]
            + [_row(top=0.5, changed=0.2, street="turn") for _ in range(10)])
    streets = study.summarise(rows, 0.90)["streets"]
    assert streets["river"]["split"]["changed"] == pytest.approx(1.0)
    assert streets["turn"]["split"]["changed"] == pytest.approx(0.2)
    assert streets["flop"]["split"]["n"] == 0


# -- rule 5: the quiet branch counts, it does not round ------------------

def test_the_quiet_branch_carries_a_held_count_beside_its_rate():
    """At 22 of 22 a percentage reads "0%" and claims more than 22 spots
    support, so the count is what the copy quotes."""
    rows = [_row(top=0.99, changed=0.0) for _ in range(22)]
    cell = study.summarise(rows + [_row(top=0.5, changed=1.0)], 0.90)["streets"]["flop"]
    assert cell["decisive"]["held"] == 22 and cell["decisive"]["n"] == 22


# -- the node axis M177 exists to force ----------------------------------

def test_a_facing_a_bet_node_is_constructed_rather_than_hoped_for():
    """M177's rule, and M301 broke it inside a study re-measuring M177:
    a street's first decision is never a facing-a-bet node."""
    import random

    opening = study.body_for(["raise", "call_or_check", "call_or_check"],
                             "flop", 3, False, random.Random(1))
    facing = study.body_for(["raise", "call_or_check", "call_or_check"],
                            "flop", 3, True, random.Random(1))
    assert "flop_action_path" not in opening
    assert facing["flop_action_path"] == ["raise"]


def test_a_later_street_still_closes_the_ones_before_it_per_live_player():
    """M252: closing a street with two checks only works heads-up, and
    that is how multiway turn and river spots were silently dropped."""
    import random

    body = study.body_for(["raise"] + ["call_or_check"] * 3, "river", 4, True,
                          random.Random(1))
    assert body["flop_action_path"] == ["call_or_check"] * 4
    assert body["turn_action_path"] == ["call_or_check"] * 4
    assert body["river_action_path"] == ["raise"]
    assert "turn_card" in body and "river_card" in body


def test_both_node_types_are_summarised_separately():
    rows = ([_row(top=0.5, changed=1.0, facing=True) for _ in range(10)]
            + [_row(top=0.99, changed=0.0, facing=True) for _ in range(10)]
            + [_row(top=0.5, changed=0.2) for _ in range(10)]
            + [_row(top=0.99, changed=0.0) for _ in range(10)])
    nodes = study.summarise(rows, 0.90)["nodes"]
    assert nodes["facing"]["delta"] == pytest.approx(1.0)
    assert nodes["opening"]["delta"] == pytest.approx(0.2)


# -- rule 7: confidence is not re-decidable here -------------------------

def test_confidence_stays_low_whatever_this_measures():
    """Multiway has no converged reference of any kind (F46/M163), so
    "high" is not available to claim - and M245 exists because it was."""
    summary = study.summarise(_population([1.0], [0.0]), 0.90)
    assert study.verdict(summary)["confidence_stays_low"] is True
    quiet = study.summarise(_population([0.0], [0.0]), 0.90)
    assert study.verdict(quiet)["confidence_stays_low"] is True


def test_the_study_moves_the_seed_and_keeps_the_shipped_one_first():
    assert study.SEEDS[0] == 0, "what /advise itself runs"
    assert len(set(study.SEEDS)) == len(study.SEEDS) >= 3


# -- the committed rows --------------------------------------------------

FIXTURE = pathlib.Path(__file__).parent / "data" / "multiway_instability_m306.json"


def _recorded():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["spots"]


def test_the_sample_reaches_every_street_and_both_node_types():
    spots = _recorded()
    for street in study.STREETS:
        assert sum(1 for s in spots if s["street"] == street) >= 10, street
    assert sum(1 for s in spots if s["facing"]) >= 10
    assert sum(1 for s in spots if not s["facing"]) >= 10
    assert all(s["live"] >= 3 for s in spots), "every spot is genuinely multiway"


def test_the_shipped_constants_reproduce_from_the_recorded_rows():
    from api import config as cfg

    summary = study.summarise(_recorded(), cfg.MULTIWAY_STABLE_MAX_TOP_ACTION)
    assert summary["n"] == cfg.MULTIWAY_INSTABILITY_SPOTS
    assert round(summary["whole"]["sigma"], 2) == cfg.MULTIWAY_INSTABILITY_SIGMA
    assert round(summary["whole"]["split"]["tvd"], 4) == cfg.MULTIWAY_UNSTABLE_TVD
    for street, value in {"flop": cfg.MULTIWAY_UNSTABLE_FLOP_SPOTS,
                          "turn": cfg.MULTIWAY_UNSTABLE_TURN_SPOTS,
                          "river": cfg.MULTIWAY_UNSTABLE_RIVER_SPOTS}.items():
        assert summary["streets"][street]["split"]["n"] == value, street
    assert round(summary["whole"]["split"]["changed"], 4) == cfg.MULTIWAY_UNSTABLE_ACTION_CHANGES
    assert round(summary["whole"]["decisive"]["changed"], 4) == cfg.MULTIWAY_STABLE_ACTION_CHANGES
    assert round(summary["whole"]["decisive"]["tvd"], 4) == cfg.MULTIWAY_STABLE_TVD
    assert summary["whole"]["decisive"]["held"] == cfg.MULTIWAY_STABLE_HELD_SPOTS
    assert summary["whole"]["decisive"]["n"] == cfg.MULTIWAY_STABLE_SPOTS
    for street, (held, spots) in {
            "flop": (cfg.MULTIWAY_STABLE_FLOP_HELD, cfg.MULTIWAY_STABLE_FLOP_SPOTS),
            "turn": (cfg.MULTIWAY_STABLE_TURN_HELD, cfg.MULTIWAY_STABLE_TURN_SPOTS),
            "river": (cfg.MULTIWAY_STABLE_RIVER_HELD, cfg.MULTIWAY_STABLE_RIVER_SPOTS),
    }.items():
        cell = summary["streets"][street]["decisive"]
        assert (cell["held"], cell["n"]) == (held, spots), street
    for street, value in {"flop": cfg.MULTIWAY_UNSTABLE_FLIP_FLOP,
                          "turn": cfg.MULTIWAY_UNSTABLE_FLIP_TURN,
                          "river": cfg.MULTIWAY_UNSTABLE_FLIP_RIVER}.items():
        assert round(summary["streets"][street]["split"]["changed"], 4) == value, street


def test_the_gate_survives_and_now_at_both_node_types():
    """M254 and M267 both graded the gate over opening decisions and
    whatever facing-a-bet nodes happened to be in the list. M177's rule
    is that they are CONSTRUCTED, and here they are - so the gate is
    graded separately at each, which no earlier run did."""
    summary = study.summarise(_recorded())
    assert study.verdict(summary)["gate_survives"] is True
    for name in ("opening", "facing"):
        cell = summary["nodes"][name]
        assert cell["delta"] >= study.MIN_SEPARATION, name
        assert cell["sigma"] >= study.MIN_SIGMA, name


def test_the_per_street_figures_moved_in_both_directions():
    """The finding. The flop was published at 0.50 and measures under a
    third; the turn was published at 0.3333 and measures nearly a half.
    A warning may not overstate its defect (M232) OR understate it."""
    streets = study.summarise(_recorded())["streets"]
    assert streets["flop"]["split"]["changed"] < 0.50
    assert streets["turn"]["split"]["changed"] > 0.3333
    assert streets["river"]["split"]["changed"] > 0.4902
