"""Tests for bench/real_replay.py - replaying real hands through /advise."""
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
