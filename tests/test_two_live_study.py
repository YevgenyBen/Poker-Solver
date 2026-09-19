"""M285: the two-live study, re-derived from the repository.

The headline test pins the SHIPPED constants to MEASURED rows - M282's own,
committed as `tests/data/two_live_m282.json` - which is the link F58 found
missing everywhere: before this, the only thing any constant was checked
against was copy that quoted it.
"""
import json
import pathlib

import pytest

from api import config as cfg
from bench.studies import two_live as study
from poker_solver.game_tree import DecisionNode, GameConfig, build_game_tree

FIXTURE = pathlib.Path(__file__).parent / "data" / "two_live_m282.json"


def test_the_shipped_constants_reproduce_from_the_measured_rows():
    data = json.loads(FIXTURE.read_text())
    fresh = study.summarise(data["rows"], data["control"], cfg.PREFLOP_TWO_LIVE_GRADE_MIN_RAISES)
    quoted = {k: v for k, v in fresh.items() if not k.startswith("_")}
    assert quoted == {k: getattr(cfg, k) for k in quoted}
    assert study.drift(fresh, cfg) == []
    assert fresh["_warning_fired"] == len(data["rows"]), "every row must be the gate's own"


def _node_at(players, path):
    node = build_game_tree(GameConfig(positions=study.seats(players), stack_bb=study.STACK))
    for kind in path:
        node = node.children[next(a for a in node.legal_actions if a.kind == kind)]
    return node


@pytest.mark.parametrize("control", [False, True])
def test_every_drawn_spot_is_the_population_it_claims(control):
    """The gate's predicate re-checked on each spot by walking its path,
    not trusted from the sampler that produced it."""
    gate = cfg.PREFLOP_TWO_LIVE_RERAISE_MIN_TO_CALL_BB
    spots = study.draw_spots(gate, control=control, target=3, sizes=(3, 6, 9))
    assert spots
    for spot in spots:
        node = _node_at(spot["players"], spot["path"])
        assert isinstance(node, DecisionNode)
        live = [p for p in node.invested if p not in node.folded]
        owed = max(node.invested.values()) - node.invested[node.player_to_act]
        assert (len(live) >= 3) if control else (len(live) == 2), spot
        assert owed >= gate and node.raises_so_far == spot["raises"]


def test_the_draw_is_seeded():
    gate = cfg.PREFLOP_TWO_LIVE_RERAISE_MIN_TO_CALL_BB
    assert (study.draw_spots(gate, target=4, sizes=(4, 7))
            == study.draw_spots(gate, target=4, sizes=(4, 7)))


def test_the_draw_reproduces_the_population_m282_measured():
    """The same seed and rules give the same 84 spots M282 measured, so a
    re-run measures the population the shipped figure describes."""
    data = json.loads(FIXTURE.read_text())
    spots = study.draw_spots(cfg.PREFLOP_TWO_LIVE_RERAISE_MIN_TO_CALL_BB)
    assert len(spots) == len(data["rows"]) == cfg.PREFLOP_TWO_LIVE_NODES
    assert (sorted((s["players"], s["raises"]) for s in spots)
            == sorted((r["players"], r["raises"]) for r in data["rows"]))


def _row(raises, cont, fired=True):
    return {"players": 6, "raises": raises, "trash_continue": cont, "warning_fired": fired}


def test_summarise_grades_by_raise_count():
    rows = [_row(3, 0.9), _row(3, 1.0), _row(2, 0.2), _row(2, 0.4)]
    out = study.summarise(rows, [_row(2, 0.1)], grade_min_raises=3)
    assert out["PREFLOP_TWO_LIVE_FOUR_BET_CONTINUES"] == 0.95
    assert out["PREFLOP_TWO_LIVE_THREE_BET_CONTINUES"] == pytest.approx(0.3)
    assert out["PREFLOP_TWO_LIVE_NODES_OVER_90"] == 1
    assert out["PREFLOP_MANY_LIVE_TRASH_CONTINUES"] == 0.1


def test_drift_is_read_against_the_samples_own_interval():
    """M282's rule: a mean moves only if it leaves the fresh sample's 95%
    interval; a count - the sample is seeded - moves if it changes at all."""
    data = json.loads(FIXTURE.read_text())
    fresh = study.summarise(data["rows"], data["control"], cfg.PREFLOP_TWO_LIVE_GRADE_MIN_RAISES)
    ci = fresh["_ci95_trash"]
    nudged = dict(fresh, PREFLOP_TWO_LIVE_TRASH_CONTINUES=cfg.PREFLOP_TWO_LIVE_TRASH_CONTINUES + ci / 2)
    assert study.drift(nudged, cfg) == []
    moved = dict(fresh, PREFLOP_TWO_LIVE_TRASH_CONTINUES=cfg.PREFLOP_TWO_LIVE_TRASH_CONTINUES + ci * 2)
    assert any("TRASH_CONTINUES" in line for line in study.drift(moved, cfg))
    recounted = dict(fresh, PREFLOP_TWO_LIVE_NODES_OVER_90=cfg.PREFLOP_TWO_LIVE_NODES_OVER_90 + 1)
    assert any("NODES_OVER_90" in line for line in study.drift(recounted, cfg))
