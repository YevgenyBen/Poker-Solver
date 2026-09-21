"""`bench/studies/river_measured.py` - M301's reading rule, audit R3.

`RIVER_MEASURED_NOTE` makes four claims and they need two different
instruments, so the rule is pinned clause by clause here: a later edit
that would flip a verdict has to fail a test rather than re-read the
same rows more kindly.
"""
import pytest

from bench.studies import river_measured as study


def _a_row(regret=0.05, tvd=0.1, percentile=0.5, **extra):
    return {"regret_bb": regret, "tvd": tvd, "percentile": percentile,
            "reach_fraction": 1.0, "off_support": 0.0,
            "hero": "AcTh", "tag": "river_3bet_7h2s3s7c3h", **extra}


def _b_row(shipped=0.5, reference=0.5, facing=False, percentile=0.5):
    return {"shipped_aggression": shipped, "reference_aggression": reference,
            "facing": facing, "percentile": percentile}


# -- reading the board back out of M296's rows ---------------------------

def test_the_board_is_read_back_out_of_the_reference_tag():
    """It is what lets a later question ask about hand strength without
    re-solving 41 references."""
    assert study.board_of("river_3bet_7h2s3s7c3h") == "7h2s3s7c3h"
    assert study.board_of("river_srp_9c2c8h5sKd") == "9c2c8h5sKd"


def test_a_tag_without_a_five_card_board_is_refused():
    """Silently accepting a short board would score every hand on the
    wrong one and still return a number."""
    with pytest.raises(ValueError, match="five-card board"):
        study.board_of("river_3bet_7h2s3s")


# -- rule 1's inclusion, which is M296's own -----------------------------

def test_m296s_own_inclusion_rule_is_re_applied_not_re_invented():
    assert study.keep_a([_a_row(reach_fraction=0.01)]) == []
    assert study.keep_a([_a_row(off_support=0.9)]) == []
    assert len(study.keep_a([_a_row()])) == 1


# -- rule 1: the strength split ------------------------------------------

def _split_rows(strong_value, weak_value, n=20):
    rows = []
    for i in range(n):
        rows.append(_a_row(regret=strong_value + 0.001 * i,
                           tvd=strong_value + 0.001 * i, percentile=0.9))
        rows.append(_a_row(regret=weak_value + 0.001 * i,
                           tvd=weak_value + 0.001 * i, percentile=0.2))
    return rows


def test_the_strength_claim_survives_when_strong_hands_are_worse():
    summary = study.strength_split(_split_rows(0.50, 0.02))
    assert study.verdict_a(summary)["strong_hands_worse"] is True


def test_the_strength_claim_dies_when_the_bands_are_alike():
    summary = study.strength_split(_split_rows(0.20, 0.20))
    assert study.verdict_a(summary)["strong_hands_worse"] is False


def test_the_strength_claim_dies_when_strong_hands_are_BETTER():
    """The direction matters, not just the size: a note saying strong
    hands are worse must not survive a sample where they are better."""
    summary = study.strength_split(_split_rows(0.02, 0.50))
    assert summary["regret_bb"]["whole"]["delta"] < 0
    assert study.verdict_a(summary)["strong_hands_worse"] is False


def test_the_weak_hand_claim_stands_or_falls_with_the_split():
    """"Weak-hand advice measured accurate" is comparative; without the
    split it is an absolute claim this study does not support."""
    verdict = study.verdict_a(study.strength_split(_split_rows(0.20, 0.20)))
    assert verdict["weak_hands_accurate_claim"] == verdict["strong_hands_worse"]


def test_the_three_times_as_often_figure_is_re_derived():
    """M177 counted spots over 0.10 per band; so does this."""
    rows = ([_a_row(tvd=0.5, percentile=0.9) for _ in range(9)]
            + [_a_row(tvd=0.01, percentile=0.9)]
            + [_a_row(tvd=0.5, percentile=0.2) for _ in range(2)]
            + [_a_row(tvd=0.01, percentile=0.2) for _ in range(8)])
    out = study.strength_split(rows)["over_threshold"]
    assert out["strong"] == pytest.approx(0.9)
    assert out["weak"] == pytest.approx(0.2)
    assert out["times_as_often"] == pytest.approx(4.5)


# -- rule 2 and 3: the direction, and the facing-a-bet emphasis ----------

def test_the_direction_claim_needs_a_positive_signed_gap():
    """"It puts chips in more often than a fuller solve does" is a signed
    claim, and an unsigned distance cannot carry it."""
    rows = [_b_row(shipped=0.6 + 0.001 * i, reference=0.2) for i in range(30)]
    assert study.verdict_b(study.direction_split(rows))["errs_in_one_direction"] is True


def test_the_direction_claim_dies_when_the_engine_bets_LESS():
    rows = [_b_row(shipped=0.2, reference=0.6 + 0.001 * i) for i in range(30)]
    summary = study.direction_split(rows)
    assert summary["signed"] < 0
    assert study.verdict_b(summary)["errs_in_one_direction"] is False


def test_the_direction_claim_dies_when_it_is_not_separable():
    rows = [_b_row(shipped=0.5 + (0.3 if i % 2 else -0.3), reference=0.5)
            for i in range(30)]
    assert study.verdict_b(study.direction_split(rows))["errs_in_one_direction"] is False


def test_the_especially_facing_a_bet_wording_needs_its_own_evidence():
    rows = ([_b_row(shipped=0.9, reference=0.1 + 0.001 * i, facing=True) for i in range(20)]
            + [_b_row(shipped=0.5, reference=0.5 + 0.001 * i) for i in range(20)])
    assert study.verdict_b(study.direction_split(rows))["especially_facing_a_bet"] is True


def test_the_especially_wording_goes_when_facing_is_no_worse():
    rows = ([_b_row(shipped=0.6, reference=0.5 + 0.001 * i, facing=True) for i in range(20)]
            + [_b_row(shipped=0.6, reference=0.5 + 0.001 * i) for i in range(20)])
    assert study.verdict_b(study.direction_split(rows))["especially_facing_a_bet"] is False


# -- the replication guard the whole rule leans on -----------------------

def test_a_split_whose_halves_disagree_in_direction_is_refused():
    """M166/M167 in one line: the whole sample can separate while a half
    points the other way, and this note is a claim M177 made at 14 spots
    per band."""
    cell = {"whole": {"n_left": 30, "n_right": 30, "delta": 0.4, "sigma": 5.0},
            "halves": [{"delta": 0.5}, {"delta": -0.1}]}
    assert study.separates(cell) is False
    cell["halves"][1]["delta"] = 0.3
    assert study.separates(cell) is True


def test_a_split_with_too_few_rows_on_one_side_is_refused():
    cell = {"whole": {"n_left": 2, "n_right": 90, "delta": 0.4, "sigma": 9.0},
            "halves": [{"delta": 0.5}, {"delta": 0.3}]}
    assert study.separates(cell) is False


# -- the committed rows --------------------------------------------------

import json
import pathlib

FIXTURE_B = pathlib.Path(__file__).parent / "data" / "river_measured_m301.jsonl"


def _recorded_b():
    return [json.loads(line) for line in FIXTURE_B.read_text().splitlines() if line.strip()]


def test_arm_b_carries_both_node_types_in_equal_measure():
    """The amendment that cost this study a second pass: arm B's first
    run drew each hand's first river decision and produced 120 rows with
    ZERO facing a bet - M177's own rule failing inside the study that
    re-measures M177."""
    rows = _recorded_b()
    assert len(rows) == 240
    facing = sum(1 for r in rows if r["facing"])
    assert facing == 120 and len(rows) - facing == 120


def test_the_shipped_constants_reproduce_from_the_recorded_rows():
    from api import config as cfg

    summary = study.summarise(_recorded_b())
    assert summary["arm"] == "B"
    assert summary["n"] == cfg.RIVER_MEASURED_ROWS
    assert round(summary["signed"], 4) == cfg.RIVER_MEASURED_SIGNED_GAP
    assert round(summary["facing"]["whole"]["mean_left"], 4) == cfg.RIVER_MEASURED_FACING_ERROR
    assert round(summary["facing"]["whole"]["mean_right"], 4) == cfg.RIVER_MEASURED_OPENING_ERROR


def test_the_direction_claim_is_the_one_that_survived():
    """Small, separable and replicated on both halves."""
    summary = study.summarise(_recorded_b())
    assert summary["verdict"]["errs_in_one_direction"] is True
    assert summary["sigma"] >= study.MIN_SIGMA
    assert all(half["mean"] > 0 for half in summary["halves"])
    assert summary["signed"] < 0.05, "the lean is small and must be quoted as such"


def test_the_facing_a_bet_emphasis_is_backwards_and_removed():
    """M177 told players to be *particularly* suspicious facing a bet.
    Facing a bet measures MORE reliable than acting first - the same
    direction M300 found on the flop."""
    summary = study.summarise(_recorded_b())
    assert summary["verdict"]["especially_facing_a_bet"] is False
    assert summary["facing"]["whole"]["delta"] < 0


def test_the_strength_split_dies_on_the_independent_reference():
    """Arm A, over M296's rows: the claim the note was built on."""
    summary = study.summarise(study.load_a())
    assert summary["arm"] == "A"
    assert summary["verdict"]["strong_hands_worse"] is False
    assert summary["verdict"]["weak_hands_accurate_claim"] is False
    # Not merely unproven - the chips point the other way, and the
    # frequency shares are level where the copy claimed 3x.
    assert summary["regret_bb"]["whole"]["delta"] < 0
    assert summary["over_threshold"]["times_as_often"] < 1.5
