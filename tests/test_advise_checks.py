"""Tests for bench/advise_checks.py."""
from bench.advise_checks import response_defects


def _payload(row, confidence="high", cap=97.5):
    return {"hero": {"cards": "AhKd", "strategy": row},
            "solver_confidence": confidence, "max_affordable_bb": cap}


def test_a_clean_response_has_no_defects():
    assert response_defects(_payload({"call_or_check": 0.4, "raise:3.30": 0.6})) == []


def test_a_missing_hero_row_is_a_defect_whatever_the_confidence():
    """M262: the defect no harness caught."""
    assert "no hero row" in response_defects(_payload(None))
    assert "missing hero row at high confidence" in response_defects(_payload(None))
    low = response_defects(_payload(None, confidence="low"))
    assert low == ["no hero row"]


def test_sums_signs_sizes_and_uniform_rows_are_checked():
    assert response_defects(_payload({"fold": 0.5, "call_or_check": 0.4}))[0].startswith("row sums")
    assert "negative frequency" in response_defects(
        _payload({"fold": 1.1, "call_or_check": -0.1}))
    assert response_defects(_payload({"call_or_check": 0.4, "all_in:99.00": 0.6})) == [
        "unaffordable all_in:99.00 (max 97.50)"]
    assert response_defects(_payload({"fold": 0.5, "call_or_check": 0.5})) == [
        "uniform hero row at high confidence"]
    assert response_defects(_payload({"fold": 0.5, "call_or_check": 0.5}, "low")) == []


def test_a_request_without_hero_cards_needs_no_hero_row():
    assert response_defects({"hero": None, "strategy": {}}, {"stack_bb": 100}) == []
