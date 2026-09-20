"""The R7 re-measurement's rule and its draw detector (M292)."""
import pytest

from bench.studies import aggression_caveat as study


def test_aggression_counts_every_way_of_putting_chips_in():
    row = {"fold": 0.1, "call_or_check": 0.2, "raise:7.50": 0.4, "all_in:97.50": 0.3}
    assert study.aggression(row) == pytest.approx(0.7)
    assert study.folding(row) == pytest.approx(0.1)


@pytest.mark.parametrize("hero,board,expect", [
    ("9h8h", "Kd7c6s", True),        # 6-7-8-9, both ends live
    ("Ts9s", "8d7c2h", True),
    ("5h4h", "3d2c9s", True),        # 2-3-4-5: an ace or a six completes it
    ("KhQh", "JdTc2h", True),        # T-J-Q-K: a nine or an ace completes it
    ("AhKh", "QdJc2h", False),       # J-Q-K-A is a one-ender - only a ten
    ("AhKh", "Kd7c2h", False),       # no draw at all
    ("Jh9h", "8d7c2h", False),       # gutshot, not open-ended - M140 kept these apart
    ("2h2d", "9d8c7s", False),       # the BOARD's draw, not hero's
])
def test_open_ended_detection(hero, board, expect):
    assert study.is_open_ended(hero, board) is expect


def test_a_made_straight_is_not_a_draw():
    assert study.is_open_ended("9h8h", "7d6c5s") is False


def _rows(n, signed, facing=False, open_ended=False, percentile=0.5, gap=0.0):
    return [{"shipped_aggression": 0.5 + signed + (0.01 if k % 2 else -0.01),
             "reference_aggression": 0.5,
             "shipped_continue": 0.5 + gap + (0.01 if k % 3 else -0.02),
             "reference_continue": 0.5,
             "facing": facing, "open_ended": open_ended, "percentile": percentile}
            for k in range(n)]


def test_the_headline_is_the_mean_and_worst_absolute_error():
    rows = _rows(4, 0.10)
    out = study.summarise(rows)
    assert out["mean_error"] == pytest.approx(0.10)
    assert out["worst_error"] == pytest.approx(0.11)


def test_a_clause_survives_only_on_its_own_cell():
    out = study.summarise(_rows(20, 0.10, open_ended=True))
    assert study.verdict(out)["open_ended_clause"]
    assert not study.verdict(out)["weak_facing_clause"]


def test_a_clause_dies_when_the_direction_reverses():
    out = study.summarise(_rows(20, -0.10, open_ended=True))
    assert not study.verdict(out)["open_ended_clause"]


def test_a_clause_dies_on_too_few_rows():
    out = study.summarise(_rows(4, 0.10, open_ended=True))
    assert not study.verdict(out)["open_ended_clause"]


def test_the_weak_facing_cell_is_weak_hands_facing_a_bet_only():
    rows = (_rows(20, 0.0, facing=True, percentile=0.1, gap=0.2)
            + _rows(20, 0.0, facing=True, percentile=0.9, gap=-0.5)
            + _rows(20, 0.0, facing=False, percentile=0.1, gap=-0.5))
    out = study.summarise(rows)
    assert out["weak_facing"]["n"] == 20
    assert study.verdict(out)["weak_facing_clause"]


# -- M292: the recorded run ------------------------------------------------

import json
import pathlib

from api import config as cfg

FIXTURE = pathlib.Path(__file__).parent / "data" / "aggression_caveat_m292.json"


def _recorded():
    return json.loads(FIXTURE.read_text())


def test_the_shipped_constants_reproduce_from_the_recorded_rows():
    out = study.summarise(_recorded())
    assert round(out["mean_error"], 4) == cfg.POSTFLOP_AGGRESSION_ERROR_MEAN
    assert round(out["worst_error"], 4) == cfg.POSTFLOP_AGGRESSION_ERROR_WORST
    assert out["n"] == cfg.POSTFLOP_AGGRESSION_ERROR_ROWS


def test_both_named_clauses_die_on_the_recorded_rows():
    """The verdict that removed them, re-derived - not remembered."""
    out = study.summarise(_recorded())
    assert study.verdict(out) == {"open_ended_clause": False, "weak_facing_clause": False}
    assert out["open_ended"]["n"] >= 30 and out["open_ended"]["mean"] < 0, (
        "the open-ender cell must be well fed AND pointing the other way, or "
        "the clause died of thin data rather than of evidence"
    )
    assert out["weak_facing"]["n"] >= study.MIN_ROWS
    assert 0 < out["weak_facing"]["sigma"] < study.MIN_SIGMA, (
        "the weak-hand cell kept its direction and missed the bar; if it ever "
        "clears 2 sigma the clause should come back"
    )


def test_the_population_is_the_one_the_rule_asked_for():
    rows = _recorded()
    assert sum(r["facing"] for r in rows) >= 40, "facing-a-bet spots were built"
    assert sum(r["open_ended"] for r in rows) >= 30, "the open-ender quota filled"
    assert len({(r["hand"], r["i"]) for r in rows}) == len(rows), "no decision twice"


def test_the_reference_arm_really_was_the_expensive_one():
    """A control on the arms themselves: an uncapped solve cannot be as
    cheap as the shipped one, and if it were, both arms ran the same
    configuration (M245's trap, where the wrong arm looks faster)."""
    rows = [r for r in _recorded() if r["shipped_seconds"] and r["reference_seconds"]]
    shipped = sorted(r["shipped_seconds"] for r in rows)[len(rows) // 2]
    reference = sorted(r["reference_seconds"] for r in rows)[len(rows) // 2]
    assert reference > 3 * shipped, (shipped, reference)
