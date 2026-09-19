"""Tests for bench/real_replay.py - replaying real hands through /advise."""
import json
import random

import pytest

from bench import hand_db, real_replay
from tests.test_hand_db import ONLINE, _write


@pytest.fixture()
def heads_up_hand(tmp_path):
    db = hand_db.connect(tmp_path / "hands.sqlite")
    hand_db.ingest_file(db, _write(tmp_path, "a.phhs", ONLINE), "online")
    yield next(hand_db.query(db, "n_players = 2"))
    db.close()


def _menu_post(calls):
    def post(body):
        calls.append(body)
        return 200, {"pot": 18.0, "modelled_bet_sizes": [5.94, 13.5, 45.0, 66.0],
                     "max_affordable_bb": 66.0}
    return post


def test_deal_gives_every_player_two_cards_off_the_board():
    cards = real_replay.deal("Tc7s5h2c3d", 6, random.Random(1))
    held = [c[i:i + 2] for c in cards.values() for i in (0, 2)]
    assert len(cards) == 6 and len(set(held)) == 12
    assert not {"Tc", "7s", "5h", "2c", "3d"} & set(held)


def test_a_real_bet_maps_to_the_nearest_modelled_size(heads_up_hand):
    acts = [a for a in heads_up_hand.streets() if a.kind != "show"]
    turn = next(i for i, a in enumerate(acts) if a.street == "turn")
    calls = []
    cards = real_replay.deal(heads_up_hand.board, 2, random.Random(0))
    body, why, worst = real_replay.request_for(
        heads_up_hand, acts, turn, 75.0, cards, _menu_post(calls))
    assert why is None
    assert body["preflop_action_path"] == ["raise", "raise", "call_or_check"]
    # a 10bb bet into 18bb, asked at our 18bb pot: 13.5 is nearest in log space
    assert body["flop_action_path"] == ["raise:13.50", "call_or_check"]
    assert body["board"] == "Tc7s5h" and body["turn_card"] == "2c"
    assert "turn_action_path" not in body
    assert worst == pytest.approx(1.35)
    # the walk asked at the flop node where the bet was made, as the bettor
    assert len(calls) == 1 and calls[0]["hero_cards"] == cards[acts[3].player]
    assert "flop_action_path" not in calls[0]


def test_a_walk_the_product_refuses_is_unrepresentable_with_its_status(heads_up_hand):
    acts = [a for a in heads_up_hand.streets() if a.kind != "show"]
    turn = next(i for i, a in enumerate(acts) if a.street == "turn")
    body, why, _ = real_replay.request_for(
        heads_up_hand, acts, turn, 75.0, {0: "AsAh", 1: "KsKh"},
        lambda b: (422, {"detail": "no"}))
    assert body is None and why == "walk flop 422"


def test_the_preflop_raise_cap_is_written_as_all_in(heads_up_hand):
    acts = [a for a in heads_up_hand.streets() if a.kind != "show"]
    old = real_replay.PREFLOP_MAX_RAISES
    try:
        real_replay.PREFLOP_MAX_RAISES = 2
        body, _, _ = real_replay.request_for(
            heads_up_hand, acts, 2, 75.0, {}, _menu_post([]))
    finally:
        real_replay.PREFLOP_MAX_RAISES = old
    assert body["preflop_action_path"] == ["raise", "all_in"]


def test_replay_one_records_defects_of_the_final_answer(heads_up_hand):
    def post(body):
        if "hero_cards" in body and body.get("flop_action_path") is None and "board" in body:
            return 200, {"pot": 18.0, "modelled_bet_sizes": [13.5], "max_affordable_bb": 66.0}
        return 200, {"pot": 18.0, "positions": ["BB", "BTN"], "hero": {"cards": "x"},
                     "strategy": {}, "modelled_bet_sizes": [13.5], "max_affordable_bb": 66.0}
    rows = [real_replay.replay_one(heads_up_hand, random.Random(s), post) for s in range(12)]
    answered = [r for r in rows if r["outcome"] == "answered"]
    assert answered, rows
    assert all("no hero row" in r["defects"] for r in answered)
    assert all(r["live"] == 2 for r in answered)


def test_a_refused_decision_keeps_the_products_reason(heads_up_hand):
    row = real_replay.replay_one(heads_up_hand, random.Random(0),
                                 lambda b: (422, {"detail": "every remaining player all in"}))
    assert row["outcome"] in ("refused", "unrepresentable")
    if row["outcome"] == "refused":
        assert "all in" in row["why"]


def test_sample_hands_is_seeded_and_bounded(tmp_path):
    db = hand_db.connect(tmp_path / "hands.sqlite")
    hand_db.ingest_file(db, _write(tmp_path, "a.phhs", ONLINE), "online")
    first = [h.id for h in real_replay.sample_hands(db, 5, seed=3, where="1")]
    again = [h.id for h in real_replay.sample_hands(db, 5, seed=3, where="1")]
    assert first == again and len(first) == 2
    assert len(list(real_replay.sample_hands(db, 1, seed=3, where="1"))) == 1
    assert list(real_replay.sample_hands(db, 5, seed=3, where="1",
                                         accept=lambda h: h.n_players == 3))[0].n_players == 3
    db.close()


class _FakeTime:
    """A clock that only moves when the fake work says so."""
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _slow_machine(factor):
    """A DriftClock and a post() on a machine running `factor` times slow:
    the reference takes 0.25s x factor and every request 1.5s x factor."""
    from bench.reference_units import DriftClock
    t = _FakeTime()

    def workload():
        t.now += 0.25 * factor
        return 0.25 * factor

    def post(body):
        t.now += 1.5 * factor
        return 200, {"pot": 18.0, "positions": ["BB", "BTN"],
                     "hero": {"cards": "x", "strategy": {"call_or_check": 1.0}},
                     "modelled_bet_sizes": [13.5], "max_affordable_bb": 66.0}
    return DriftClock(timer=t, workload=workload), post


def test_a_clock_records_units_that_a_uniform_slowdown_cannot_move(heads_up_hand):
    """M286. The property M240 validated, on the replay's own path: the
    same work on a machine three times slower reads three times the
    SECONDS and the same UNITS. That is what lets two audits' latency be
    compared at all - the 2026-09-18 audit could not."""
    def answered_rows(factor):
        clock, post = _slow_machine(factor)
        rows = [real_replay.replay_one(heads_up_hand, random.Random(s), post, clock)
                for s in range(12)]
        return [r for r in rows if r["outcome"] == "answered"]

    fast, slow = answered_rows(1.0), answered_rows(3.0)
    assert fast and len(fast) == len(slow)
    for a, b in zip(fast, slow):
        assert b["seconds"] == pytest.approx(3 * a["seconds"])
        assert b["units"] == pytest.approx(a["units"]) == pytest.approx(6.0)
        assert b["reference_seconds"] == pytest.approx(3 * a["reference_seconds"])


def test_without_a_clock_the_row_carries_seconds_only(heads_up_hand):
    """The default path is unchanged: no reference runs, no units field."""
    def post(body):
        return 200, {"pot": 18.0, "positions": ["BB", "BTN"],
                     "hero": {"cards": "x", "strategy": {"call_or_check": 1.0}},
                     "modelled_bet_sizes": [13.5], "max_affordable_bb": 66.0}
    rows = [real_replay.replay_one(heads_up_hand, random.Random(s), post) for s in range(12)]
    answered = [r for r in rows if r["outcome"] == "answered"]
    assert answered and all("seconds" in r and "units" not in r for r in answered)


def test_replay_to_carries_the_clock_into_every_row_and_writes_the_drift(heads_up_hand, tmp_path):
    """M286's first baseline parsed `--units` and never handed the clock
    on: 1,200 rows in seconds alone and no drift report. The loop now
    lives here, and a clock given to it must reach every answered row."""
    clock, post = _slow_machine(2.0)
    out = tmp_path / "rows.jsonl"
    report = real_replay.replay_to(str(out), [heads_up_hand] * 6, random.Random(1), post, clock)
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    answered = [r for r in rows if r["outcome"] == "answered"]
    assert len(rows) == 6 and answered
    assert all(r["units"] == pytest.approx(6.0) for r in answered)
    assert json.loads((tmp_path / "rows.jsonl.drift.json").read_text()) == report
    assert report["calibrations"] == len(answered) and report["drift"] == 1.0


def test_replay_to_without_a_clock_writes_no_drift_report(heads_up_hand, tmp_path):
    _, post = _slow_machine(1.0)
    out = tmp_path / "rows.jsonl"
    assert real_replay.replay_to(str(out), [heads_up_hand] * 3, random.Random(1), post) is None
    assert not (tmp_path / "rows.jsonl.drift.json").exists()
