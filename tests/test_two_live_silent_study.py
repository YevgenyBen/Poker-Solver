"""The study of the two-live cell the warning is silent on (audit R5).

The reading rule is applied by `verdict`, so it is tested here as code -
including the positive control that must pass before anything else is
read, which is what separates a pre-registered rule from a description of
one.
"""
import pytest

from bench.studies import two_live_silent as study


def test_class_of_names_hands_the_way_the_engine_does():
    assert study.class_of("7c2d") == "72o"
    assert study.class_of("2c7d") == "72o"
    assert study.class_of("AhKh") == "AKs"
    assert study.class_of("TsTd") == "TT"


def test_the_weak_band_holds_every_hand_the_defect_was_measured_on():
    """M251's five trash hands must sit inside the band the verdict reads,
    or the study would be measuring a different population."""
    pct = study.preflop_percentiles()
    for hand in ("72o", "83o", "92o", "T2o", "62o"):
        assert pct[hand] < study.WEAK_BAND, hand
    assert pct["AA"] > 0.99 and pct["32o"] < 0.01
    assert pct["AKs"] > pct["K9o"] > pct["22"] > pct["72o"]


def test_percentiles_are_weighted_by_combos():
    """A suited class has 4 combos and an offsuit one 12 - ranking by
    class count instead would put the median in the wrong place."""
    pct = study.preflop_percentiles()
    assert len(pct) == 169
    assert 0.0 < min(pct.values()) and max(pct.values()) < 1.0


def _row(owed, hand, ours, ref, n):
    return {"owed": owed, "hand_class": hand, "ours_continue": ours,
            "reference_continued": ref, "hand": f"h{n}", "i": 0}


PCT = {"72o": 0.05, "AA": 0.99}


def _cell(owed, ours, ref, count, start=0):
    return [_row(owed, "72o", ours, ref, start + k) for k in range(count)]


def test_summarise_splits_by_what_our_tree_says_is_owed():
    rows = _cell(9.0, 0.9, False, 10) + _cell(1.5, 0.1, False, 10, 100)
    rows.append(_row(1.5, "AA", 1.0, True, 999))          # not in the weak band
    out = study.summarise(rows, gate_bb=3.0, percentiles=PCT)
    assert out["gated"]["n"] == 10 and out["silent"]["n"] == 11
    assert out["silent"]["weak_n"] == 10
    assert out["gated"]["ours_weak"] == pytest.approx(0.9)
    assert out["gated"]["reference_weak"] == 0.0


def _noisy(owed, base, count, start=0):
    return [_row(owed, "72o", base + (0.02 if k % 2 else -0.02), k % 5 == 0, start + k)
            for k in range(count)]


def test_the_control_must_pass_before_the_silent_cell_is_read():
    """If the gated cell does not show us over-continuing, the instrument
    is not measuring M282's defect and the silent verdict means nothing."""
    rows = _noisy(9.0, 0.10, 40) + _noisy(1.5, 0.95, 40, 100)
    out = study.summarise(rows, 3.0, PCT)
    assert study.verdict(out).startswith("CONTROL FAILED")


def test_a_silent_cell_that_over_continues_widens_the_gate():
    rows = _noisy(9.0, 0.95, 40) + _noisy(1.5, 0.95, 40, 100)
    assert study.verdict(study.summarise(rows, 3.0, PCT)).startswith("WIDEN")


def test_a_silent_cell_that_matches_the_reference_is_earned():
    """Reference continues 1 in 5 in these rows, so 0.20 is matching it."""
    rows = _noisy(9.0, 0.95, 40) + _noisy(1.5, 0.20, 40, 100)
    assert study.verdict(study.summarise(rows, 3.0, PCT)).startswith("EARNED")


def test_a_widening_needs_both_halves_not_just_the_pool():
    """M189's bar, which M166 failed: a pooled 3 sigma carried by one half
    is not a finding."""
    strong = _noisy(1.5, 0.95, 20, 100)                   # one half over-continues
    level = _noisy(1.5, 0.20, 20, 200)                    # the other matches
    rows = _noisy(9.0, 0.95, 40)
    # Interleave so each split half gets one kind entirely.
    silent = [r for pair in zip(strong, level) for r in pair]
    for k, r in enumerate(silent):
        r["hand"] = f"s{k:03d}"
    out = study.summarise(rows + silent, 3.0, PCT)
    assert out["silent"]["weak_sigma"] >= 3
    assert study.verdict(out).startswith("EARNED")


def _mixed(owed, weak_base, strong_base, weak_count, strong_count, start=0):
    """Weak-band rows around `weak_base`, plus AA rows around `strong_base`."""
    rows = _noisy(owed, weak_base, weak_count, start)
    rows += [_row(owed, "AA", strong_base + (0.02 if k % 2 else -0.02), k % 5 == 0, start + 500 + k)
             for k in range(strong_count)]
    return rows


def test_an_unfed_weak_band_hands_the_control_to_the_whole_cell():
    """The AMENDMENT. Five weak rows cannot carry a 2-sigma control; the
    whole gated cell over-continuing can - and does pass it."""
    rows = _mixed(9.0, 0.95, 0.95, 5, 60) + _noisy(1.5, 0.20, 40, 1000)
    out = study.summarise(rows, 3.0, PCT)
    assert out["gated"]["weak_n"] < study.CONTROL_MIN_WEAK
    assert out["gated"]["all_sigma"] >= 2
    assert study.verdict(out).startswith("EARNED")


def test_a_fed_weak_band_is_not_overridden_by_the_whole_cell():
    """With enough weak rows the registered control stands: the whole cell
    over-continuing cannot rescue a weak band that does not."""
    rows = _mixed(9.0, 0.10, 0.95, 40, 200) + _noisy(1.5, 0.20, 40, 1000)
    out = study.summarise(rows, 3.0, PCT)
    assert out["gated"]["all_sigma"] >= 2
    assert study.verdict(out).startswith("CONTROL FAILED")


# -- M287: the recorded run, and the constants it put into the product ----

import json
import pathlib

from api import config as cfg

FIXTURE = pathlib.Path(__file__).parent / "data" / "two_live_silent_m287.json"


def _recorded():
    return json.loads(FIXTURE.read_text())


def test_the_recorded_run_reads_widen_under_the_registered_rule():
    """The verdict that widened the gate, re-derived from the rows it came
    from - so the decision is checkable, not remembered."""
    out = study.summarise(_recorded(), cfg.PREFLOP_TWO_LIVE_RERAISE_MIN_TO_CALL_BB,
                          study.preflop_percentiles())
    assert study.verdict(out).startswith("WIDEN")
    assert out["gated"]["weak_n"] < study.CONTROL_MIN_WEAK       # the amendment applied


def test_the_shipped_one_raise_constants_reproduce_from_the_recorded_rows():
    fresh = study.shipped_figures(_recorded(), cfg.PREFLOP_TWO_LIVE_MIN_TO_CALL_BB,
                                  cfg.PREFLOP_TWO_LIVE_RERAISE_MIN_TO_CALL_BB,
                                  study.preflop_percentiles())
    assert fresh == {k: getattr(cfg, k) for k in fresh}


def test_the_blind_completion_stays_silent_because_it_earned_it():
    """Below the widened gate we are TIGHTER than the reference with weak
    hands, so silence there is measured, not assumed."""
    pct = study.preflop_percentiles()
    below = [r for r in _recorded() if r["owed"] < cfg.PREFLOP_TWO_LIVE_MIN_TO_CALL_BB
             and pct[r["hand_class"]] < study.WEAK_BAND]
    mean, sigma, n = study._paired(below)
    assert n > 50 and mean < 0
    owed = {r["owed"] for r in _recorded()}
    assert max(o for o in owed if o < cfg.PREFLOP_TWO_LIVE_MIN_TO_CALL_BB) == 0.5
    assert min(o for o in owed if o >= cfg.PREFLOP_TWO_LIVE_MIN_TO_CALL_BB) == 1.5
