"""Tests for bench/hand_db.py - the local store of real played hands."""
import json

import pytest

from bench import hand_db

PLURIBUS_LIKE = """variant = 'NT'
ante_trimming_status = true
antes = [0, 0, 0, 0, 0, 0]
blinds_or_straddles = [50, 100, 0, 0, 0, 0]
min_bet = 100
starting_stacks = [10000, 10000, 10000, 10000, 10000, 10000]
actions = ['d dh p1 TcQc', 'd dh p2 8s4c', 'd dh p3 9c3d', 'd dh p4 Ah4h', 'd dh p5 Th5s', 'd dh p6 6c7s', 'p3 f', 'p4 cbr 210', 'p5 f', 'p6 f', 'p1 cc', 'p2 f', 'd db 7d5h9d', 'p1 cc', 'p4 cc', 'd db 7c', 'p1 cc', 'p4 cc', 'd db Qh', 'p1 cbr 230', 'p4 f']
hand = 0
players = ['MrBlue', 'MrBlonde', 'MrWhite', 'MrPink', 'MrBrown', 'Pluribus']
finishing_stacks = [10310, 9900, 10000, 9790, 10000, 10000]
"""

ONLINE = """[1]
variant = 'NT'
antes = [0, 0]
blinds_or_straddles = [1, 2]
min_bet = 2
starting_stacks = [200, 150]
actions = ['d dh p1 ????', 'd dh p2 ????', 'p2 cbr 6', 'p1 cbr 18', 'p2 cc', 'd db Tc7s5h', 'p1 cbr 20', 'p2 cc', 'd db 2c', 'p1 cc', 'p2 cc', 'd db 3d', 'p1 cc', 'p2 cc', 'p1 sm AhAd', 'p2 sm ????']
venue = 'PokerStars'
day = 2
month = 7
year = 2009
hand = 111
seat_count = 6
winnings = [76, 0]

[2]
variant = 'NT'
antes = [0, 0, 0]
blinds_or_straddles = [1, 2, 0]
min_bet = 2
starting_stacks = [100, 100, 100]
actions = ['d dh p1 ????', 'd dh p2 ????', 'd dh p3 ????', 'p3 f', 'p1 cc', 'p2 cc', 'd db 9h5sJd', 'p1 cbr 2', 'p2 f']
venue = 'PokerStars'
hand = 112
winnings = [4, 0, 0]

[3]
variant = 'FT'
antes = [0, 0]
blinds_or_straddles = [1, 2]
min_bet = 2
starting_stacks = [100, 100]
actions = ['d dh p1 ????', 'd dh p2 ????', 'p2 f']
hand = 113
"""


@pytest.fixture()
def store(tmp_path):
    db = hand_db.connect(tmp_path / "hands.sqlite")
    yield db, tmp_path
    db.close()


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_position_names_follow_phh_order():
    assert hand_db.position_names(2) == ["BB", "BTN"]
    assert hand_db.position_names(3) == ["SB", "BB", "BTN"]
    assert hand_db.position_names(6) == ["SB", "BB", "LJ", "HJ", "CO", "BTN"]
    assert hand_db.position_names(9)[:3] == ["SB", "BB", "UTG"]
    assert hand_db.position_names(9)[-1] == "BTN"
    with pytest.raises(ValueError):
        hand_db.position_names(1)


def test_heads_up_blinds_are_swapped_onto_the_players_who_post_them():
    """PHH heads-up: p1 is the big blind, the list still reads [sb, bb]."""
    assert hand_db.player_blinds([1, 2]) == [2, 1]
    assert hand_db.player_blinds([50, 100, 0]) == [50, 100, 0]


def test_a_single_hand_file_is_derived(store):
    db, tmp = store
    seen, added = hand_db.ingest_file(db, _write(tmp, "0.phh", PLURIBUS_LIKE), "pluribus")
    assert (seen, added) == (1, 1)
    row = db.execute("SELECT * FROM hands").fetchone()
    assert row["n_players"] == 6 and row["bb"] == 100
    assert row["eff_stack_bb"] == 100 and row["pot_type"] == "single_raised"
    assert row["players_to_flop"] == 2 and row["players_to_river"] == 2
    assert row["all_cards_known"] == 1 and row["board"] == "7d5h9d7cQh"
    # pot before the flop: 2.1 + 2.1 + 1 (folded big blind) = 5.2 bb
    assert row["pot_at_flop_bb"] == pytest.approx(5.2)
    assert json.loads(row["hole_cards"])["5"] == "6c7s"


def test_many_hands_per_file_and_only_holdem_is_kept(store):
    db, tmp = store
    seen, added = hand_db.ingest_file(db, _write(tmp, "a.phhs", ONLINE), "online")
    assert (seen, added) == (3, 2), "the fixed-limit hand is seen and skipped"
    hu = db.execute("SELECT * FROM hands WHERE n_players = 2").fetchone()
    assert hu["pot_type"] == "three_bet" and hu["eff_stack_bb"] == 75
    assert hu["showdown"] == 1 and hu["known_cards"] == 1
    assert hu["played_on"] == "2009-07-02"
    three = db.execute("SELECT * FROM hands WHERE n_players = 3").fetchone()
    assert three["pot_type"] == "limped" and three["players_to_flop"] == 2
    assert three["players_to_turn"] == 0


def test_the_replay_carries_sizes_and_the_pot_heads_up(store):
    db, tmp = store
    hand_db.ingest_file(db, _write(tmp, "a.phhs", ONLINE), "online")
    hand = next(hand_db.query(db, "n_players = 2"))
    acts = hand.streets()
    first = acts[0]
    assert (first.position, first.kind, first.to_bb) == ("BTN", "raise", 3.0)
    assert first.pot_before_bb == pytest.approx(1.5)
    three_bet = acts[1]
    assert (three_bet.position, three_bet.kind, three_bet.to_bb) == ("BB", "raise", 9.0)
    assert three_bet.facing_bb == pytest.approx(2.0)
    flop_bet = next(a for a in acts if a.street == "flop")
    assert (flop_bet.position, flop_bet.kind, flop_bet.to_bb) == ("BB", "bet", 10.0)
    assert flop_bet.pot_before_bb == pytest.approx(18.0)
    turn_check = next(a for a in acts if a.street == "turn")
    assert turn_check.kind == "check" and turn_check.pot_before_bb == pytest.approx(38.0)


def test_appending_is_idempotent(store):
    """The whole point: re-running an ingest adds nothing twice."""
    db, tmp = store
    path = _write(tmp, "a.phhs", ONLINE)
    assert hand_db.ingest_path(db, path, "online")["hands_added"] == 2
    again = hand_db.ingest_path(db, path, "online")
    assert again == {"files": 0, "skipped_files": 1, "hands_seen": 0, "hands_added": 0}
    # A different file carrying the same hands adds nothing either.
    copy = _write(tmp, "b.phhs", ONLINE + "\n")
    assert hand_db.ingest_path(db, copy, "online")["hands_added"] == 0
    assert db.execute("SELECT COUNT(*) FROM hands").fetchone()[0] == 2
    # ... and a genuinely new file adds only its hand.
    hand_db.ingest_path(db, _write(tmp, "1.phh", PLURIBUS_LIKE), "pluribus")
    assert hand_db.stats(db)["hands"] == 3
    assert hand_db.stats(db)["by_source"] == {"online": 2, "pluribus": 1}


def test_a_directory_is_walked(store):
    db, tmp = store
    sub = tmp / "raw" / "x"
    sub.mkdir(parents=True)
    _write(sub, "0.phh", PLURIBUS_LIKE)
    _write(sub, "a.phhs", ONLINE)
    _write(sub, "notes.txt", "ignored")
    totals = hand_db.ingest_path(db, tmp / "raw", "mixed")
    assert totals["files"] == 2 and totals["hands_added"] == 3


def test_rederive_rebuilds_stale_rows_from_what_is_stored(store):
    db, tmp = store
    hand_db.ingest_file(db, _write(tmp, "0.phh", PLURIBUS_LIKE), "pluribus")
    db.execute("UPDATE hands SET derived_version = 0, pot_type = 'wrong'")
    assert hand_db.rederive(db) == 1
    row = db.execute("SELECT pot_type, derived_version FROM hands").fetchone()
    assert tuple(row) == ("single_raised", hand_db.SCHEMA_VERSION)
    assert hand_db.rederive(db) == 0


def test_the_cli_ingests_and_reports(tmp_path, capsys):
    path = _write(tmp_path, "0.phh", PLURIBUS_LIKE)
    hand_db.main(["--db", str(tmp_path / "h.sqlite"), "ingest", str(path),
                  "--source", "pluribus"])
    out = capsys.readouterr().out
    assert '"hands_added": 1' in out and '"hands": 1' in out


UNKNOWN_STACKS = """variant = 'NT'
antes = [0, 0, 0]
blinds_or_straddles = [1, 2, 0]
min_bet = 2
starting_stacks = [inf, inf, inf]
actions = ['d dh p1 ????', 'd dh p2 ????', 'd dh p3 ????', 'p3 f', 'p1 f']
"""

NEGATIVE_BLIND = """variant = 'NT'
antes = [0, 0, 0]
blinds_or_straddles = [1, 2, -2]
min_bet = 2
starting_stacks = [100, 100, 100]
actions = ['d dh p1 ????', 'd dh p2 ????', 'd dh p3 ????', 'p3 f', 'p1 f']
"""


def test_unknown_stacks_are_kept_but_not_trusted(store):
    """The spec writes an unknown stack as `inf`; every iPoker hand is one."""
    db, tmp = store
    hand_db.ingest_file(db, _write(tmp, "u.phh", UNKNOWN_STACKS), "x")
    row = db.execute("SELECT * FROM hands").fetchone()
    assert row["clean"] == 0
    assert row["eff_stack_bb"] is None and row["min_stack_bb"] is None
    assert next(hand_db.query(db)).streets()[0].kind == "fold"


def test_a_negative_forced_bet_marks_the_hand_unclean(store):
    db, tmp = store
    hand_db.ingest_file(db, _write(tmp, "n.phh", NEGATIVE_BLIND), "x")
    hand_db.ingest_file(db, _write(tmp, "0.phh", PLURIBUS_LIKE), "x")
    assert [r[0] for r in db.execute("SELECT clean FROM hands ORDER BY n_players")] == [0, 1]


def test_heads_up_antes_are_reversed_too():
    assert hand_db.player_blinds([0, 1]) == [1, 0]


def test_an_older_store_gains_new_columns_and_can_be_rederived(tmp_path):
    path = tmp_path / "old.sqlite"
    db = hand_db.connect(path)
    hand_db.ingest_file(db, _write(tmp_path, "0.phh", PLURIBUS_LIKE), "p")
    db.commit()
    db.execute("ALTER TABLE hands DROP COLUMN clean")
    db.execute("UPDATE hands SET derived_version = 1")
    db.commit()
    db.close()
    db = hand_db.connect(path)                 # adds `clean` back, empty
    assert db.execute("SELECT clean FROM hands").fetchone()[0] is None
    assert hand_db.stats(db)["stale_rows"] == 1
    assert hand_db.rederive(db) == 1
    assert db.execute("SELECT clean FROM hands").fetchone()[0] == 1
    db.close()
