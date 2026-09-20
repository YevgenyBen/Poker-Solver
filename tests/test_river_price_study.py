"""The R6 pricing rule, as code, and the run it produced (M296)."""
import json
import pathlib

import pytest

from bench.studies import river_price as study

FIXTURE = pathlib.Path(__file__).parent / "data" / "river_price_m296.json"


def _recorded():
    return json.loads(FIXTURE.read_text())


def _row(kind, regret, slack=0.0, reach=1.0, off=0.0):
    return {"kind": kind, "regret_bb": regret, "slack_bb": slack,
            "reach_fraction": reach, "off_support": off}


def test_rows_the_reference_barely_reaches_are_dropped():
    rows = [_row("facing", 1.0, reach=0.04), _row("facing", 1.0, reach=0.05)]
    assert len(study.keep(rows)) == 1


def test_rows_off_the_references_support_are_dropped():
    """M258: at a pure node regret prices the sliver that overlaps."""
    rows = [_row("facing", 1.0, off=0.6), _row("facing", 1.0, off=0.5)]
    assert len(study.keep(rows)) == 1


def test_the_net_figure_subtracts_the_references_own_slack():
    out = study.cell([_row("facing", 0.30, slack=0.10), _row("facing", 0.10, slack=0.05)])
    assert out["bb"] == pytest.approx(0.20)
    assert out["bb_net"] == pytest.approx(0.125)
    assert out["per_100"] == pytest.approx(20.0)


def test_the_median_is_reported_beside_the_mean():
    """M183's shape: a mean alone reads as a typical case when the cost
    is tail-driven."""
    out = study.cell([_row("facing", 0.0)] * 9 + [_row("facing", 10.0)])
    assert out["bb_median"] == 0.0 and out["bb"] == pytest.approx(1.0)
    assert out["worst_bb"] == 10.0


def test_the_weighting_is_by_real_exposure():
    rows = [_row("opening", 1.0), _row("facing", 1.0)]
    out = study.summarise(rows, exposure={"opening": 0.10, "facing": 0.05})
    assert out["weighted"]["per_100_postflop_decisions"] == pytest.approx(15.0)


def test_the_recorded_run_reproduces_the_audits_price():
    out = study.summarise(_recorded())
    assert out["facing"]["n"] == 180 and out["opening"]["n"] == 123
    assert round(out["facing"]["per_100"], 1) == 11.0
    assert round(out["facing"]["per_100_net"], 1) == 6.5
    assert round(out["opening"]["per_100"], 1) == 3.0
    assert round(out["weighted"]["per_100_postflop_decisions"], 2) == 0.87


def test_the_river_facing_cost_is_tail_shaped():
    """The mean is 3.6x the median and the worst row is 5.5 bb, so the
    figure a player should plan around is not the average."""
    out = study.summarise(_recorded())["facing"]
    assert out["bb"] > 3 * out["bb_median"]
    assert out["worst_bb"] > 5.0


def test_facing_a_bet_costs_more_than_opening():
    """M188's standing split, in the external instrument's own units."""
    out = study.summarise(_recorded())
    assert out["facing"]["bb"] > 3 * out["opening"]["bb"]
