"""Rule tests for M312's head-start study, written before any spot ran."""
from __future__ import annotations

import json
import pathlib

import numpy as np
import pytest

from bench.studies import warm_start as study
from poker_solver.cfr import InfoSetTable


# ---------------------------------------------------------- the neighbour

def test_the_neighbour_moves_the_lowest_unpaired_card_up_one_rank():
    assert study.neighbour_board("Kd8d3c", "AhQh") == "Kd8d4c"


def test_the_neighbour_keeps_the_suit():
    out = study.neighbour_board("Kd8d3c", "AhQh")
    assert out[4:6][1] == "c"


def test_the_neighbour_never_makes_a_pair():
    """3 -> 4 would pair a 4 already on the board, so it steps down."""
    assert study.neighbour_board("Kd4h3c", "AhQh") == "Kd4h2c"


def test_the_neighbour_skips_a_paired_card():
    """Moving one of a pair would unpair the board - a texture change."""
    assert study.neighbour_board("8d8c3h", "AhQh") == "8d8c4h"
    assert study.neighbour_board("3d3cKh", "Js9s") == "3d3cAh"


def test_no_neighbour_when_hero_holds_both_candidate_cards():
    """The only unpaired card is the Kh; its neighbours Ah and Qh are both
    hero's, so there is nothing legal to move it to."""
    assert study.neighbour_board("3d3cKh", "AhQh") is None


def test_the_neighbour_never_lands_on_heros_card():
    assert study.neighbour_board("Kd8d3c", "4cQh") == "Kd8d2c"


def test_a_trips_board_has_no_neighbour():
    assert study.neighbour_board("7d7c7h", "AhQh") is None


def test_an_ace_steps_down_not_off_the_end():
    assert study.neighbour_board("AdKc2h", "QsJs") == "AdKc3h"


# --------------------------------------------------------- the head start

def _table(regret, strategy):
    return InfoSetTable(regret_sum=np.array(regret, dtype=float),
                        strategy_sum=np.array(strategy, dtype=float),
                        last_regret=None, last_strategy=None)


def test_the_head_start_scales_regrets_to_count_as_much_as_the_refinement():
    out = study.head_start({(): _table([[400.0, -40.0]], [[9.0, 1.0]])},
                           donor_iterations=4000, budget=1000)
    assert np.allclose(out[()].regret_sum, [[100.0, -10.0]])


def test_the_head_start_resets_strategy_sums():
    """Kept, a 4,000-iteration donor's strategy sums carry ~16x the linear
    weight of 1,000 refinement iterations and the answer IS the donor's."""
    out = study.head_start({(): _table([[1.0, 2.0]], [[9.0, 1.0]])})
    assert not out[()].strategy_sum.any()


def test_the_head_start_does_not_change_where_refinement_starts():
    """Regret matching normalises positive regret, so scaling leaves the
    starting policy exactly where the donor left it."""
    donor = _table([[30.0, 10.0, -5.0]], [[0.0, 0.0, 0.0]])
    out = study.head_start({(): donor})
    def policy(r):
        pos = np.maximum(r, 0.0)
        return pos / pos.sum()
    assert np.allclose(policy(out[()].regret_sum[0]), policy(donor.regret_sum[0]))


def test_the_head_start_never_mutates_the_donor():
    donor = _table([[400.0, -40.0]], [[9.0, 1.0]])
    study.head_start({(): donor})
    assert donor.regret_sum.tolist() == [[400.0, -40.0]]
    assert donor.strategy_sum.tolist() == [[9.0, 1.0]]


# ------------------------------------------------------------- the rule

def _rows(n, **delta):
    """Synthetic rows: every arm at a base score plus a per-arm delta with
    variation, one row per hand so hand sigma is defined."""
    rows = []
    for i in range(n):
        wobble = ((i % 7) - 3) * 0.01
        base = 0.40 + (i % 5) * 0.01
        row = {"hand": "h%03d" % i, "i": 0, "source": "pluribus"}
        for arm in study.ARMS:
            row[arm] = base + delta.get(arm, 0.0) + (wobble if arm != "C1000" else 0.0)
            row[arm + "_blind"] = 0.30
        rows.append(row)
    return rows


def test_a_failed_instrument_reads_nothing():
    rows = _rows(60)
    out = study.verdict(study.summarise(rows))
    assert out == {"instrument": "FAILED", "result": "NOT READ"}


def test_a_perfect_proposal_that_does_not_help_means_the_mechanism_failed():
    """The load-bearing control: if even the oracle cannot lift 1,000
    iterations, nothing about the neighbour is read."""
    rows = _rows(60, C4000=0.10, ORACLE0=0.10, WARM_O=0.0, WARM_N=0.10)
    out = study.verdict(study.summarise(rows))
    assert out["mechanism"] == "FAILED" and out["result"] == "NOT READ"


def test_a_neighbour_that_reaches_c4000_reads_prize():
    rows = _rows(60, C4000=0.10, ORACLE0=0.10, WARM_O=0.10, WARM_N=0.10)
    out = study.verdict(study.summarise(rows))
    assert out["result"] == "PRIZE" and out["halves"] == "held"


def test_a_neighbour_that_helps_but_falls_short_reads_partial():
    rows = _rows(60, C4000=0.30, ORACLE0=0.30, WARM_O=0.30, WARM_N=0.08)
    out = study.verdict(study.summarise(rows))
    assert out["result"] == "PARTIAL"


def test_a_neighbour_that_does_not_help_reads_no_help():
    rows = _rows(60, C4000=0.10, ORACLE0=0.10, WARM_O=0.10, WARM_N=0.0)
    out = study.verdict(study.summarise(rows))
    assert out["result"] == "NO HELP"


def test_a_decisiveness_only_gain_reads_artifact():
    rows = _rows(60, C4000=0.10, ORACLE0=0.10, WARM_O=0.10, WARM_N=0.10)
    for n, r in enumerate(rows):
        r["WARM_N_blind"] = r["WARM_N"] + 0.2 + (n % 3) * 0.001
        r["C1000_blind"] = r["C1000"]
    out = study.verdict(study.summarise(rows))
    assert out["result"] == "ARTIFACT"


def test_only_the_reference_agents_rows_are_read():
    rows = _rows(60, C4000=0.10, ORACLE0=0.10, WARM_O=0.10, WARM_N=0.10)
    noise = _rows(200, C4000=-0.5)
    for r in noise:
        r["source"] = "handhq-2009"
        r["hand"] = "x" + r["hand"]
    s = study.summarise(rows + noise)
    assert s["n"] == 60


def test_the_bar_is_inherited():
    from bench.studies import three_live_budget
    assert study.MIN_SIGMA == abs(three_live_budget.MIN_SIGMA)


def test_the_budgets_are_the_two_the_product_ships_at_three_and_four_live():
    from api import config as cfg
    assert study.DONOR_ITERATIONS == cfg.DEFAULT_MULTIWAY_PATH_QUERY_FLOP_ITERATIONS
    assert study.BUDGET == cfg.MULTIWAY_WIDE_POT_ITERATIONS["flop"]


# ------------------------------------------------------- committed rows

ROWS = (pathlib.Path(__file__).resolve().parent / "data" / "warm_start_m312.json")


@pytest.mark.skipif(not ROWS.exists(), reason="the campaign has not run yet")
def test_every_committed_arm_ran_at_its_claimed_budget():
    for r in json.loads(ROWS.read_text()):
        assert r["C1000_iterations"] == study.BUDGET
        assert r["C4000_iterations"] == study.DONOR_ITERATIONS
        assert r["WARM_O_iterations"] == study.BUDGET
        assert r["WARM_N_iterations"] == study.BUDGET
        assert r["NBR0_iterations"] == study.DONOR_ITERATIONS


@pytest.mark.skipif(not ROWS.exists(), reason="the campaign has not run yet")
def test_every_committed_neighbour_differs_from_its_board_by_one_card():
    for r in json.loads(ROWS.read_text()):
        a = {r["board"][i:i + 2] for i in range(0, 6, 2)}
        b = {r["neighbour"][i:i + 2] for i in range(0, 6, 2)}
        assert len(a - b) == 1 and len(b - a) == 1


# ------------------------------------------------------ the far-board arm

def test_the_far_board_shares_no_card_with_the_target_or_hero():
    far = study.far_board("Kd8d3c", "AhQh", "spot:1")
    cards = {far[i:i + 2] for i in range(0, 6, 2)}
    assert len(cards) == 3
    assert not cards & {"Kd", "8d", "3c", "Ah", "Qh"}


def test_the_far_board_is_reproducible_from_the_spots_identity():
    assert (study.far_board("Kd8d3c", "AhQh", "spot:1")
            == study.far_board("Kd8d3c", "AhQh", "spot:1"))
    assert (study.far_board("Kd8d3c", "AhQh", "spot:1")
            != study.far_board("Kd8d3c", "AhQh", "spot:2"))


def _fu(n, far_delta, near_delta, matches=True):
    rows = []
    for i in range(n):
        wobble = ((i % 7) - 3) * 0.01
        base = 0.40 + (i % 5) * 0.01
        rows.append({"hand": "h%03d" % i, "i": 0, "source": "pluribus",
                     "C1000": base, "WARM_F": base + far_delta + wobble,
                     "WARM_N": base + near_delta + wobble * 0.5,
                     "c1000_matches": matches})
    return rows


def test_one_c1000_mismatch_voids_the_join():
    """Joining onto the first run is only sound if the solve reproduces."""
    rows = _fu(60, 0.10, 0.10)
    rows[5]["c1000_matches"] = False
    assert study.followup_verdict(rows)["result"] == "NOT READ"


def test_a_far_board_that_helps_as_much_reads_generic():
    out = study.followup_verdict(_fu(60, 0.10, 0.10))
    assert out["result"] == "GENERIC"


def test_a_far_board_that_does_not_help_reads_specific():
    out = study.followup_verdict(_fu(60, 0.0, 0.10))
    assert out["result"] == "SPECIFIC" and not out["far_helps"]


def test_a_far_board_that_helps_less_than_the_neighbour_reads_specific():
    out = study.followup_verdict(_fu(60, 0.05, 0.30))
    assert out["result"] == "SPECIFIC" and out["near_separably_better"]


def test_no_followup_rows_is_not_a_result():
    assert study.followup_verdict([]) == {"result": "NO DATA"}


# ---------------------------------------------- the committed results

FAR = (pathlib.Path(__file__).resolve().parent / "data" / "warm_start_far_m312.json")


@pytest.mark.skipif(not ROWS.exists(), reason="the campaign has not run yet")
def test_the_committed_main_result_is_the_one_the_milestone_quotes():
    s = study.summarise(json.loads(ROWS.read_text()))
    out = study.verdict(s)
    assert out["result"] == "PRIZE"
    assert out["mechanism"] == "passed" and out["guard"] == "held"
    assert s["C1000->C4000"]["hand_sigma"] == pytest.approx(3.22, abs=0.05)
    assert s["C1000->WARM_N"]["hand_sigma"] == pytest.approx(3.64, abs=0.05)


@pytest.mark.skipif(not FAR.exists(), reason="the follow-up has not run yet")
def test_the_follow_up_join_is_valid_because_c1000_reproduced_on_every_spot():
    rows = json.loads(FAR.read_text())
    assert rows and all(r["c1000_matches"] for r in rows)


@pytest.mark.skipif(not FAR.exists(), reason="the follow-up has not run yet")
def test_the_follow_up_reads_specific_under_its_own_rule_and_neither_test_clears():
    """Pinned as pre-registered, and with the reason it is NOT evidence
    that proximity matters: SPECIFIC was the rule's default, and neither
    the far arm nor the near-over-far difference cleared 2 sigma. The
    honest reading is unresolved; the rule is left as it was written."""
    rows = json.loads(FAR.read_text())
    out = study.followup_verdict(rows)
    assert out["result"] == "SPECIFIC"
    assert out["far_helps"] is False and out["near_separably_better"] is False
