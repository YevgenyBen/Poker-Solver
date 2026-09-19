"""The R9 study's rule, as code (M289)."""
import pytest

from bench.studies import preflop_fold_seeds as study


def _nodes(size, facing_move, first_move, n=40):
    return [{"players": size, "facing": k % 4 < 2, "count": 10,
             "fold_move": facing_move if k % 4 < 2 else first_move} for k in range(n)]


def test_it_fires_where_facing_moves_and_first_in_does_not():
    out = study.verdict(study.summarise(_nodes(6, 0.18, 0.04)))
    assert out == {6: 0.18}


def test_it_is_silent_when_the_move_is_small():
    assert study.verdict(study.summarise(_nodes(6, 0.08, 0.01))) == {}


def test_it_is_silent_when_first_in_moves_as_much():
    """A size where EVERYTHING moves is not a facing-a-raise defect, and
    scoping the copy to first-in would be false there."""
    assert study.verdict(study.summarise(_nodes(6, 0.18, 0.16))) == {}


def test_each_size_is_judged_on_its_own():
    nodes = _nodes(6, 0.18, 0.04) + _nodes(3, 0.05, 0.04)
    assert study.verdict(study.summarise(nodes)) == {6: 0.18}


def test_both_halves_must_hold():
    nodes = _nodes(6, 0.18, 0.04)
    for k, node in enumerate(nodes):
        if k % 4 == 0:                 # half0's facing nodes stop moving
            node["fold_move"] = 0.02
    assert study.verdict(study.summarise(nodes)) == {}


def test_the_mean_is_weighted_by_occurrence():
    nodes = [{"players": 6, "facing": True, "count": 90, "fold_move": 0.30},
             {"players": 6, "facing": True, "count": 10, "fold_move": 0.00}]
    assert study.summarise(nodes)[6]["all"]["facing"] == pytest.approx(0.27)


def test_fold_move_reads_the_fold_column_only():
    base = [[0.2, 0.8, 0.0]]
    assert study.fold_move(base, [[[0.2, 0.0, 0.8]]], 0, [1]) == 0.0
    assert study.fold_move(base, [[[0.5, 0.5, 0.0]], [[0.3, 0.7, 0.0]]], 0, [1]) \
        == pytest.approx(0.2)


def test_the_seeds_are_fresh():
    from bench.studies import node_spread
    assert study.SEEDS[0] == 1
    used = set(node_spread.SEEDS) | set(node_spread.FOLD_SEEDS)
    assert not set(study.SEEDS[1:]) & used


# -- M289: the recorded run and what it put into the product -------------

import json
import pathlib

from api import config as cfg

FIXTURE = pathlib.Path(__file__).parent / "data" / "preflop_fold_seeds_m289.json"


def test_the_shipped_sizes_and_figures_reproduce_from_the_recorded_rows():
    """The table sizes the note fires at, and the number each quotes, are
    re-derived from the run - not remembered."""
    fired = study.verdict(study.summarise(json.loads(FIXTURE.read_text())))
    assert set(fired) == set(cfg.PREFLOP_FOLD_SEED_REASONS)
    for size, move in fired.items():
        assert getattr(cfg, f"PREFLOP_FOLD_SEED_MOVE_{size}") == move


def test_every_multiway_size_was_measured():
    sizes = {n["players"] for n in json.loads(FIXTURE.read_text())}
    assert sizes == set(study.SIZES)
