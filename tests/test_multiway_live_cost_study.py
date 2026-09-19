"""The R6 study's rule and spot builder, as code (M291)."""
import random

from bench.studies import multiway_live_cost as study


def test_the_line_leaves_exactly_the_live_count():
    for live in study.LIVES:
        line = study.preflop_line(live)
        assert line.count("fold") == study.SIZE - live
        assert line.count("raise") == 1
        assert line.count("call_or_check") == live - 1


def test_bodies_use_distinct_cards_and_close_earlier_streets():
    rng = random.Random(0)
    body = study.body_for("river", 4, rng)
    cards = [body["board"][i:i + 2] for i in (0, 2, 4)] + [body["turn_card"], body["river_card"],
                                                         body["hero_cards"][:2], body["hero_cards"][2:]]
    assert len(set(cards)) == len(cards)
    assert body["flop_action_path"] == ["call_or_check"] * 4
    assert body["turn_action_path"] == ["call_or_check"] * 4
    assert "turn_card" not in study.body_for("flop", 3, rng)


def _rows(street, live, units, ref=0.25):
    return [{"street": street, "live": live, "units": u, "reference_seconds": ref} for u in units]


def test_the_bar_is_five_seconds_in_this_runs_units():
    s = study.summarise(_rows("flop", 3, [1.0] * 10, ref=0.25))
    assert s["bar_units"] == 20.0


def test_an_over_bar_cell_with_real_exposure_needs_a_cap():
    rows = _rows("flop", 4, [30.0] * 10) + _rows("flop", 3, [5.0] * 10)
    needed, deciding = study.verdict(study.summarise(rows))
    assert needed and deciding == ["flop:4"]


def test_an_over_bar_cell_nobody_meets_does_not():
    """Six live on the river is 0.07% of real river decisions."""
    rows = _rows("river", 6, [30.0] * 10) + _rows("flop", 3, [5.0] * 10)
    needed, deciding = study.verdict(study.summarise(rows))
    assert not needed and deciding == []
    assert study.summarise(rows)["cells"]["river:6"]["over"]
