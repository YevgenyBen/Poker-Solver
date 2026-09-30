"""Rule tests for M311's end-to-end equity lookup A/B."""
from __future__ import annotations

import json
import pathlib

import pytest

from bench.studies import equity_lookup_ab as study


def _row(street, reference, vectorised, identical=True, rows=150):
    return {"hand": "h", "i": 0, "street": street, "reference": reference,
            "vectorised": vectorised, "identical": identical, "rows": rows,
            "iterations": 4000}


def test_summarise_counts_identity_over_every_request():
    out = study.summarise([_row("flop", 6.0, 4.0), _row("turn", 5.0, 4.0)])
    assert out["requests"] == 2 and out["identical"] == 2
    assert out["rows_compared"] == 300


def test_summarise_reports_each_street_separately():
    out = study.summarise([_row("flop", 6.0, 4.0), _row("flop", 7.0, 5.0),
                           _row("turn", 5.0, 4.0)])
    assert out["flop"]["n"] == 2 and out["turn"]["n"] == 1


def test_the_speedup_is_the_median_of_per_request_ratios():
    """Paired per request, not a ratio of medians: the arms ran on the same
    spot one after another, and pairing is what cancels the machine's
    drift (M70)."""
    out = study.summarise([_row("flop", 6.0, 3.0), _row("flop", 4.0, 4.0),
                           _row("flop", 9.0, 3.0)])
    assert out["flop"]["speedup_median"] == pytest.approx(2.0)
    assert out["flop"]["speedup_min"] == pytest.approx(1.0)


def test_requests_over_the_bar_are_counted_per_arm():
    out = study.summarise([_row("flop", 6.0, 4.9), _row("flop", 5.5, 5.1)])
    assert out["flop"]["over_bar_reference"] == 2
    assert out["flop"]["over_bar_vectorised"] == 1


def test_the_bar_is_the_one_m291_measured_against():
    assert study.LATENCY_BAR_SECONDS == 5.0


def test_one_changed_request_refuses_the_change():
    """The rule is every request identical, not most: one ULP can move an
    MCCFR solve at a near-tie (M74), so 'almost identical' is a different
    answer."""
    rows = [_row("flop", 6.0, 4.0)] * 25 + [_row("flop", 6.0, 4.0, identical=False)]
    assert study.verdict(study.summarise(rows)).startswith("REFUSED: 1 of 26")


def test_all_identical_ships():
    rows = [_row("flop", 6.0, 4.0)] * 3
    assert study.verdict(study.summarise(rows)) == "SHIP: bit-identical on all 3 requests"


def test_no_data_is_not_a_ship():
    assert study.verdict(study.summarise([])) == "NO DATA"


ROWS = (pathlib.Path(__file__).resolve().parent / "data"
        / "equity_lookup_ab_m311.json")


@pytest.mark.skipif(not ROWS.exists(), reason="the A/B has not run yet")
def test_the_committed_ab_is_bit_identical_on_every_request():
    rows = json.loads(ROWS.read_text())
    summary = study.summarise(rows)
    assert summary["requests"] >= 20
    assert study.verdict(summary).startswith("SHIP")


@pytest.mark.skipif(not ROWS.exists(), reason="the A/B has not run yet")
def test_the_committed_ab_ran_at_the_shipped_three_live_budget():
    from api import config as cfg
    for row in json.loads(ROWS.read_text()):
        assert row["iterations"] == cfg.DEFAULT_MULTIWAY_PATH_QUERY_FLOP_ITERATIONS


@pytest.mark.skipif(not ROWS.exists(), reason="the A/B has not run yet")
def test_every_committed_request_got_faster():
    """Not just the median: a change that sped up the typical request and
    slowed some others would be a different claim."""
    for row in json.loads(ROWS.read_text()):
        assert row["vectorised"] < row["reference"], row
