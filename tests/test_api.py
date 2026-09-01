import threading

from unittest import mock

import pytest
from fastapi.testclient import TestClient

from api import caches
from api import config as api_config
from api import main as api_main
from api import solving as api_solving
from api.main import app
from poker_solver.game_tree import walk
from poker_solver.cards import parse_cards, remaining_deck
from poker_solver.starting_hands import StartingHand

# api.main's real multiway iteration counts (100K/30K/300, see main.py's
# module docstring for why they differ by table size) are tuned for a
# reasonably-converged live demo, not test speed. Convergence itself is
# already covered by test_solver.py's multiway fixtures (same hand
# pool); these tests only need the HTTP plumbing to work, so a much
# smaller count keeps them fast — kept deliberately tiny (not just
# "smaller than the real default") since 9-max's per-iteration cost is
# high enough that even a few hundred iterations, multiplied across
# several parametrized players=9 tests, was measured during M9 to make
# the test suite itself slow.
FAST_MULTIWAY_ITERATIONS = 30
# M67 made the real multiway preflop pool all 169 classes, which costs
# ~170s (6-max) / ~215s (9-max) per spot at production settings. The
# autouse fixture clears caches between tests, so every multiway test
# would pay that afresh — untestable at any iteration count. These tests
# only need the HTTP plumbing to work, so they run against the small
# curated pool that used to BE the production pool (M9-M66). Its
# premium-heaviness is precisely why it was replaced for real use, and
# equally why it's harmless here: no test in this file asserts a
# strategy VALUE, only shapes, keys and status codes.
FAST_MULTIWAY_HANDS = [
    StartingHand("A", "A"),
    StartingHand("K", "K"),
    StartingHand("A", "K", suited=True),
    StartingHand("Q", "Q"),
    StartingHand("A", "K", suited=False),
    StartingHand("T", "9", suited=False),
    StartingHand("7", "2", suited=False),
    StartingHand("3", "2", suited=False),
]
# /solve_flop_cached's own fast-test iteration count — its own named
# constant even though the value happens to coincide with others below,
# matching this file's existing per-endpoint naming precedent.
FAST_FLOP_QUERY_ITERATIONS = 20
# /solve_flop_from_path's (M24) own fast-test flop-stage iteration count
# and range cap — mirrors FLOP_QUERY_ITERATIONS' own shrink, plus a
# class-per-side cap far below MAX_PATH_QUERY_CLASSES_PER_SIDE's real
# value (6) so even the full 169-class preflop pool caps down to a tiny,
# fast flop-stage solve (see the module docstring's Finding 1 for why an
# uncapped pool is untestable at any speed).
FAST_PATH_QUERY_ITERATIONS = 20
FAST_MAX_PATH_QUERY_CLASSES_PER_SIDE = 2
# /solve_turn_from_path (M26) reuses FLOP_TURN_MAX_RAISES/RAISE_SIZES
# directly (no demo-class constants of its own to shrink, same
# situation /solve_flop_from_path is in) — so it's already shrunk by
# this fixture's own FLOP_TURN_MAX_RAISES=1/RAISE_SIZES=() patch below,
# for the same test-speed reason. Its own class cap needs its own
# separate shrink, mirroring FAST_MAX_PATH_QUERY_CLASSES_PER_SIDE.
FAST_MAX_TURN_PATH_QUERY_CLASSES_PER_SIDE = 1
# M119 raised this from 1. One combo per side is not a small range, it
# is no range at all: the river solve becomes one fixed hand against one
# fixed hand, and every "does advice discriminate by hand strength"
# assertion below is decided by WHICH single villain combo the cap
# happens to pick. `test_advise_river_decision_discriminates_by_hand_
# strength` passed at 1 only because the pre-M119 combo weighting
# happened to pick a convenient one; correcting that weighting made the
# same degenerate fixture fail. Every river test passes at 2 and above.
# Measured on the river subset: cap 1 = 33.6s, cap 3 = 45.0s, cap 6 =
# 83.4s — 3 buys a real range for ~11s and 6 is not worth 50s.
FAST_RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE = 3


@pytest.fixture(autouse=True)
def _disable_prewarm_and_clear_cache(monkeypatch):
    # Pre-warming solves 7 full 169-hand spots (plus one spot per
    # multiway table size, plus one solve_flop_turn/solve_flop_to_river
    # spot) on startup — great for a real server, unnecessary cost for a
    # test run. Each test also gets clean caches so tests can't leak
    # state into each other.
    monkeypatch.setenv("POKER_SOLVER_PREWARM", "0")
    fast_table_configs = {
        players: {**table, "iterations": FAST_MULTIWAY_ITERATIONS}
        for players, table in api_config.MULTIWAY_TABLE_CONFIGS.items()
    }
    monkeypatch.setattr(api_config, "MULTIWAY_TABLE_CONFIGS", fast_table_configs)
    monkeypatch.setattr(api_config, "MULTIWAY_PREFLOP_HANDS", FAST_MULTIWAY_HANDS)
    # DEMO_CHAINED_FLOP_HERO_/VILLAIN_CLASSES' real 12-combo pool costs
    # ~18-26s (solve_flop_turn) / ~63-105s (solve_flop_to_river) per
    # solve — real numbers from M14's own PR, far too slow to pay
    # per-test. range_from_class_frequencies always expands a class to
    # its full suit-count (a suited class is the smallest possible, 4
    # combos), so 4+4=8 combos is the practical floor via this code
    # path — shrunk to that floor here, plus FLOP_TURN_MAX_RAISES down
    # to 1 (matching test_solver.py's own tiny_flop_turn_result fixture
    # scale — this tests HTTP plumbing, not the real demo tree's
    # convergence, which nothing here asserts on anyway).
    monkeypatch.setattr(api_config, "DEMO_CHAINED_FLOP_HERO_CLASSES", {StartingHand("9", "8", suited=True): 1.0})
    monkeypatch.setattr(api_config, "DEMO_CHAINED_FLOP_VILLAIN_CLASSES", {StartingHand("6", "4", suited=True): 1.0})
    monkeypatch.setattr(api_config, "FLOP_TURN_MAX_RAISES", 1)
    monkeypatch.setattr(api_config, "FLOP_TURN_RAISE_SIZES", ())
    # /solve_flop_cached (M22) has no `iterations` query param (see its
    # own module-docstring paragraph for why — nothing not part of the
    # canonical key is request-controllable), so unlike every other
    # /solve_flop* endpoint's tests, there's no per-request lever to
    # keep a real solve fast — the fixed pool/iteration constants
    # themselves have to be monkeypatched down instead.
    monkeypatch.setattr(api_config, "FLOP_QUERY_HERO_CLASSES", {StartingHand("A", "A"): 1.0})
    monkeypatch.setattr(api_config, "FLOP_QUERY_VILLAIN_CLASSES", {StartingHand("K", "K"): 1.0})
    monkeypatch.setattr(api_config, "FLOP_QUERY_ITERATIONS", FAST_FLOP_QUERY_ITERATIONS)
    # /solve_flop_from_path (M24) solves a REAL preflop spot over the
    # full 169-class pool internally (that's the whole point — a real
    # derived situation, not a fixed demo range), so there's no fixed
    # class-pool constant to shrink the way FLOP_QUERY_HERO_CLASSES
    # shrinks /solve_flop_cached's. Instead this fixture shrinks the
    # *cap* applied at request time, and the flop-stage iteration count.
    monkeypatch.setattr(api_config, "PATH_QUERY_ITERATIONS", FAST_PATH_QUERY_ITERATIONS)
    monkeypatch.setattr(api_config, "MAX_PATH_QUERY_CLASSES_PER_SIDE", FAST_MAX_PATH_QUERY_CLASSES_PER_SIDE)
    monkeypatch.setattr(api_config, "MAX_TURN_PATH_QUERY_CLASSES_PER_SIDE", FAST_MAX_TURN_PATH_QUERY_CLASSES_PER_SIDE)
    # /solve_river_from_path (M46) — its own combo-level cap, shrunk to
    # the practical floor (1 combo per side, 2 total) for the same
    # test-speed reason as every cap above; FLOP_TO_RIVER_MAX_RAISES/
    # RAISE_SIZES are already at their minimal production values (1/())
    # so need no further shrink here.
    monkeypatch.setattr(api_config, "RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE", FAST_RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE)
    # DEMO_MULTIWAY_FLOP_CLASSES (M37) — same shrink-to-the-floor idiom as
    # DEMO_CHAINED_FLOP_HERO_/VILLAIN_CLASSES above: one small suited
    # class per position (4 combos each via range_from_class_frequencies,
    # the practical floor), plus MULTIWAY_FLOP_MAX_RAISES down to 1 —
    # this tests HTTP plumbing, not the real demo tree's convergence.
    monkeypatch.setattr(
        api_config,
        "DEMO_MULTIWAY_FLOP_CLASSES",
        {
            "OOP": {StartingHand("9", "8", suited=True): 1.0},
            "MID": {StartingHand("6", "4", suited=True): 1.0},
            "IP": {StartingHand("5", "3", suited=True): 1.0},
        },
    )
    monkeypatch.setattr(api_config, "MULTIWAY_FLOP_MAX_RAISES", 1)
    monkeypatch.setattr(api_config, "MULTIWAY_FLOP_RAISE_SIZES", ())
    # M60: one call, not a hand-maintained list. Every cache
    # registers itself (see api/main.py's _SolveCache), so a new
    # endpoint's cache can no longer be forgotten here.
    api_main._SolveCache.clear_all()
    yield
    # M60: one call, not a hand-maintained list. Every cache
    # registers itself (see api/main.py's _SolveCache), so a new
    # endpoint's cache can no longer be forgotten here.
    api_main._SolveCache.clear_all()


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


# A small iteration count keeps these tests fast; correctness of the
# solve itself is already covered by test_cfr.py / test_solver.py. This
# also cache-isolates test traffic from any "real" default-iteration
# request, since the cache key includes iterations.
FAST_ITERATIONS = 20


def test_solve_returns_200_with_well_formed_response(client):
    response = client.get(f"/solve/100?iterations={FAST_ITERATIONS}")
    assert response.status_code == 200
    body = response.json()
    assert body["stack_bb"] == 100.0
    assert body["iterations"] == FAST_ITERATIONS
    assert body["elapsed_seconds"] >= 0.0
    assert len(body["opening_range"]) == 169
    assert len(body["trained"]) == 169
    # The exact HU solver visits every hand exhaustively — nothing here
    # should ever read as untrained.
    assert all(body["trained"].values())


def test_solve_frequencies_sum_to_one_per_hand(client):
    response = client.get(f"/solve/100?iterations={FAST_ITERATIONS}")
    opening_range = response.json()["opening_range"]
    for hand, freqs in opening_range.items():
        assert sum(freqs.values()) == pytest.approx(1.0, abs=1e-6)


def test_solve_rejects_nonpositive_stack(client):
    response = client.get(f"/solve/0?iterations={FAST_ITERATIONS}")
    assert response.status_code == 422

    response = client.get(f"/solve/-5?iterations={FAST_ITERATIONS}")
    assert response.status_code == 422


def test_solve_rejects_a_stack_shorter_than_the_big_blind(client):
    # M117 raised this bound from the small blind to the big one: 0.6bb
    # used to return a confident 200 whose pots counted 67% chips nobody
    # had, because the BB posts 1bb unconditionally. Surfaced as a 422
    # rather than a 500.
    for stack in ("0.3", "0.6", "0.99"):
        response = client.get(f"/solve/{stack}?iterations={FAST_ITERATIONS}")
        assert response.status_code == 422, stack


def test_solve_rejects_nonpositive_iterations(client):
    response = client.get("/solve/100?iterations=0")
    assert response.status_code == 422


def test_solve_rejects_excessive_iterations(client):
    response = client.get("/solve/100?iterations=999999")
    assert response.status_code == 422


def test_solve_uses_default_iterations_when_omitted(client):
    response = client.get("/solve/100")
    assert response.status_code == 200
    # Not asserting on the actual default value here (that's
    # solver.DEFAULT_ITERATIONS's concern) — just that omitting the
    # query param works and returns *something* consistent/well-formed.
    body = response.json()
    assert body["iterations"] > 0
    assert len(body["opening_range"]) == 169


def test_repeated_requests_are_served_from_cache(client):
    first = client.get(f"/solve/100?iterations={FAST_ITERATIONS}").json()
    second = client.get(f"/solve/100?iterations={FAST_ITERATIONS}").json()
    # elapsed_seconds is frozen into the cached response at solve time —
    # an exact match means the second call was a cache hit, not a fresh
    # (and independently timed) solve.
    assert first["elapsed_seconds"] == second["elapsed_seconds"]
    assert first == second


def test_different_stack_depths_are_cached_separately(client):
    at_50 = client.get(f"/solve/50?iterations={FAST_ITERATIONS}").json()
    at_100 = client.get(f"/solve/100?iterations={FAST_ITERATIONS}").json()
    # Each response must reflect its own request, not a wrong cache hit
    # bleeding through from the other stack depth.
    assert at_50["stack_bb"] == 50.0
    assert at_100["stack_bb"] == 100.0


def test_heads_up_response_reports_position_and_positions(client):
    body = client.get(f"/solve/100?iterations={FAST_ITERATIONS}").json()
    assert body["position"] == "BTN"
    assert body["positions"] == ["BTN", "BB"]


# ---------------------------------------------------------------------------
# Multiway (players=3/6/9): M8 added 3-max, M9 added 6-max and 9-max —
# see api/main.py's module docstring for why these use a small curated
# hand subset rather than the full 169, and why iteration budgets shrink
# as player count grows.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "players,expected_positions",
    [
        (3, ["BTN", "SB", "BB"]),
        (6, ["UTG", "MP", "CO", "BTN", "SB", "BB"]),
        (9, ["UTG", "UTG1", "MP1", "MP2", "MP3", "CO", "BTN", "SB", "BB"]),
    ],
)
def test_multiway_solve_returns_200_with_well_formed_response(client, players, expected_positions):
    response = client.get(f"/solve/100?players={players}")
    assert response.status_code == 200
    body = response.json()
    assert body["stack_bb"] == 100.0
    assert body["position"] == expected_positions[0]
    assert body["positions"] == expected_positions
    assert len(body["opening_range"]) == len(FAST_MULTIWAY_HANDS)


@pytest.mark.parametrize("players", [3, 6, 9])
def test_multiway_solve_frequencies_sum_to_one_per_hand(client, players):
    body = client.get(f"/solve/100?players={players}").json()
    for freqs in body["opening_range"].values():
        assert sum(freqs.values()) == pytest.approx(1.0, abs=1e-6)


def test_multiway_solve_trained_covers_the_same_hands_as_opening_range(client):
    body = client.get("/solve/100?players=9").json()
    assert set(body["trained"].keys()) == set(body["opening_range"].keys())


def test_multiway_solve_bb_has_genuinely_untrained_hands(client):
    # M28, docs/full-table-diagnostic-2026-08.md's SS3.3, verified end to
    # end through the real HTTP layer this time: at 9-max's real
    # (test-fixture-shrunk) iteration budget, a deep position genuinely
    # has hands MCCFR never touched, and the live response now says so.
    body = client.get("/solve/100?players=9&position=BB").json()
    assert not all(body["trained"].values())


def test_multiway_solve_defaults_to_first_to_act_position(client):
    default_body = client.get("/solve/100?players=3").json()
    btn_body = client.get("/solve/100?players=3&position=BTN").json()
    assert default_body["opening_range"] == btn_body["opening_range"]


def test_multiway_solve_position_selects_a_different_strategy(client):
    btn_body = client.get("/solve/100?players=3&position=BTN").json()
    bb_body = client.get("/solve/100?players=3&position=BB").json()
    assert bb_body["position"] == "BB"
    assert btn_body["opening_range"] != bb_body["opening_range"]


def test_multiway_solve_rejects_unknown_position(client):
    response = client.get("/solve/100?players=3&position=NOTAPOSITION")
    assert response.status_code == 422


def test_multiway_solve_is_cached_across_positions(client):
    # Selecting a different position must never trigger a re-solve —
    # both requests should be served from the same cached StrategyResult
    # (see _get_or_solve_multiway).
    first = client.get("/solve/100?players=3&position=BTN").json()
    second = client.get("/solve/100?players=3&position=SB").json()
    assert first["elapsed_seconds"] == second["elapsed_seconds"]
    assert first["iterations"] == second["iterations"]


def test_multiway_solve_is_cached_separately_per_table_size(client):
    # 3-max and 6-max at the same stack depth must be independent solves
    # (different trees, different position lists) — not accidentally
    # sharing a cache entry keyed only on stack depth.
    three_max = client.get("/solve/100?players=3").json()
    six_max = client.get("/solve/100?players=6").json()
    assert three_max["positions"] != six_max["positions"]


def test_solve_rejects_unsupported_player_count(client):
    response = client.get("/solve/100?players=4")
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# M10: GET /equity — combo-level, board-aware equity between two hands
# (see api/main.py's module docstring and poker_solver/board_equity.py).
# ---------------------------------------------------------------------------


def test_equity_returns_200_with_well_formed_response(client):
    response = client.get("/equity?hand_a=AhAd&hand_b=3h4h&board=2c7d9h")
    assert response.status_code == 200
    body = response.json()
    assert body["hand_a"] == "AhAd"
    assert body["hand_b"] == "4h3h"  # HandCombo normalizes to higher card first
    assert body["board"] == "2c7d9h"
    assert body["equity_a"] + body["equity_b"] == pytest.approx(1.0)


def test_equity_complete_board_matches_known_outcome(client):
    # A river board makes this exact (no Monte Carlo noise) — pocket
    # aces pairs a dry, unpaired board for one pair; two unconnected
    # undercards improve nothing, so aces should win outright.
    response = client.get("/equity?hand_a=AhAd&hand_b=3h4h&board=2c7d9hJcKs")
    body = response.json()
    assert body["equity_a"] == pytest.approx(1.0)
    assert body["equity_b"] == pytest.approx(0.0)


def test_equity_defaults_board_to_empty(client):
    response = client.get("/equity?hand_a=AhAd&hand_b=KhKd")
    assert response.status_code == 200
    assert response.json()["board"] == ""


def test_equity_rejects_malformed_hand(client):
    response = client.get("/equity?hand_a=Ah&hand_b=KhKd")
    assert response.status_code == 422


def test_equity_rejects_hands_sharing_a_card(client):
    response = client.get("/equity?hand_a=AhAd&hand_b=AhKd")
    assert response.status_code == 422


def test_equity_rejects_hand_blocked_by_the_board(client):
    response = client.get("/equity?hand_a=AhAd&hand_b=KhKd&board=Ah7d9h")
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# M11: GET /solve_flop — a real heads-up (OOP/IP) flop betting round over
# DEMO_FLOP_HERO_CLASSES/DEMO_FLOP_VILLAIN_CLASSES's board-legal combo
# expansion (see api/main.py's module docstring).
# ---------------------------------------------------------------------------

# Small iteration count keeps these tests fast — convergence itself is
# already covered by test_solver.py's solve_flop fixture/tests; these
# only need the HTTP plumbing to work.
FAST_FLOP_ITERATIONS = 20


def test_solve_flop_returns_200_with_well_formed_response(client):
    response = client.get(f"/solve_flop?board=Jh7d2c&pot=10&stack_bb=40&iterations={FAST_FLOP_ITERATIONS}")
    assert response.status_code == 200
    body = response.json()
    assert body["board"] == "Jh7d2c"
    assert body["pot"] == 10.0
    assert body["stack_bb"] == 40.0
    assert body["iterations"] == FAST_FLOP_ITERATIONS
    assert body["elapsed_seconds"] >= 0.0
    assert body["position"] == "OOP"
    assert set(body["trained"].keys()) == set(body["strategy"].keys())
    assert body["positions"] == ["OOP", "IP"]
    assert len(body["strategy"]) > 0


def test_solve_flop_frequencies_sum_to_one_per_combo(client):
    response = client.get(f"/solve_flop?board=Jh7d2c&pot=10&stack_bb=40&iterations={FAST_FLOP_ITERATIONS}")
    for freqs in response.json()["strategy"].values():
        assert sum(freqs.values()) == pytest.approx(1.0, abs=1e-6)


def test_solve_flop_rejects_a_board_that_isnt_exactly_three_cards(client):
    too_few = client.get(f"/solve_flop?board=Jh7d&pot=10&stack_bb=40&iterations={FAST_FLOP_ITERATIONS}")
    assert too_few.status_code == 422

    too_many = client.get(f"/solve_flop?board=Jh7d2c9h&pot=10&stack_bb=40&iterations={FAST_FLOP_ITERATIONS}")
    assert too_many.status_code == 422


def test_solve_flop_rejects_nonpositive_pot_or_stack(client):
    bad_pot = client.get(f"/solve_flop?board=Jh7d2c&pot=0&stack_bb=40&iterations={FAST_FLOP_ITERATIONS}")
    assert bad_pot.status_code == 422

    bad_stack = client.get(f"/solve_flop?board=Jh7d2c&pot=10&stack_bb=0&iterations={FAST_FLOP_ITERATIONS}")
    assert bad_stack.status_code == 422


def test_solve_flop_rejects_unknown_position(client):
    response = client.get(
        f"/solve_flop?board=Jh7d2c&pot=10&stack_bb=40&iterations={FAST_FLOP_ITERATIONS}&position=NOTAPOSITION"
    )
    assert response.status_code == 422


def test_solve_flop_defaults_to_oop_the_first_to_act_position(client):
    default_body = client.get(f"/solve_flop?board=Jh7d2c&pot=10&stack_bb=40&iterations={FAST_FLOP_ITERATIONS}").json()
    oop_body = client.get(
        f"/solve_flop?board=Jh7d2c&pot=10&stack_bb=40&iterations={FAST_FLOP_ITERATIONS}&position=OOP"
    ).json()
    assert default_body["strategy"] == oop_body["strategy"]


def test_solve_flop_position_selects_a_different_strategy(client):
    oop_body = client.get(
        f"/solve_flop?board=Jh7d2c&pot=10&stack_bb=40&iterations={FAST_FLOP_ITERATIONS}&position=OOP"
    ).json()
    ip_body = client.get(
        f"/solve_flop?board=Jh7d2c&pot=10&stack_bb=40&iterations={FAST_FLOP_ITERATIONS}&position=IP"
    ).json()
    assert ip_body["position"] == "IP"
    assert oop_body["strategy"] != ip_body["strategy"]


def test_solve_flop_is_cached_across_positions(client):
    first = client.get(
        f"/solve_flop?board=Jh7d2c&pot=10&stack_bb=40&iterations={FAST_FLOP_ITERATIONS}&position=OOP"
    ).json()
    second = client.get(
        f"/solve_flop?board=Jh7d2c&pot=10&stack_bb=40&iterations={FAST_FLOP_ITERATIONS}&position=IP"
    ).json()
    assert first["elapsed_seconds"] == second["elapsed_seconds"]


def test_solve_flop_different_boards_are_cached_separately(client):
    dry = client.get(f"/solve_flop?board=Jh7d2c&pot=10&stack_bb=40&iterations={FAST_FLOP_ITERATIONS}").json()
    wet = client.get(f"/solve_flop?board=9h8h7h&pot=10&stack_bb=40&iterations={FAST_FLOP_ITERATIONS}").json()
    assert dry["board"] != wet["board"]
    assert dry["strategy"] != wet["strategy"]


def test_solve_flop_rejects_a_board_that_blocks_the_entire_demo_range(client):
    # Three of a kind on the board (needs all 3 aces from the deck)
    # leaves only one ace behind — not enough left to form AA, one of
    # DEMO_FLOP_HERO_CLASSES's classes, but the *other* hero/villain
    # classes stay legal, so this isn't actually the "every combo
    # blocked" edge case _get_or_solve_flop guards — it's here to prove
    # a partially-blocked board is still handled fine (200, not a
    # crash), the more realistic case.
    response = client.get(f"/solve_flop?board=AhAdAc&pot=10&stack_bb=40&iterations={FAST_FLOP_ITERATIONS}")
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# M14: GET /solve_flop_turn and GET /solve_flop_to_river — same board/pot/
# stack-in, hero's-strategy-out shape as /solve_flop, backed by
# solve_flop_turn (M12)/solve_flop_to_river (M13) instead — see api/main.py's
# module docstring for the real cost numbers behind DEMO_CHAINED_FLOP_HERO_/
# VILLAIN_CLASSES and the two iteration caps.
#
# Unlike /solve_flop's tests, these are NOT free to spread one assertion
# per test function — even at the fixture's shrunk-to-the-floor combo pool
# (see the autouse fixture above), one real solve_flop_to_river solve still
# costs real seconds. Assertions are deliberately consolidated onto as few
# actual (uncached) solves as possible per endpoint; separate test
# functions are reserved for checks that fail *before* any solve happens
# (malformed board, nonpositive pot/stack, an iterations value above the
# cap) — those stay cheap regardless.
# ---------------------------------------------------------------------------

FAST_FLOP_TURN_ITERATIONS = 20
# solve_flop_to_river's cost is dominated by chance-node construction, not
# iteration count, at this fixture's scale (see api/main.py's module
# docstring) — 1 is the cheapest legal value (Query requires gt=0), and
# measured no slower than 5 iterations here, so there's no reason to pay
# for more in a plumbing-only test.
FAST_FLOP_TO_RIVER_ITERATIONS = 1

_CHAINED_FLOP_BOARD = "Jh7d2c"  # disjoint from the fixture's 9,8,6,4-rank hero/villain classes


def test_solve_flop_turn_returns_200_well_formed_and_cached_across_positions(client):
    url = f"/solve_flop_turn?board={_CHAINED_FLOP_BOARD}&pot=10&stack_bb=40&iterations={FAST_FLOP_TURN_ITERATIONS}"
    first = client.get(url)
    assert first.status_code == 200
    body = first.json()
    assert body["board"] == _CHAINED_FLOP_BOARD
    assert body["pot"] == 10.0
    assert body["stack_bb"] == 40.0
    assert body["iterations"] == FAST_FLOP_TURN_ITERATIONS
    assert body["elapsed_seconds"] >= 0.0
    assert body["position"] == "OOP"
    assert body["positions"] == ["OOP", "IP"]
    assert len(body["strategy"]) > 0
    for freqs in body["strategy"].values():
        assert sum(freqs.values()) == pytest.approx(1.0, abs=1e-6)

    # Selecting a different position must be served from the same cached
    # StrategyResult, not trigger a second (real, slow) solve.
    ip_body = client.get(f"{url}&position=IP").json()
    assert ip_body["elapsed_seconds"] == body["elapsed_seconds"]
    assert ip_body["position"] == "IP"

    # An unknown position is rejected — still a cache hit for the
    # underlying solve (position isn't part of the cache key), so this
    # doesn't pay for a third real solve either.
    bad_position = client.get(f"{url}&position=NOTAPOSITION")
    assert bad_position.status_code == 422


def test_solve_flop_turn_rejects_a_board_that_isnt_exactly_three_cards(client):
    response = client.get(f"/solve_flop_turn?board=Jh7d&pot=10&stack_bb=40&iterations={FAST_FLOP_TURN_ITERATIONS}")
    assert response.status_code == 422


def test_solve_flop_turn_rejects_nonpositive_pot_or_stack(client):
    bad_pot = client.get(f"/solve_flop_turn?board={_CHAINED_FLOP_BOARD}&pot=0&stack_bb=40")
    assert bad_pot.status_code == 422
    bad_stack = client.get(f"/solve_flop_turn?board={_CHAINED_FLOP_BOARD}&pot=10&stack_bb=0")
    assert bad_stack.status_code == 422


def test_solve_flop_turn_rejects_iterations_above_the_cap(client):
    response = client.get(
        f"/solve_flop_turn?board={_CHAINED_FLOP_BOARD}&pot=10&stack_bb=40"
        f"&iterations={api_config.MAX_FLOP_TURN_ITERATIONS + 1}"
    )
    assert response.status_code == 422


def test_solve_flop_to_river_returns_200_well_formed_and_cached_across_positions(client):
    url = f"/solve_flop_to_river?board={_CHAINED_FLOP_BOARD}&pot=10&stack_bb=40&iterations={FAST_FLOP_TO_RIVER_ITERATIONS}"
    first = client.get(url)
    assert first.status_code == 200
    body = first.json()
    assert body["board"] == _CHAINED_FLOP_BOARD
    assert body["pot"] == 10.0
    assert body["stack_bb"] == 40.0
    assert body["iterations"] == FAST_FLOP_TO_RIVER_ITERATIONS
    assert body["elapsed_seconds"] >= 0.0
    assert body["position"] == "OOP"
    assert body["positions"] == ["OOP", "IP"]
    assert len(body["strategy"]) > 0
    for freqs in body["strategy"].values():
        assert sum(freqs.values()) == pytest.approx(1.0, abs=1e-6)

    ip_body = client.get(f"{url}&position=IP").json()
    assert ip_body["elapsed_seconds"] == body["elapsed_seconds"]
    assert ip_body["position"] == "IP"

    bad_position = client.get(f"{url}&position=NOTAPOSITION")
    assert bad_position.status_code == 422


def test_solve_flop_to_river_rejects_a_board_that_isnt_exactly_three_cards(client):
    response = client.get(
        f"/solve_flop_to_river?board=Jh7d&pot=10&stack_bb=40&iterations={FAST_FLOP_TO_RIVER_ITERATIONS}"
    )
    assert response.status_code == 422


def test_solve_flop_to_river_rejects_nonpositive_pot_or_stack(client):
    bad_pot = client.get(f"/solve_flop_to_river?board={_CHAINED_FLOP_BOARD}&pot=0&stack_bb=40")
    assert bad_pot.status_code == 422
    bad_stack = client.get(f"/solve_flop_to_river?board={_CHAINED_FLOP_BOARD}&pot=10&stack_bb=0")
    assert bad_stack.status_code == 422


def test_solve_flop_to_river_rejects_iterations_above_the_cap(client):
    # MAX_FLOP_TO_RIVER_ITERATIONS is set equal to its own default (see
    # api/main.py's module docstring) — asserting the cap directly, not
    # a magic number, so this stays correct if that default ever changes.
    response = client.get(
        f"/solve_flop_to_river?board={_CHAINED_FLOP_BOARD}&pot=10&stack_bb=40"
        f"&iterations={api_config.MAX_FLOP_TO_RIVER_ITERATIONS + 1}"
    )
    assert response.status_code == 422


def test_solve_flop_turn_and_solve_flop_to_river_are_cached_independently(client):
    # Direct regression test for _get_or_solve_flop_turn/_get_or_solve_
    # flop_to_river using *separate* cache dicts (see api/main.py's
    # module-level comment on _flop_turn_cache/_flop_to_river_cache): an
    # identical (board, pot, stack_bb, iterations) tuple must never let
    # one endpoint's cached StrategyResult bleed into the other's cache.
    #
    # Not asserted via the two responses' *strategy values* differing —
    # this fixture monkeypatches FLOP_TURN_MAX_RAISES down to match
    # FLOP_TO_RIVER_MAX_RAISES (both 1, for cost reasons above), so the
    # two solved trees are similarly shaped and could coincidentally
    # produce very similar numbers, making a value-equality check
    # fragile. Instead this checks the one structural difference that's
    # true by construction regardless of tree shape: solve_flop_turn's
    # chance_data never populates a branch's own chance_fn (chain_to_
    # river=False), while solve_flop_to_river's does wherever real stack
    # remains (chain_to_river=True) — see chance.py's build_chance_node.
    shared_iterations = 1
    client.get(f"/solve_flop_turn?board={_CHAINED_FLOP_BOARD}&pot=10&stack_bb=40&iterations={shared_iterations}")
    client.get(
        f"/solve_flop_to_river?board={_CHAINED_FLOP_BOARD}&pot=10&stack_bb=40&iterations={shared_iterations}"
    )

    # Look the results up by iterating the caches rather than reconstructing
    # `key` by hand (Card equality/hashing, not a plain tuple of strings,
    # is the real cache key — see _get_or_solve_flop_turn) — avoids this
    # test silently passing on a key that never actually matches.
    assert len(api_main._flop_turn_cache) == 1
    assert len(api_main._flop_to_river_cache) == 1
    turn_result = next(iter(api_main._flop_turn_cache.entries.values()))
    river_result = next(iter(api_main._flop_to_river_cache.entries.values()))

    def _any_branch_has_a_populated_chance_fn(result):
        return any(
            branch.chance_fn is not None
            for chance_node in result.chance_data.values()
            for branch in chance_node.branches.values()
        )

    assert not _any_branch_has_a_populated_chance_fn(turn_result)
    assert _any_branch_has_a_populated_chance_fn(river_result)


# ---------------------------------------------------------------------------
# M37: GET /solve_flop_multiway and GET /solve_flop_turn_multiway — the
# first live endpoints for true multiway (3+ live position) postflop
# solving (M30-M36). Same board/pot/stack-in, one-position's-strategy-out
# shape as every other /solve_flop* endpoint, but `position` now accepts
# OOP, MID, or IP, and `positions` in the response carries all 3 — see
# api/main.py's module docstring for the real cost numbers behind
# DEMO_MULTIWAY_FLOP_CLASSES and the two iteration caps.
# ---------------------------------------------------------------------------

_MULTIWAY_FLOP_BOARD = "Jh7d2c"  # disjoint from the fixture's 9,8,6,4,5,3-rank position classes

FAST_FLOP_MULTIWAY_ITERATIONS = 20
FAST_FLOP_TURN_MULTIWAY_ITERATIONS = 5


def test_solve_flop_multiway_returns_200_well_formed_and_cached_across_positions(client):
    url = (
        f"/solve_flop_multiway?board={_MULTIWAY_FLOP_BOARD}&pot=10&stack_bb=40"
        f"&iterations={FAST_FLOP_MULTIWAY_ITERATIONS}"
    )
    first = client.get(url)
    assert first.status_code == 200
    body = first.json()
    assert body["board"] == _MULTIWAY_FLOP_BOARD
    assert body["pot"] == 10.0
    assert body["stack_bb"] == 40.0
    assert body["iterations"] == FAST_FLOP_MULTIWAY_ITERATIONS
    assert body["elapsed_seconds"] >= 0.0
    assert body["position"] == "OOP"
    assert body["positions"] == ["OOP", "MID", "IP"]
    assert len(body["strategy"]) > 0
    for freqs in body["strategy"].values():
        assert sum(freqs.values()) == pytest.approx(1.0, abs=1e-6)

    # A different live position must be served from the same cached
    # StrategyResult, not trigger a second (real) solve.
    mid_body = client.get(f"{url}&position=MID").json()
    assert mid_body["elapsed_seconds"] == body["elapsed_seconds"]
    assert mid_body["position"] == "MID"

    bad_position = client.get(f"{url}&position=NOTAPOSITION")
    assert bad_position.status_code == 422


def test_solve_flop_multiway_rejects_a_board_that_isnt_exactly_three_cards(client):
    response = client.get(
        f"/solve_flop_multiway?board=Jh7d&pot=10&stack_bb=40&iterations={FAST_FLOP_MULTIWAY_ITERATIONS}"
    )
    assert response.status_code == 422


def test_solve_flop_multiway_rejects_nonpositive_pot_or_stack(client):
    bad_pot = client.get(f"/solve_flop_multiway?board={_MULTIWAY_FLOP_BOARD}&pot=0&stack_bb=40")
    assert bad_pot.status_code == 422
    bad_stack = client.get(f"/solve_flop_multiway?board={_MULTIWAY_FLOP_BOARD}&pot=10&stack_bb=0")
    assert bad_stack.status_code == 422


def test_solve_flop_multiway_rejects_iterations_above_the_cap(client):
    response = client.get(
        f"/solve_flop_multiway?board={_MULTIWAY_FLOP_BOARD}&pot=10&stack_bb=40"
        f"&iterations={api_config.MAX_FLOP_MULTIWAY_ITERATIONS + 1}"
    )
    assert response.status_code == 422


def test_solve_flop_turn_multiway_returns_200_well_formed_and_cached_across_positions(client):
    url = (
        f"/solve_flop_turn_multiway?board={_MULTIWAY_FLOP_BOARD}&pot=10&stack_bb=40"
        f"&iterations={FAST_FLOP_TURN_MULTIWAY_ITERATIONS}"
    )
    first = client.get(url)
    assert first.status_code == 200
    body = first.json()
    assert body["board"] == _MULTIWAY_FLOP_BOARD
    assert body["positions"] == ["OOP", "MID", "IP"]
    assert len(body["strategy"]) > 0
    for freqs in body["strategy"].values():
        assert sum(freqs.values()) == pytest.approx(1.0, abs=1e-6)

    ip_body = client.get(f"{url}&position=IP").json()
    assert ip_body["elapsed_seconds"] == body["elapsed_seconds"]
    assert ip_body["position"] == "IP"


def test_solve_flop_turn_multiway_rejects_a_board_that_isnt_exactly_three_cards(client):
    response = client.get(
        f"/solve_flop_turn_multiway?board=Jh7d&pot=10&stack_bb=40&iterations={FAST_FLOP_TURN_MULTIWAY_ITERATIONS}"
    )
    assert response.status_code == 422


def test_solve_flop_turn_multiway_rejects_nonpositive_pot_or_stack(client):
    bad_pot = client.get(f"/solve_flop_turn_multiway?board={_MULTIWAY_FLOP_BOARD}&pot=0&stack_bb=40")
    assert bad_pot.status_code == 422
    bad_stack = client.get(f"/solve_flop_turn_multiway?board={_MULTIWAY_FLOP_BOARD}&pot=10&stack_bb=0")
    assert bad_stack.status_code == 422


def test_solve_flop_turn_multiway_rejects_iterations_above_the_cap(client):
    response = client.get(
        f"/solve_flop_turn_multiway?board={_MULTIWAY_FLOP_BOARD}&pot=10&stack_bb=40"
        f"&iterations={api_config.MAX_FLOP_TURN_MULTIWAY_ITERATIONS + 1}"
    )
    assert response.status_code == 422


def test_solve_flop_multiway_and_solve_flop_turn_multiway_are_cached_independently(client):
    # Direct regression test for _get_or_solve_flop_multiway/_get_or_
    # solve_flop_turn_multiway's own separate-dict design (see the
    # module-level comment by _flop_multiway_cache) — an identical
    # (board, pot, stack_bb, iterations) key must not collide between
    # the two endpoints, which use different max_raises/chance-dispatch
    # behavior.
    shared_iterations = min(FAST_FLOP_MULTIWAY_ITERATIONS, FAST_FLOP_TURN_MULTIWAY_ITERATIONS)
    client.get(f"/solve_flop_multiway?board={_MULTIWAY_FLOP_BOARD}&pot=10&stack_bb=40&iterations={shared_iterations}")
    client.get(
        f"/solve_flop_turn_multiway?board={_MULTIWAY_FLOP_BOARD}&pot=10&stack_bb=40&iterations={shared_iterations}"
    )
    assert len(api_main._flop_multiway_cache) == 1
    assert len(api_main._flop_turn_multiway_cache) == 1
    flop_result = next(iter(api_main._flop_multiway_cache.entries.values()))
    turn_result = next(iter(api_main._flop_turn_multiway_cache.entries.values()))
    assert flop_result.chance_data == {}  # solve_flop_multiway never dispatches chance
    assert len(turn_result.chance_data) > 0  # solve_flop_turn_multiway does


# ---------------------------------------------------------------------------
# M40: GET /solve_flop_to_river_multiway — the same 3-max multiway flop as
# /solve_flop_multiway/`/solve_flop_turn_multiway`, chained all the way to a
# real multiway river decision (wiring up M39's solve_flop_to_river_
# multiway). Same shape/pool/board as the M37 section above.
# ---------------------------------------------------------------------------

FAST_FLOP_TO_RIVER_MULTIWAY_ITERATIONS = 5


def test_solve_flop_to_river_multiway_returns_200_well_formed_and_cached_across_positions(client):
    url = (
        f"/solve_flop_to_river_multiway?board={_MULTIWAY_FLOP_BOARD}&pot=10&stack_bb=40"
        f"&iterations={FAST_FLOP_TO_RIVER_MULTIWAY_ITERATIONS}"
    )
    first = client.get(url)
    assert first.status_code == 200
    body = first.json()
    assert body["board"] == _MULTIWAY_FLOP_BOARD
    assert body["pot"] == 10.0
    assert body["stack_bb"] == 40.0
    assert body["iterations"] == FAST_FLOP_TO_RIVER_MULTIWAY_ITERATIONS
    assert body["elapsed_seconds"] >= 0.0
    assert body["position"] == "OOP"
    assert body["positions"] == ["OOP", "MID", "IP"]
    assert len(body["strategy"]) > 0
    for freqs in body["strategy"].values():
        assert sum(freqs.values()) == pytest.approx(1.0, abs=1e-6)

    # A different live position must be served from the same cached
    # StrategyResult, not trigger a second (real) solve.
    ip_body = client.get(f"{url}&position=IP").json()
    assert ip_body["elapsed_seconds"] == body["elapsed_seconds"]
    assert ip_body["position"] == "IP"

    bad_position = client.get(f"{url}&position=NOTAPOSITION")
    assert bad_position.status_code == 422


def test_solve_flop_to_river_multiway_rejects_a_board_that_isnt_exactly_three_cards(client):
    response = client.get(
        f"/solve_flop_to_river_multiway?board=Jh7d&pot=10&stack_bb=40&iterations={FAST_FLOP_TO_RIVER_MULTIWAY_ITERATIONS}"
    )
    assert response.status_code == 422


def test_solve_flop_to_river_multiway_rejects_nonpositive_pot_or_stack(client):
    bad_pot = client.get(f"/solve_flop_to_river_multiway?board={_MULTIWAY_FLOP_BOARD}&pot=0&stack_bb=40")
    assert bad_pot.status_code == 422
    bad_stack = client.get(f"/solve_flop_to_river_multiway?board={_MULTIWAY_FLOP_BOARD}&pot=10&stack_bb=0")
    assert bad_stack.status_code == 422


def test_solve_flop_to_river_multiway_rejects_iterations_above_the_cap(client):
    response = client.get(
        f"/solve_flop_to_river_multiway?board={_MULTIWAY_FLOP_BOARD}&pot=10&stack_bb=40"
        f"&iterations={api_config.MAX_FLOP_TO_RIVER_MULTIWAY_ITERATIONS + 1}"
    )
    assert response.status_code == 422


def test_solve_flop_to_river_multiway_is_cached_independently_from_the_other_two(client):
    # Same collision-safety regression as test_solve_flop_multiway_and_
    # solve_flop_turn_multiway_are_cached_independently, extended to the
    # third endpoint's own dict.
    shared_iterations = min(FAST_FLOP_TURN_MULTIWAY_ITERATIONS, FAST_FLOP_TO_RIVER_MULTIWAY_ITERATIONS)
    client.get(
        f"/solve_flop_turn_multiway?board={_MULTIWAY_FLOP_BOARD}&pot=10&stack_bb=40&iterations={shared_iterations}"
    )
    client.get(
        f"/solve_flop_to_river_multiway?board={_MULTIWAY_FLOP_BOARD}&pot=10&stack_bb=40&iterations={shared_iterations}"
    )
    assert len(api_main._flop_turn_multiway_cache) == 1
    assert len(api_main._flop_to_river_multiway_cache) == 1
    river_result = next(iter(api_main._flop_to_river_multiway_cache.entries.values()))
    assert len(river_result.chance_data) > 0
    assert any(len(branch.board) == 5 for branch in river_result.chance_data.values())


# ---------------------------------------------------------------------------
# M22 deliverable: /solve_flop_cached — canonicalize-then-lookup, falling
# back to an on-demand solve on a miss (poker_solver.library.query_
# strategy, M21). Unlike every other /solve_flop* endpoint, only `board`
# and `stack_bb` are query params — see api/main.py's own module-docstring
# paragraph for why everything else is a fixed server constant.
# ---------------------------------------------------------------------------


def test_solve_flop_cached_returns_200_with_well_formed_response(client):
    response = client.get("/solve_flop_cached?board=Jh7d2c&stack_bb=40")
    assert response.status_code == 200
    body = response.json()
    assert body["board"] == "Jh7d2c"
    assert body["stack_bb"] == 40.0
    assert body["pot"] == api_config.FLOP_QUERY_POT
    assert body["hit"] is False  # a never-before-seen board is always a miss
    assert body["elapsed_seconds"] >= 0.0
    assert body["position"] == "OOP"
    assert body["positions"] == ["OOP", "IP"]
    assert len(body["canonical_board"]) == 6  # 3 cards, 2 chars each
    assert body["canonical_stack_bb"] > 0
    assert len(body["strategy"]) > 0


def test_solve_flop_cached_frequencies_sum_to_one_per_combo(client):
    response = client.get("/solve_flop_cached?board=Jh7d2c&stack_bb=40")
    for freqs in response.json()["strategy"].values():
        assert sum(freqs.values()) == pytest.approx(1.0, abs=1e-6)


def test_solve_flop_cached_rejects_a_board_that_isnt_exactly_three_cards(client):
    too_few = client.get("/solve_flop_cached?board=Jh7d&stack_bb=40")
    assert too_few.status_code == 422

    too_many = client.get("/solve_flop_cached?board=Jh7d2c9h&stack_bb=40")
    assert too_many.status_code == 422


def test_solve_flop_cached_rejects_nonpositive_stack(client):
    response = client.get("/solve_flop_cached?board=Jh7d2c&stack_bb=0")
    assert response.status_code == 422


def test_solve_flop_cached_rejects_a_board_that_blocks_the_entire_demo_range(client):
    # Unlike /solve_flop's own same-named test (which asserts 200 — its
    # 3-class hero pool only gets *partially* blocked by AhAdAc, see that
    # test's own comment), this fixture shrinks FLOP_QUERY_HERO_CLASSES
    # to a single class (AA), which AhAdAc blocks completely — a genuine
    # 422, not a copy-paste mistake.
    response = client.get("/solve_flop_cached?board=AhAdAc&stack_bb=40")
    assert response.status_code == 422


def test_solve_flop_cached_miss_then_hit_on_the_same_board(client):
    first = client.get("/solve_flop_cached?board=Jh7d2c&stack_bb=40").json()
    assert first["hit"] is False

    second = client.get("/solve_flop_cached?board=Jh7d2c&stack_bb=40").json()
    assert second["hit"] is True
    assert second["strategy"] == first["strategy"]
    assert second["canonical_board"] == first["canonical_board"]
    # Unlike /solve_flop*'s own cached responses (elapsed_seconds frozen
    # at original-solve time, see test_solve_flop_is_cached_across_
    # positions' own exact-equality assertion), query_strategy measures
    # elapsed_seconds fresh on every call, hit or miss — the concrete
    # "hit is fast" proof this endpoint exists to show, not a bug.
    assert second["elapsed_seconds"] < first["elapsed_seconds"]


def test_solve_flop_cached_hits_a_board_isomorphic_to_a_previous_miss(client):
    # The actual point of the whole real-time-speed roadmap. 2h7s9d and
    # 2c7d9h are the same isomorphic pair tests/test_library.py's own
    # engine-level tests already use — reused here for consistency
    # rather than inventing and hoping a new pair is right.
    first = client.get("/solve_flop_cached?board=2h7s9d&stack_bb=40").json()
    assert first["hit"] is False

    second = client.get("/solve_flop_cached?board=2c7d9h&stack_bb=40").json()
    assert second["hit"] is True
    assert second["board"] == "2c7d9h"  # echoes the real query board, not the first one
    assert second["canonical_board"] == first["canonical_board"]
    assert second["canonical_stack_bb"] == first["canonical_stack_bb"]
    # Deliberately NOT asserting strategy equality here — the combo keys
    # are translated to each board's own real suits and legitimately
    # differ. That finer-grained bit-exactness already belongs to
    # tests/test_library.py's own engine-level cross-check; this test
    # only needs the HTTP plumbing (a real cache hit occurred) to work.


def test_solve_flop_cached_different_boards_are_not_confused(client):
    first = client.get("/solve_flop_cached?board=2h7s9d&stack_bb=40").json()
    second = client.get("/solve_flop_cached?board=3c8dTh&stack_bb=40").json()
    assert first["hit"] is False
    assert second["hit"] is False
    assert first["canonical_board"] != second["canonical_board"]


# ---------------------------------------------------------------------------
# M24 deliverable: POST /solve_flop_from_path — a real preflop action
# sequence (not a fixed demo range) in, flop advice out, chaining
# derive_ranges_from_path (M16) into query_strategy_from_path (M23).
# ---------------------------------------------------------------------------

_PATH_ITERATIONS = 200  # a real per-request preflop solve, not fixture-capped — kept small for test speed


def _path_body(action_path, stack_bb=100.0, board="2h6d9c", iterations=_PATH_ITERATIONS, players=2):
    return {
        "stack_bb": stack_bb,
        "action_path": action_path,
        "board": board,
        "iterations": iterations,
        "players": players,
    }


def test_solve_flop_from_path_returns_200_for_a_real_open_call_line(client):
    response = client.post("/solve_flop_from_path", json=_path_body(["raise", "call_or_check"]))
    assert response.status_code == 200
    body = response.json()
    assert body["hit"] is False
    assert body["board"] == "2h6d9c"
    assert len(body["canonical_board"]) == 6
    assert body["position"] in ("BTN", "BB")
    assert set(body["positions"]) == {"BTN", "BB"}
    assert len(body["strategy"]) > 0
    for freqs in body["strategy"].values():
        assert sum(freqs.values()) == pytest.approx(1.0, abs=1e-6)


def test_solve_flop_from_path_returns_200_for_a_real_open_3bet_call_line(client):
    # A 3-step path (BTN acts twice) — pot must exceed the 2-step
    # open-call line's, a real, cheap, meaningful check.
    open_call = client.post("/solve_flop_from_path", json=_path_body(["raise", "call_or_check"])).json()
    open_3bet_call = client.post(
        "/solve_flop_from_path", json=_path_body(["raise", "raise", "call_or_check"])
    ).json()
    assert open_3bet_call["pot"] > open_call["pot"]


def test_solve_flop_from_path_rejects_an_unknown_action_kind(client):
    response = client.post("/solve_flop_from_path", json=_path_body(["not_a_real_kind"]))
    assert response.status_code == 422


def test_solve_flop_from_path_rejects_a_kind_illegal_at_a_specific_step(client):
    # BB facing a limp has no fold option — step 1 (0-indexed) fails.
    response = client.post("/solve_flop_from_path", json=_path_body(["call_or_check", "fold"]))
    assert response.status_code == 422
    assert "step 1" in response.json()["detail"]


def test_solve_flop_from_path_rejects_a_non_terminal_path(client):
    response = client.post("/solve_flop_from_path", json=_path_body(["raise"]))
    assert response.status_code == 422


def test_solve_flop_from_path_rejects_an_empty_action_path(client):
    response = client.post("/solve_flop_from_path", json=_path_body([]))
    assert response.status_code == 422


def test_solve_flop_from_path_rejects_a_folded_out_path(client):
    response = client.post("/solve_flop_from_path", json=_path_body(["fold"]))
    assert response.status_code == 422


def test_solve_flop_from_path_rejects_a_malformed_board(client):
    response = client.post(
        "/solve_flop_from_path", json=_path_body(["raise", "call_or_check"], board="Jh7d")
    )
    assert response.status_code == 422


def test_solve_flop_from_path_reuses_the_cached_preflop_solve_across_different_paths(client):
    client.post("/solve_flop_from_path", json=_path_body(["raise", "call_or_check"]))
    client.post("/solve_flop_from_path", json=_path_body(["call_or_check", "call_or_check"]))
    assert len(api_main._preflop_raw_cache) == 1


def test_solve_flop_from_path_partitions_different_paths_into_separate_libraries(client):
    # The actual point of Finding 2's fix: two different action_paths at
    # the same stack_bb/board must never share one canonical-key
    # library, or one could silently serve the other's answer.
    open_call = client.post("/solve_flop_from_path", json=_path_body(["raise", "call_or_check"])).json()
    limp_check = client.post(
        "/solve_flop_from_path", json=_path_body(["call_or_check", "call_or_check"])
    ).json()
    assert len(api_main._path_query_libraries) == 2
    assert open_call["pot"] != limp_check["pot"]
    assert open_call["strategy"] != limp_check["strategy"]


def test_solve_flop_from_path_repeat_query_hits(client):
    body = _path_body(["raise", "call_or_check"])
    first = client.post("/solve_flop_from_path", json=body).json()
    second = client.post("/solve_flop_from_path", json=body).json()
    assert first["hit"] is False
    assert second["hit"] is True
    assert second["elapsed_seconds"] < first["elapsed_seconds"]


def test_solve_flop_from_path_hits_a_board_isomorphic_to_a_previous_miss(client):
    first = client.post("/solve_flop_from_path", json=_path_body(["raise", "call_or_check"], board="2h6d9c")).json()
    second = client.post(
        "/solve_flop_from_path", json=_path_body(["raise", "call_or_check"], board="2c6d9h")
    ).json()
    assert first["hit"] is False
    assert second["hit"] is True
    assert second["canonical_board"] == first["canonical_board"]


# ---------------------------------------------------------------------------
# M42: POST /solve_flop_multiway_from_path — the multiway analog of
# /solve_flop_from_path, for a real action path that leaves 3+ live
# positions at the flop (a case /solve_flop_from_path structurally
# can't serve — see api/main.py's module docstring).
# ---------------------------------------------------------------------------

FAST_MULTIWAY_PATH_FLOP_ITERATIONS = 20

_THREE_LIVE_PATH = ["call_or_check", "call_or_check", "call_or_check"]  # BTN limps, SB calls, BB checks


def _multiway_path_body(
    action_path,
    stack_bb=100.0,
    board="2h6d9c",
    iterations=_PATH_ITERATIONS,
    flop_iterations=FAST_MULTIWAY_PATH_FLOP_ITERATIONS,
    players=3,
):
    return {
        "stack_bb": stack_bb,
        "action_path": action_path,
        "board": board,
        "iterations": iterations,
        "flop_iterations": flop_iterations,
        "players": players,
    }


def test_solve_flop_multiway_from_path_returns_200_for_a_real_three_live_line(client):
    response = client.post("/solve_flop_multiway_from_path", json=_multiway_path_body(_THREE_LIVE_PATH))
    assert response.status_code == 200
    body = response.json()
    assert body["players"] == 3
    assert set(body["positions"]) == {"BTN", "SB", "BB"}
    assert body["position"] in body["positions"]
    assert body["flop_iterations"] == FAST_MULTIWAY_PATH_FLOP_ITERATIONS
    assert body["board"] == "2h6d9c"
    assert len(body["strategy"]) > 0
    for freqs in body["strategy"].values():
        assert sum(freqs.values()) == pytest.approx(1.0, abs=1e-6)


def test_solve_flop_multiway_from_path_rejects_a_two_survivor_path(client):
    # BTN opens, SB folds, BB calls -> only 2 live; this endpoint's own
    # job is genuinely 3+ live positions — /solve_flop_from_path already
    # serves the 2-survivor case, via the exact (not MCCFR-approximate)
    # 2-position solver.
    response = client.post(
        "/solve_flop_multiway_from_path", json=_multiway_path_body(["raise", "fold", "call_or_check"])
    )
    assert response.status_code == 422
    assert "solve_flop_from_path" in response.json()["detail"]


def test_solve_flop_multiway_from_path_rejects_a_non_terminal_path(client):
    response = client.post("/solve_flop_multiway_from_path", json=_multiway_path_body(["call_or_check"]))
    assert response.status_code == 422


def test_solve_flop_multiway_from_path_rejects_an_unknown_action_kind(client):
    response = client.post("/solve_flop_multiway_from_path", json=_multiway_path_body(["not_a_real_kind"]))
    assert response.status_code == 422


def test_solve_flop_multiway_from_path_rejects_a_malformed_board(client):
    response = client.post(
        "/solve_flop_multiway_from_path", json=_multiway_path_body(_THREE_LIVE_PATH, board="Jh7d")
    )
    assert response.status_code == 422


def test_solve_flop_multiway_from_path_rejects_flop_iterations_above_the_cap(client):
    response = client.post(
        "/solve_flop_multiway_from_path",
        json=_multiway_path_body(
            _THREE_LIVE_PATH, flop_iterations=api_config.MAX_MULTIWAY_PATH_QUERY_FLOP_ITERATIONS + 1
        ),
    )
    assert response.status_code == 422


def test_solve_flop_multiway_from_path_repeat_query_is_cached(client):
    body = _multiway_path_body(_THREE_LIVE_PATH)
    first = client.post("/solve_flop_multiway_from_path", json=body).json()
    second = client.post("/solve_flop_multiway_from_path", json=body).json()
    assert first["elapsed_seconds"] == second["elapsed_seconds"]
    assert len(api_main._flop_multiway_path_cache) == 1


def test_solve_flop_multiway_from_path_a_different_flop_iterations_gets_its_own_cache_entry(client):
    client.post(
        "/solve_flop_multiway_from_path",
        json=_multiway_path_body(_THREE_LIVE_PATH, flop_iterations=FAST_MULTIWAY_PATH_FLOP_ITERATIONS),
    )
    client.post(
        "/solve_flop_multiway_from_path",
        json=_multiway_path_body(_THREE_LIVE_PATH, flop_iterations=FAST_MULTIWAY_PATH_FLOP_ITERATIONS + 1),
    )
    assert len(api_main._flop_multiway_path_cache) == 2


# ---------------------------------------------------------------------------
# M25 deliverable: POST /preflop_walk — a board-independent, pure
# preflop-tree-state query ("what's legal from here"), the companion
# endpoint /solve_flop_from_path's own docstring always said this app
# was still missing. Reuses the same _get_or_solve_preflop_raw cache
# /solve_flop_from_path already populates; no range derivation, no
# board, no query_strategy involved.
#
# Expected values below are hand-derived from GameConfig's real
# defaults (positions=(BTN, BB), small_blind=0.5, big_blind=1.0,
# raise_sizes=(2.5, 3.0, 2.2), max_raises=4), not guessed.
# ---------------------------------------------------------------------------


def _walk_body(action_path, stack_bb=100.0, iterations=_PATH_ITERATIONS, players=2):
    return {"stack_bb": stack_bb, "action_path": action_path, "iterations": iterations, "players": players}


def _by_kind(legal_actions):
    return {option["kind"]: option for option in legal_actions}


def test_preflop_walk_root_reports_btn_to_act_with_all_four_actions(client):
    response = client.post("/preflop_walk", json=_walk_body([]))
    assert response.status_code == 200
    body = response.json()
    assert body["is_terminal"] is False
    assert body["player_to_act"] == "BTN"
    assert body["live_positions"] == ["BTN", "BB"]
    assert body["pot"] == pytest.approx(1.5)

    by_kind = _by_kind(body["legal_actions"])
    assert set(by_kind) == {"fold", "call_or_check", "raise", "all_in"}
    assert by_kind["fold"]["size"] is None
    assert by_kind["fold"]["to_call"] is None
    assert by_kind["call_or_check"]["to_call"] == pytest.approx(0.5)
    assert by_kind["call_or_check"]["size"] is None
    assert by_kind["raise"]["size"] == pytest.approx(2.5)
    assert by_kind["raise"]["to_call"] is None
    assert by_kind["all_in"]["size"] == pytest.approx(100.0)


def test_preflop_walk_after_a_raise_reports_the_real_amount_to_call(client):
    response = client.post("/preflop_walk", json=_walk_body(["raise"]))
    assert response.status_code == 200
    body = response.json()
    assert body["player_to_act"] == "BB"
    by_kind = _by_kind(body["legal_actions"])
    assert by_kind["call_or_check"]["to_call"] == pytest.approx(1.5)


def test_preflop_walk_after_a_limp_offers_a_free_check_with_no_fold(client):
    response = client.post("/preflop_walk", json=_walk_body(["call_or_check"]))
    assert response.status_code == 200
    body = response.json()
    assert body["player_to_act"] == "BB"
    by_kind = _by_kind(body["legal_actions"])
    assert "fold" not in by_kind
    assert by_kind["call_or_check"]["to_call"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# M60: the cache registry. Every solve cache registers itself on
# construction, so clearing them is one call rather than a
# hand-maintained list that each new endpoint had to remember to update
# in two places (docs/project-audit-2026-08-21.md's SS3.2).
# ---------------------------------------------------------------------------


def test_every_cache_registers_itself():
    registered = api_main._SolveCache.registered()
    # Names are unique, and the registry covers every module-level cache
    # object — checked by comparing against a live scan of the module
    # rather than a hardcoded list, which would reintroduce exactly the
    # hand-maintained inventory this milestone removed.
    names = [cache.name for cache in registered]
    assert len(names) == len(set(names)), f"duplicate cache names: {names}"

    # M158: scan where caches are DECLARED, not where they happen to be
    # re-exported. `api_main` re-exports the ones it uses, so counting
    # there missed `_canonical_warm_starts` — declared in api.caches and
    # imported only by api.solving — and reported a registry larger than
    # the module. The property being guarded is that no cache escapes the
    # registry (and therefore the bounded-size test), which is a fact
    # about the declaring module.
    from api import caches as cache_module
    from api import solving as solving_module

    module_caches = {
        id(value)
        for module in (cache_module, solving_module, api_main)
        for value in vars(module).values()
        if isinstance(value, api_main._SolveCache)
    }
    assert len(module_caches) == len(registered), (
        f"{len(registered)} caches registered but {len(module_caches)} found by "
        "scanning — a cache is registered without being reachable, or vice versa"
    )
    # `module_caches` already holds ids, so compare directly — taking
    # id() of an id was the bug this line had after the scan changed.
    assert module_caches == {id(cache) for cache in registered}


def test_clear_all_empties_every_populated_cache(client):
    client.get(f"/solve/100?iterations={_PATH_ITERATIONS}")
    client.get(f"/solve_flop?board={_CHAINED_FLOP_BOARD}&pot=10&stack_bb=40&iterations={FAST_FLOP_ITERATIONS}")
    populated = [c for c in api_main._SolveCache.registered() if len(c) > 0]
    assert populated, "expected at least one cache to have been populated"

    api_main._SolveCache.clear_all()
    assert all(len(c) == 0 for c in api_main._SolveCache.registered())


def test_each_cache_bundles_its_own_lock():
    # The pairing a dict-plus-separate-lock convention could silently
    # break; now impossible to have one without the other.
    for cache in api_main._SolveCache.registered():
        assert isinstance(cache.entries, dict)
        assert hasattr(cache.lock, "acquire")


# ---------------------------------------------------------------------------
# M58: one preflop solve cache, and `position` honored at every table
# size. GET /solve used to keep its own formatted-response cache that
# independently re-solved the identical spot every path-derived endpoint
# had already solved (docs/project-audit-2026-08-21.md's SS2.1), and its
# heads-up branch silently ignored `position`.
# ---------------------------------------------------------------------------


def test_solve_and_preflop_walk_share_one_preflop_solve(client):
    # The audit's own verified finding, now a regression test: hitting
    # GET /solve then a path-derived endpoint must leave exactly ONE
    # cached preflop solve, not one per cache.
    client.get(f"/solve/100?iterations={_PATH_ITERATIONS}")
    assert len(api_main._preflop_raw_cache) == 1
    client.post("/preflop_walk", json=_walk_body([], iterations=_PATH_ITERATIONS))
    assert len(api_main._preflop_raw_cache) == 1


def test_solve_honors_position_at_heads_up(client):
    # Previously silently ignored: heads-up returned first-to-act
    # whatever the caller asked for, while multiway honored `position`.
    btn = client.get(f"/solve/100?iterations={_PATH_ITERATIONS}").json()
    bb = client.get(f"/solve/100?iterations={_PATH_ITERATIONS}&position=BB").json()
    assert btn["position"] == "BTN"
    assert bb["position"] == "BB"
    assert bb["opening_range"] != btn["opening_range"]


def test_solve_still_honors_position_at_multiway(client):
    body = client.get("/solve/100?players=3&position=BB").json()
    assert body["position"] == "BB"


def test_solve_rejects_a_position_that_is_not_at_the_table(client):
    response = client.get(f"/solve/100?iterations={_PATH_ITERATIONS}&position=NOTAPOSITION")
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# M50: _derive_path_situation — the shared front half extracted from all
# five path-derived endpoints' own near-identical orchestrators. Tested
# directly here (not only through the five endpoints that now delegate to
# it) so its own parameterized behavior — the live-position rule, the two
# capping modes, the error text — has real coverage of its own.
# ---------------------------------------------------------------------------

_DERIVE_BOARD = tuple(parse_cards("2h6d9c"))


def _derive(**overrides):
    kwargs = {
        "action_kinds": ["raise", "call_or_check"],
        "stack_bb": 100.0,
        "board_cards": _DERIVE_BOARD,
        "iterations": _PATH_ITERATIONS,
        "players": 2,
        "multiway": False,
        "sibling_endpoint": "/sibling",
        "max_classes_per_position": 2,
    }
    kwargs.update(overrides)
    return api_solving._derive_path_situation(**kwargs)


def test_derive_path_situation_returns_a_well_formed_two_position_situation():
    situation = _derive()
    assert len(situation.postflop_positions) == 2
    assert set(situation.postflop_positions) == {"BTN", "BB"}
    assert situation.effective_stack_bb > 0
    assert set(situation.position_ranges) == set(situation.postflop_positions)
    for combo_dict in situation.position_ranges.values():
        assert combo_dict  # non-empty, board-legal combos
    # Class-level capping populates capped_scenario (the canonical
    # library needs a real StartingHand-keyed PathScenario).
    assert situation.capped_scenario is not None
    for range_dict in situation.capped_scenario.ranges.values():
        assert len(range_dict) <= 2


def test_derive_path_situation_combo_capping_leaves_capped_scenario_none():
    situation = _derive(max_classes_per_position=None, max_combos_per_position=3)
    assert situation.capped_scenario is None
    for combo_dict in situation.position_ranges.values():
        assert 0 < len(combo_dict) <= 3


def test_derive_path_situation_requires_exactly_one_capping_mode():
    with pytest.raises(RuntimeError):
        _derive(max_combos_per_position=3)  # both set
    with pytest.raises(RuntimeError):
        _derive(max_classes_per_position=None)  # neither set


def test_derive_path_situation_rejects_a_non_terminal_path_naming_the_client_field():
    # The point is that the error names the CLIENT's own field rather
    # than an internal one — unchanged by M90, which rewrote the wording
    # from "does not reach a terminal" (a fact about the tree that taught
    # the caller nothing) to something that explains the rule and both
    # ways to satisfy it.
    with pytest.raises(ValueError, match="preflop_action_path does not close the preflop betting"):
        _derive(action_kinds=["raise"], path_field_name="preflop_action_path")
    with pytest.raises(ValueError, match="PREFLOP decision instead"):
        _derive(action_kinds=["raise"], path_field_name="preflop_action_path")


def test_derive_path_situation_two_position_mode_rejects_three_live_naming_the_sibling():
    with pytest.raises(ValueError, match=r"3 live positions, not 2.*/sibling"):
        _derive(action_kinds=["call_or_check"] * 3, players=3)


def test_derive_path_situation_multiway_mode_rejects_two_survivors_naming_the_sibling():
    with pytest.raises(ValueError, match=r"only 2 live position\(s\).*/sibling"):
        _derive(action_kinds=["raise", "fold", "call_or_check"], players=3, multiway=True)


def test_derive_path_situation_multiway_mode_accepts_three_live():
    situation = _derive(action_kinds=["call_or_check"] * 3, players=3, multiway=True)
    assert len(situation.postflop_positions) == 3
    assert set(situation.postflop_positions) == {"BTN", "SB", "BB"}
    # Every live position's stack matches — M23's TerminalNode guarantee,
    # N-generally, which _derive_path_situation asserts internally.
    stacks = {situation.path_scenario.stacks[p] for p in situation.postflop_positions}
    assert len(stacks) == 1


# ---------------------------------------------------------------------------
# M51: POST /advise — one front door for the whole real-situation
# advisor. Street depth inferred from which fields are present; each
# (street, table size) cell delegates to the sibling endpoint that
# already serves it. Adds two things no sibling has: a `hero_cards`
# answer (force-included before capping) and a `source` field naming
# which backend actually answered.
# ---------------------------------------------------------------------------


def _advise_body(preflop_action_path=None, **overrides):
    body = {
        "stack_bb": 100.0,
        "preflop_action_path": ["raise", "call_or_check"] if preflop_action_path is None else preflop_action_path,
        "iterations": _PATH_ITERATIONS,
    }
    body.update(overrides)
    return body


def test_advise_preflop_needs_no_board_and_reads_off_the_cached_solve(client):
    response = client.post("/advise", json=_advise_body(preflop_action_path=[]))
    assert response.status_code == 200
    body = response.json()
    assert body["street"] == "preflop"
    assert body["source"] == "preflop"
    assert body["is_terminal"] is False
    assert body["player_to_act"] == "BTN"
    assert body["position"] == "BTN"
    assert isinstance(body["trained"], dict)
    assert body["hero"] is None
    for freqs in body["strategy"].values():
        assert sum(freqs.values()) == pytest.approx(1.0, abs=1e-6)


def test_advise_preflop_hero_is_keyed_by_hand_class_not_combo(client):
    # The real bug a smoke test caught during M51: preflop strategies are
    # keyed by CLASS ("AKs"), every postflop street by concrete combo
    # ("AsKs"), so a route that assumed one shape silently returned no
    # hero advice at all. At heads-up the solve covers all 169 classes,
    # so hero is always in range here.
    response = client.post("/advise", json=_advise_body(preflop_action_path=[], hero_cards="AsKs"))
    assert response.status_code == 200
    hero = response.json()["hero"]
    assert hero["cards"] == "AsKs"
    assert hero["in_range"] is True
    assert hero["trained"] is True
    assert sum(hero["strategy"].values()) == pytest.approx(1.0, abs=1e-6)


def test_advise_answers_a_flop_decision_that_is_not_the_streets_first(client):
    """M84: the round-8 diagnostic's headline gap.

    /advise could only answer the OPENING decision of each street. A
    player facing a bet on the flop — the most common and most
    consequential decision in poker — got a 422 telling them
    flop_action_path was not allowed. The product answered "what do I do
    first on this street" when its purpose is "what do I do now".

    Never a solver limitation: solve_flop_turn already solves the whole
    flop subtree and _resolve_action_path already walks into it. The data
    existed and nothing asked for it.
    """
    base = _advise_body(board="2h6d9c")

    opening = client.post("/advise", json=base)
    assert opening.status_code == 200
    first_actor = opening.json()["position"]

    # "all_in" rather than a sized raise, and deliberately NOT derived
    # from the opening response's own action set. The two flop decisions
    # are answered by different solvers with different trees (F12 in
    # docs/diagnostic-2026-08-22.md): the opening one comes from the
    # canonical library at solve_flop's defaults, this one from
    # solve_flop_turn's narrower tree. all_in is the one aggressive action
    # both always offer, so the test checks reachability rather than
    # accidentally asserting that inconsistency away.
    facing_a_bet = client.post("/advise", json={**base, "flop_action_path": ["all_in"]})
    assert facing_a_bet.status_code == 200, facing_a_bet.json()
    body = facing_a_bet.json()

    # A different player is now to act, and they can fold — which is the
    # tell that this is a real "facing a bet" node and not the opening one
    # dressed up (the opening decision has no fold: checking is free).
    assert body["position"] != first_actor
    assert "fold" in next(iter(body["strategy"].values()))


def test_advise_flop_decision_discriminates_by_hand_strength(client):
    """The behavioural half of M84, and the reason the gap mattered.

    Before it, every postflop scenario in the diagnostic returned
    call_or_check as its top action for every hand — set, flush draw and
    air alike. That looked like a degenerate solver and was not: the only
    reachable node was BB first-to-act, where checking the whole range
    genuinely IS correct. Once a facing-a-bet node became reachable, hand
    strength separates the way poker says it must.
    """
    base = dict(_advise_body(board="2h6d9c"), flop_action_path=["all_in"])

    strong = client.post("/advise", json={**base, "hero_cards": "9s9d"}).json()["hero"]
    weak = client.post("/advise", json={**base, "hero_cards": "5c4d"}).json()["hero"]
    assert strong["strategy"] and weak["strategy"]

    # A set continues; air folds. Asserted as a comparison rather than
    # against fixed numbers, so this survives solver retuning.
    assert weak["strategy"].get("fold", 0.0) > strong["strategy"].get("fold", 0.0) + 0.5


def test_advise_rejects_a_flop_path_whose_action_already_closed(client):
    """A closed flop line has no flop decision left — the honest answer is
    to say so and point at the turn, not to invent one."""
    base = _advise_body(board="2h6d9c")
    response = client.post(
        "/advise", json={**base, "flop_action_path": ["call_or_check", "call_or_check"]}
    )
    assert response.status_code == 422
    assert "no flop decision left" in response.json()["detail"]


def test_advise_answers_a_turn_decision_that_is_not_the_streets_first(client):
    """M85: the turn half of M84's gap.

    The turn cell read `chance_node.branches[turn_card].root` and its own
    comment called exposing only that "a deliberate cut, not an
    oversight". It was the same cut M84 removed on the flop, wrong for
    the same reason — a player facing a bet on the turn could not ask.
    The subtree is already solved; resolving into it costs nothing.
    """
    base = _advise_body(
        board="2h6d9c",
        flop_action_path=["call_or_check", "call_or_check"],
        turn_card="Kd",
    )
    opening = client.post("/advise", json=base)
    assert opening.status_code == 200
    first_actor = opening.json()["position"]

    facing_a_bet = client.post("/advise", json={**base, "turn_action_path": ["all_in"]})
    assert facing_a_bet.status_code == 200, facing_a_bet.json()
    body = facing_a_bet.json()
    assert body["street"] == "turn"
    assert body["position"] != first_actor
    # Facing a bet means fold is on the table; the opening turn decision
    # has no fold, so this is the tell that a different node answered.
    assert "fold" in next(iter(body["strategy"].values()))


def test_advise_turn_decision_discriminates_by_hand_strength(client):
    base = _advise_body(
        board="2h6d9c",
        flop_action_path=["call_or_check", "call_or_check"],
        turn_card="Kd",
        turn_action_path=["all_in"],
    )
    strong = client.post("/advise", json={**base, "hero_cards": "9s9d"}).json()["hero"]
    weak = client.post("/advise", json={**base, "hero_cards": "5c4d"}).json()["hero"]
    assert strong["strategy"] and weak["strategy"]
    assert weak["strategy"].get("fold", 0.0) > strong["strategy"].get("fold", 0.0) + 0.5


def test_advise_rejects_a_turn_path_whose_action_already_closed(client):
    base = _advise_body(
        board="2h6d9c",
        flop_action_path=["call_or_check", "call_or_check"],
        turn_card="Kd",
    )
    response = client.post(
        "/advise", json={**base, "turn_action_path": ["call_or_check", "call_or_check"]}
    )
    assert response.status_code == 422
    assert "no turn decision left" in response.json()["detail"]


def test_advise_answers_a_multiway_turn_decision_that_is_not_the_first(client):
    """M89. This test asserted the OPPOSITE until M89 — that multiway turn
    paths were refused — because M85/M87 reasoned the cell reads its node
    off a SAMPLED chance branch where a deeper node may never have been
    built.

    That was true when written and had already stopped being true: **M75
    trains the on-demand branch**, running mccfr_solve over its subtree
    and merging into result.node_data, so every node inside it is solved
    for. The blocker had been removed by an earlier milestone and nobody
    went back to check. Worth remembering — a limitation documented as
    structural can quietly become false.
    """
    body = _advise_body(
        preflop_action_path=_THREE_LIVE_PATH,
        board="2h6d9c",
        flop_action_path=["call_or_check"] * 3,
        turn_card="Kd",
        players=3,
    )
    opening = client.post("/advise", json=body)
    assert opening.status_code == 200
    first_actor = opening.json()["position"]

    facing_a_bet = client.post("/advise", json={**body, "turn_action_path": ["all_in"]})
    assert facing_a_bet.status_code == 200, facing_a_bet.json()
    deeper = facing_a_bet.json()
    assert deeper["players"] == 3
    assert deeper["position"] != first_actor
    assert "fold" in next(iter(deeper["strategy"].values()))


def test_advise_rejects_a_closed_multiway_turn_path(client):
    body = _advise_body(
        preflop_action_path=_THREE_LIVE_PATH,
        board="2h6d9c",
        flop_action_path=["call_or_check"] * 3,
        turn_card="Kd",
        turn_action_path=["call_or_check"] * 3,
        players=3,
    )
    response = client.post("/advise", json=body)
    assert response.status_code == 422
    assert "no turn decision left" in response.json()["detail"]


def _advise_river_body(**overrides):
    """An /advise body reaching a real river decision. Deliberately NOT
    named _river_body — that name is already taken further down by the
    deprecated /solve_river_from_path helper, which has a different
    signature."""
    body = _advise_body(
        board="2h6d9c",
        flop_action_path=["call_or_check", "call_or_check"],
        turn_card="Kd",
        turn_action_path=["call_or_check", "call_or_check"],
        river_card="4s",
    )
    body.update(overrides)
    return body


def test_advise_answers_a_river_decision_that_is_not_the_streets_first(client):
    """M86 completes the arc M84 (flop) and M85 (turn) began: the river's
    later decisions were the last unreachable ones in the heads-up tree,
    and facing a river bet is the largest single decision in a hand."""
    opening = client.post("/advise", json=_advise_river_body())
    assert opening.status_code == 200
    first_actor = opening.json()["position"]

    facing_a_bet = client.post("/advise", json=_advise_river_body(river_action_path=["all_in"]))
    assert facing_a_bet.status_code == 200, facing_a_bet.json()
    body = facing_a_bet.json()
    assert body["street"] == "river"
    assert body["position"] != first_actor
    assert "fold" in next(iter(body["strategy"].values()))


def test_advise_river_decision_discriminates_by_hand_strength(client):
    base = _advise_river_body(river_action_path=["all_in"])
    strong = client.post("/advise", json={**base, "hero_cards": "9s9d"}).json()["hero"]
    weak = client.post("/advise", json={**base, "hero_cards": "5c4d"}).json()["hero"]
    assert strong["strategy"] and weak["strategy"]
    assert weak["strategy"].get("fold", 0.0) > strong["strategy"].get("fold", 0.0) + 0.5


def test_advise_rejects_a_river_path_that_ends_the_hand(client):
    response = client.post(
        "/advise", json=_advise_river_body(river_action_path=["call_or_check", "call_or_check"])
    )
    assert response.status_code == 422
    assert "no river decision left" in response.json()["detail"]


def test_advise_rejects_a_river_action_path_without_a_river_card(client):
    """The new field needs the same contradiction guards its siblings
    have, or it is silently ignored — the quietest kind of wrong."""
    body = _advise_body(
        board="2h6d9c",
        flop_action_path=["call_or_check", "call_or_check"],
        turn_card="Kd",
        river_action_path=["all_in"],
    )
    response = client.post("/advise", json=body)
    assert response.status_code == 422
    assert "river_action_path" in response.json()["detail"]


def test_advise_answers_a_multiway_flop_decision_that_is_not_the_first(client):
    """M87: the flop half of R15.

    M84-M86 made every heads-up decision reachable; multiway still
    answered only each street's opening one. The FLOP extends cleanly
    because `solve_flop_multiway` returns a StrategyResult over the whole
    flop tree — flop-only, no chance dispatch — so a deeper decision is
    already solved for and needs no new solve. The turn and river do NOT
    extend this way: they read their node off a sampled chance branch,
    where the node a client asks about may never have been built. That
    difference is why this test exists for the flop alone.
    """
    base = _advise_body(preflop_action_path=_THREE_LIVE_PATH, board="2h6d9c", players=3)

    opening = client.post("/advise", json=base)
    assert opening.status_code == 200
    first_actor = opening.json()["position"]

    facing_a_bet = client.post("/advise", json={**base, "flop_action_path": ["all_in"]})
    assert facing_a_bet.status_code == 200, facing_a_bet.json()
    body = facing_a_bet.json()
    assert body["players"] == 3
    assert body["position"] != first_actor
    assert "fold" in next(iter(body["strategy"].values()))


def test_advise_rejects_a_closed_multiway_flop_path(client):
    base = _advise_body(preflop_action_path=_THREE_LIVE_PATH, board="2h6d9c", players=3)
    response = client.post(
        "/advise", json={**base, "flop_action_path": ["call_or_check"] * 3}
    )
    assert response.status_code == 422
    assert "no flop decision left" in response.json()["detail"]


def test_advise_explains_the_action_path_contract_when_it_is_violated(client):
    """M90: the round-9 finding, and it caught me before it caught a user.

    Playing a whole hand through /advise, my own harness got this wrong
    twice. `flop_action_path` has OPPOSITE requirements depending on
    which street is being asked about — asking about a later FLOP
    decision it must NOT close the street; asking about the TURN it MUST.
    Same field, same hand, contradictory rules. The old error said
    "does not reach a terminal — action isn't capped yet", which states a
    fact about the tree and teaches the caller nothing.

    For a product other people integrate against, an error that does not
    explain the rule it enforces is a defect in its own right.
    """
    body = _advise_body(
        board="2h6d9c",
        flop_action_path=["call_or_check"],  # does NOT close the flop
        turn_card="Kd",                      # ...but asks about the turn
    )
    response = client.post("/advise", json=body)
    assert response.status_code == 422
    detail = response.json()["detail"]
    # Names the actual problem...
    assert "does not close the flop" in detail
    # ...and both ways out, since the caller cannot be expected to guess
    # which of the two questions they meant.
    assert "turn_card" in detail and "FLOP decision" in detail


@pytest.mark.parametrize(
    "board",
    ["2h2h9c", "AsAsAs", "2h6d2h"],
    ids=["adjacent-duplicate", "all-three-same", "separated-duplicate"],
)
def test_advise_rejects_a_board_that_repeats_a_card(client, board):
    """M91: found by round 10's input-robustness probe.

    A board naming the same card twice cannot exist, and the product
    answered anyway — "AsAsAs" returned a confident `call 1.00`. Hero's
    own two cards had always been checked ("HandCombo needs two distinct
    cards") and a turn card colliding with the board was caught
    downstream, so the gap was specifically the flop's cards against each
    other. Per-field validation is exactly how that pairing got missed.

    A real answer to an impossible question is the failure mode this
    whole diagnostic arc keeps turning up, and the least detectable one:
    nothing looks wrong in the response.
    """
    body = _advise_body(board=board, hero_cards="KsQd")
    response = client.post("/advise", json=body)
    assert response.status_code == 422
    assert "appears twice in the same field" in response.json()["detail"]


def test_advise_rejects_a_card_repeated_across_fields(client):
    """The same check, across fields rather than within one — every
    pairing among board / turn / river / hero is equally impossible."""
    body = _advise_body(board="2h6d9c", hero_cards="9c2h")
    response = client.post("/advise", json=body)
    assert response.status_code == 422
    assert "in both board and hero_cards" in response.json()["detail"]


def test_advise_still_answers_a_legitimate_board(client):
    """The guard must not be so eager it rejects real hands — a check
    that fires on everything is as useless as one that never fires."""
    response = client.post("/advise", json=_advise_body(board="2h6d9c", hero_cards="KsQd"))
    assert response.status_code == 200
    assert response.json()["strategy"]


def test_advise_gives_every_hero_advice_regardless_of_who_asked_first(client):
    """M76: the severest bug the 2026-08-22 diagnostic found.

    `_derive_path_situation` force-includes hero's own combo into every
    live position's derived range before the top-K cap, so the SOLVE
    depends on hero. No cache key included hero, so the first request for
    a spot fixed the pool and every later request for that same spot
    holding a different hand found its combo missing and got **no advice
    at all**. On a server serving more than one hand, most users got
    silence.

    Invisible to the rest of the suite precisely because the autouse
    fixture clears caches between tests — the one condition under which
    the bug cannot appear. So this test deliberately does NOT clear
    between asks: the shared cache is the thing under test.
    """
    body = {
        "stack_bb": 100.0,
        "players": 2,
        "preflop_action_path": ["raise", "call_or_check"],
        "board": "2h6d9c",
    }
    # Four different hero hands, same spot, same process, one cache.
    # Deliberately different CLASSES (AK / 99 / AA / KQs) so each needs
    # its own force-inclusion.
    heroes = ["AsKd", "9s9d", "AsAh", "KsQs"]
    answered = {}
    for hero in heroes:
        response = client.post("/advise", json={**body, "hero_cards": hero})
        assert response.status_code == 200, f"{hero}: HTTP {response.status_code}"
        answered[hero] = bool((response.json()["hero"] or {}).get("strategy"))

    missing = [hero for hero, ok in answered.items() if not ok]
    assert not missing, (
        f"no advice for {missing} when asked after another hand — hero must be part "
        "of the path-query cache key (see _hero_cache_component)"
    )

    # And the order must not matter: reversed, all four still answered.
    for hero in reversed(heroes):
        response = client.post("/advise", json={**body, "hero_cards": hero})
        assert (response.json()["hero"] or {}).get("strategy"), (
            f"{hero} lost its advice when asked in the reverse order"
        )


def test_advise_preflop_in_range_is_false_for_a_hand_outside_the_solved_pool(client):
    """M67: in_range used to be hardcoded True preflop, on the reasoning
    that "a preflop solve covers every class". True heads-up, false at
    multiway — which this fixture makes concrete, since it shrinks the
    multiway pool to 8 classes exactly as the pre-M67 production config
    did. A hand outside the pool got `in_range: true` next to a null
    strategy: confidently wrong, the failure mode this project's honesty
    signals exist to prevent.

    Production no longer has a pool this narrow (MULTIWAY_PREFLOP_HANDS
    is all 169 classes), but the signal must stay derived rather than
    assumed — any future pool restriction has to report itself honestly.
    """
    body = _advise_body(preflop_action_path=["fold"], hero_cards="Ts7s")
    body["players"] = 6
    response = client.post("/advise", json=body)
    assert response.status_code == 200
    hero = response.json()["hero"]
    assert hero["cards"] == "Ts7s"
    # T7s is not in FAST_MULTIWAY_HANDS, so there is genuinely no advice...
    assert hero["strategy"] is None
    # ...and the response must say so rather than claim otherwise.
    assert hero["in_range"] is False


def test_advise_preflop_in_range_is_true_for_a_hand_inside_the_solved_pool(client):
    """The other half: the honest signal must not simply always say False
    at multiway — a hand that IS in the pool still gets real advice."""
    body = _advise_body(preflop_action_path=["fold"], hero_cards="AsKs")
    body["players"] = 6
    response = client.post("/advise", json=body)
    assert response.status_code == 200
    hero = response.json()["hero"]
    assert hero["in_range"] is True
    assert sum(hero["strategy"].values()) == pytest.approx(1.0, abs=1e-6)


def test_advise_preflop_rejects_an_already_terminal_path(client):
    # Deliberately the INVERSE requirement of every postflop cell: those
    # need the preflop action closed, preflop advice needs it still open.
    response = client.post("/advise", json=_advise_body(["raise", "fold"]))
    assert response.status_code == 422
    assert "no preflop decision left" in response.json()["detail"]


def test_advise_flop_heads_up_reports_library_miss_then_hit_with_real_trained(client):
    """M76: this cell used to report `trained: null`, documented as a
    structural limitation of the canonical library ("persists only a
    flattened strategy dict, so per-hand confidence structurally isn't
    available"). It was not structural — `LibraryEntry` simply did not
    carry the flags `StrategyResult` already had. It does now, so a
    library-served answer reports real per-combo confidence like every
    other cell, on both the miss and the subsequent hit.
    """
    body = _advise_body(board="2h6d9c")
    first = client.post("/advise", json=body)
    assert first.status_code == 200
    assert first.json()["source"] == "library_miss"
    assert first.json()["street"] == "flop"

    trained = first.json()["trained"]
    assert isinstance(trained, dict) and trained, "library path must report real trained flags"
    assert all(isinstance(flag, bool) for flag in trained.values())
    assert set(trained) == set(first.json()["strategy"]), (
        "trained and strategy must cover exactly the same combos"
    )
    # Not vacuous in either direction: a real solve trains some hands and
    # leaves others untouched, and asserting only "is a dict" would pass
    # on an all-False stub.
    assert any(trained.values()), "no combo trained — the flags are not real"

    second = client.post("/advise", json=body)
    assert second.json()["source"] == "library_hit"
    # A HIT must carry the flags too — they travel through a different
    # code path (lookup_trained's suit translation) than the miss.
    hit_trained = second.json()["trained"]
    assert isinstance(hit_trained, dict) and hit_trained
    assert hit_trained == trained, "hit and miss must agree on confidence"


def test_advise_force_includes_hero_outside_the_cap_and_says_so(client):
    # The whole point of force-inclusion: a hand outside the derived
    # range's top-K still gets real advice, and `in_range: False` reports
    # honestly that it had to be added rather than earning its place.
    body = _advise_body(board="2h6d9c", hero_cards="AsKs")
    response = client.post("/advise", json=body)
    assert response.status_code == 200
    hero = response.json()["hero"]
    assert hero["cards"] == "AsKs"
    assert hero["in_range"] is False
    assert hero["strategy"] is not None
    assert sum(hero["strategy"].values()) == pytest.approx(1.0, abs=1e-6)


def test_advise_rejects_hero_cards_that_share_a_card_with_the_board(client):
    # M91 moved this rejection earlier, into the unified duplicate-card
    # check that covers board / turn / river / hero together, so the
    # wording changed from "shares a card with the board" to one that
    # names both fields. The behaviour under test is unchanged: a card
    # cannot be in two places, and the caller is told which two.
    response = client.post("/advise", json=_advise_body(board="2h6d9c", hero_cards="2hKs"))
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "2h" in detail
    assert "board" in detail and "hero_cards" in detail


def test_advise_rejects_malformed_hero_cards(client):
    response = client.post("/advise", json=_advise_body(board="2h6d9c", hero_cards="AsKsQd"))
    assert response.status_code == 422


def test_advise_flop_multiway_dispatches_to_mccfr_with_real_trained(client):
    response = client.post(
        "/advise",
        json=_advise_body(
            _THREE_LIVE_PATH, players=3, board="2h6d9c",
            solve_iterations=FAST_MULTIWAY_PATH_FLOP_ITERATIONS,
        ),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["street"] == "flop"
    assert body["source"] == "mccfr"
    assert isinstance(body["trained"], dict)  # unlike the library-backed heads-up cell
    assert len(body["positions"]) == 3


def test_advise_turn_heads_up_dispatches_to_the_exact_solver(client):
    response = client.post(
        "/advise",
        json=_advise_body(
            board="2h6d9c", flop_action_path=["call_or_check", "call_or_check"], turn_card="Ts",
            solve_iterations=_TURN_PATH_ITERATIONS,
        ),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["street"] == "turn"
    assert body["source"] == "exact"
    assert isinstance(body["trained"], dict)


def test_advise_river_heads_up_dispatches_to_the_exact_solver(client):
    response = client.post(
        "/advise",
        json=_advise_body(
            board="2h6d9c", flop_action_path=["call_or_check", "call_or_check"], turn_card="Ts",
            turn_action_path=["call_or_check", "call_or_check"], river_card="4h",
            iterations=_RIVER_PATH_ITERATIONS, solve_iterations=_RIVER_PATH_ITERATIONS,
        ),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["street"] == "river"
    assert body["source"] == "exact"


def test_advise_river_multiway_completes_the_street_by_table_size_matrix(client):
    # M53 filled the last cell. This test previously asserted a 422 with
    # the reason it was unsupported — flipped, not deleted, so an
    # accidental regression back to "unsupported" would still be caught.
    response = client.post(
        "/advise",
        json=_advise_body(
            _THREE_LIVE_PATH, players=3, board="2h6d9c",
            flop_action_path=_THREE_LIVE_FLOP_PATH, turn_card="Ts",
            turn_action_path=_THREE_LIVE_FLOP_PATH, river_card="4h",
            solve_iterations=5,
        ),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["street"] == "river"
    assert body["source"] == "mccfr"
    assert len(body["positions"]) == 3
    assert body["is_terminal"] is False
    for freqs in body["strategy"].values():
        assert sum(freqs.values()) == pytest.approx(1.0, abs=1e-6)


def test_advise_no_cell_is_left_unsupported(client):
    # M53: the matrix is complete. Kept as a live assertion rather than a
    # comment so that re-adding an unsupported cell is a deliberate,
    # visible act rather than something that quietly reappears.
    assert api_solving._ADVISE_UNSUPPORTED_CELLS == {}


def test_advise_routes_a_multiway_origin_folded_to_two_survivors_to_the_exact_solver(client):
    # M52's real dispatch bug: /advise used to pick its solver from
    # request.players (the ORIGIN table size), so a 6-max hand folding
    # down to a heads-up flop — the most common real full-ring shape,
    # which M29 built support for specifically — got routed to the
    # multiway cell and correctly refused, making /advise unusable for
    # exactly that case. Survivor count is the right question.
    response = client.post(
        "/advise",
        json=_advise_body(
            ["raise", "raise", "fold", "fold", "fold", "fold", "call_or_check"],
            players=6, board="2h6d9c",
        ),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source"] in ("library_hit", "library_miss")  # the exact 2-position path
    assert len(body["positions"]) == 2


def test_advise_range_confidence_fires_on_a_real_untrained_derivation(client):
    # The signal has to actually FIRE somewhere, or it proves nothing.
    # This is M29's own measured case reproduced: a deep 6-max 3-bet
    # line leaves the opener's derived range untrained — confident-
    # looking, fabricated, and before M52 silently indistinguishable
    # from a converged one in any API response.
    response = client.post(
        "/advise",
        json=_advise_body(
            ["raise", "raise", "fold", "fold", "fold", "fold", "call_or_check"],
            players=6, board="2h6d9c", hero_cards="AsKs",
        ),
    )
    assert response.status_code == 200
    body = response.json()
    confidence = body["range_confidence"]
    assert set(confidence) == set(body["positions"])
    assert any(not c["fully_trained"] for c in confidence.values()), (
        "expected at least one position's derived range to be untrained on this deep 6-max line"
    )
    for entry in confidence.values():
        assert 0 <= entry["trained_classes"] <= entry["total_classes"]
        assert entry["fully_trained"] == (entry["trained_classes"] == entry["total_classes"])
    # hero.range_trained is about the PREFLOP derivation, distinct from
    # hero.trained (the postflop solve node) — both are reported.
    assert body["hero"]["range_trained"] is False


def test_advise_range_confidence_is_present_and_clean_on_a_shallow_heads_up_line(client):
    response = client.post("/advise", json=_advise_body(board="2h6d9c"))
    assert response.status_code == 200
    confidence = response.json()["range_confidence"]
    assert set(confidence) == {"BTN", "BB"}
    for entry in confidence.values():
        assert entry["fully_trained"] is True


def test_advise_preflop_has_no_range_confidence(client):
    # Preflop derives no range at all — it reads the full solved
    # 169-class strategy directly, so there is nothing to have been
    # fabricated. An explicit null, not a fake all-trained summary.
    response = client.post("/advise", json=_advise_body(preflop_action_path=[]))
    assert response.status_code == 200
    assert response.json()["range_confidence"] is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"flop_action_path": ["call_or_check"]},  # no board at all
        {"turn_card": "Ts"},  # no board
        {"river_card": "4h"},  # no board
        {"board": "2h6d9c", "river_card": "4h"},  # river without turn
        {"board": "2h6d9c", "flop_action_path": ["call_or_check", "call_or_check"]},  # flop action, no turn card
        {"board": "2h6d9c", "turn_card": "Ts"},  # turn card without flop action
        {
            "board": "2h6d9c", "flop_action_path": ["call_or_check", "call_or_check"],
            "turn_card": "Ts", "turn_action_path": ["call_or_check", "call_or_check"],
        },  # turn action without river card
    ],
)
def test_advise_rejects_partial_or_skipped_street_field_combinations(client, overrides):
    response = client.post("/advise", json=_advise_body(**overrides))
    assert response.status_code == 422


def test_advise_enforces_the_per_cell_solve_iterations_cap(client):
    response = client.post(
        "/advise",
        json=_advise_body(board="2h6d9c", flop_action_path=["call_or_check", "call_or_check"],
                          turn_card="Ts", solve_iterations=api_config.MAX_FLOP_TURN_ITERATIONS + 1),
    )
    assert response.status_code == 422
    assert "solve_iterations must be between" in response.json()["detail"]


def test_advise_rejects_a_too_long_action_path(client):
    too_long = ["call_or_check"] * (api_config.MAX_PATH_LENGTH + 1)
    response = client.post("/advise", json=_advise_body(too_long))
    assert response.status_code == 422
    assert "too long" in response.json()["detail"]


def test_derive_path_situation_rejects_a_board_that_blocks_a_whole_capped_range():
    # A real, constructible case, not a contrived one: at a 1-class cap,
    # if BB's single top class is a PAIR, a board of three of that rank
    # leaves one card of it in the deck and blocks every combo. A pair is
    # the only class shape a 3-card flop can fully block, since 4 of a
    # rank exist and a pair needs 2 of them.
    #
    # WHICH pair is not hardcoded any more. It used to be 22 at
    # _PATH_ITERATIONS, but M71's convergence fixes reordered the derived
    # range (BB's top class there is now A2, which no flop can fully
    # block) and this test failed for a reason that had nothing to do
    # with the guard it exists to cover. So the premise is now derived and
    # asserted explicitly: if it ever stops holding, the failure says so
    # instead of looking like a broken guard.
    iterations = 40
    probe = _derive(iterations=iterations, max_classes_per_position=1)
    bb_combos = list(probe.position_ranges["BB"])
    assert bb_combos, "premise: BB's capped range must be non-empty on the probe board"
    ranks = {combo.cards[0].rank for combo in bb_combos} | {combo.cards[1].rank for combo in bb_combos}
    assert len(ranks) == 1, (
        f"premise broken: BB's top capped class is not a pair (ranks {sorted(ranks)}), "
        "so no 3-card flop can block it — pick an iteration count where it is one"
    )
    pair_rank = ranks.pop()
    blocking_board = tuple(parse_cards("".join(f"{pair_rank}{suit}" for suit in ("h", "s", "d"))))

    with pytest.raises(ValueError, match=r"blocks every combo in BB's derived \(capped\) range"):
        _derive(board_cards=blocking_board, iterations=iterations, max_classes_per_position=1)


def test_preflop_walk_closing_call_is_a_real_two_live_position_terminal(client):
    response = client.post("/preflop_walk", json=_walk_body(["raise", "call_or_check"]))
    assert response.status_code == 200
    body = response.json()
    assert body["is_terminal"] is True
    assert body["legal_actions"] == []
    assert body["player_to_act"] is None
    assert body["live_positions"] == ["BTN", "BB"]
    assert body["pot"] == pytest.approx(5.0)


def test_preflop_walk_fold_at_root_is_a_one_live_position_terminal(client):
    response = client.post("/preflop_walk", json=_walk_body(["fold"]))
    assert response.status_code == 200
    body = response.json()
    assert body["is_terminal"] is True
    assert body["live_positions"] == ["BB"]
    assert body["pot"] == pytest.approx(1.5)


def test_preflop_walk_rejects_an_unknown_action_kind(client):
    response = client.post("/preflop_walk", json=_walk_body(["not_a_real_kind"]))
    assert response.status_code == 422


def test_preflop_walk_rejects_a_too_long_action_path(client):
    too_long = ["call_or_check"] * (api_config.MAX_PATH_LENGTH + 1)
    response = client.post("/preflop_walk", json=_walk_body(too_long))
    assert response.status_code == 422


def test_preflop_walk_rejects_a_stack_shorter_than_the_big_blind(client):
    # Mirrors test_solve_rejects_a_stack_shorter_than_the_big_blind (M117).
    for stack in (0.3, 0.6, 0.99):
        response = client.post("/preflop_walk", json=_walk_body([], stack_bb=stack))
        assert response.status_code == 422, stack


def test_preflop_walk_shares_the_raw_preflop_cache_with_solve_flop_from_path(client):
    client.post("/preflop_walk", json=_walk_body(["raise", "call_or_check"]))
    client.post("/solve_flop_from_path", json=_path_body(["raise", "call_or_check"]))
    assert len(api_main._preflop_raw_cache) == 1


# ---------------------------------------------------------------------------
# M29: players != 2 walks a real multiway-origin tree — the same
# capability query_strategy_from_path/postflop_action_order unlocked at
# the engine level, now reachable live. Uses the SAME MULTIWAY_TABLE_
# CONFIGS this fixture already shrinks to FAST_MULTIWAY_ITERATIONS, so
# these stay fast without any new fixture patch.
# ---------------------------------------------------------------------------


def test_preflop_walk_with_players_3_walks_the_three_max_tree(client):
    response = client.post("/preflop_walk", json=_walk_body([], players=3))
    assert response.status_code == 200
    body = response.json()
    assert body["player_to_act"] == "BTN"
    assert body["live_positions"] == ["BTN", "SB", "BB"]
    assert body["positions"] == ["BTN", "SB", "BB"]


def test_preflop_walk_players_defaults_to_two_and_reports_the_heads_up_positions(client):
    body = client.post("/preflop_walk", json=_walk_body([])).json()
    assert body["positions"] == ["BTN", "BB"]


def test_preflop_walk_rejects_an_unsupported_players_value(client):
    response = client.post("/preflop_walk", json=_walk_body([], players=5))
    assert response.status_code == 422


def test_preflop_walk_shares_the_multiway_cache_with_get_solve(client):
    # _get_or_solve_preflop_raw(players=3) now delegates to
    # _get_or_solve_multiway outright — a user who already loaded the
    # 3-max range chart triggers no redundant second solve opening the
    # wizard next, and vice versa.
    client.get("/solve/100?players=3")
    client.post("/preflop_walk", json=_walk_body([], players=3))
    assert len(api_main._multiway_cache) == 1


def test_solve_flop_from_path_accepts_a_multiway_origin_narrowed_to_two_survivors(client):
    # BTN opens, SB folds, BB calls — a real 3-max hand folding down to
    # a heads-up flop. BB is left of the button (SB folded) and is
    # therefore OOP; BTN is IP.
    response = client.post(
        "/solve_flop_from_path", json=_path_body(["raise", "fold", "call_or_check"], players=3)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["players"] == 3
    assert set(body["positions"]) == {"BTN", "BB"}
    assert body["position"] == "BB"
    assert len(body["strategy"]) > 0
    for freqs in body["strategy"].values():
        assert sum(freqs.values()) == pytest.approx(1.0, abs=1e-6)


def test_solve_flop_from_path_rejects_a_multiway_path_with_three_live_survivors(client):
    # BTN limps, SB calls, BB checks — everyone stays live. solve_flop's
    # postflop machinery is 2-position only, regardless of origin size.
    response = client.post(
        "/solve_flop_from_path", json=_path_body(["call_or_check", "call_or_check", "call_or_check"], players=3)
    )
    assert response.status_code == 422
    # M50: this used to surface as a bare "too many values to unpack"
    # ValueError from postflop_action_order's own 2-tuple unpack (still a
    # 422, but useless to a caller) — the shared _derive_path_situation
    # now checks live-position count explicitly, for every endpoint, and
    # names the sibling endpoint that DOES serve this case.
    detail = response.json()["detail"]
    assert "3 live positions, not 2" in detail
    assert "/solve_flop_multiway_from_path" in detail


def test_solve_flop_from_path_players_2_and_3_partition_separately(client):
    # A players=3 path needs its own real terminal (3 steps, one more
    # position to act than heads-up) — reusing the 2-step heads-up path
    # verbatim at players=3 would leave BB's decision still pending
    # (a non-terminal node), rejected before ever reaching the library,
    # which wouldn't actually prove the two partition separately.
    r2 = client.post("/solve_flop_from_path", json=_path_body(["raise", "call_or_check"], players=2))
    r3 = client.post("/solve_flop_from_path", json=_path_body(["raise", "fold", "call_or_check"], players=3))
    assert r2.status_code == 200
    assert r3.status_code == 200
    assert len(api_main._path_query_libraries) == 2


def test_preflop_walk_fold_option_serializes_size_as_null_not_an_omitted_key(client):
    body = client.post("/preflop_walk", json=_walk_body([])).json()
    fold_option = next(option for option in body["legal_actions"] if option["kind"] == "fold")
    assert "size" in fold_option
    assert fold_option["size"] is None
    assert "to_call" in fold_option
    assert fold_option["to_call"] is None


# ---------------------------------------------------------------------------
# M26 deliverable: POST /solve_turn_from_path — real turn-level advice,
# reading poker_solver.solver.solve_flop_turn's own chance_data live,
# not just a flop-level number improved by real turn action baked in.
#
# This fixture's own FLOP_TURN_MAX_RAISES=1/FLOP_TURN_RAISE_SIZES=()
# patch above (shared with /solve_flop_turn's own tests, since this
# endpoint reuses those same production constants directly) leaves only
# 3 real showdown-eligible flop lines reachable: check-check (no chips
# move — a real, if unexciting, turn decision), all-in+call, and the
# fold variants off of an all-in (all_in+fold, or check+all_in+fold) —
# re-derived directly from a real tree walk under these exact patched
# values before writing any assertion below, not assumed from this
# milestone's own (unpatched, max_raises=2) planning-time enumeration.
# ---------------------------------------------------------------------------

_TURN_PATH_ITERATIONS = 200  # a real per-request solve, not fixture-capped — kept small for test speed
_TURN_CARD = "Ts"  # doesn't collide with the default test board "2h6d9c"


def _turn_body(
    preflop_action_path,
    flop_action_path,
    turn_card=_TURN_CARD,
    stack_bb=100.0,
    board="2h6d9c",
    iterations=_TURN_PATH_ITERATIONS,
    turn_iterations=_TURN_PATH_ITERATIONS,
    players=2,
):
    return {
        "stack_bb": stack_bb,
        "preflop_action_path": preflop_action_path,
        "board": board,
        "flop_action_path": flop_action_path,
        "turn_card": turn_card,
        "iterations": iterations,
        "turn_iterations": turn_iterations,
        "players": players,
    }


def test_solve_turn_from_path_returns_a_real_non_uniform_turn_strategy(client):
    response = client.post(
        "/solve_turn_from_path", json=_turn_body(["raise", "call_or_check"], ["call_or_check", "call_or_check"])
    )
    assert response.status_code == 200
    body = response.json()
    assert body["is_terminal"] is False
    assert body["board"] == "2h6d9c"
    assert body["turn_card"] == "Ts"
    assert body["player_to_act"] in ("BTN", "BB")
    assert set(body["positions"]) == {"BTN", "BB"}
    assert body["pot"] > 0
    assert body["effective_stack_bb"] > 0
    assert len(body["strategy"]) > 0
    for freqs in body["strategy"].values():
        assert sum(freqs.values()) == pytest.approx(1.0, abs=1e-6)
    assert set(body["trained"].keys()) == set(body["strategy"].keys())


def test_solve_turn_from_path_reuses_the_same_cache_entry_across_turn_cards_and_flop_lines(client):
    # The exact regression a real bug in an early draft of this
    # milestone would have caught: a first design keyed the cache by
    # the full request (including flop_action_path/turn_card), which
    # would force a full re-solve per distinct turn-card query against
    # an identical situation — defeating the entire point of reading
    # chance_data live. One shared solve should answer all of these.
    base = _turn_body(["raise", "call_or_check"], ["call_or_check", "call_or_check"])
    first = client.post("/solve_turn_from_path", json=base).json()
    assert len(api_main._turn_path_cache) == 1

    # M173 CHANGED THIS. The chained solve built every turn card's branch
    # in one pass, so all of them shared a single cache entry. Solving the
    # turn as its own street means one solve per turn BOARD, so a
    # different card is a different entry.
    #
    # That is a real trade and it is worth stating: chained amortised
    # 1.67s across all 45 cards, standalone pays 0.28s per card, so
    # standalone is cheaper until roughly the sixth card asked about on
    # one flop line. A player asks about the card that actually came, so
    # it wins in practice — but a caller sweeping turn cards would not
    # want this.
    different_turn_card = client.post("/solve_turn_from_path", json={**base, "turn_card": "4c"}).json()
    assert len(api_main._turn_path_cache) == 2
    assert different_turn_card["turn_card"] != first["turn_card"]

    already_all_in = client.post(
        "/solve_turn_from_path", json={**base, "flop_action_path": ["all_in", "call_or_check"]}
    ).json()
    # No solve at all: the flop action left nothing behind, so this returns
    # a terminal without touching the cache.
    assert len(api_main._turn_path_cache) == 2
    assert already_all_in["is_terminal"] is True
    assert already_all_in["strategy"] == {}
    assert already_all_in["trained"] == {}
    assert already_all_in["effective_stack_bb"] == pytest.approx(0.0)

    fold_out = client.post("/solve_turn_from_path", json={**base, "flop_action_path": ["all_in", "fold"]}).json()
    # Also no solve: a fold ends the hand, so nothing is cached here either.
    assert len(api_main._turn_path_cache) == 2
    assert fold_out["is_terminal"] is True
    assert fold_out["strategy"] == {}
    assert fold_out["trained"] == {}
    assert fold_out["player_to_act"] is None


def test_solve_turn_from_path_partitions_different_preflop_legs_into_separate_cache_entries(client):
    client.post(
        "/solve_turn_from_path", json=_turn_body(["raise", "call_or_check"], ["call_or_check", "call_or_check"])
    )
    client.post(
        "/solve_turn_from_path",
        json=_turn_body(["call_or_check", "call_or_check"], ["call_or_check", "call_or_check"]),
    )
    assert len(api_main._turn_path_cache) == 2


def test_solve_turn_from_path_rejects_an_illegal_turn_card(client):
    # "9c" is already on the board (board="2h6d9c").
    response = client.post(
        "/solve_turn_from_path",
        json=_turn_body(["raise", "call_or_check"], ["call_or_check", "call_or_check"], turn_card="9c"),
    )
    assert response.status_code == 422


def test_solve_turn_from_path_rejects_a_malformed_turn_card(client):
    response = client.post(
        "/solve_turn_from_path",
        json=_turn_body(["raise", "call_or_check"], ["call_or_check", "call_or_check"], turn_card="TsJd"),
    )
    assert response.status_code == 422


def test_solve_turn_from_path_rejects_an_illegal_flop_action_kind(client):
    response = client.post(
        "/solve_turn_from_path", json=_turn_body(["raise", "call_or_check"], ["not_a_real_kind"])
    )
    assert response.status_code == 422


def test_solve_turn_from_path_rejects_a_non_terminal_flop_path(client):
    response = client.post(
        "/solve_turn_from_path", json=_turn_body(["raise", "call_or_check"], ["call_or_check"])
    )
    assert response.status_code == 422


def test_solve_turn_from_path_rejects_a_non_terminal_preflop_path(client):
    # Mirrors test_solve_flop_from_path_rejects_a_non_terminal_path — the
    # exact safety check a real early draft of this milestone silently
    # dropped (see CLAUDE.md's M26 entry): library.query_strategy_from_
    # path's own TerminalNode check, ported here explicitly since this
    # endpoint deliberately bypasses that function.
    response = client.post(
        "/solve_turn_from_path", json=_turn_body(["raise"], ["call_or_check", "call_or_check"])
    )
    assert response.status_code == 422


def test_solve_turn_from_path_rejects_a_too_long_flop_action_path(client):
    too_long = ["call_or_check"] * (api_config.MAX_PATH_LENGTH + 1)
    response = client.post("/solve_turn_from_path", json=_turn_body(["raise", "call_or_check"], too_long))
    assert response.status_code == 422


def test_solve_turn_from_path_rejects_a_too_long_preflop_action_path(client):
    too_long = ["call_or_check"] * (api_config.MAX_PATH_LENGTH + 1)
    response = client.post(
        "/solve_turn_from_path", json=_turn_body(too_long, ["call_or_check", "call_or_check"])
    )
    assert response.status_code == 422


def test_solve_turn_from_path_rejects_out_of_range_turn_iterations(client):
    response = client.post(
        "/solve_turn_from_path",
        json=_turn_body(
            ["raise", "call_or_check"], ["call_or_check", "call_or_check"],
            turn_iterations=api_config.MAX_FLOP_TURN_ITERATIONS + 1,
        ),
    )
    assert response.status_code == 422


def test_solve_turn_from_path_accepts_a_multiway_origin_narrowed_to_two_survivors(client):
    # BTN opens, SB folds, BB calls, closing a real 3-max preflop leg
    # down to a heads-up flop+turn line — same check-check flop line
    # the heads-up tests above already use.
    response = client.post(
        "/solve_turn_from_path",
        json=_turn_body(["raise", "fold", "call_or_check"], ["call_or_check", "call_or_check"], players=3),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["players"] == 3
    assert set(body["positions"]) == {"BTN", "BB"}
    assert body["is_terminal"] is False
    assert len(body["strategy"]) > 0


def test_solve_turn_from_path_rejects_a_multiway_preflop_path_with_three_live_survivors(client):
    response = client.post(
        "/solve_turn_from_path",
        json=_turn_body(
            ["call_or_check", "call_or_check", "call_or_check"], ["call_or_check", "call_or_check"], players=3
        ),
    )
    assert response.status_code == 422


def test_solve_turn_from_path_players_2_and_3_do_not_share_a_cache_entry(client):
    r2 = client.post(
        "/solve_turn_from_path",
        json=_turn_body(["raise", "call_or_check"], ["call_or_check", "call_or_check"], players=2),
    )
    r3 = client.post(
        "/solve_turn_from_path",
        json=_turn_body(["raise", "fold", "call_or_check"], ["call_or_check", "call_or_check"], players=3),
    )
    assert r2.status_code == 200
    assert r3.status_code == 200
    assert len(api_main._turn_path_cache) == 2


# ---------------------------------------------------------------------------
# M46: POST /solve_river_from_path — real river-level advice, one street
# further than /solve_turn_from_path (M26), via poker_solver.solver.
# solve_flop_to_river's own chance_data, read live, two hops deep.
#
# FLOP_TO_RIVER_MAX_RAISES=1/RAISE_SIZES=() are ALREADY at their minimal
# production values (unlike FLOP_TURN_MAX_RAISES/RAISE_SIZES, which the
# fixture patches down for the turn endpoint's own tests) — so only
# fold/call_or_check/all_in exist at each street; the same real terminal
# lines the turn-path section above already validated apply here too,
# one street further.
# ---------------------------------------------------------------------------

_RIVER_PATH_ITERATIONS = 5  # within MAX_RIVER_PATH_QUERY_ITERATIONS's own zero-headroom cap (20)
_RIVER_TURN_CARD = "Ts"  # doesn't collide with the default test board "2h6d9c"
_RIVER_CARD = "4h"  # doesn't collide with the board or _RIVER_TURN_CARD


def _river_body(
    preflop_action_path,
    flop_action_path,
    turn_action_path,
    turn_card=_RIVER_TURN_CARD,
    river_card=_RIVER_CARD,
    stack_bb=100.0,
    board="2h6d9c",
    iterations=_RIVER_PATH_ITERATIONS,
    river_iterations=_RIVER_PATH_ITERATIONS,
    players=2,
):
    return {
        "stack_bb": stack_bb,
        "preflop_action_path": preflop_action_path,
        "board": board,
        "flop_action_path": flop_action_path,
        "turn_card": turn_card,
        "turn_action_path": turn_action_path,
        "river_card": river_card,
        "iterations": iterations,
        "river_iterations": river_iterations,
        "players": players,
    }


def test_solve_river_from_path_returns_a_real_non_uniform_river_strategy(client):
    response = client.post(
        "/solve_river_from_path",
        json=_river_body(
            ["raise", "call_or_check"], ["call_or_check", "call_or_check"], ["call_or_check", "call_or_check"]
        ),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["is_terminal"] is False
    assert body["board"] == "2h6d9c"
    assert body["turn_card"] == "Ts"
    assert body["river_card"] == "4h"
    assert body["player_to_act"] in ("BTN", "BB")
    assert set(body["positions"]) == {"BTN", "BB"}
    assert body["pot"] > 0
    assert body["effective_stack_bb"] > 0
    assert len(body["strategy"]) > 0
    for freqs in body["strategy"].values():
        assert sum(freqs.values()) == pytest.approx(1.0, abs=1e-6)
    assert set(body["trained"].keys()) == set(body["strategy"].keys())
    assert body["river_iterations"] == _RIVER_PATH_ITERATIONS


def test_the_standalone_river_keys_its_cache_per_board_and_that_is_affordable(client):
    """M174 CHANGED this deliberately, and the change is a real tradeoff.

    The chained river solved flop->turn->river once and served every turn
    and river card off that one tree, so the cache held a single entry for
    a whole action line. The standalone river solves ONE street on a
    COMPLETE board, so a different runout is a different solve and a
    different entry — the reuse is genuinely gone.

    What repays it, measured rather than assumed: a chained entry cost
    **38.45 MB** and a standalone one costs **0.42 MB**, so the ceiling
    went 4 -> 256. The cache holds 64x more boards for two thirds of the
    memory, and each solve is ~24x cheaper to recompute anyway.

    The property that must NOT change is that a repeat of the SAME
    question is still served from cache.
    """
    base = _river_body(
        ["raise", "call_or_check"], ["call_or_check", "call_or_check"], ["call_or_check", "call_or_check"]
    )
    first = client.post("/solve_river_from_path", json=base).json()
    before = len(api_main._river_path_cache)
    assert before == 1

    # The same question again must not re-solve.
    client.post("/solve_river_from_path", json=base)
    assert len(api_main._river_path_cache) == before, "a repeat question re-solved"

    # A different runout is a different board and so a different solve.
    other = client.post("/solve_river_from_path", json={**base, "river_card": "5s"}).json()
    assert other["river_card"] != first["river_card"]
    assert len(api_main._river_path_cache) == before + 1

    # And the ceiling has to be sized for that, or a player walking runouts
    # would thrash a 4-entry cache.
    assert api_main._river_path_cache.maxsize >= 64, (
        "standalone keys per board, so the river cache must hold many boards")


def test_solve_turn_from_path_partitions_different_preflop_legs_into_separate_cache_entries(client):
    client.post(
        "/solve_turn_from_path", json=_turn_body(["raise", "call_or_check"], ["call_or_check", "call_or_check"])
    )
    client.post(
        "/solve_turn_from_path",
        json=_turn_body(["call_or_check", "call_or_check"], ["call_or_check", "call_or_check"]),
    )
    assert len(api_main._turn_path_cache) == 2


def test_solve_turn_from_path_rejects_an_illegal_turn_card(client):
    # "9c" is already on the board (board="2h6d9c").
    response = client.post(
        "/solve_turn_from_path",
        json=_turn_body(["raise", "call_or_check"], ["call_or_check", "call_or_check"], turn_card="9c"),
    )
    assert response.status_code == 422


def test_solve_turn_from_path_rejects_a_malformed_turn_card(client):
    response = client.post(
        "/solve_turn_from_path",
        json=_turn_body(["raise", "call_or_check"], ["call_or_check", "call_or_check"], turn_card="TsJd"),
    )
    assert response.status_code == 422


def test_solve_turn_from_path_rejects_an_illegal_flop_action_kind(client):
    response = client.post(
        "/solve_turn_from_path", json=_turn_body(["raise", "call_or_check"], ["not_a_real_kind"])
    )
    assert response.status_code == 422


def test_solve_turn_from_path_rejects_a_non_terminal_flop_path(client):
    response = client.post(
        "/solve_turn_from_path", json=_turn_body(["raise", "call_or_check"], ["call_or_check"])
    )
    assert response.status_code == 422


def test_solve_turn_from_path_rejects_a_non_terminal_preflop_path(client):
    # Mirrors test_solve_flop_from_path_rejects_a_non_terminal_path — the
    # exact safety check a real early draft of this milestone silently
    # dropped (see CLAUDE.md's M26 entry): library.query_strategy_from_
    # path's own TerminalNode check, ported here explicitly since this
    # endpoint deliberately bypasses that function.
    response = client.post(
        "/solve_turn_from_path", json=_turn_body(["raise"], ["call_or_check", "call_or_check"])
    )
    assert response.status_code == 422


def test_solve_turn_from_path_rejects_a_too_long_flop_action_path(client):
    too_long = ["call_or_check"] * (api_config.MAX_PATH_LENGTH + 1)
    response = client.post("/solve_turn_from_path", json=_turn_body(["raise", "call_or_check"], too_long))
    assert response.status_code == 422


def test_solve_turn_from_path_rejects_a_too_long_preflop_action_path(client):
    too_long = ["call_or_check"] * (api_config.MAX_PATH_LENGTH + 1)
    response = client.post(
        "/solve_turn_from_path", json=_turn_body(too_long, ["call_or_check", "call_or_check"])
    )
    assert response.status_code == 422


def test_solve_turn_from_path_rejects_out_of_range_turn_iterations(client):
    response = client.post(
        "/solve_turn_from_path",
        json=_turn_body(
            ["raise", "call_or_check"], ["call_or_check", "call_or_check"],
            turn_iterations=api_config.MAX_FLOP_TURN_ITERATIONS + 1,
        ),
    )
    assert response.status_code == 422


def test_solve_turn_from_path_accepts_a_multiway_origin_narrowed_to_two_survivors(client):
    # BTN opens, SB folds, BB calls, closing a real 3-max preflop leg
    # down to a heads-up flop+turn line — same check-check flop line
    # the heads-up tests above already use.
    response = client.post(
        "/solve_turn_from_path",
        json=_turn_body(["raise", "fold", "call_or_check"], ["call_or_check", "call_or_check"], players=3),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["players"] == 3
    assert set(body["positions"]) == {"BTN", "BB"}
    assert body["is_terminal"] is False
    assert len(body["strategy"]) > 0


def test_solve_turn_from_path_rejects_a_multiway_preflop_path_with_three_live_survivors(client):
    response = client.post(
        "/solve_turn_from_path",
        json=_turn_body(
            ["call_or_check", "call_or_check", "call_or_check"], ["call_or_check", "call_or_check"], players=3
        ),
    )
    assert response.status_code == 422


def test_solve_turn_from_path_players_2_and_3_do_not_share_a_cache_entry(client):
    r2 = client.post(
        "/solve_turn_from_path",
        json=_turn_body(["raise", "call_or_check"], ["call_or_check", "call_or_check"], players=2),
    )
    r3 = client.post(
        "/solve_turn_from_path",
        json=_turn_body(["raise", "fold", "call_or_check"], ["call_or_check", "call_or_check"], players=3),
    )
    assert r2.status_code == 200
    assert r3.status_code == 200
    assert len(api_main._turn_path_cache) == 2


# ---------------------------------------------------------------------------
# M46: POST /solve_river_from_path — real river-level advice, one street
# further than /solve_turn_from_path (M26), via poker_solver.solver.
# solve_flop_to_river's own chance_data, read live, two hops deep.
#
# FLOP_TO_RIVER_MAX_RAISES=1/RAISE_SIZES=() are ALREADY at their minimal
# production values (unlike FLOP_TURN_MAX_RAISES/RAISE_SIZES, which the
# fixture patches down for the turn endpoint's own tests) — so only
# fold/call_or_check/all_in exist at each street; the same real terminal
# lines the turn-path section above already validated apply here too,
# one street further.
# ---------------------------------------------------------------------------

_RIVER_PATH_ITERATIONS = 5  # within MAX_RIVER_PATH_QUERY_ITERATIONS's own zero-headroom cap (20)
_RIVER_TURN_CARD = "Ts"  # doesn't collide with the default test board "2h6d9c"
_RIVER_CARD = "4h"  # doesn't collide with the board or _RIVER_TURN_CARD


def _river_body(
    preflop_action_path,
    flop_action_path,
    turn_action_path,
    turn_card=_RIVER_TURN_CARD,
    river_card=_RIVER_CARD,
    stack_bb=100.0,
    board="2h6d9c",
    iterations=_RIVER_PATH_ITERATIONS,
    river_iterations=_RIVER_PATH_ITERATIONS,
    players=2,
):
    return {
        "stack_bb": stack_bb,
        "preflop_action_path": preflop_action_path,
        "board": board,
        "flop_action_path": flop_action_path,
        "turn_card": turn_card,
        "turn_action_path": turn_action_path,
        "river_card": river_card,
        "iterations": iterations,
        "river_iterations": river_iterations,
        "players": players,
    }


def test_solve_river_from_path_returns_a_real_non_uniform_river_strategy(client):
    response = client.post(
        "/solve_river_from_path",
        json=_river_body(
            ["raise", "call_or_check"], ["call_or_check", "call_or_check"], ["call_or_check", "call_or_check"]
        ),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["is_terminal"] is False
    assert body["board"] == "2h6d9c"
    assert body["turn_card"] == "Ts"
    assert body["river_card"] == "4h"
    assert body["player_to_act"] in ("BTN", "BB")
    assert set(body["positions"]) == {"BTN", "BB"}
    assert body["pot"] > 0
    assert body["effective_stack_bb"] > 0
    assert len(body["strategy"]) > 0
    for freqs in body["strategy"].values():
        assert sum(freqs.values()) == pytest.approx(1.0, abs=1e-6)
    assert set(body["trained"].keys()) == set(body["strategy"].keys())
    assert body["river_iterations"] == _RIVER_PATH_ITERATIONS


def test_the_river_short_circuits_when_the_hand_is_already_decided(client):
    """The terminal cases, kept from the test M174 replaced.

    That test asserted the river cache stayed at ONE entry across every
    turn card, river card and action line, because the chained solve
    covered them all from a single tree. M174's standalone river keys per
    board and that claim is deliberately no longer true — see
    `test_the_standalone_river_keys_its_cache_per_board_and_that_is_
    affordable`.

    What is NOT about caching, and still has to hold: when the flop or
    turn action already put a player all in or folded someone out, there
    is no river decision to advise, and the response says so instead of
    inventing a strategy. The standalone path expresses these through its
    own `terminal_response`, so they are worth re-asserting against it
    rather than dropping with the test they came from.
    """
    base = _river_body(
        ["raise", "call_or_check"], ["call_or_check", "call_or_check"], ["call_or_check", "call_or_check"]
    )

    already_all_in_flop = client.post(
        "/solve_river_from_path", json={**base, "flop_action_path": ["all_in", "call_or_check"]}
    ).json()
    assert already_all_in_flop["is_terminal"] is True
    assert already_all_in_flop["strategy"] == {}
    assert already_all_in_flop["trained"] == {}

    fold_out_flop = client.post(
        "/solve_river_from_path", json={**base, "flop_action_path": ["all_in", "fold"]}
    ).json()
    assert fold_out_flop["is_terminal"] is True
    assert fold_out_flop["player_to_act"] is None

    already_all_in_turn = client.post(
        "/solve_river_from_path", json={**base, "turn_action_path": ["all_in", "call_or_check"]}
    ).json()
    assert already_all_in_turn["is_terminal"] is True
    assert already_all_in_turn["effective_stack_bb"] == pytest.approx(0.0)

    fold_out_turn = client.post(
        "/solve_river_from_path", json={**base, "turn_action_path": ["all_in", "fold"]}
    ).json()
    assert fold_out_turn["is_terminal"] is True
    assert fold_out_turn["player_to_act"] is None


def test_solve_river_from_path_partitions_different_preflop_legs_into_separate_cache_entries(client):
    client.post(
        "/solve_river_from_path",
        json=_river_body(
            ["raise", "call_or_check"], ["call_or_check", "call_or_check"], ["call_or_check", "call_or_check"]
        ),
    )
    client.post(
        "/solve_river_from_path",
        json=_river_body(
            ["call_or_check", "call_or_check"],
            ["call_or_check", "call_or_check"],
            ["call_or_check", "call_or_check"],
        ),
    )
    assert len(api_main._river_path_cache) == 2


def test_solve_river_from_path_rejects_an_illegal_river_card(client):
    # "9c" is already on the board (board="2h6d9c").
    response = client.post(
        "/solve_river_from_path",
        json=_river_body(
            ["raise", "call_or_check"],
            ["call_or_check", "call_or_check"],
            ["call_or_check", "call_or_check"],
            river_card="9c",
        ),
    )
    assert response.status_code == 422


def test_solve_river_from_path_rejects_a_malformed_river_card(client):
    response = client.post(
        "/solve_river_from_path",
        json=_river_body(
            ["raise", "call_or_check"],
            ["call_or_check", "call_or_check"],
            ["call_or_check", "call_or_check"],
            river_card="4h5h",
        ),
    )
    assert response.status_code == 422


def test_solve_river_from_path_rejects_an_illegal_turn_action_kind(client):
    response = client.post(
        "/solve_river_from_path",
        json=_river_body(["raise", "call_or_check"], ["call_or_check", "call_or_check"], ["not_a_real_kind"]),
    )
    assert response.status_code == 422


def test_solve_river_from_path_rejects_a_non_terminal_turn_path(client):
    response = client.post(
        "/solve_river_from_path",
        json=_river_body(["raise", "call_or_check"], ["call_or_check", "call_or_check"], ["call_or_check"]),
    )
    assert response.status_code == 422


def test_solve_river_from_path_rejects_a_non_terminal_flop_path(client):
    response = client.post(
        "/solve_river_from_path",
        json=_river_body(["raise", "call_or_check"], ["call_or_check"], ["call_or_check", "call_or_check"]),
    )
    assert response.status_code == 422


def test_solve_river_from_path_rejects_a_non_terminal_preflop_path(client):
    # Mirrors test_solve_turn_from_path_rejects_a_non_terminal_preflop_
    # path — the same TerminalNode safety check, ported here since this
    # endpoint also bypasses library.query_strategy_from_path.
    response = client.post(
        "/solve_river_from_path",
        json=_river_body(["raise"], ["call_or_check", "call_or_check"], ["call_or_check", "call_or_check"]),
    )
    assert response.status_code == 422


def test_solve_river_from_path_rejects_a_too_long_turn_action_path(client):
    too_long = ["call_or_check"] * (api_config.MAX_PATH_LENGTH + 1)
    response = client.post(
        "/solve_river_from_path",
        json=_river_body(["raise", "call_or_check"], ["call_or_check", "call_or_check"], too_long),
    )
    assert response.status_code == 422


def test_solve_river_from_path_rejects_out_of_range_river_iterations(client):
    response = client.post(
        "/solve_river_from_path",
        json=_river_body(
            ["raise", "call_or_check"],
            ["call_or_check", "call_or_check"],
            ["call_or_check", "call_or_check"],
            river_iterations=api_config.MAX_RIVER_PATH_QUERY_ITERATIONS + 1,
        ),
    )
    assert response.status_code == 422


def test_solve_river_from_path_accepts_a_multiway_origin_narrowed_to_two_survivors(client):
    response = client.post(
        "/solve_river_from_path",
        json=_river_body(
            ["raise", "fold", "call_or_check"],
            ["call_or_check", "call_or_check"],
            ["call_or_check", "call_or_check"],
            players=3,
        ),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["players"] == 3
    assert set(body["positions"]) == {"BTN", "BB"}
    assert body["is_terminal"] is False


def test_solve_river_from_path_rejects_a_multiway_preflop_path_with_three_live_survivors(client):
    response = client.post(
        "/solve_river_from_path",
        json=_river_body(
            ["call_or_check", "call_or_check", "call_or_check"],
            ["call_or_check", "call_or_check", "call_or_check"],
            ["call_or_check", "call_or_check", "call_or_check"],
            players=3,
        ),
    )
    assert response.status_code == 422


def test_solve_river_from_path_players_2_and_3_do_not_share_a_cache_entry(client):
    r2 = client.post(
        "/solve_river_from_path",
        json=_river_body(
            ["raise", "call_or_check"],
            ["call_or_check", "call_or_check"],
            ["call_or_check", "call_or_check"],
            players=2,
        ),
    )
    r3 = client.post(
        "/solve_river_from_path",
        json=_river_body(
            ["raise", "fold", "call_or_check"],
            ["call_or_check", "call_or_check"],
            ["call_or_check", "call_or_check"],
            players=3,
        ),
    )
    assert r2.status_code == 200
    assert r3.status_code == 200
    assert len(api_main._river_path_cache) == 2


# ---------------------------------------------------------------------------
# M44: POST /solve_turn_multiway_from_path — the multiway analog of
# /solve_turn_from_path, for a real preflop path that leaves 3+ live
# positions at the flop (a case /solve_turn_from_path structurally can't
# serve — see api/main.py's module docstring). Reuses _THREE_LIVE_PATH
# and board="2h6d9c" from the M42 section above.
# ---------------------------------------------------------------------------

FAST_MULTIWAY_TURN_PATH_FLOP_ITERATIONS = 20
_MULTIWAY_TURN_CARD = "Ts"

_THREE_LIVE_FLOP_PATH = ["call_or_check", "call_or_check", "call_or_check"]  # checked through, all 3 stay live


def _multiway_turn_body(
    preflop_action_path,
    flop_action_path=_THREE_LIVE_FLOP_PATH,
    turn_card=_MULTIWAY_TURN_CARD,
    stack_bb=100.0,
    board="2h6d9c",
    iterations=_PATH_ITERATIONS,
    flop_iterations=FAST_MULTIWAY_TURN_PATH_FLOP_ITERATIONS,
    players=3,
):
    return {
        "stack_bb": stack_bb,
        "preflop_action_path": preflop_action_path,
        "board": board,
        "flop_action_path": flop_action_path,
        "turn_card": turn_card,
        "iterations": iterations,
        "flop_iterations": flop_iterations,
        "players": players,
    }


def test_solve_turn_multiway_from_path_returns_200_for_a_real_three_live_line(client):
    response = client.post("/solve_turn_multiway_from_path", json=_multiway_turn_body(_THREE_LIVE_PATH))
    assert response.status_code == 200
    body = response.json()
    assert body["players"] == 3
    assert set(body["positions"]) == {"BTN", "SB", "BB"}
    assert body["board"] == "2h6d9c"
    assert body["turn_card"] == "Ts"
    assert body["flop_iterations"] == FAST_MULTIWAY_TURN_PATH_FLOP_ITERATIONS
    # Either a real turn decision or a genuine all-in-already terminal —
    # both are legitimate outcomes of a real solve at this tiny fixture
    # scale; assert the shape is self-consistent either way.
    if body["is_terminal"]:
        assert body["strategy"] == {}
        assert body["trained"] == {}
    else:
        assert len(body["strategy"]) > 0
        for freqs in body["strategy"].values():
            assert sum(freqs.values()) == pytest.approx(1.0, abs=1e-6)
        assert set(body["trained"].keys()) == set(body["strategy"].keys())


def test_solve_turn_multiway_from_path_rejects_a_two_survivor_preflop_path(client):
    # BTN opens, SB folds, BB calls -> only 2 live; this endpoint's own
    # job is genuinely 3+ live positions — /solve_turn_from_path already
    # serves the 2-survivor case.
    response = client.post(
        "/solve_turn_multiway_from_path",
        json=_multiway_turn_body(["raise", "fold", "call_or_check"], flop_action_path=["call_or_check", "call_or_check"]),
    )
    assert response.status_code == 422
    assert "solve_turn_from_path" in response.json()["detail"]


def test_solve_turn_multiway_from_path_rejects_a_non_terminal_preflop_path(client):
    response = client.post("/solve_turn_multiway_from_path", json=_multiway_turn_body(["call_or_check"]))
    assert response.status_code == 422


def test_solve_turn_multiway_from_path_rejects_a_non_terminal_flop_path(client):
    response = client.post(
        "/solve_turn_multiway_from_path", json=_multiway_turn_body(_THREE_LIVE_PATH, flop_action_path=["call_or_check"])
    )
    assert response.status_code == 422


def test_solve_turn_multiway_from_path_rejects_an_illegal_turn_card(client):
    # "9c" is already on the board (board="2h6d9c").
    response = client.post(
        "/solve_turn_multiway_from_path", json=_multiway_turn_body(_THREE_LIVE_PATH, turn_card="9c")
    )
    assert response.status_code == 422


def test_solve_turn_multiway_from_path_rejects_a_malformed_turn_card(client):
    response = client.post(
        "/solve_turn_multiway_from_path", json=_multiway_turn_body(_THREE_LIVE_PATH, turn_card="TsJd")
    )
    assert response.status_code == 422


def test_solve_turn_multiway_from_path_rejects_flop_iterations_above_the_cap(client):
    response = client.post(
        "/solve_turn_multiway_from_path",
        json=_multiway_turn_body(
            _THREE_LIVE_PATH, flop_iterations=api_config.MAX_MULTIWAY_TURN_PATH_QUERY_FLOP_ITERATIONS + 1
        ),
    )
    assert response.status_code == 422


def test_solve_turn_multiway_from_path_reuses_the_same_cache_entry_across_turn_cards_and_flop_lines(client):
    base = _multiway_turn_body(_THREE_LIVE_PATH)
    client.post("/solve_turn_multiway_from_path", json=base)
    assert len(api_main._turn_multiway_path_cache) == 1

    different_turn_card = client.post("/solve_turn_multiway_from_path", json={**base, "turn_card": "4c"})
    assert different_turn_card.status_code == 200
    assert len(api_main._turn_multiway_path_cache) == 1


def test_solve_turn_multiway_from_path_partitions_different_preflop_legs_into_separate_cache_entries(client):
    client.post("/solve_turn_multiway_from_path", json=_multiway_turn_body(_THREE_LIVE_PATH))
    # A different (still genuinely 3-live) preflop line: BTN raises, SB
    # calls, BB calls.
    client.post(
        "/solve_turn_multiway_from_path",
        json=_multiway_turn_body(["raise", "call_or_check", "call_or_check"]),
    )
    assert len(api_main._turn_multiway_path_cache) == 2


def test_solve_turn_multiway_from_path_builds_and_trains_an_unsampled_but_legal_card(
    client,
):
    # The real, structural gap M44 exists to close: solve_flop_turn_
    # multiway's own chance_data only contains (terminal, card) pairs
    # MCCFR actually sampled while solving — a real, legal turn card can
    # easily be one it never happened to sample.
    #
    # M75 CHANGED WHAT HAPPENS NEXT, and this test with it. It used to
    # assert the branch came back UNTRAINED, which was the documented
    # behaviour. Measured through /advise at production settings, that
    # tradeoff turned out to be total rather than occasional: a real
    # 6-max turn node reported 0 of 132 combos trained, every strategy
    # exactly uniform, and the river the same. The branch is now SOLVED
    # on demand, so this asserts the fix rather than the limitation.
    # Drive this through the
    # real HTTP layer, not just the engine-level ensure_flop_turn_
    # multiway_branch tests in test_solver.py: populate the cache with
    # one real request, inspect the cached StrategyResult's own
    # chance_data to find a real, definitely-never-sampled card for the
    # SAME flop terminal, then request exactly that card.
    base = _multiway_turn_body(_THREE_LIVE_PATH)
    first = client.post("/solve_turn_multiway_from_path", json=base)
    assert first.status_code == 200
    assert len(api_main._turn_multiway_path_cache) == 1

    result = next(iter(api_main._turn_multiway_path_cache.entries.values()))
    board_cards = tuple(api_main.parse_cards("2h6d9c"))

    # Find the real terminal object this flop_action_path resolves to,
    # then a card that terminal's own chance_data has never sampled.
    _actions, flop_terminal = api_solving._resolve_action_path(result.root, _THREE_LIVE_FLOP_PATH)
    already_sampled = {card for (tid, card) in result.chance_data if tid == id(flop_terminal)}
    unsampled_card = next(c for c in remaining_deck(board_cards) if c not in already_sampled)

    response = client.post(
        "/solve_turn_multiway_from_path", json={**base, "turn_card": str(unsampled_card)}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["turn_card"] == str(unsampled_card)
    # Only meaningful if this specific card reaches a real turn decision
    # (not an all-in-already-reused terminal, which reports strategy={}
    # regardless of whether the branch was freshly built or sampled).
    if not body["is_terminal"]:
        assert len(body["trained"]) > 0
        # At least SOME combo must now be genuinely trained — the whole
        # point of M75. Not all of them: MCCFR samples paths, so not every
        # combo reaches every node, and `trained` reports that honestly
        # per combo instead of pretending otherwise.
        assert any(body["trained"].values()), (
            "an on-demand branch must be solved, not returned uniform — if this "
            "fails, MULTIWAY_BRANCH_TRAIN_ITERATIONS is likely 0"
        )
        # And a trained combo must carry a real, non-uniform strategy.
        trained_combos = [combo for combo, ok in body["trained"].items() if ok]
        spreads = [
            max(body["strategy"][combo].values()) - min(body["strategy"][combo].values())
            for combo in trained_combos
        ]
        assert max(spreads) > 1e-9, "every trained combo is still exactly uniform"


# ---------------------------------------------------------------------------
# M95 — no advice may name a bet the player cannot make
# ---------------------------------------------------------------------------


def _oversized_actions(body):
    """Every action in a response whose size exceeds the money behind."""
    stack = body.get("effective_stack_bb")
    if stack is None:
        return []
    rows = list(body.get("strategy", {}).values())
    hero = body.get("hero")
    if hero and hero.get("strategy"):
        rows.append(hero["strategy"])
    return sorted(
        {
            action
            for row in rows
            for action in row
            if ":" in action and float(action.split(":", 1)[1]) > stack + 1e-9
        }
    )


@pytest.mark.parametrize("stack_bb", [100.0, 97.5, 60.0, 43.0, 22.0, 8.0])
@pytest.mark.parametrize(
    "preflop_action_path",
    [
        ["call_or_check", "call_or_check"],
        ["raise", "call_or_check"],
        ["raise", "raise", "call_or_check"],
    ],
)
def test_advise_never_offers_a_bet_larger_than_the_stack(client, stack_bb, preflop_action_path):
    """The product-level statement of M95's invariant.

    The library canonicalizes stack depth into buckets, and every action
    size in a solved tree comes from the depth the tree was built at. When
    that rounded UP, ordinary spots produced impossible advice — a 100bb
    limped pot leaves 99bb behind and came back `all_in:100.00`.

    Swept across stacks and lines rather than spot-checked, because the
    failing case was not exotic: it was the default stack on the simplest
    preflop line there is, and it survived every hand-written example.
    Asserted here at the response boundary, where a user would see it,
    rather than only on the rounding function — a future change anywhere
    between the two would otherwise reintroduce it silently.
    """
    response = client.post(
        "/advise",
        json=_advise_body(
            preflop_action_path=preflop_action_path,
            hero_cards="AsKh",
            board="Kd7c2h",
            players=2,
            stack_bb=stack_bb,
        ),
    )
    assert response.status_code == 200
    body = response.json()
    assert not _oversized_actions(body), (
        f"advice names bets the player cannot make with "
        f"{body['effective_stack_bb']}bb: {_oversized_actions(body)}"
    )


# ---------------------------------------------------------------------------
# M98 — the sizing axis is unreliable multiway, and now says so
# ---------------------------------------------------------------------------


def test_multiway_preflop_advice_flags_its_sizing_as_unreliable(client):
    """`api/config.py` has recorded since M67 that multiway preflop is
    "trustworthy for 'is this hand playable from this seat' and NOT for
    'which sizing'". No response ever carried that, so a 6-max player
    asking whether to raise or shove was answered with exactly the same
    confidence as one asking whether to play at all.
    """
    response = client.post(
        "/advise",
        json=_advise_body(preflop_action_path=[], hero_cards="AsAh", players=6),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["street"] == "preflop"
    assert body["sizing_confidence"] == "low"
    assert body["sizing_confidence_reason"]
    # The fold-vs-play half is the part that IS converged at 6-max, so the
    # overall verdict must NOT be dragged down with it.
    assert body["solver_confidence"] == "high"


def test_heads_up_preflop_does_not_carry_the_sizing_caveat(client):
    """The caveat has to discriminate — a field that fires everywhere
    tells a user nothing about which answers to distrust.

    **What this pins is current behaviour, not a proof that heads-up
    sizing is sound (M99).** The original version of this test argued
    "heads-up preflop is solved exactly, so a low verdict would be
    decoration". Exact SOLVING does not repair the defect M98 found,
    which is in terminal PRICING: heads-up preflop has the same three
    unmodelled streets, and a called 2.5bb raise is scored `equity * 5.5`
    as though the hand ended there.

    Heads-up sizing looks right (AA jams ~3%, matching poker intuition)
    and that is the entire basis for trusting it — which is exactly the
    kind of evidence this project keeps having to retract. It is also not
    measurable in-repo: there is no deeper preflop tree to compare
    against, so no reference exists. Recorded as an open question rather
    than asserted in either direction.
    """
    response = client.post(
        "/advise",
        json=_advise_body(preflop_action_path=[], hero_cards="AsAh", players=2),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["sizing_confidence"] == "high"
    assert body["sizing_confidence_reason"] is None


def test_the_sizing_caveat_is_scoped_to_preflop(client):
    """Postflop multiway is solved per request against a real derived
    range and carries its own `trained`/`range_confidence` signals; the
    unconverged sizing split is a property of the sampled PREFLOP solve.
    A caveat that fired on every multiway response would tell a user
    nothing about which answers to distrust."""
    response = client.post(
        "/advise",
        json=_advise_body(
            preflop_action_path=["raise", "call_or_check"],
            hero_cards="AsKh",
            board="Kd7c2h",
            players=2,
        ),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["street"] == "flop"
    assert body["sizing_confidence"] == "high"


def test_nine_max_carries_both_warnings_without_either_masking_the_other(client):
    """9-max is low-confidence overall AND has the sizing problem. The two
    fields are independent, so a consumer rendering only one still shows
    something true — but both must be present, or the more specific
    warning silently disappears behind the general one."""
    response = client.post(
        "/advise",
        json=_advise_body(preflop_action_path=[], hero_cards="AsAh", players=9),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["solver_confidence"] == "low"
    assert body["solver_confidence_reason"]
    assert body["sizing_confidence"] == "low"
    assert body["sizing_confidence_reason"]
    assert body["solver_confidence_reason"] != body["sizing_confidence_reason"]


# ---------------------------------------------------------------------------
# M101 — the affordability guarantee, restored at every node
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,body",
    [
        ("preflop open", dict(preflop_action_path=[], hero_cards="AsKh", players=2)),
        ("preflop facing 3bet", dict(preflop_action_path=["raise", "raise"],
                                     hero_cards="QsQd", players=2)),
        ("flop opening", dict(preflop_action_path=["raise", "call_or_check"],
                              hero_cards="5c4d", board="Kd7c2h", players=2)),
        ("flop facing a bet", dict(preflop_action_path=["raise", "call_or_check"],
                                   flop_action_path=["raise"],
                                   hero_cards="5c4d", board="Kd7c2h", players=2)),
        ("flop after a check", dict(preflop_action_path=["raise", "call_or_check"],
                                    flop_action_path=["call_or_check"],
                                    hero_cards="5c4d", board="Kd7c2h", players=2)),
        ("limped flop", dict(preflop_action_path=["call_or_check", "call_or_check"],
                             hero_cards="AsKh", board="Kd7c2h", players=2)),
        # M143/F39: turn and river were absent from this sweep, and the bound
        # was broken at EVERY one of them — 8 of 8 turn-facing-a-bet nodes
        # named `all_in:97.50` while reporting `max_affordable_bb: 85.0`.
        # M101 restored the guarantee "at every node, on every street" and
        # verified it on preflop and flop only; all eleven turn/river
        # responses omitted the field entirely and fell through to
        # main.py's `effective_stack_bb` default, i.e. the original bug.
        ("turn opening", dict(preflop_action_path=["raise", "call_or_check"],
                              flop_action_path=["call_or_check", "call_or_check"],
                              turn_card="Ts",
                              hero_cards="5c4d", board="Kd7c2h", players=2)),
        ("river opening", dict(preflop_action_path=["raise", "call_or_check"],
                               flop_action_path=["call_or_check", "call_or_check"],
                               turn_card="Ts",
                               turn_action_path=["call_or_check", "call_or_check"],
                               river_card="4c",
                               hero_cards="5c4d", board="Kd7c2h", players=2)),
    ],
)
def test_no_advice_names_a_bet_larger_than_max_affordable(client, label, body):
    """M95's guarantee — no advice names a bet the player cannot make —
    asserted where it was previously unverifiable.

    M95 swept only each street's OPENING decision, and there
    `effective_stack_bb` happens to mean "money behind", so comparing
    sizes against it worked. M101's audit found that one decision later
    the same field means the SHORTEST remaining stack once someone has
    bet, and that preflop it is the stack net of blinds while preflop
    sizes are total commitment. A real flop node reports
    `effective_stack_bb: 85.0` beside `all_in:97.50` — both correct, not
    comparable, and enough to make the guarantee unverifiable exactly
    where a player is most likely to be looking.

    `max_affordable_bb` is the one bound every size can be checked
    against, so this sweeps mid-street nodes too.
    """
    response = client.post("/advise", json=_advise_body(stack_bb=100.0, **body))
    assert response.status_code == 200, response.json()
    payload = response.json()
    if payload["is_terminal"]:
        return
    bound = payload["max_affordable_bb"]
    assert bound > 0, f"{label}: no affordability bound reported"

    rows = list(payload["strategy"].values())
    hero = payload.get("hero") or {}
    if hero.get("strategy"):
        rows.append(hero["strategy"])
    oversized = sorted(
        {
            action
            for row in rows
            for action in row
            if ":" in action and float(action.split(":", 1)[1]) > bound + 1e-9
        }
    )
    assert not oversized, (
        f"{label}: advice names bets above the {bound}bb the player can commit: {oversized}"
    )


def test_the_affordability_bound_survives_a_turn_node_facing_a_bet(client, monkeypatch):
    """M143 / F39. The bug M101 believed it had already fixed everywhere.

    M101 restored the guarantee "at every node, on every street" and
    swept preflop and flop only. **All eleven turn and river responses
    omitted `max_affordable_bb` entirely**, falling through to
    api/main.py's `raw.get("max_affordable_bb", raw["effective_stack_bb"])`
    default — i.e. exactly the pre-M101 behaviour. Measured through
    /advise at production settings, 8 of 8 turn-facing-a-bet nodes named
    `all_in:97.50` while reporting `max_affordable_bb: 85.0`: advice for
    a bet the player is told they cannot make.

    This case needs its own fixture work, which is also why the sweep
    missed it: `_disable_prewarm_and_clear_cache` sets
    `FLOP_TURN_RAISE_SIZES = ()` for speed, so under the suite the turn
    tree offers only check and all-in and no mid-street turn node is
    reachable at all. Restoring one real size is what makes the node —
    and therefore the bug — exist in a test.
    """
    monkeypatch.setattr(api_config, "FLOP_TURN_RAISE_SIZES", (2.5,))
    monkeypatch.setattr(api_config, "FLOP_TURN_MAX_RAISES", 2)
    body = _advise_body(
        preflop_action_path=["raise", "call_or_check"],
        flop_action_path=["call_or_check", "call_or_check"],
        turn_card="Ts", turn_action_path=["raise"],
        hero_cards="5c4d", board="Kd7c2h", players=2, stack_bb=100.0,
    )
    response = client.post("/advise", json=body)
    assert response.status_code == 200, response.json()
    payload = response.json()
    assert payload["street"] == "turn"
    bound = payload["max_affordable_bb"]

    rows = list(payload["strategy"].values())
    hero = payload.get("hero") or {}
    if hero.get("strategy"):
        rows.append(hero["strategy"])
    oversized = sorted({
        action for row in rows for action in row
        if ":" in action and float(action.split(":", 1)[1]) > bound + 1e-9
    })
    assert not oversized, (
        f"turn node facing a bet names bets above the {bound}bb bound: {oversized}"
    )
    # And the bound must be the street-entry stack, not the post-bet
    # remainder — if they were equal the guarantee would be vacuous here,
    # which is precisely how the bug hid.
    assert bound > payload["effective_stack_bb"], (
        "the bound equals the post-bet remainder again; that is the shape of "
        "F39 and it makes every size at this node look unaffordable"
    )


def test_the_affordability_bound_survives_a_multiway_turn_facing_a_bet(client, monkeypatch):
    """M147. The MULTIWAY half of M143's fix, which had no test.

    F39 patched `_query_turn_multiway_from_path` alongside the heads-up
    path, but only heads-up was swept afterwards — so the multiway
    responses were fixed on the strength of reading the code, which is
    exactly the habit F36 through F42 kept punishing.

    Reachable only with the tree restored, the same reason the heads-up
    sibling needs it: `_disable_prewarm_and_clear_cache` sets
    `MULTIWAY_FLOP_RAISE_SIZES = ()` for speed, so under the suite the
    multiway turn offers only check and all-in and no mid-street node
    exists to test.

    Screened live at production settings across 8 multiway turn/river
    nodes facing a bet (3-max and 6-max, two boards): no violations, no
    untrained nodes. This pins that result.
    """
    monkeypatch.setattr(api_config, "MULTIWAY_FLOP_RAISE_SIZES", (2.5,))
    monkeypatch.setattr(api_config, "MULTIWAY_FLOP_MAX_RAISES", 2)
    body = _advise_body(
        preflop_action_path=["raise", "call_or_check", "call_or_check"],
        flop_action_path=["call_or_check", "call_or_check", "call_or_check"],
        turn_card="Ts", turn_action_path=["raise"],
        hero_cards="5c4d", board="Kd7c2h", players=3, stack_bb=100.0,
    )
    response = client.post("/advise", json=body)
    assert response.status_code == 200, response.json()
    payload = response.json()
    assert payload["street"] == "turn"
    assert payload["players"] == 3
    bound = payload["max_affordable_bb"]

    rows = list(payload["strategy"].values())
    hero = payload.get("hero") or {}
    if hero.get("strategy"):
        rows.append(hero["strategy"])
    oversized = sorted({
        action for row in rows for action in row
        if ":" in action and float(action.split(":", 1)[1]) > bound + 1e-9
    })
    assert not oversized, (
        f"multiway turn facing a bet names bets above the {bound}bb bound: {oversized}"
    )
    assert bound > payload["effective_stack_bb"], (
        "the bound equals the post-bet remainder again — F39's shape, which makes "
        "every size at this node look unaffordable"
    )


def test_max_affordable_is_not_silently_equal_to_effective_stack(client):
    """The field has to earn its existence.

    If it merely mirrored `effective_stack_bb` everywhere it would be
    noise, and the bug it exists to expose would still be invisible. So
    pin the case that motivated it: a mid-street node where the two
    genuinely differ, and where comparing sizes against the WRONG one
    reports a violation that is not real.
    """
    response = client.post(
        "/advise",
        json=_advise_body(
            preflop_action_path=["raise", "call_or_check"],
            flop_action_path=["raise"],
            hero_cards="5c4d", board="Kd7c2h", players=2, stack_bb=100.0,
        ),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["max_affordable_bb"] > payload["effective_stack_bb"], (
        "this node is supposed to be one where the two fields diverge — if they now "
        "agree, either the tree changed or the divergence was fixed at the source, "
        "and this test should be revisited rather than deleted"
    )


def test_a_malformed_action_path_is_rejected_without_paying_for_a_solve(client):
    """M101. A cold 6-max request whose preflop path leaves four players
    still to act took **76.2 seconds to return a 422** — nearly all of it
    solving a game the rejection never reads.

    Two causes, both "the cache makes this free" reasoning that holds
    only after someone has already paid: the live-player count used to
    fetch the solve to walk a tree, and the path-shape check sat behind
    the solve rather than in front of it. Both now use a throwaway tree.

    Asserted as a BUDGET, not a wall-clock threshold — this machine
    drifts ~1.7x between sessions, so a seconds-based bound would either
    flake or be set so loose it proves nothing. Counting solves is exact:
    a rejected request must not populate the expensive preflop cache at
    all.
    """
    caches._SolveCache.clear_all()
    response = client.post(
        "/advise",
        json=_advise_body(
            preflop_action_path=["raise", "call_or_check"],  # leaves 4 still to act
            hero_cards="AsAh", board="Kd7c2h", players=6, stack_bb=100.0,
        ),
    )
    assert response.status_code == 422
    assert "does not close the preflop betting" in response.json()["detail"]
    assert len(caches._multiway_cache.entries) == 0, (
        "a rejected request solved the 6-max preflop tree anyway — the validation "
        "is back behind the expensive call"
    )


def test_a_valid_multiway_request_still_reaches_the_solver(client):
    """The other half: making rejection cheap must not make acceptance
    broken. A path that DOES close the round still routes through and
    gets real advice."""
    caches._SolveCache.clear_all()
    response = client.post(
        "/advise",
        json=_advise_body(preflop_action_path=[], hero_cards="AsAh", players=6, stack_bb=100.0),
    )
    assert response.status_code == 200
    assert response.json()["street"] == "preflop"
    assert len(caches._multiway_cache.entries) > 0, "the real path no longer solves anything"


# ---------------------------------------------------------------------------
# M102 — a typo must not become a confident answer to a different question
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,body,offending",
    [
        (
            "hero_cards misspelled",
            dict(stack_bb=100.0, preflop_action_path=[], hero_card="AsAh", players=2),
            "hero_card",
        ),
        (
            "players misspelled",
            dict(stack_bb=100.0, preflop_action_path=[], hero_cards="AsAh", player=6),
            "player",
        ),
        (
            "a field that never existed",
            dict(stack_bb=100.0, preflop_action_path=[], hero_cards="AsAh",
                 players=2, position="BTN"),
            "position",
        ),
    ],
)
def test_an_unknown_request_field_is_rejected_by_name(client, label, body, offending):
    """M102's audit measured what these used to do, and it was the worst
    failure shape this project has:

        {"hero_card": "AsAh", ...}  -> 200, hero: null
        {"player": 6, ...}          -> 200, players: 2

    The first silently drops the hand the user asked about and returns a
    range chart. The second answers a 6-max question with heads-up
    advice. Both look exactly like correct responses.

    `position` is in here deliberately: it is not a field of
    `AdviseRequest` (the acting seat is inferred from the action path),
    yet CLAUDE.md described it as an input and every probe written during
    this session sent it. It did nothing, silently, for the whole
    session.
    """
    response = client.post("/advise", json=body)
    assert response.status_code == 422, (
        f"{label}: silently accepted — this is how a typo becomes wrong advice"
    )
    assert offending in str(response.json()["detail"]), (
        f"{label}: rejected, but the message does not name `{offending}`, so a "
        "caller cannot tell which field to fix"
    )


def test_a_correct_request_is_unaffected_by_strictness(client):
    """The other half: rejecting extras must not reject the real thing."""
    response = client.post(
        "/advise",
        json=_advise_body(preflop_action_path=[], hero_cards="AsAh", players=2, stack_bb=100.0),
    )
    assert response.status_code == 200
    assert (response.json().get("hero") or {}).get("cards") == "AsAh"


# ---------------------------------------------------------------------------
# M103 — the app itself is served, and the mount does not eat the API
# ---------------------------------------------------------------------------


def test_the_built_frontend_is_actually_served(client):
    """Nothing covered this before M103's UI sweep, and it is the one
    failure that would break every tab at once while leaving the suite
    green: the whole test suite talks to `/advise` and `/solve` directly
    and never asks for the page a user opens.

    Skipped rather than failed when `frontend/dist` is absent — a fresh
    clone has not run `npm run build`, and a test that punishes that
    would just get marked xfail and stop meaning anything.
    """
    if not api_config.FRONTEND_DIST_DIR.is_dir():
        pytest.skip("frontend/dist not built — nothing to serve")
    response = client.get("/")
    assert response.status_code == 200
    body = response.text
    assert "<div id=\"root\"" in body or "<div id=root" in body, (
        "served something, but not the app shell — the mount may be pointing at "
        "the wrong directory"
    )
    assert "/assets/" in body, "the shell references no bundle, so the page would render blank"


def test_the_static_mount_does_not_shadow_the_api(client):
    """The mount is registered at "/" with `html=True`, which makes it a
    catch-all. Starlette matches routes in registration order, so it is
    registered last on purpose — and "on purpose" is exactly the kind of
    ordering that a later edit reshuffles without noticing.

    If this fails, the API still exists but every endpoint returns the
    HTML page instead of JSON, and the UI breaks in a way that looks like
    a frontend bug.
    """
    response = client.get("/solve/100")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json"), (
        "an API route is being served by the static mount — check that the mount "
        "is still registered LAST in api/main.py"
    )


# ---------------------------------------------------------------------------
# M104 — the thundering herd M92 missed
# ---------------------------------------------------------------------------


def test_concurrent_cold_requests_solve_the_preflop_tree_once(client, monkeypatch):
    """M92 replaced check-then-compute with single-flight everywhere it
    found it, and missed `_get_or_solve_preflop_raw`.

    Measured on the real endpoint before the fix: **8 concurrent cold
    requests ran 8 real `solve_preflop` calls** where one was needed.
    Every thread checked the cache, found it empty, and solved. This is
    the heads-up preflop solve that every heads-up POSTFLOP request
    depends on first, so a burst of traffic on a cold cache paid for it
    N times over — exactly the scenario M92 exists to prevent.

    Nothing caught it because the suite never issues two requests at
    once, and duplicated work is invisible from a single caller: every
    response is correct, just paid for repeatedly.

    Counts SOLVES rather than timing, so it states the property directly
    and cannot flake on a machine that drifts ~1.7x between sessions.
    """
    calls = []
    calls_lock = threading.Lock()
    real_solve = api_solving.solve_preflop

    def counting_solve(*args, **kwargs):
        with calls_lock:
            calls.append(1)
        return real_solve(*args, **kwargs)

    monkeypatch.setattr(api_solving, "solve_preflop", counting_solve)
    caches._SolveCache.clear_all()

    # A FLOP request: the preflop path closes the round, and the flop
    # advice then depends on the preflop solve — which is the shared,
    # expensive thing the herd used to duplicate.
    body = _advise_body(preflop_action_path=["raise", "call_or_check"],
                        hero_cards="AsKh", board="Kd7c2h", players=2, stack_bb=100.0)
    statuses = []
    statuses_lock = threading.Lock()

    def hammer():
        response = client.post("/advise", json=body)
        with statuses_lock:
            statuses.append(response.status_code)

    threads = [threading.Thread(target=hammer) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert statuses == [200] * 6, f"not every concurrent request succeeded: {statuses}"
    assert len(calls) == 1, (
        f"{len(calls)} preflop solves ran for 6 concurrent requests on one key — "
        "single-flight is broken again; see _get_or_solve_preflop_raw"
    )


# ---------------------------------------------------------------------------
# M107 — /advise is a front door, not a second implementation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,action_path,board",
    [
        ("raise and call", ["raise", "call_or_check"], "Kd7c2h"),
        ("limped", ["call_or_check", "call_or_check"], "Kd7c2h"),
        ("3bet pot", ["raise", "raise", "call_or_check"], "Qs8d3c"),
    ],
)
def test_advise_agrees_exactly_with_the_endpoint_it_replaced(client, label, action_path, board):
    """`api/solving.py` states that `/advise` "deliberately delegates
    rather than reimplements: every cell's own cache, cap constant, and
    solver choice stays exactly as its sibling endpoint already had it"
    and that it is "a unified FRONT DOOR, not a second implementation to
    keep in sync".

    That is a testable claim, and until M107 nothing tested it. If the two
    disagree, a user's advice depends on which URL they happened to call —
    and the deprecated route is still live, so both are reachable.

    Asserted as EXACT equality, not approximate: delegation means the same
    solve, so any difference at all means a second implementation has
    appeared. Measured worst delta at the time of writing: 0.0.
    """
    caches._SolveCache.clear_all()
    deprecated = client.post(
        "/solve_flop_from_path",
        json={"stack_bb": 100.0, "action_path": action_path, "board": board, "players": 2},
    )
    caches._SolveCache.clear_all()
    front_door = client.post(
        "/advise",
        json={"stack_bb": 100.0, "preflop_action_path": action_path,
              "board": board, "players": 2},
    )

    assert deprecated.status_code == front_door.status_code == 200
    old, new = deprecated.json(), front_door.json()
    assert old["position"] == new["position"]
    assert old["pot"] == new["pot"]
    assert old["effective_stack_bb"] == new["effective_stack_bb"]
    assert set(old["strategy"]) == set(new["strategy"]), (
        f"{label}: the two routes solved different combo pools"
    )
    for combo, row in old["strategy"].items():
        assert row == new["strategy"][combo], (
            f"{label}: {combo} differs between /solve_flop_from_path and /advise — "
            "the front door has become a second implementation"
        )


@pytest.mark.parametrize(
    "label,body",
    [
        ("preflop", dict(preflop_action_path=[], hero_cards="AsKh", players=2)),
        ("flop", dict(preflop_action_path=["raise", "call_or_check"],
                      hero_cards="AsKh", board="Kd7c2h", players=2)),
        ("flop mid-street", dict(preflop_action_path=["raise", "call_or_check"],
                                 flop_action_path=["raise"], hero_cards="5c4d",
                                 board="Kd7c2h", players=2)),
        ("3-max preflop", dict(preflop_action_path=[], hero_cards="JsJd", players=3)),
    ],
)
def test_the_same_question_gets_the_same_answer_twice(client, label, body):
    """Caches are cleared between the two calls, so the second genuinely
    re-solves rather than returning the first one's stored answer.

    Without that clearing this test would pass trivially — it would be
    reading one solve twice. What it actually guards is the absence of
    accumulated state: a module-level RNG advanced by the previous solve,
    or a structure mutated in place, would make advice depend on whatever
    the server happened to do beforehand. Invisible from any single call,
    and 3-max is included specifically because it uses SAMPLED MCCFR,
    where a mis-seeded run is exactly the failure this catches.
    """
    answers = []
    for _ in range(2):
        caches._SolveCache.clear_all()
        response = client.post("/advise", json=_advise_body(stack_bb=100.0, **body))
        assert response.status_code == 200
        payload = response.json()
        answers.append((payload["strategy"], (payload.get("hero") or {}).get("strategy")))

    assert answers[0] == answers[1], (
        f"{label}: two cold solves of the same spot disagreed — the solve is "
        "carrying state between runs"
    )


# ---------------------------------------------------------------------------
# M108 — what a failing solve must never do
# ---------------------------------------------------------------------------


def test_a_failing_solve_never_returns_confident_advice(client, monkeypatch):
    """The worst possible outcome for this product, asserted directly.

    Every other test in this suite exercises the happy path or a request
    rejected BEFORE solving. Nothing asked what happens when the solver
    itself raises — and the failure mode that would matter is not an
    ugly error, it is a **200 with fabricated advice**, which is the
    thing this whole codebase's honesty machinery exists to prevent.

    So: an exception from the solver must propagate as a server error,
    never be swallowed into a plausible-looking answer. Deliberately NOT
    converted into a friendly 422 — dressing a bug up as a validation
    failure would hide it.
    """
    import poker_solver.library as library

    state = {"calls": 0}
    real_solve = library.solve_flop

    def exploding_solve(*args, **kwargs):
        state["calls"] += 1
        raise RuntimeError("simulated solver failure")

    monkeypatch.setattr(library, "solve_flop", exploding_solve)
    caches._SolveCache.clear_all()

    body = _advise_body(preflop_action_path=["raise", "call_or_check"],
                        hero_cards="AsKh", board="Kd7c2h", players=2, stack_bb=100.0)
    with pytest.raises(RuntimeError, match="simulated solver failure"):
        client.post("/advise", json=body)
    assert state["calls"] > 0, (
        "the injected failure never fired — this test proves nothing. "
        "Check that the flop path still solves through poker_solver.library."
    )


def test_a_failed_solve_does_not_corrupt_the_next_answer(client, monkeypatch):
    """Recovery is not just "the next request returns 200" — a
    partially-written cache entry would also return 200, with advice
    derived from it.

    So the post-failure answer is compared against a CLEAN one, computed
    before any failure happened. They must be identical.
    """
    import poker_solver.library as library

    real_solve = library.solve_flop
    body = _advise_body(preflop_action_path=["raise", "call_or_check"],
                        hero_cards="AsKh", board="Kd7c2h", players=2, stack_bb=100.0)

    caches._SolveCache.clear_all()
    clean = client.post("/advise", json=body).json()

    state = {"calls": 0}

    def fail_once(*args, **kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("simulated solver failure")
        return real_solve(*args, **kwargs)

    monkeypatch.setattr(library, "solve_flop", fail_once)
    caches._SolveCache.clear_all()
    with pytest.raises(RuntimeError):
        client.post("/advise", json=body)

    # Deliberately NOT clearing the caches here: the point is to see
    # whatever the failed request left behind.
    recovered = client.post("/advise", json=body)
    assert recovered.status_code == 200, "the server stayed broken after one failed solve"
    assert recovered.json()["strategy"] == clean["strategy"], (
        "the answer after a failure differs from a clean one — a partial entry survived"
    )


# ---------------------------------------------------------------------------
# M109 — every input that changes the answer must reach the cache key
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,override",
    [
        ("stack_bb", {"stack_bb": 60.0}),
        ("board", {"board": "Qs8d3c"}),
        ("hero_cards", {"hero_cards": "7h2d"}),
        ("preflop_action_path", {"preflop_action_path": ["call_or_check", "call_or_check"]}),
        ("flop_action_path", {"flop_action_path": ["raise"]}),
    ],
)
def test_a_warm_cache_never_answers_a_different_question(client, field, override):
    """M76's bug, generalised into an invariant.

    `hero_cards` changed the solved POOL but was absent from the
    path-query cache keys, so on a shared server the FIRST asker for a
    spot fixed the answer and everyone after received advice for someone
    else's hand — correct-looking, and wrong. Nothing checked whether any
    other field had the same problem.

    It is structurally invisible to the rest of this suite, whose fixture
    clears caches between tests: every test is always the first caller.
    So this one deliberately does NOT clear between the two requests.

    The method distinguishes "missing from the key" from "genuinely does
    not affect the answer": run the variant warm (after a baseline has
    populated the cache) and cold (alone). If they differ, the key is
    incomplete. Verified by mutation — reintroducing M76's bug makes the
    `hero_cards` case fail, and only that case.

    M158 changed what `hero_cards` may do. A request now warm-starts from
    an earlier solve of the same canonical spot and refines it, which is
    what took a repeat flop request from ~13s to ~2.7s. Hero's answer may
    therefore differ slightly from a cold solve — measured 0.0037-0.0147,
    against seed-only noise of 0.024-0.112 for the identical solve. So
    the hero case asserts a TOLERANCE and that hero still gets an answer
    of their own; every other field still asserts exact equality, because
    a difference there means a different QUESTION was answered.
    """
    base = _advise_body(preflop_action_path=["raise", "call_or_check"],
                        hero_cards="AsKh", board="Kd7c2h", players=2, stack_bb=100.0)
    variant = {**base, **override}

    def answer(body):
        response = client.post("/advise", json=body)
        assert response.status_code == 200, response.json()
        payload = response.json()
        return payload["strategy"], (payload.get("hero") or {}).get("strategy")

    # Warm: baseline first, then the variant against a populated cache.
    caches._SolveCache.clear_all()
    answer(base)
    warm = answer(variant)

    # Cold: the variant alone.
    caches._SolveCache.clear_all()
    cold = answer(variant)

    if field == "hero_cards":
        # M158 deliberately shares one solve across heroes: a request
        # warm-starts from an earlier solve of the same canonical spot and
        # refines it, which took a repeat flop request from ~13s to ~2.7s
        # — inside the 15-30s a player at a table actually has.
        #
        # So exact equality no longer holds for hero, by design. What must
        # still hold is M76's ACTUAL guarantee, which is what that bug
        # broke: every hero gets a real answer for THEIR hand, not the
        # previous caller's. Fidelity of the warm path is a separate
        # question, measured where convergence means something —
        # tests/test_warmstart.py. This fixture runs 20 iterations over 2
        # classes, a regime in which nothing is converged and warm-vs-cold
        # differences say nothing about bias.
        _, warm_hero = warm
        _, cold_hero = cold
        assert warm_hero, "hero got no answer of their own — this is M76's bug"
        assert cold_hero
        baseline = answer(base)[1]
        assert warm_hero != baseline, (
            "the warm response served the BASELINE hero's row — M76's bug exactly: "
            "the first asker for a spot fixed the answer for everyone after"
        )
        return

    assert warm == cold, (
        f"`{field}` changes the answer but is missing from a cache key: the warm "
        "response served a previous caller's solve. This is M76's bug in a new field."
    )


def test_the_cache_key_probe_would_notice_a_field_that_does_nothing(client):
    """Guards the guard.

    The invariant above passes trivially if a field simply has no effect —
    warm and cold would agree because nothing varies. So at least one
    variant must genuinely produce a different answer on a cold cache, or
    the whole parametrisation is asserting nothing.
    """
    base = _advise_body(preflop_action_path=["raise", "call_or_check"],
                        hero_cards="AsKh", board="Kd7c2h", players=2, stack_bb=100.0)

    def answer(body):
        response = client.post("/advise", json=body)
        assert response.status_code == 200
        return response.json()["strategy"]

    caches._SolveCache.clear_all()
    baseline = answer(base)
    caches._SolveCache.clear_all()
    different_board = answer({**base, "board": "Qs8d3c"})
    assert baseline != different_board, (
        "changing the board did not change the answer — the probe above cannot "
        "detect anything, because nothing varies"
    )


def test_the_river_combo_cap_spreads_across_classes_instead_of_one():
    """M119 (audit round 12). The river cap selects round-robin across
    classes, not flat top-N by weight.

    Since M119 every combo of a class carries that class's own frequency
    — correctly, because the prior over concrete combos is uniform — so
    a flat top-N breaks ties by iteration order and the single most
    frequent class swallows the entire budget. Measured on a real river
    spot at the shipped cap of 9, a flat top-N returned nine combos of
    ONE offsuit class. Nine combos spanning nine classes is a strictly
    better model of a range, and costs exactly the same to solve.

    The old per-combo weighting hid this: dividing a class's frequency
    across its combos made suited classes outrank offsuit ones per
    combo, so the pool spread across classes by accident.
    """
    import random

    from poker_solver.cards import Card
    from poker_solver.combos import combo_class
    from poker_solver.starting_hands import all_starting_hands
    from api.solving import _cap_range_to_combos

    board = frozenset(Card.from_str(t) for t in ("2h", "6d", "9c", "Kd", "4s"))
    rng = random.Random(0)
    varied = {hand: round(rng.random(), 3) for hand in all_starting_hands()}

    kept = _cap_range_to_combos(varied, 9, board)
    assert len(kept) == 9
    assert len({str(combo_class(combo)) for combo in kept}) == 9, (
        f"the cap collapsed onto too few classes: "
        f"{sorted({str(combo_class(c)) for c in kept})}"
    )
    assert not any({str(c.card_a), str(c.card_b)} & {str(b) for b in board} for c in kept)

    # Ties must keep canonical class order (AA first), NOT alphabetical —
    # sorting ties by class label was tried and puts 22 and 32o ahead of AA.
    tied = {hand: 1.0 for hand in all_starting_hands()}
    top = _cap_range_to_combos(tied, 9, board)
    assert [str(combo_class(combo)) for combo in top][:3] == ["AA", "KK", "QQ"]


def test_the_river_combo_cap_is_a_no_op_when_the_range_already_fits():
    """M119. The round-robin path must not disturb a range small enough
    to keep whole — the cap is a budget, not a resampler."""
    from poker_solver.cards import Card
    from poker_solver.combos import range_from_class_frequencies
    from poker_solver.starting_hands import StartingHand
    from api.solving import _cap_range_to_combos

    board = frozenset(Card.from_str(t) for t in ("2h", "6d", "9c"))
    freqs = {StartingHand("A", "A"): 0.9, StartingHand("K", "Q", suited=True): 0.4}
    expected = range_from_class_frequencies(freqs, exclude=board)
    assert _cap_range_to_combos(freqs, 100, board) == expected


def test_the_sizing_caveat_does_not_repeat_a_withdrawn_measurement():
    """M123 (audit round 15). A user-facing string is the last place a
    retracted measurement should survive.

    This text used to tell users "at 6-max the button has measured
    TIGHTER than under the gun". That is M110's claim, and **M111
    withdrew it in the same milestone that sharpened the finding** — the
    1.7pp gap it rested on is smaller than the 2.8pp CO varies between
    seeds. M111's actual result is stronger and simpler: among the
    non-blind seats position is not learned at all, fold mass flat at
    0.82-0.84 across UTG/MP/CO/BTN.

    Guarded by shape, not just by phrasing: the caveat must describe
    flatness, and must not assert that any one seat opens tighter or
    looser than another, which is the form of claim that was retracted.
    """
    caveat = api_config.SIZING_CAVEAT_REASON.lower()

    assert "flat" in caveat, "the caveat should state what was measured: a flat range"
    for withdrawn in ("tighter than", "looser than", "wider than"):
        assert withdrawn not in caveat, (
            f"the caveat asserts a seat-vs-seat comparison ({withdrawn!r}); M111 withdrew "
            "exactly that claim as an over-read of a gap smaller than seed variance"
        )


@pytest.mark.parametrize("players,solver,sizing", [
    (2, "high", "high"), (3, "high", "low"), (6, "high", "low"), (9, "low", "low"),
])
def test_every_honesty_signal_reaches_the_caller_with_a_reason(client, players, solver, sizing):
    """M123. The engine can be right and the product still mislead. Each
    caveat must arrive with a plain-language reason attached whenever it
    fires, and must NOT fire (or carry a reason) when it does not apply.

    Heads-up is clean on both axes because it solves exactly (CFR+)
    against a precomputed table. 9-max is low on both. The sizing caveat
    is deliberately preflop-only — M99 measured the flop-level analogue
    at ~5pp per street and chose not to surface it, on the grounds that
    flagging every postflop response would devalue the preflop warning
    that marks a genuinely unusable axis.
    """
    response = client.post("/advise", json=_advise_body(preflop_action_path=[], players=players))
    assert response.status_code == 200
    body = response.json()

    assert body["solver_confidence"] == solver
    assert bool(body["solver_confidence_reason"]) is (solver == "low"), (
        "a reason must be present exactly when the signal fires"
    )
    assert body["sizing_confidence"] == sizing
    assert bool(body["sizing_confidence_reason"]) is (sizing == "low")


def test_the_prewarm_records_every_step_and_actually_fills_the_caches(monkeypatch):
    """M124 (D2). The pre-warm is the only thing standing between a user
    and a 66-93 second cold multiway preflop solve, and nothing tested it.

    It was the largest uncovered block in the project: seven duplicated
    `except Exception: logger.exception(...)` blocks, run in a daemon
    thread nobody joins. A config typo or a renamed helper would not have
    failed anything — it would have looked like "the product is slow",
    with a stack trace in a log nobody reads. Same failure shape as F25
    (M107), where nothing verified the app was served at all.

    Shrunk to two cheap steps so this is a test rather than a benchmark;
    what it pins is that a warm is ATTEMPTED for everything config names,
    that success is recorded, and that the cache is genuinely populated
    afterwards.
    """
    monkeypatch.setattr(api_config, "PREWARM_STACK_DEPTHS", (100,))
    monkeypatch.setattr(api_config, "MULTIWAY_PREWARM_STACK_DEPTHS", ())
    calls = []
    for name in ("_get_or_solve_flop_turn", "_get_or_solve_flop_to_river",
                 "_query_turn_from_path", "_query_river_from_path"):
        monkeypatch.setattr(api_main, name,
                            lambda *a, _n=name, **k: calls.append(_n))
    monkeypatch.setattr(api_main, "DEFAULT_ITERATIONS", FAST_ITERATIONS)

    api_main._prewarm_common_depths()
    status = api_main.PREWARM_STATUS

    assert status["started"] and status["finished"]
    assert status["steps"], "the pre-warm recorded nothing at all"
    failed = [s for s in status["steps"] if not s["ok"]]
    assert not failed, f"pre-warm steps failed: {failed}"

    names = [s["name"] for s in status["steps"]]
    assert "preflop stack_bb=100" in names
    assert len(calls) == 4, f"not every deep pre-warm ran: {calls}"
    # the point of the whole exercise: the cache is warm afterwards
    assert len(api_main._preflop_raw_cache) >= 1, "the pre-warm populated nothing"


def test_a_failing_prewarm_step_is_recorded_rather_than_lost(monkeypatch):
    """M124 (D2). The other half. A failure must still not stop the
    remaining warms — one unavailable spot should not cost every other
    one, which is why the original swallowed exceptions. The defect was
    that it swallowed them WITHOUT TRACE.
    """
    monkeypatch.setattr(api_config, "PREWARM_STACK_DEPTHS", (100,))
    monkeypatch.setattr(api_config, "MULTIWAY_PREWARM_STACK_DEPTHS", ())

    def explode(*args, **kwargs):
        raise RuntimeError("solver unavailable")

    monkeypatch.setattr(api_main, "_get_or_solve_preflop_raw", explode)
    later = []
    for name in ("_get_or_solve_flop_turn", "_get_or_solve_flop_to_river",
                 "_query_turn_from_path", "_query_river_from_path"):
        monkeypatch.setattr(api_main, name, lambda *a, _n=name, **k: later.append(_n))

    api_main._prewarm_common_depths()
    status = api_main.PREWARM_STATUS

    assert status["finished"], "a failing step must not abandon the run"
    failed = [s for s in status["steps"] if not s["ok"]]
    assert len(failed) == 1, f"expected exactly one recorded failure, got {failed}"
    assert failed[0]["name"] == "preflop stack_bb=100"
    assert "solver unavailable" in failed[0]["error"], (
        f"the failure was recorded without its cause: {failed[0]}"
    )
    assert len(later) == 4, "a failed step stopped the later warms from running"


# ---------------------------------------------------------------------------
# M124 (D4): direct property tests for the api/solving.py helpers that carry
# real logic.
#
# These functions were already at ~94% LINE coverage before this section
# existed — they run on essentially every request. Coverage was never the
# gap. `_cap_range_to_combos` executed on every single river request and
# still shipped the defect M119 found, collapsing a river range onto nine
# ways to hold J3o, because an end-to-end HTTP assertion cannot see that a
# range degenerated on the way through. Coverage proves a line ran; only an
# assertion proves what it returned.
# ---------------------------------------------------------------------------


def test_cap_range_keeps_the_most_frequent_classes_and_nothing_else():
    """`_cap_range` must select BY frequency, not by dict order — the
    solved strategy has already ranked classes by relevance, and an
    arbitrary slice would discard that ranking silently."""
    from poker_solver.starting_hands import StartingHand
    from api.solving import _cap_range

    ranked = {
        StartingHand("7", "2", suited=False): 0.10,
        StartingHand("A", "A"): 0.99,
        StartingHand("9", "4", suited=False): 0.05,
        StartingHand("K", "K"): 0.90,
        StartingHand("Q", "Q"): 0.80,
    }
    kept = _cap_range(ranked, 3)
    assert len(kept) == 3
    assert {str(h) for h in kept} == {"AA", "KK", "QQ"}, (
        f"the cap did not keep the top three by frequency: {sorted(str(h) for h in kept)}"
    )
    # a range that already fits must come back untouched, same object contents
    assert _cap_range(ranked, 99) == ranked


def test_cap_range_is_stable_when_frequencies_tie():
    """M119's lesson applied to the class-level cap: with ties, selection
    falls to sort order, and a stable sort keeps the canonical order
    (AA first) rather than an alphabetical one that would prefer 22."""
    from poker_solver.starting_hands import all_starting_hands
    from api.solving import _cap_range

    tied = {hand: 0.5 for hand in all_starting_hands()}
    kept = [str(h) for h in _cap_range(tied, 3)]
    assert kept == ["AA", "KK", "QQ"], f"tied classes were not kept in canonical order: {kept}"


@pytest.mark.parametrize("fields,expected", [
    ({}, "preflop"),
    ({"board": "2h6d9c"}, "flop"),
    ({"board": "2h6d9c", "flop_action_path": ["call_or_check", "call_or_check"],
      "turn_card": "Kd"}, "turn"),
    ({"board": "2h6d9c", "flop_action_path": ["call_or_check", "call_or_check"],
      "turn_card": "Kd", "turn_action_path": ["call_or_check", "call_or_check"],
      "river_card": "4s"}, "river"),
])
def test_infer_street_reads_the_street_off_the_fields_present(fields, expected):
    """Street depth is INFERRED from which fields are present rather than
    named by the client, so that a client cannot state a street that
    contradicts its own data. This pins the mapping directly."""
    from api.schemas import AdviseRequest
    from api.solving import _infer_street

    request = AdviseRequest(stack_bb=100.0, preflop_action_path=["raise", "call_or_check"],
                            **fields)
    assert _infer_street(request) == expected


@pytest.mark.parametrize("fields", [
    {"turn_card": "Kd"},                                   # turn card, no board
    {"board": "2h6d9c", "river_card": "4s"},               # river card, no turn
    {"board": "2h6d9c", "turn_action_path": ["call_or_check"]},  # turn action, no turn card
])
def test_infer_street_refuses_a_skipped_street(fields):
    """The contradiction guards. Without these a partial combination
    would be answered as some other street — a confident answer to a
    question nobody asked, which is F23's shape."""
    from api.schemas import AdviseRequest
    from api.solving import _infer_street

    request = AdviseRequest(stack_bb=100.0, preflop_action_path=["raise", "call_or_check"],
                            **fields)
    with pytest.raises(ValueError):
        _infer_street(request)


def test_multiway_depths_in_one_bucket_share_a_single_solve(client, monkeypatch):
    """M124 (D1). The multiway preflop solve is the most expensive thing
    in the product — 66s at 6-max, 93s at 9-max, measured cold — and it
    used to be keyed on `round(stack_bb)`, so a client walking depths
    paid it once per integer bb while the pre-warm covered three depths
    of the ~200 plausible ones.

    Counts SOLVES, not seconds, for the same reason M101's own guard
    does: the number is the invariant, the wall-clock is the symptom.
    """
    from api import solving as solving_module

    solves = []
    original = solving_module.solve_preflop

    def counting(*args, **kwargs):
        solves.append(kwargs.get("config"))
        return original(*args, **kwargs)

    monkeypatch.setattr(solving_module, "solve_preflop", counting)
    solving_module._multiway_cache.clear()

    for depth in (95.0, 96.0, 97.0, 98.0, 99.0):
        assert solving_module._get_or_solve_multiway(depth, 3) is not None
    assert len(solves) == 1, (
        f"five depths inside one 5bb band caused {len(solves)} solves, not 1"
    )

    # ...and the solve ran at the FLOOR of the band, never above it, so
    # every size it derives is affordable at the shallowest real stack in
    # the band (M95's invariant, which bucketing must not break).
    assert solves[0].stack_bb == 95.0, (
        f"solved at {solves[0].stack_bb}, which is not the floor of the band"
    )

    # a depth in the next band down is genuinely a different solve
    solving_module._get_or_solve_multiway(92.0, 3)
    assert len(solves) == 2
    assert solves[1].stack_bb == 90.0


def test_a_bucketed_multiway_solve_never_names_an_unaffordable_bet(client):
    """M124 (D1). The failure mode bucketing could have introduced, and
    the reason the solve runs at the bucketed depth rather than the
    requested one.

    Keying on the bucket while solving at the real depth would serve the
    first caller's deeper tree to everyone in the band — and a tree built
    at 99bb offers bets a 95bb player cannot make. That is exactly F13,
    which M95 fixed for the postflop library by flooring. Same guarantee,
    checked here at the top and bottom of a band and at a sub-bucket
    depth.
    """
    for stack in (99.0, 95.0, 24.0, 6.0):
        response = client.post("/advise", json=_advise_body(
            preflop_action_path=[], players=3, stack_bb=stack))
        assert response.status_code == 200, (stack, response.json())
        body = response.json()
        for action in body["strategy"]:
            if ":" in action:
                size = float(action.split(":")[1])
                assert size <= stack + 1e-9, (
                    f"{stack}bb stack was advised {action}, which it cannot afford"
                )


def test_every_api_route_is_reachable_through_the_dev_proxy():
    """M125 (E1). `frontend/vite.config.ts` proxies API calls to the
    backend by prefix, and a route whose name matches no prefix falls
    through to the SPA's index.html and 404s in dev.

    This has happened three times — M10's `/equity`, M25's
    `/preflop_walk`, M56's `/advise` — and the config's own comment says
    why nothing caught it:

        Caught by live browser verification (a real 404), NOT by the
        unit tests, which stub fetch and so can never see a proxy gap.

    That is still true of the frontend suite, which is why this lives on
    the Python side: it is the only place that can see both the route
    table and the proxy config at once. A fourth occurrence now fails a
    test instead of waiting for someone to click the right tab.
    """
    import re
    from pathlib import Path

    config = Path(__file__).resolve().parents[1] / "frontend" / "vite.config.ts"
    prefixes = set(re.findall(r"'(/[a-z_]+)':\s*'http", config.read_text(encoding="utf-8")))
    assert prefixes, "found no proxy entries — has vite.config.ts changed shape?"

    routes = {
        route.path
        for route in api_main.app.routes
        if getattr(route, "path", "").startswith("/")
        and not route.path.startswith("/openapi")
        and route.path not in ("/", "/docs", "/redoc", "/docs/oauth2-redirect")
    }
    assert routes, "found no API routes to check"

    unreachable = sorted(r for r in routes if not any(r.startswith(p) for p in prefixes))
    assert not unreachable, (
        f"these routes are not covered by any vite proxy prefix and will 404 in dev: "
        f"{unreachable}. Add an entry to frontend/vite.config.ts."
    )


@pytest.mark.parametrize("players,solver,sizing", [
    (2, "high", "high"), (3, "high", "low"), (6, "high", "low"), (9, "low", "low"),
])
def test_the_range_chart_endpoint_carries_the_same_caveats_as_advise(
    client, players, solver, sizing
):
    """M125 (E2). `solver_confidence` and `sizing_confidence` used to
    exist on `AdviseResponse` alone — one of eleven response models.

    This response serves `GET /solve/{stack_bb}?players=9`, the 9-max
    preflop range chart, and it is what the frontend's own Preflop
    Ranges tab calls. CLAUDE.md says "9-max preflop output is NOT
    reliable (M68, measured)" and "Don't present 9-max advice as
    authoritative"; M76 added the signal for exactly that and attached
    it to one endpoint. A caller here received a complete,
    confident-looking 169-class chart of an under-trained solve with
    nothing in the payload saying so.

    Asserted against the same table as /advise's own guard, so the two
    endpoints cannot drift apart on the same question.
    """
    response = client.get(f"/solve/100?players={players}&iterations={FAST_ITERATIONS}")
    assert response.status_code == 200
    body = response.json()

    assert body["solver_confidence"] == solver
    assert bool(body["solver_confidence_reason"]) is (solver == "low")
    assert body["sizing_confidence"] == sizing
    assert bool(body["sizing_confidence_reason"]) is (sizing == "low")


def test_advise_and_the_range_chart_agree_on_confidence(client):
    """M125 (E2). The two endpoints answer the same underlying question
    from the same cached solve, so a caller must not be able to get a
    warning from one and silence from the other."""
    for players in (2, 3, 6, 9):
        chart = client.get(f"/solve/100?players={players}&iterations={FAST_ITERATIONS}").json()
        advice = client.post("/advise", json=_advise_body(
            preflop_action_path=[], players=players)).json()
        assert chart["solver_confidence"] == advice["solver_confidence"], players
        assert chart["sizing_confidence"] == advice["sizing_confidence"], players


@pytest.mark.parametrize("path,fields,street,expected", [
    # a preflop path that still leaves a preflop decision to make
    ([], {}, "preflop", "high"),
    (["raise"], {}, "preflop", "high"),
    # ...and paths that close preflop, so a board can be dealt
    (["raise", "call_or_check"], {"board": "2h6d9c"}, "flop", "low"),
    (["raise", "call_or_check"],
     {"board": "2h6d9c", "flop_action_path": ["call_or_check", "call_or_check"],
      "turn_card": "Kd"}, "turn", "low"),
])
def test_postflop_advice_carries_the_aggression_caveat(client, path, fields, street, expected):
    """M128. The postflop counterpart to the preflop sizing caveat, and
    it exists for a measured reason.

    The range a postflop solve models is capped to
    `MAX_PATH_QUERY_CLASSES_PER_SIDE` — a pure COST control the user
    never sees. Sweeping it 10 -> 26 on one flop spot moves a value
    hand's aggression NON-MONOTONICALLY across a 250x range:

        cap        10    12    14    16    18    20    22    24    26
        9s9d set  .003  .004  .019  .025  .771  .071  .122  .393  .402
        QdQh      .007  .006  .006  .031  .533  .018  .023  .023  .032

    Not noise — solving twice at the same cap gives a delta of exactly
    0.0. And widening is no escape: cap 26 takes one flop decision from
    10.8s to 52.1s.

    Scoped to the AGGRESSION axis deliberately. The fold-versus-play call
    held up across 275 advised decisions in M127's play session, so it is
    not implicated; saying otherwise would devalue the caveat.
    """
    response = client.post("/advise", json=_advise_body(
        preflop_action_path=path, players=2, **fields))
    assert response.status_code == 200
    body = response.json()
    assert body["street"] == street
    assert body["aggression_confidence"] == expected
    assert bool(body["aggression_confidence_reason"]) is (expected == "low")


def test_the_aggression_caveat_does_not_claim_the_fold_call_is_broken():
    """M128, premise CORRECTED by M142.

    This used to assert the caveat covers HOW aggressively to play and
    not WHETHER to continue, "because only the former was measured
    unstable". That premise was false, and the reason it survived is
    worth keeping: every measurement behind it was taken at a street's
    OPENING decision, where folding is not a legal action at all because
    checking is free. Measured at a node FACING A BET, the fold call is
    as wrong as the aggression call (mean 0.1870 vs 0.1694) and its worst
    case is worse (0.8017 vs 0.5573).

    So the caveat must no longer vouch for the fold axis. What it must
    still avoid is the opposite over-claim — the axis is exact for made
    hands and strong draws, and telling players to distrust it wholesale
    would be as wrong as the promise it replaces.
    """
    reason = api_config.POSTFLOP_AGGRESSION_CAVEAT_REASON.lower()
    assert "how often" in reason or "how aggressively" in reason
    assert "fold" in reason, "the caveat should still address the fold axis"
    for withdrawn in ("far sounder", "trust whether to continue"):
        assert withdrawn not in reason, (
            f"the caveat still vouches for the fold call ({withdrawn!r}); M142 "
            "measured it as bad as the aggression call at a node facing a bet"
        )
    assert "essentially exact" in reason, (
        "the caveat should still say where the fold call IS reliable — with a "
        "made hand or a strong draw — or it over-claims in the other direction"
    )
    for overclaim in ("do not trust", "unusable", "ignore this advice"):
        assert overclaim not in reason, (
            f"the caveat overclaims ({overclaim!r}); only the aggression axis was "
            "measured unstable, and the fold/play call held across 275 decisions"
        )


def test_the_range_cap_drops_premiums_the_raiser_actually_holds():
    """M130. The measured mechanism behind M128's aggression caveat,
    pinned because the caveat's wording now depends on it.

    `_cap_range` keeps the top classes by HOW OFTEN they took the
    observed action. Premiums MIX — at 100bb the raiser's AA raises 0.495
    of the time because it also jams — while mediocre hands raise purely
    at 0.99+. The tenth-place cut is 0.9912, so every premium falls below
    it and the modelled opponent holds no big pairs. That is why value
    hands are told to check: there is nothing to raise for value against.

    Widening the cap lets them back in and the answer converges — a
    flopped set on 2h6d9c measures .003 / .694 / .468 / .301 / .336 /
    .347 at caps 10/18/26/34/44/60, settling near 0.35 against the
    shipped 0.003.

    Asserted on the RAISER only. The big blind's exclusion is correct and
    unrelated: premiums 3-bet rather than call, so their calling
    frequency genuinely is 0, and asserting over both would pass for the
    wrong reason on one of them.
    """
    from poker_solver.game_tree import CALL_OR_CHECK, RAISE, GameConfig
    from poker_solver.solver import derive_ranges_from_path, solve_preflop
    from api.solving import _cap_range

    premiums = {"AA", "KK", "QQ", "JJ", "AKs", "AKo"}
    result = solve_preflop(config=GameConfig(stack_bb=100.0, raise_sizes=(2.5, 3.0, 2.2),
                                             max_raises=4), iterations=400)
    root = result.root
    raise_action = next(a for a in root.legal_actions if a.kind == RAISE)
    call_action = next(a for a in root.children[raise_action].legal_actions
                       if a.kind == CALL_OR_CHECK)
    scenario = derive_ranges_from_path(result, [raise_action, call_action])

    raiser = scenario.live_positions[0]
    full = scenario.ranges[raiser]
    present = {str(h) for h, f in full.items() if str(h) in premiums and f > 0}
    assert present, "the raiser's real range should contain premiums at 100bb"

    kept = {str(h) for h in _cap_range(full, api_config.MAX_PATH_QUERY_CLASSES_PER_SIDE)}
    assert not (kept & premiums), (
        f"premiums survived the cap ({sorted(kept & premiums)}) — if the ranking has "
        "changed, POSTFLOP_AGGRESSION_CAVEAT_REASON describes a mechanism that no "
        "longer applies and must be rewritten"
    )
    # ...and the reason is purity, not absence: they are in the range,
    # just below a cut made of hands that take one action every time.
    cut = min(_cap_range(full, api_config.MAX_PATH_QUERY_CLASSES_PER_SIDE).values())
    assert cut > 0.9, f"the cut should sit among near-pure strategies, got {cut}"
    assert all(full[h] < cut for h in full if str(h) in present)


def test_the_aggression_caveat_names_the_mechanism_but_no_longer_a_direction():
    """M131. This test previously required the caveat to name a
    DIRECTION, because M130 measured every spot as too passive and told
    users to lean more aggressive.

    The rebalance flipped it. Against a full-range reference across five
    spots, all five now lean slightly the other way — a flopped set
    reads 0.486 against a 0.347 reference — and four of those five errors
    are under 0.01. A direction read off residuals that small, and that
    have already reversed once, is not something to put in front of a
    player: correcting the wrong way is worse than not correcting.

    So the caveat still names the MECHANISM, which is unchanged and
    verified by `test_the_range_cap_drops_premiums_the_raiser_actually_
    holds`, and deliberately stops naming a direction.
    """
    reason = api_config.POSTFLOP_AGGRESSION_CAVEAT_REASON.lower()
    assert "mix" in reason, "the caveat should still name the mechanism"
    assert "fold" in reason, "the caveat should still say which axis IS usable"
    assert "without a consistent direction" in reason, (
        "the caveat should say the residual has no reliable direction"
    )
    for stale in ("lean more aggressive", "too passive", "check more than they should"):
        assert stale not in reason, (
            f"the caveat still tells users to correct for a bias that has flipped "
            f"({stale!r}) — the residual now leans the other way"
        )


def test_the_aggression_caveat_quotes_its_own_measurement():
    """M138. The magnitudes in user-facing copy must match the recorded
    measurement, in the units the copy uses.

    This exists because the copy drifted from its evidence and nothing
    caught it. The caveat told users the raising frequency was off "by
    about 6 percentage points on average and by 17 at worst". Those came
    from errors against a cap-60 solve labelled a full-range reference,
    which was not converged: the same spot reads 0.381 / 0.5948 / 0.9186
    at caps 60 / 100 / 200 and 0.987 uncapped at 2,500 iterations. The
    real errors are 0.1222 mean / 0.4381 worst — the worst case was
    understated 2.6x in the one sentence a player actually reads.

    Pinning the constants alone would not have caught it, because the
    constants agreed with the prose; both were wrong together. So this
    asserts the PROSE against the constants, and a separate assertion
    keeps the constants tied to the reference that produced them.
    """
    reason = api_config.POSTFLOP_AGGRESSION_CAVEAT_REASON
    mean_pp = round(api_config.POSTFLOP_AGGRESSION_ERROR_MEAN * 100)
    worst_pp = round(api_config.POSTFLOP_AGGRESSION_ERROR_WORST * 100)
    assert f"{mean_pp} percentage points" in reason, (
        f"the caveat's average ({reason!r}) no longer matches "
        f"POSTFLOP_AGGRESSION_ERROR_MEAN ({mean_pp} percentage points)"
    )
    assert f"by {worst_pp} at worst" in reason, (
        f"the caveat's worst case no longer matches "
        f"POSTFLOP_AGGRESSION_ERROR_WORST ({worst_pp})"
    )
    # The old figures must not survive anywhere in the copy.
    for stale in ("6 percentage points", "by 17 at worst",
                  "3 percentage points", "by 14 at worst"):
        assert stale not in reason, (
            f"the caveat still quotes a withdrawn measurement ({stale!r})"
        )
    assert "uncapped" in reason, (
        "the caveat should say what it was measured against — the cap-60 "
        "solve it used to cite was not a full-range reference"
    )


def test_the_caveat_names_the_open_ender_case_it_measured():
    """M140. The one postflop error big enough, consistent enough and
    nameable enough for a player to act on must stay in the copy.

    Open-ended straight draws are over-bet 3 of 3 measured, by +0.170 to
    +0.881. The worst is 7h8h on 2h6d9c, where the product recommends a
    2.5x-pot bet 0.88 of the time and the converged solve checks 100% —
    reproducible byte-identical across runs, with the reference itself
    converged at that spot (0.0004 / 0.0001 / 0.0 at 1k / 2.5k / 5k).

    Gutshots and a no-draw control are clean, so the caveat must say
    OPEN-ENDED rather than "draws": over-generalising here would be the
    same over-claim M110/M111 had to withdraw, one step in the other
    direction.
    """
    reason = api_config.POSTFLOP_AGGRESSION_CAVEAT_REASON.lower()
    assert "open-ended straight draw" in reason, (
        "the caveat must name the one case measured consistent enough to act on"
    )
    assert "discount" in reason, (
        "naming the case without telling the player what to do with it is not "
        "actionable — the measurement supports discounting these bets"
    )
    # It must not over-generalise to all draws: gutshots measured clean
    # (+0.0006 and 0.0), so warning about them would be unsupported.
    assert "discount any suggestion to bet a draw" not in reason, (
        "gutshots measured clean; the warning is specific to open-enders"
    )
    # The worst case in the copy must be the open-ender's, not a stale one.
    assert str(round(api_config.POSTFLOP_AGGRESSION_ERROR_WORST * 100)) in reason


def test_the_caveat_warns_about_weak_hands_facing_a_bet():
    """M142 / F38. The most consequential warning in the response.

    Facing a bet with a weak hand the product does not merely over-call:
    holding nine-high (8s9s on Ac7d2h) it recommends shoving 97.5bb
    0.5672 of the time where the converged solve folds 0.9869. Verified
    byte-identical across runs with the reference identical at 1k / 2.5k
    / 5k iterations.

    A player who follows that loses a stack, so the copy must say it —
    and must say it in terms a player can apply before knowing the
    answer: weak hand, facing a bet.
    """
    reason = api_config.POSTFLOP_AGGRESSION_CAVEAT_REASON.lower()
    assert "facing a bet" in reason, (
        "the warning must name the node type, since the same hands are fine "
        "at a street's opening decision"
    )
    assert "weak hand" in reason, "the warning must name the hand type"
    assert "all-in" in reason or "commit chips" in reason, (
        "over-calling understates it — the product recommends going all-in "
        "with nine-high 57% of the time here"
    )
    assert "fold weak hands facing a bet more often" in reason, (
        "the warning must tell the player what to do, not only that the "
        "number is unreliable"
    )


def test_the_river_now_models_a_real_bet_size(client):
    """M174 closes F40 — and this test is the one M144 asked for.

    M144's version asserted `modelled_bet_sizes == [all_in]` and said in
    its own failure message: "if intermediate sizes now exist this test
    should be revisited rather than deleted, and the disclosure below
    relaxed". That is exactly what happened.

    The river was the only street that modelled no bet size at all,
    because it was the third leg of a chained solve taking ONE
    `raise_sizes` for all three streets — widening the river widened the
    flop and turn, which was unaffordable. Solved as its own street it
    sets its own menu, and `RIVER_STANDALONE_RAISE_SIZES` is real.

    **The sizes were adopted on a WASH, not an improvement** (paired delta
    +0.0093 +/- 0.0181 sem against a full-range reference). They are here
    because they answer a question the response previously had to decline,
    at no measured accuracy cost. What actually fixed the river's accuracy
    was coverage: 9 combos -> 26 classes, mean error 0.1948 -> 0.0626.
    """
    body = _advise_body(
        preflop_action_path=["raise", "call_or_check"],
        flop_action_path=["call_or_check", "call_or_check"], turn_card="Ts",
        turn_action_path=["call_or_check", "call_or_check"], river_card="4c",
        hero_cards="5c4d", board="Kd7c2h", players=2, stack_bb=100.0,
    )
    payload = client.post("/advise", json=body).json()
    assert payload["street"] == "river"

    sizes = payload["modelled_bet_sizes"]
    assert sizes, "the response should report which sizes the tree offered"
    bound = payload["max_affordable_bb"]
    assert all(size <= bound + 1e-9 for size in sizes), (
        f"M101/M143: every modelled size must be affordable, got {sizes} "
        f"against {bound}bb")
    # The point of the milestone: a size that is NOT the whole stack.
    intermediate = [s for s in sizes if s < bound - 1e-9]
    assert intermediate, (
        f"the river should now model a real bet size, got {sizes} against a "
        f"{bound}bb bound — if this regresses, F40 is back and the "
        "BET_SIZING_COVERAGE_NOTE below must come back with it")

    # And the F40 disclosure must NOT fire any more, because it is no
    # longer true. It is derived from the response's own rows, so this
    # follows automatically — which is the property M144 built it for.
    reason = payload["aggression_confidence_reason"]
    assert "no intermediate bet size" not in reason, (
        "the river models an intermediate size now; still saying it does "
        "not is a false disclosure")


def test_an_earlier_street_does_not_carry_the_river_sizing_note(client):
    """M144. The disclosure must be node-derived, not blanket-postflop.

    Attaching it to every postflop response would make it noise, and
    would be false: the flop and turn do offer an intermediate size.
    """
    body = _advise_body(
        preflop_action_path=["raise", "call_or_check"],
        hero_cards="5c4d", board="Kd7c2h", players=2, stack_bb=100.0,
    )
    payload = client.post("/advise", json=body).json()
    assert payload["street"] == "flop"
    assert any(
        0 < size < payload["max_affordable_bb"] for size in payload["modelled_bet_sizes"]
    ), "the flop is expected to offer at least one intermediate size"
    assert "no intermediate bet size" not in payload["aggression_confidence_reason"], (
        "the sizing note fired on a street that does model intermediate bets"
    )


def test_a_node_nothing_was_trained_at_is_not_reported_as_high_confidence():
    """M145 / F41. The signal a user checks first must know whether the
    spot was solved.

    `solver_confidence` was a pure function of TABLE SIZE. Measured: a
    3-max river (Kd7c2h Ts 4c) returns **0 of 136 hands trained, every
    row exactly the uniform prior** — hero reads 0.3333 / 0.3333 /
    0.3333 — while the response said `solver_confidence: "high"` and
    `range_confidence: fully_trained: true` for all three positions. Two
    of three confidence signals vouched for an answer never computed.

    Unit-level rather than through the API: the real case is an
    occasional multiway river (1 of 6 measured), which is exactly the
    kind of thing that cannot be pinned by a live request without
    depending on which branch happened to get trained.
    """
    untrained = {"trained": {"AA": False, "KK": False}, "street": "river"}
    level, reason = api_main._solver_confidence(untrained, players=2)
    assert level == "low"
    assert "was not actually solved" in reason
    # and it must say what an even split MEANS, since that is how the
    # fabricated answer disguises itself
    assert "even split" in reason

    trained = {"trained": {"AA": True, "KK": False}, "street": "river"}
    assert api_main._solver_confidence(trained, players=2) == ("high", None)


def test_the_untrained_signal_does_not_fire_on_one_untrained_hand():
    """M145. Scope discipline — a confidence signal that fires everywhere
    is as useless as one that never fires.

    `trained_hands` documents a benign reason a single hand reads
    untrained: a hand with zero reach in this position's range is
    untrained at any iteration count. Flagging on that would make "low"
    the normal case. Zero trained hands at the WHOLE node cannot be
    benign — it means the node was never visited.
    """
    mostly = {"trained": {h: True for h in ("AA", "KK", "QQ")}, "street": "flop"}
    mostly["trained"]["72o"] = False
    assert api_main._solver_confidence(mostly, players=2) == ("high", None)
    # an absent or empty trained dict is not evidence of anything
    assert api_main._solver_confidence({"street": "flop"}, players=2) == ("high", None)
    assert api_main._solver_confidence({"trained": {}}, players=2) == ("high", None)


def test_both_confidence_reasons_are_reported_when_both_apply():
    """M145. A 9-max table AND an untrained node are different problems;
    a user acting on one should still be told the other."""
    untrained = {"trained": {"AA": False}, "street": "river"}
    level, reason = api_main._solver_confidence(untrained, players=9)
    assert level == "low"
    assert "was not actually solved" in reason
    assert api_config.LOW_CONFIDENCE_TABLE_SIZES[9] in reason


def test_a_uniform_hero_row_that_claims_to_be_trained_is_flagged():
    """M149 / F43. `trained` means VISITED, not LEARNED.

    Measured through /advise: a 6-max player holding AA facing a 4-bet is
    told **fold 0.3333 / call 0.3333 / all-in 0.3333** while the response
    reports `hero.trained: true`, `solver_confidence: "high"`, and 101 of
    169 hands trained at the node. Folding aces to a 4-bet a third of the
    time is a stack-losing instruction.

    F41/M145's signal correctly stays quiet — most of the node IS
    trained. `trained_mask()` asks whether a hand accumulated any
    strategy_sum, i.e. whether it was visited; `current_strategy()`
    returns the uniform prior whenever every regret is <= 0, which M73
    measured at ~70% of rows. So a hand can be visited repeatedly and
    still average to exactly the prior.
    """
    hero = {"trained": True,
            "strategy": {"fold": 1 / 3, "call_or_check": 1 / 3, "all_in:100.00": 1 / 3}}
    assert api_main._hero_row_is_the_prior(hero)
    level, reason = api_main._solver_confidence({"trained": {"AA": True}}, 6, hero)
    assert level == "low"
    assert "even split" in reason
    # It must say the split is not a recommendation to mix — an even
    # split across three actions is otherwise a legitimate solver output.
    assert "not a recommendation to mix" in reason


def test_the_uniform_row_signal_stays_quiet_where_it_should():
    """M149. Scope, pinned in three directions.

    A signal that fires everywhere is worth nothing, and two of these
    would make it fire constantly.
    """
    # A real answer.
    assert not api_main._hero_row_is_the_prior(
        {"trained": True, "strategy": {"fold": 0.0, "all_in:100.00": 1.0}})
    # NEAR-uniform is a real computed answer close to indifference.
    assert not api_main._hero_row_is_the_prior(
        {"trained": True, "strategy": {"a": 0.3334, "b": 0.3333, "c": 0.3333}})
    # M163/F47 CORRECTED this one. M149 asserted here that `trained:
    # false` needs no signal because it "already carries a louder
    # hero-specific warning". It does — in the `hero.trained` FIELD — but
    # `solver_confidence` never read that field, so the headline signal
    # still said "high" over a row that is purely the prior. Measured in
    # a 120-hand session: a six-handed flop decision returned 0.3333
    # across fold/call/all-in and called itself high confidence. The row
    # IS the prior either way; only the reason differs.
    assert api_main._hero_row_is_the_prior(
        {"trained": False, "strategy": {"a": 1 / 3, "b": 1 / 3, "c": 1 / 3}})
    # No hero, single-action rows, and absent strategies are not evidence.
    assert not api_main._hero_row_is_the_prior(None)
    assert not api_main._hero_row_is_the_prior({"trained": True, "strategy": {"fold": 1.0}})
    assert not api_main._hero_row_is_the_prior({"trained": True})



def test_an_untrained_uniform_hero_row_is_not_served_as_high_confidence():
    """M163/F47. Found by running three 120-hand sessions instead of one.

    A six-handed flop decision returned `fold 0.3333 / call 0.3333 /
    all-in 0.3333` — the solver's untouched starting assumption — while
    the response reported `solver_confidence: "high"`. It appeared in one
    session of three, which is also why single-session "0 defects" claims
    in this project's history cannot be trusted.

    M149 built exactly this signal but gated it on `trained is True`,
    reasoning that an untrained hero already carries a louder warning.
    That warning is a FIELD on the hero block; the headline confidence
    signal never consulted it. This is F41's shape — a signal vouching
    for something that was never computed — one layer further down.

    Both causes must report low, and they must give DIFFERENT reasons: a
    hand that was reached and never formed a preference is a different
    problem from one that was never reached, and a user can act on the
    difference.
    """
    uniform = {"fold": 1 / 3, "call_or_check": 1 / 3, "all_in:97.50": 1 / 3}

    reached, reached_why = api_main._solver_confidence(
        {"trained": {"AA": True}}, 6, {"trained": True, "strategy": uniform})
    never, never_why = api_main._solver_confidence(
        {"trained": {"AA": True}}, 6, {"trained": False, "strategy": uniform})

    assert reached == "low"
    assert never == "low", "an untrained uniform row still claimed high confidence"
    assert reached_why != never_why, "both causes gave the same reason"
    assert "never reached" in never_why
    assert "never learned a preference" in reached_why

    # A real computed row is unaffected either way — the signal must not
    # start firing on ordinary answers.
    real = {"fold": 0.1, "call_or_check": 0.8, "all_in:97.50": 0.1}
    assert api_main._solver_confidence({}, 6, {"trained": True, "strategy": real})[0] == "high"

def test_the_uniform_row_signal_is_actually_wired_to_the_response(client):
    """M149. The unit tests above passed while the signal never fired.

    `hero` is assembled inside `advise` and never lands in `raw`, so a
    first version reading `raw.get("hero")` returned False for every real
    request — and every unit test still passed, because they fed it a
    hand-built dict shaped the way the response was ASSUMED to look.

    So this drives the real endpoint. It asserts the wiring, not the
    logic: whatever spot the fixture produces, if hero's row comes back
    exactly uniform while claiming to be trained, the confidence must say
    so.
    """
    # The real 6-max spot needs a full-budget solve; under the suite's
    # shrunken fixture it does not reproduce, and a skipping test proves
    # nothing. So the SOLVE is stubbed and everything downstream of it —
    # hero assembly, the confidence call, response shaping — is real.
    uniform = {"fold": 1 / 3, "call_or_check": 1 / 3, "all_in:100.00": 1 / 3}
    raw = {
        "street": "preflop", "positions": ["BTN", "BB"], "position": "BTN",
        "player_to_act": "BTN", "is_terminal": False, "pot": 40.5,
        "effective_stack_bb": 83.5, "max_affordable_bb": 83.5,
        "strategy": {"AA": dict(uniform), "72o": dict(uniform)},
        "trained": {"AA": True, "72o": True},
        "hero_key": "AA", "hero_in_range": True, "hero_range_trained": True,
        "source": "mccfr", "elapsed_seconds": 0.1,
    }

    def fake_advise(*_args, **_kwargs):
        return raw

    with mock.patch.object(api_main, "_advise", fake_advise):
        response = client.post(
            "/advise",
            json=_advise_body(preflop_action_path=["raise", "raise", "raise"],
                              hero_cards="AsAh", players=6, stack_bb=100.0),
        )
    assert response.status_code == 200, response.json()
    payload = response.json()
    assert payload["hero"]["trained"] is True, "the fixture must be the MISLEADING case"
    assert payload["solver_confidence"] == "low", (
        "hero's row is exactly the uniform prior and the response still reports high "
        "confidence — the signal is computed but not wired to the response, which is "
        "exactly how the first version of this fix shipped as a no-op"
    )
    assert "even split" in (payload["solver_confidence_reason"] or "")


def test_a_deep_preflop_node_is_solved_on_demand():
    """M150. The architectural fix: solve the deep node, don't disclose it.

    The 6-max preflop tree has 289,036 decision nodes and the shipped
    solve learns roughly the first four levels (production cached solve,
    learned rows by depth: 80% at d3, 48% d4, 21% d5, 12% d6, 3% d7, 0%
    at d8+ where ~285,000 nodes live). Neither obvious fix applies:
    285,000 nodes cannot be targeted-trained, and M72/M73 measured 6-max
    destabilising at 12k iterations.

    So this borrows the postflop pattern — `ensure_mccfr_chance_branch`
    trains a branch when a client asks for it. A deep preflop subtree is
    SMALL for the same reason it is deep: the node below has 10 nodes.

    Measured through /advise: AA facing a 4-bet went from an even 0.3333
    split to **jam 0.9999**, and trash at the same node from 0.3333 to
    **fold 0.998** — one solve repairs every hand at the node.
    """
    from api import solving

    result = solving._get_or_solve_multiway(100.0, 3)
    # Walk to the deepest reachable node, which is where the budget runs out.
    node, depth = result.root, 0
    while depth < 6:
        nxt = None
        for action in node.legal_actions:
            child = node.children[action]
            if hasattr(child, "legal_actions") and child.legal_actions:
                nxt = child
                break
        if nxt is None:
            break
        node, depth = nxt, depth + 1

    strategy = result.strategy_at(node)
    hero_key = next(iter(strategy))
    # Force the trigger: whatever this fixture's node looks like, the
    # helper must act when hero's row IS the prior and must not when it
    # is not. Both directions are asserted below.
    uniform_rows = [k for k, row in strategy.items() if solving._row_is_the_prior(row)]
    if not uniform_rows:
        pytest.skip("this fixture's deepest node is already fully learned")
    hero_key = uniform_rows[0]

    did_work = solving._ensure_preflop_node_trained(result, node, 3, hero_key)
    assert did_work, "an exactly-uniform hero row should have been solved for"
    assert not solving._row_is_the_prior(result.strategy_at(node)[hero_key]), (
        "the row is still the uniform prior after solving on demand"
    )

    # And a second ask must not redo the work.
    assert not solving._ensure_preflop_node_trained(result, node, 3, hero_key), (
        "an already-solved node was solved again — every request would pay for it"
    )


def test_on_demand_preflop_training_leaves_heads_up_alone():
    """M150. Heads-up has nothing to fix and must not pay for a check.

    Its exact solver enumerates every hand at every node, so every row is
    real — measured: BTN opens 0.998, facing a 4-bet jams 1.0.
    """
    from api import solving

    result = solving._get_or_solve_preflop_raw(100.0, 200, players=2)
    node = result.root
    assert not solving._ensure_preflop_node_trained(result, node, 2, "AA")


def test_the_prior_test_is_exact_not_approximate():
    """M150. A near-uniform row is a real answer near indifference.

    Treating it as the prior would trigger on-demand solves for genuinely
    solved nodes, which is cost for nothing.
    """
    from api import solving

    assert solving._row_is_the_prior({"a": 1 / 3, "b": 1 / 3, "c": 1 / 3})
    assert not solving._row_is_the_prior({"a": 0.3334, "b": 0.3333, "c": 0.3333})
    assert not solving._row_is_the_prior({"a": 1.0})
    assert not solving._row_is_the_prior({})


def test_the_injected_equity_builder_honours_its_seed():
    """M153 / F44. `equity_seed` was silently dropped on the flop path.

    `parallel_board_equity_table` took no seed and hardcoded
    `DEFAULT_SEED`, while `solve_flop` called it as
    `equity_table_fn(board, combos, equity_samples)`. From M132 onward the
    production path therefore ignored `equity_seed` entirely.

    Nothing was WRONG with the tables — still deterministic, still
    correct. What broke was the ability to vary the seed as a convergence
    check, and M138 cited "the seed does not move it" as evidence its
    reference had converged. That evidence was empty: the seed could not
    move it. Measured afterwards, two spots gave byte-identical values
    across seeds twice each, which is what prompted looking.
    """
    import numpy as np
    from api.parallel import parallel_board_equity_table
    from poker_solver.cards import Card
    from poker_solver.combos import HandCombo

    board = tuple(Card.from_str(t) for t in ("2h", "6d", "9c"))
    combos = [HandCombo(Card.from_str(a), Card.from_str(b)) for a, b in
              (("9s", "9d"), ("Qd", "Qh"), ("Ah", "Kh"), ("Ts", "Tc"),
               ("7s", "7c"), ("5s", "4d"))]
    first = np.nan_to_num(parallel_board_equity_table(board, combos, 30, seed=0))
    other = np.nan_to_num(parallel_board_equity_table(board, combos, 30, seed=99))
    again = np.nan_to_num(parallel_board_equity_table(board, combos, 30, seed=0))

    assert not np.allclose(first, other), (
        "the injected equity builder ignores its seed — a seed-variation "
        "convergence check on this path cannot vary anything"
    )
    assert np.allclose(first, again), "the same seed must still reproduce"


def test_solve_flop_passes_its_seed_to_an_injected_builder():
    """M153. The parameter has to survive the call, not just exist.

    The defect was in the CALL, not the builder: `solve_flop` dropped the
    seed on the floor when an `equity_table_fn` was injected, which is
    the production configuration.
    """
    from poker_solver.cards import Card
    from poker_solver.combos import HandCombo
    from poker_solver.solver import solve_flop

    seen = {}

    def spy(board, combos, samples, seed=None):
        seen["seed"] = seed
        from poker_solver.board_equity import build_board_equity_table
        import random
        return build_board_equity_table(board, combos, samples=samples or 10,
                                        rng=random.Random(seed or 0))

    board = tuple(Card.from_str(t) for t in ("2h", "6d", "9c"))
    hero = {HandCombo(Card.from_str("9s"), Card.from_str("9d")): 1.0}
    villain = {HandCombo(Card.from_str("Ah"), Card.from_str("Kh")): 1.0}
    solve_flop(board, hero, villain, pot=5.0, effective_stack_bb=20.0,
               positions=("OOP", "IP"), iterations=5, equity_samples=10,
               equity_seed=1234, equity_table_fn=spy)
    assert seen.get("seed") == 1234, (
        f"solve_flop did not pass equity_seed to the injected builder (got "
        f"{seen.get('seed')!r}) — this is exactly how F44 shipped"
    )


def test_nine_max_uses_the_budget_that_actually_converges():
    """M157. 9-max was left at a budget its own config called insufficient.

    `api/config.py` said 9-max "does not converge at any affordable
    budget" and kept 3,000 iterations with the CFR+ clamp "because more
    is directionally better, not because it is enough". The 12.5% T7s
    figure behind that was measured at ONE budget; the conclusion that a
    converging count is unaffordable was an inference, and nobody ran a
    higher one.

    Measured across three seeds, no overlap between arms:

        arm             T7s fold                 AA jam    72o fold
        3,000 + clamp   .1522 / .0678 / .1450    .81-.85   .973-.982
        12,000 plain    .8628 / .4508 / .8783    .06-.17   1.0000

    T7s reaches a mean 0.731 against 6-max's documented 0.874. M71 kept
    the clamp here "until its budget can support the better rule" — at
    1,333 traversals per seat instead of 333, it does.
    """
    # The autouse fixture rewrites MULTIWAY_TABLE_CONFIGS' iteration
    # counts down for speed, so the SHIPPED budget has to be read from
    # the source rather than the patched module.
    import ast
    import inspect

    source = inspect.getsource(api_config)
    tree = ast.parse(source)
    shipped = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "MULTIWAY_TABLE_CONFIGS" for t in node.targets
        ):
            shipped = ast.literal_eval(node.value)
    assert shipped is not None, "MULTIWAY_TABLE_CONFIGS not found in source"
    assert shipped[9]["iterations"] >= 12_000, (
        "9-max is back below the budget where it converges; at 3,000 its "
        "under-the-gun fold rate for T7s was 0.12 against 6-max's 0.87"
    )
    table = api_config.MULTIWAY_TABLE_CONFIGS[9]
    assert not table.get("floor_regret"), (
        "9-max is back on the CFR+ clamp. M71 kept it only until the budget "
        "could support plain CFR, and at 12,000 iterations plain CFR wins on "
        "every measure across three seeds"
    )


def test_nine_max_still_warns_but_no_longer_quotes_withdrawn_numbers():
    """M157. The warning has to match what was measured.

    It previously told users T7s "reaches only 0.30 at 9,000 iterations",
    and that 9-max "does not converge at any affordable budget". Both are
    withdrawn: 12,000 iterations without the clamp reach 0.73 on average.
    The cell is still flagged, because the seed spread is 0.43.
    """
    reason = api_config.LOW_CONFIDENCE_TABLE_SIZES[9]
    assert "does not converge at any affordable budget" not in reason, (
        "the warning still states the claim M157 measured as false"
    )
    assert "0.30" not in reason, "the warning still quotes a withdrawn measurement"
    assert "seed" in reason, (
        "the warning should name what is still wrong — the answer moves with "
        "the solver's seed — rather than only that it is imperfect"
    )
    assert 9 in api_config.LOW_CONFIDENCE_TABLE_SIZES, "9-max must stay flagged"


def test_every_prewarm_step_succeeds(monkeypatch):
    """M133. The pre-warm swallows each step's exception so one bad spot
    cannot cost the others — which meant a step could fail forever in
    silence. `solve_river_from_path` did exactly that: it asked for a
    flop line of ["raise", "call_or_check"], but that endpoint's tree
    runs at FLOP_TO_RIVER_MAX_RAISES=1 with no raise sizes, so no sized
    raise exists and every attempt raised "'raise' is not legal at this
    node". It had never once warmed anything, and every default river
    request paid the full ~43s cold cost.

    M124 made the outcomes recordable; this asserts they are all OK, so
    a pre-warm line that stops matching its tree fails here rather than
    quietly costing users the thing the pre-warm exists to prevent.

    The heavy steps are stubbed — this checks that each line is LEGAL and
    reachable, not that the solves are fast.
    """
    monkeypatch.setattr(api_config, "PREWARM_STACK_DEPTHS", (100,))
    monkeypatch.setattr(api_config, "MULTIWAY_PREWARM_STACK_DEPTHS", ())
    monkeypatch.setattr(api_main, "DEFAULT_ITERATIONS", FAST_ITERATIONS)
    monkeypatch.setattr(api_config, "DEFAULT_RIVER_PATH_QUERY_ITERATIONS", FAST_ITERATIONS)
    monkeypatch.setattr(api_config, "RIVER_PATH_QUERY_MAX_COMBOS_PER_SIDE", 1)
    monkeypatch.setattr(api_config, "MAX_PATH_QUERY_CLASSES_PER_SIDE", 2)
    # The autouse fixture shrinks the turn tree to max_raises=1 with no
    # raise sizes for speed, which removes the sized raise the turn
    # pre-warm's line depends on. Restore the PRODUCTION shape for these
    # two, because the question here is whether each line is legal in the
    # tree it will actually run against — under the shrunken tree the
    # turn line is legitimately illegal and would fail for the wrong
    # reason. That fragility is itself the point: a line is only legal
    # because of a config value living somewhere else.
    monkeypatch.setattr(api_config, "FLOP_TURN_MAX_RAISES", 2)
    monkeypatch.setattr(api_config, "FLOP_TURN_RAISE_SIZES", (2.5,))

    api_main._prewarm_common_depths()

    failed = [s for s in api_main.PREWARM_STATUS["steps"] if not s["ok"]]
    assert not failed, (
        "pre-warm steps failed: "
        + "; ".join(f"{s['name']} -> {s['error']}" for s in failed)
    )
    assert api_main.PREWARM_STATUS["finished"]


def test_the_combo_budget_cap_keeps_every_class_including_the_premiums():
    """M135. `_cap_combo_range` is kept at its default (unused) because
    it was MEASURED WORSE, not because it fails to do what it claims —
    and the distinction is the finding.

    It does exactly what M130's diagnosis asked for: trimming 1,176
    combos to 300 keeps all four premiums and all 169 classes, where the
    shipped class-level cap drops every premium. And the advice it
    produces is **2x worse** at matched pool size (mean error 0.111
    against 0.058), non-monotone in budget.

    So premium exclusion is a correlate, not the cause. This pins the
    structural claim so the negative result stays interpretable: if this
    ever stopped keeping premiums, the measurement above would no longer
    mean what it says.
    """
    from poker_solver.cards import Card
    from poker_solver.combos import combo_class, range_from_class_frequencies
    from poker_solver.starting_hands import all_starting_hands
    from api.solving import _cap_combo_range

    board = frozenset(Card.from_str(t) for t in ("2h", "6d", "9c"))
    premiums = {"AA", "KK", "QQ", "AKs"}
    # premiums at LOWER frequency, the way a raiser's real range has them
    freqs = {hand: (0.5 if str(hand) in premiums else 0.99)
             for hand in all_starting_hands()}
    full = range_from_class_frequencies(freqs, exclude=board)

    capped = _cap_combo_range(full, 300)
    assert len(capped) == 300
    kept_classes = {str(combo_class(combo)) for combo in capped}
    assert premiums <= kept_classes, (
        f"the combo budget dropped premiums it is supposed to keep: "
        f"{sorted(premiums - kept_classes)}"
    )
    assert len(kept_classes) == 169, (
        f"round-robin should reach every class, got {len(kept_classes)}"
    )
    # a range that already fits comes back untouched
    assert _cap_combo_range(full, len(full) + 10) == full


# ---------------------------------------------------------------------------
# M163: the mid-flop node solves like the opening decision on its street.
# ---------------------------------------------------------------------------


def test_the_mid_flop_node_solves_at_the_same_equity_precision_as_its_street(
        client, monkeypatch):
    """M163. The two flop decisions are supposed to model the same game.

    M88 split this path off the turn's solve precisely so a user asking
    twice on one street gets one street's answer. But this call passed
    neither `equity_samples` nor `equity_table_fn`, so it built its
    equity table at `board_equity`'s own default of 200 samples,
    SEQUENTIALLY, while the opening decision on the same board built one
    at PATH_QUERY_EQUITY_SAMPLES through the parallel builder. M131 and
    M132 each fixed exactly this for the canonical-library path and
    neither reached here.

    Equity precision is part of the game being modelled, so this is the
    F12 inconsistency in a field M88 did not check — and it cost 35.08s
    against the opening decision's 0.83s on the same board, measured
    through /advise.

    Asserts on the arguments the solve actually receives, because the
    symptom (latency) is exactly what a test fixture shrinks away.
    """
    seen = {}
    real = api_solving.solve_flop

    def spy(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(api_solving, "solve_flop", spy)
    response = client.post("/advise", json={
        "hero_cards": "AhKd", "board": "Qs7c2h", "players": 2, "stack_bb": 100.0,
        "preflop_action_path": ["raise", "call_or_check"],
        "flop_action_path": ["raise"],
    })
    assert response.status_code == 200, response.json()
    assert seen, "the mid-flop node did not reach solve_flop at all"
    assert seen["equity_samples"] == api_config.PATH_QUERY_EQUITY_SAMPLES
    assert seen["equity_table_fn"] is not None


def test_a_second_hero_on_one_board_warm_starts_the_mid_flop_solve(
        client, monkeypatch):
    """M163. `_flop_node_cache`'s key has to include hero (M76), so every
    new hero missed it and paid a full cold solve — 34.55s measured.

    The warm store drops hero and keeps everything that changes the
    ranges, the same split M158 made for the canonical library. This
    counts SOLVES rather than seconds, so the fixture's shrunken budgets
    cannot hide it, and asserts the second hero refines: fewer iterations
    than a cold solve, and a warm start actually supplied.
    """
    calls = []
    real = api_solving.solve_flop

    def spy(**kwargs):
        calls.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(api_solving, "solve_flop", spy)
    body = {"board": "Qs7c2h", "players": 2, "stack_bb": 100.0,
            "preflop_action_path": ["raise", "call_or_check"],
            "flop_action_path": ["raise"]}

    first = client.post("/advise", json={**body, "hero_cards": "AhKd"})
    assert first.status_code == 200, first.json()
    second = client.post("/advise", json={**body, "hero_cards": "9s9d"})
    assert second.status_code == 200, second.json()

    assert len(calls) == 2, "expected one solve per hero"
    assert calls[0]["warm_start"] is None
    assert calls[1]["warm_start"] is not None, (
        "the second hero re-solved from cold — the warm store is not wired"
    )
    assert calls[1]["iterations"] <= calls[0]["iterations"]


def test_the_mid_flop_warm_store_only_ever_holds_a_cold_solve(client, monkeypatch):
    """M163, mirroring `library.build_library`'s own rule.

    If a refinement were stored it would become the base of the next
    refinement, and the effective iteration count would drift away from
    anything that was measured. Three heroes on one board: the stored
    entry must still be the first, cold one.
    """
    calls = []
    real = api_solving.solve_flop

    def spy(**kwargs):
        calls.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(api_solving, "solve_flop", spy)
    body = {"board": "Qs7c2h", "players": 2, "stack_bb": 100.0,
            "preflop_action_path": ["raise", "call_or_check"],
            "flop_action_path": ["raise"]}
    for hero in ("AhKd", "9s9d", "JcTc"):
        assert client.post("/advise", json={**body, "hero": hero} if False else
                           {**body, "hero_cards": hero}).status_code == 200

    assert len(calls) == 3
    warm_starts = [c["warm_start"] for c in calls]
    assert warm_starts[0] is None
    assert warm_starts[1] is not None and warm_starts[2] is not None
    # Every refinement starts from the SAME cold solve, not from each other.
    assert warm_starts[1] is warm_starts[2]


# ---------------------------------------------------------------------------
# M163: on-demand training for a MULTIWAY FLOP node that came back as the
# uniform prior — the postflop sibling of M150's preflop trainer.
#
# The defect is RARE (one occurrence in 837 decisions across three
# sessions), so it cannot be summoned through /advise on demand. That is
# precisely the shape of bug this project has shipped no-op "fixes" for
# before, so the mechanism and the wiring are proven separately.
# ---------------------------------------------------------------------------


def _tiny_multiway_flop_result(iterations):
    """A real 3-way flop solve, deliberately under-trained so untrained
    nodes actually exist."""
    from poker_solver.cards import Card
    from poker_solver.combos import range_from_class_frequencies
    from poker_solver.solver import solve_flop_multiway
    from poker_solver.starting_hands import all_starting_hands

    board = tuple(Card.from_str(c) for c in ("Jh", "7d", "2c"))
    hands = all_starting_hands()
    positions = ("OOP", "MID", "IP")
    ranges = {
        position: range_from_class_frequencies(
            {h: 1.0 for h in hands[index * 2:index * 2 + 3]}, exclude=frozenset(board))
        for index, position in enumerate(positions)
    }
    result = solve_flop_multiway(
        board=board, position_ranges=ranges, pot=6.0, effective_stack_bb=20.0,
        positions=positions, raise_sizes=(2.5,), max_raises=2,
        iterations=iterations,
    )
    return board, result


def test_on_demand_training_repairs_an_untrained_multiway_flop_node():
    """M163. The mechanism, on a node that really is the bare prior.

    Solved at one iteration so untrained nodes exist, then trained on
    demand — the node must go from "nothing learned" to a real strategy.
    """
    board, result = _tiny_multiway_flop_result(iterations=1)
    target = None
    for node in walk(result.root):
        if not hasattr(node, "player_to_act"):
            continue
        if not any(result.trained_hands(node).values()):
            target = node
            break
    assert target is not None, "no untrained node to repair — fixture too generous"

    did_work = api_solving._ensure_flop_multiway_node_trained(result, target, board)
    assert did_work is True
    assert any(result.trained_hands(target).values()), (
        "the node is still untrained after training it"
    )


def test_on_demand_training_leaves_an_already_solved_node_alone():
    """M163. Scope. A trainer that fires on every node would re-solve the
    tree on every request and, worse, overwrite answers that were derived
    from real ranges with ones derived from a uniform assumption.
    """
    board, result = _tiny_multiway_flop_result(iterations=200)
    root = result.root
    if not any(result.trained_hands(root).values()):
        pytest.skip("fixture produced an untrained root; nothing to assert here")
    before = {hand: dict(row) for hand, row in result.strategy_at(root).items()}
    assert api_solving._ensure_flop_multiway_node_trained(result, root, board) is False
    assert result.strategy_at(root) == before


def test_on_demand_training_is_wired_to_the_multiway_flop_response(client, monkeypatch):
    """M163. The unit tests above would pass with the call site missing.

    F43 shipped exactly that: every unit test green while the signal
    never fired, because the production path did not call it. This
    asserts the real /advise path reaches the trainer, at BOTH flop
    nodes, with the node it is actually answering about.
    """
    seen = []
    real = api_solving._ensure_flop_multiway_node_trained

    def spy(result, node, board_cards, hero_combo=None):
        seen.append(node)
        return real(result, node, board_cards, hero_combo)

    monkeypatch.setattr(api_solving, "_ensure_flop_multiway_node_trained", spy)
    body = {"hero_cards": "AhKd", "board": "Qs7c2h", "players": 6, "stack_bb": 100.0,
            # Three live players after the flop — UTG/MP fold, CO raises,
            # BTN calls, SB folds, BB calls. A path that folds down to two
            # takes the HEADS-UP flop cell instead, which this trainer does
            # not serve; an earlier version of this test used one and
            # asserted against a code path it never reached.
            "preflop_action_path": ["fold", "fold", "raise", "call_or_check",
                                    "fold", "call_or_check"]}

    opening = client.post("/advise", json=body)
    assert opening.status_code == 200, opening.json()
    assert len(seen) == 1, "the opening decision never reached the trainer"

    seen.clear()
    # `call_or_check`, not `raise`: the suite fixture shrinks
    # MULTIWAY_FLOP_RAISE_SIZES, so the multiway flop root offers only
    # check and all-in. Checking still reaches the NEXT player's decision,
    # which is the mid-street node this needs.
    mid = client.post("/advise", json={**body, "flop_action_path": ["call_or_check"]})
    assert mid.status_code == 200, mid.json()
    assert len(seen) == 2, (
        "expected the opening decision AND the mid-flop node to be offered for training"
    )
    assert seen[0] is not seen[1], "the mid-flop node was not the one offered"


def test_the_multiway_flop_trainer_is_given_a_string_hero_key():
    """M164. The bug M163's own tests could not see.

    `StrategyResult.strategy_at` keys by `str(hand)`. M163 passed the
    HandCombo OBJECT, so `strategy.get(hero_key)` never matched and the
    hero-row trigger — the entire reason that function exists — could
    never fire. It still ran on the much rarer "nothing at this node is
    trained" condition, which is exactly what M163's tests exercised, so
    they passed while three play sessions showed the uniform rows
    completely unchanged.

    Asserts the KEY TYPE at the production call site, because that is the
    thing that was wrong and a behavioural test at this node needs a spot
    the suite's shrunken fixtures do not reliably produce.
    """
    import inspect

    source = inspect.getsource(api_solving._query_flop_multiway_from_path)
    assert "_ensure_flop_multiway_node_trained(" in source
    # Both call sites must stringify hero before handing it over.
    assert source.count("str(hero_combo)") >= 2, (
        "the multiway flop trainer is being passed a non-string hero key again — "
        "strategy_at() keys by str(hand), so an object key silently never matches"
    )


def test_the_multiway_flop_trainer_fires_on_a_uniform_hero_row():
    """M164. The behaviour the key type controls.

    A node where SOME hands are trained but the hand being asked about
    still reads as the bare prior is precisely the case M163 meant to fix
    and could not reach. Given a string key that is present and uniform,
    the trainer must do work and must change that row.
    """
    board, result = _tiny_multiway_flop_result(iterations=1)
    target = row_key = None
    for node in walk(result.root):
        if not hasattr(node, "player_to_act"):
            continue
        strategy = result.strategy_at(node)
        for key, row in strategy.items():
            if len(row) > 1 and max(row.values()) - min(row.values()) < 1e-9:
                target, row_key = node, key
                break
        if target is not None:
            break
    assert target is not None, "fixture produced no uniform row to repair"

    before = dict(result.strategy_at(target)[row_key])
    did_work = api_solving._ensure_flop_multiway_node_trained(
        result, target, board, row_key)
    assert did_work is True, "the trainer did not fire on a uniform hero row"
    after = result.strategy_at(target)[row_key]
    assert after != before or any(result.trained_hands(target).values()), (
        "the trainer claimed to work but the node is unchanged"
    )


# ---------------------------------------------------------------------------
# M165: the heads-up river node, repaired on demand.
#
# Different cause from M163/M164's multiway case, which is why it needed
# its own fix: the exact solver VISITS every hand at every node, so these
# rows are `trained: true` and still exactly uniform — every regret stayed
# <= 0 through the chained solve's small iteration budget.
# ---------------------------------------------------------------------------


def _river_branch_fixture():
    """A real flop->turn->river solve, plus one river branch off it."""
    from poker_solver.cards import Card
    from poker_solver.combos import range_from_class_frequencies
    from poker_solver.game_tree import TerminalNode as _Terminal
    from poker_solver.solver import solve_flop_to_river
    from poker_solver.starting_hands import all_starting_hands

    board = tuple(Card.from_str(c) for c in ("3d", "Kc", "4h"))
    hands = all_starting_hands()
    rng = range_from_class_frequencies({h: 1.0 for h in hands[:4]},
                                       exclude=frozenset(board))
    result = solve_flop_to_river(
        board=board, hero_range=rng, villain_range=rng, pot=6.0,
        effective_stack_bb=12.0, iterations=2,
        raise_sizes=(), max_raises=1,
    )
    for chance_node in result.chance_data.values():
        for branch in chance_node.branches.values():
            if not isinstance(branch.root, _Terminal) and hasattr(
                    branch.root, "player_to_act"):
                return result, branch
    return result, None


def test_the_exact_node_trainer_repairs_a_uniform_river_row():
    """M165. Measured on a real request before this existed: 10 of 19
    hands at one river node read as the bare prior, hero among them —
    `check 0.5 / all-in 0.5` holding jack-high, which is a stack-losing
    recommendation. Re-solving that subtree alone fixes it.
    """
    result, branch = _river_branch_fixture()
    if branch is None:
        pytest.skip("fixture produced no non-terminal river branch")

    strategy = result.strategy_at(branch.root)
    uniform_keys = [k for k, row in strategy.items()
                    if len(row) > 1 and max(row.values()) - min(row.values()) < 1e-9]
    if not uniform_keys:
        pytest.skip("fixture produced no uniform river row to repair")

    key = uniform_keys[0]
    did_work = api_solving._ensure_exact_node_trained(
        result, branch.root, branch.equity_table, key)
    assert did_work is True
    after = result.strategy_at(branch.root)[key]
    assert max(after.values()) - min(after.values()) > 1e-9, (
        "the row is still exactly the prior after training"
    )


def test_the_river_trainer_is_given_that_rivers_own_equity_table(client, monkeypatch):
    """M165. The mistake that would be easiest to make here and hardest
    to see: passing the FLOP's equity table would value every hand on the
    wrong board and still return a confident-looking answer.

    Also pins the key type, for the reason M164 exists.
    """
    seen = []
    real = api_solving._ensure_exact_node_trained

    def spy(result, node, equity_table, hero_key=None):
        seen.append({"table": equity_table, "hero_key": hero_key,
                     "hands": len(result.hands)})
        return real(result, node, equity_table, hero_key)

    monkeypatch.setattr(api_solving, "_ensure_exact_node_trained", spy)
    response = client.post("/advise", json={
        "hero_cards": "9cJc", "board": "3dKc4h", "players": 2, "stack_bb": 20.0,
        "preflop_action_path": ["raise", "call_or_check"],
        "flop_action_path": ["call_or_check", "call_or_check"],
        "turn_card": "4s", "turn_action_path": ["call_or_check", "call_or_check"],
        "river_card": "8c",
    })
    assert response.status_code == 200, response.json()
    assert seen, "the river cell never reached the trainer"
    entry = seen[0]
    assert isinstance(entry["hero_key"], str), (
        "hero key must be a string — strategy_at() keys by str(hand), see M164"
    )
    # A river table is square over the combo pool, and is the branch's own:
    # the flop table for the same pool is a different object.
    assert entry["table"] is not None
    assert entry["table"].shape == (entry["hands"], entry["hands"])


def test_the_exact_node_trainer_leaves_a_differentiated_node_alone():
    """M165. Scope. Firing on every node would re-solve subtrees on every
    request and overwrite answers derived from real ranges with ones
    derived from a uniform assumption.
    """
    result, branch = _river_branch_fixture()
    if branch is None:
        pytest.skip("fixture produced no non-terminal river branch")
    strategy = result.strategy_at(branch.root)
    uniform_keys = [k for k, row in strategy.items()
                    if len(row) > 1 and max(row.values()) - min(row.values()) < 1e-9]
    if not uniform_keys:
        pytest.skip("fixture produced no uniform row")
    key = uniform_keys[0]

    # Train once — at this fixture's tiny budget every row starts uniform,
    # so a differentiated row has to be produced rather than found.
    assert api_solving._ensure_exact_node_trained(
        result, branch.root, branch.equity_table, key) is True
    trained_row = dict(result.strategy_at(branch.root)[key])
    assert max(trained_row.values()) - min(trained_row.values()) > 1e-9

    # Asking again must be a no-op. This is also what stops a refinement
    # becoming the base of the next one on every subsequent request.
    assert api_solving._ensure_exact_node_trained(
        result, branch.root, branch.equity_table, key) is False
    assert result.strategy_at(branch.root)[key] == trained_row


# ---------------------------------------------------------------------------
# M166: the postflop caveat is calibrated to hero's own hand.
#
# Measured over 27 flop spots drawn from real play: nothing in the upper
# two strength bands was off by more than 0.10, and half the weak band
# was — worst 0.90. The advice could not be fixed (ten attempts are
# recorded as dead), so the response now says which band it is in.
# ---------------------------------------------------------------------------


def test_the_per_hand_certification_machinery_still_works_when_a_street_qualifies(client, monkeypatch):
    """M167's two-band mechanism, kept alive though no street currently
    qualifies for it (M180 withdrew the flop's certificate).

    The machinery is deliberately retained rather than deleted: restoring
    a certificate is a stated possibility, needing a fresh study at >= 28
    spots per band. Untested dormant code would rot, so this exercises it
    by certifying the flop for the duration of the test only.

    What it guards is the property a constant-returning implementation
    would break: same board, same action, two hands, two different
    verdicts, split at the strength threshold.
    """
    monkeypatch.setattr(api_config, "CERTIFY_RELIABILITY_ON_STREETS", ("flop",))
    base = {"board": "As7d2h", "players": 2, "stack_bb": 100.0,
            "preflop_action_path": ["raise", "call_or_check"]}

    weak = client.post("/advise", json={**base, "hero_cards": "9s8s"}).json()
    assert weak["hand_strength_percentile"] < api_config.RELIABLE_HAND_STRENGTH_PERCENTILE
    assert weak["aggression_confidence_reason"].startswith(api_config.UNCERTAIN_HAND_NOTE)

    strong = client.post("/advise", json={**base, "hero_cards": "AhAd"}).json()
    assert strong["hand_strength_percentile"] >= api_config.RELIABLE_HAND_STRENGTH_PERCENTILE
    assert strong["aggression_confidence_reason"].startswith(api_config.RELIABLE_HAND_NOTE)

    assert (strong["aggression_confidence_reason"]
            != weak["aggression_confidence_reason"])


def test_the_band_note_quotes_its_own_measurement():
    """M140's rule: copy that states a number must state the number that
    was measured, so the two cannot drift apart."""
    reliable = api_config.RELIABLE_HAND_NOTE
    assert str(round(api_config.RELIABLE_HAND_ERROR_WORST * 100)) in reliable
    uncertain = api_config.UNCERTAIN_HAND_NOTE
    # ~29% is "about one in four"; the worst measured is 0.99.
    assert 0.20 <= api_config.UNCERTAIN_SHARE_OVER_TEN_POINTS <= 0.35
    assert "99" in uncertain


def test_preflop_has_no_hand_strength_reading(client):
    """There is no board preflop, so there is nothing to measure strength
    against. Reporting one would be inventing it."""
    response = client.post("/advise", json={
        "hero_cards": "AhAd", "players": 2, "stack_bb": 100.0,
        "preflop_action_path": [],
    })
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["hand_strength_percentile"] is None
    assert body["aggression_confidence_reason"] is None


def test_reliability_is_only_certified_on_the_street_where_it_was_measured(client):
    """M168. The correction M167 needed and did not have evidence for.

    M167 measured the reliability threshold on FLOP spots and applied it
    to every street because that was cheaper than checking. Checked on
    eight turn spots, the relationship inverts: the band being certified
    was the worst one — three of four spots off by more than 0.10, worst
    0.588, on a hand at percentile 0.977.

    So a strong hand on the turn must NOT be told the advice measured
    reliable. It gets told reliability is not known, which is true.
    """
    strong_turn = client.post("/advise", json={
        "hero_cards": "AhAd", "board": "As7d2h", "players": 2, "stack_bb": 100.0,
        "preflop_action_path": ["raise", "call_or_check"],
        "flop_action_path": ["call_or_check", "call_or_check"],
        "turn_card": "9c",
    })
    assert strong_turn.status_code == 200, strong_turn.json()
    body = strong_turn.json()
    # The hand really is strong — this is not passing by accident.
    assert body["hand_strength_percentile"] >= api_config.RELIABLE_HAND_STRENGTH_PERCENTILE
    reason = body["aggression_confidence_reason"]
    assert reason.startswith(api_config.UNMEASURED_STREET_NOTE), reason[:120]
    assert not reason.startswith(api_config.RELIABLE_HAND_NOTE), (
        "a strong TURN hand is still being certified as reliable — the turn "
        "measurement says that band is the least accurate one"
    )


def test_the_flop_no_longer_certifies_a_strong_hand(client):
    """M180 replaces M168's "the flop still certifies" guard.

    M167 certified the flop on 9 spots at cap 26 / 500 iterations. M172
    changed both and it was never re-run; at 28 strong-band spots the
    certificate fails 6 times, worst 0.9535 — a top pair the reference
    bets 0.9987 where the product bets 0.045.

    A strong flop hand must now get the measured-and-refused note, not a
    reliability claim.
    """
    response = client.post("/advise", json={
        "hero_cards": "AhAd", "board": "As7d2h", "players": 2, "stack_bb": 100.0,
        "preflop_action_path": ["raise", "call_or_check"],
    })
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["hand_strength_percentile"] >= api_config.RELIABLE_HAND_STRENGTH_PERCENTILE
    reason = body["aggression_confidence_reason"]
    assert reason.startswith(api_config.FLOP_MEASURED_NOTE), reason[:120]
    assert not reason.startswith(api_config.RELIABLE_HAND_NOTE), (
        "a strong flop hand is being told the advice measured reliable; that "
        "claim was withdrawn in M180 on 28 spots")


def test_the_unmeasured_street_note_says_why_rather_than_only_that(client):
    """A caveat that says "unknown" teaches nothing. This one has to carry
    the reason, because the reason is actionable: on this street hand
    strength does not tell you which answers to trust.

    M175: this used to assert the word "least accurate", pinning M168's
    claim that the reliable-looking band was the WORST one on the turn.
    Re-measured over 24 spots that is false — the two bands are
    indistinguishable — so the note must not say it, and must equally not
    let a reader infer that weak turn hands are the safe ones.
    """
    note = api_config.UNMEASURED_STREET_NOTE.lower()
    assert "has not been measured" in note
    assert "does not substitute" in note
    # The measured fact that replaces the withdrawn one: error is large at
    # BOTH ends, so neither band is the safe one.
    assert "both ends" in note
    assert "least accurate" not in note


def test_the_costly_band_needs_BOTH_conditions(client, monkeypatch):
    """M189. The cost concentrates non-monotonically by hand strength:
    both the weakest and the strongest hands are cheap, and the money is
    in roughly the 55th-90th percentile — "is my top pair actually good?".

    Facing a bet AND in that band is **12% of postflop decisions carrying
    74% of all cost** (lift 6.1x), against M185's coarse rule at 33% and
    2.9x. Neither condition alone is the signal: an in-band hand acting
    first is cheap, and a facing-a-bet decision outside the band is much
    cheaper than one inside it.
    """
    monkeypatch.setattr(api_config, "FLOP_TURN_RAISE_SIZES", (2.5,))
    monkeypatch.setattr(api_config, "FLOP_TURN_MAX_RAISES", 2)
    base = {"stack_bb": 100.0, "preflop_action_path": ["raise", "call_or_check"],
            "players": 2, "board": "Kd7c2h"}

    def reason_for(hero, facing):
        body = {**base, "hero_cards": hero}
        if facing:
            body["flop_action_path"] = ["raise"]
        payload = client.post("/advise", json=body).json()
        return payload["aggression_confidence_reason"], payload["hand_strength_percentile"]

    # 9c9d on Kd7c2h sits inside the band; KsQh above it; Tc9c below.
    in_facing, pct_in = reason_for("9c9d", True)
    assert api_config.COSTLY_BAND_LOW <= pct_in < api_config.COSTLY_BAND_HIGH, pct_in
    assert api_config.COSTLY_BAND_NOTE in in_facing

    in_opening, _ = reason_for("9c9d", False)
    assert api_config.COSTLY_BAND_NOTE not in in_opening, (
        "the band note fired on an opening decision; acting first with an "
        "in-band hand is cheap and the signal needs BOTH conditions")

    above, pct_hi = reason_for("KsQh", True)
    assert pct_hi >= api_config.COSTLY_BAND_HIGH
    assert api_config.COSTLY_BAND_NOTE not in above, (
        "very strong hands measured 11.5% expensive against the band's 44%")

    below, pct_lo = reason_for("Tc9c", True)
    assert pct_lo < api_config.COSTLY_BAND_LOW
    assert api_config.COSTLY_BAND_NOTE not in below, (
        "weak hands measured 4-5% expensive; flagging them dilutes the signal")


def test_the_band_is_narrow_enough_to_be_worth_reading(client):
    """M189. The point of the band over M185's coarse flag is that it
    fires rarely. A signal on a third of all decisions is one a player
    learns to ignore (M167); this one fires on ~12%.

    Guarding the WIDTH rather than the prose: if someone widens the band
    to catch more cost, they trade away the property that makes it worth
    surfacing, and should do that deliberately.
    """
    width = api_config.COSTLY_BAND_HIGH - api_config.COSTLY_BAND_LOW
    assert width <= 0.40, (
        f"the costly band spans {width:.2f} of the strength range; wider than "
        "0.40 and it approaches M185's coarse flag, which fires on a third of "
        "decisions and was measured at less than half this one's lift")
    assert api_config.COSTLY_BAND_LOW > 0.4, (
        "extending the band down into weak hands adds decisions that measured "
        "4-5% expensive and dilutes it")
    assert api_config.COSTLY_BAND_HIGH < 1.0, (
        "the very strongest hands measured 11.5% expensive, well below the "
        "band's 44% — including them dilutes it")


def test_facing_a_bet_is_flagged_as_where_the_cost_is(client, monkeypatch):
    """M185. M183 priced 48 real decisions in chips and found the cost
    concentrated by NODE TYPE: facing a bet means mean |loss| 0.3107 bb
    against 0.0569 when acting first — 5.5x, separable at 2.58 sigma with
    a permutation p of 0.0054, and 85% of all measured loss.

    That is the only runtime-visible signal worth surfacing. A finer one
    was looked for and does not exist: the best runtime feature is the
    action count at +0.237, against +0.772 for TVD x spread, which needs
    a reference solve.

    The flag is derived from the ROWS — folding is legal only when facing
    a bet — so it survives changes to path shapes and size menus, the same
    reason M144 built the sizing note that way.
    """
    monkeypatch.setattr(api_config, "FLOP_TURN_RAISE_SIZES", (2.5,))
    monkeypatch.setattr(api_config, "FLOP_TURN_MAX_RAISES", 2)
    base = {"stack_bb": 100.0, "preflop_action_path": ["raise", "call_or_check"],
            "players": 2, "hero_cards": "5c4d", "board": "Kd7c2h"}

    opening = client.post("/advise", json=base).json()
    facing = client.post("/advise", json={**base, "flop_action_path": ["raise"]}).json()

    assert api_config.FACING_A_BET_COST_NOTE not in opening["aggression_confidence_reason"], (
        "the cost note fired on an opening decision, where folding is not even "
        "legal — it would then be attached to every answer and mean nothing")
    assert api_config.FACING_A_BET_COST_NOTE in facing["aggression_confidence_reason"]

    # It must not overstate: the MEDIAN facing decision costs 0.0235 bb,
    # nearly as cheap as an opening one. It is the tail that differs.
    note = api_config.FACING_A_BET_COST_NOTE.lower()
    assert "median one costs almost nothing" in note, (
        "the note must say the typical decision here is cheap; M186 measured "
        "the median facing-a-bet loss at 0.0096 bb")
    assert "most individual answers here are still accurate" in note, (
        "the note must not imply this particular answer is probably wrong; "
        "the median facing-a-bet decision is nearly as cheap as any other")


def test_the_cost_flag_follows_the_rows_not_the_request(client, monkeypatch):
    """M185. Reading the flag off `flop_action_path` would look equivalent
    and is not: the request says what was ASKED, the rows say what the
    tree actually OFFERED. Those diverge whenever a path resolves to a
    node that cannot fold — and the note would then fire on decisions
    where folding is not an option, which is exactly what makes it
    meaningless.
    """
    import inspect

    source = inspect.getsource(api_main._is_facing_a_bet)
    assert "strategy" in source and "fold" in source
    assert "action_path" not in source, (
        "the flag is being read from the request rather than the rows")

    monkeypatch.setattr(api_config, "FLOP_TURN_RAISE_SIZES", (2.5,))
    monkeypatch.setattr(api_config, "FLOP_TURN_MAX_RAISES", 2)
    facing = client.post("/advise", json={
        "stack_bb": 100.0, "preflop_action_path": ["raise", "call_or_check"],
        "players": 2, "hero_cards": "5c4d", "board": "Kd7c2h",
        "flop_action_path": ["raise"]}).json()
    rows = list(facing["strategy"].values())
    assert any("fold" in row for row in rows), (
        "this fixture is supposed to reach a node that can fold")
    assert api_main._is_facing_a_bet(
        {"strategy": facing["strategy"]}) is True
    assert api_main._is_facing_a_bet(
        {"strategy": {"AhAd": {"call_or_check": 0.5, "all_in:97.50": 0.5}}}) is False


def test_a_facing_a_bet_node_is_answerable_on_every_postflop_street(client, monkeypatch):
    """M181. Every postflop accuracy study in this project measured each
    street's OPENING decision until M177, and the reason was not an
    oversight in the studies — **the play harness never generated any
    other kind of node.** All 368 river spots across ten benchmark
    sessions were opening decisions.

    That is the blind spot F38 was eventually found through: with
    nine-high FACING A BET the product recommended shoving 97.5bb 0.567
    of the time where the correct play is to fold 0.987. Folding is not
    even a legal action at a street's opening decision, so no amount of
    measuring there could see it. M177 then measured the river properly
    and found facing-a-bet cells up to 6x worse than opening ones.

    This pins the API capability the studies depend on: a facing-a-bet
    node must be reachable and answerable on all three streets, and must
    actually offer `fold` — which is what makes it a different question.
    """
    # The suite fixture zeroes FLOP_TURN_RAISE_SIZES for speed, so under it
    # the turn and river trees offer only check and all-in and a
    # facing-a-BET node cannot exist — M143's trap, and the same reason its
    # own guard restores a real size. Restoring one is what makes the node
    # (and therefore this property) testable at all.
    monkeypatch.setattr(api_config, "FLOP_TURN_RAISE_SIZES", (2.5,))
    monkeypatch.setattr(api_config, "FLOP_TURN_MAX_RAISES", 2)
    monkeypatch.setattr(api_config, "RIVER_STANDALONE_RAISE_SIZES", (2.5,))
    monkeypatch.setattr(api_config, "RIVER_STANDALONE_MAX_RAISES", 2)

    base = {"stack_bb": 100.0, "preflop_action_path": ["raise", "call_or_check"],
            "players": 2, "hero_cards": "5c4d", "board": "Kd7c2h"}
    closed = ["call_or_check", "call_or_check"]
    cases = {
        "flop": {**base, "flop_action_path": ["raise"]},
        "turn": {**base, "flop_action_path": closed, "turn_card": "Ts",
                 "turn_action_path": ["raise"]},
        "river": {**base, "flop_action_path": closed, "turn_card": "Ts",
                  "turn_action_path": closed, "river_card": "4c",
                  "river_action_path": ["raise"]},
    }
    for street, body in cases.items():
        response = client.post("/advise", json=body)
        assert response.status_code == 200, (street, response.json())
        payload = response.json()
        assert payload["street"] == street
        hero_row = (payload["strategy"].get("5c4d")
                    or payload["strategy"].get("4d5c"))
        assert hero_row, (street, sorted(payload["strategy"])[:5])
        assert "fold" in hero_row, (
            f"{street}: a facing-a-bet node must offer fold — without it this "
            f"is the opening decision again, got {sorted(hero_row)}")
        assert sum(hero_row.values()) == pytest.approx(1.0, abs=1e-6)


def test_closing_a_street_is_required_before_the_next_card(client, monkeypatch):
    """M181, the wrinkle that makes the harness fix non-trivial: a turn
    path handed to a RIVER request has to CLOSE the turn's betting. A bare
    ["raise"] leaves the turn open, and the API correctly refuses to deal
    a river card rather than inventing one.

    Guarding it because the natural way to add facing-a-bet coverage —
    reusing the street's own path downstream — produces exactly this
    error, silently turning river coverage into 422s.
    """
    monkeypatch.setattr(api_config, "FLOP_TURN_RAISE_SIZES", (2.5,))
    monkeypatch.setattr(api_config, "FLOP_TURN_MAX_RAISES", 2)
    base = {"stack_bb": 100.0, "preflop_action_path": ["raise", "call_or_check"],
            "players": 2, "hero_cards": "5c4d", "board": "Kd7c2h",
            "flop_action_path": ["call_or_check", "call_or_check"],
            "turn_card": "Ts", "river_card": "4c"}

    unclosed = client.post("/advise", json={**base, "turn_action_path": ["raise"]})
    assert unclosed.status_code == 422, unclosed.json()
    assert "does not close" in str(unclosed.json()["detail"]).lower()

    closed = client.post("/advise", json={
        **base, "turn_action_path": ["raise", "call_or_check"]})
    assert closed.status_code == 200, closed.json()
    assert closed.json()["street"] == "river"


def test_no_street_claims_certified_reliability_any_more(client):
    """M180. The flop's certificate was granted by M167 on **9 spots**
    (mean 0.0144, worst 0.0571, zero over 0.10) at cap 26 / 500
    iterations. M172 changed both and it was never re-run.

    Re-measured on 28 strong-band spots: **6 over 0.10, worst 0.9535** —
    a top pair the reference bets 0.9987 where the product bets 0.045.
    Not coverage (it fails at cap 100, 140 and uncapped) and not precision
    (flat at 250 / 500 / 2500 iterations). No threshold rescues it; the
    failures include percentiles 0.913 and 0.978.

    Certifying needs positive evidence FOR a street. Here there is
    positive evidence AGAINST, so the claim is withdrawn rather than
    merely unsupported.
    """
    assert api_config.CERTIFY_RELIABILITY_ON_STREETS == (), (
        "a street is certified again; restoring one needs a fresh study at "
        ">= 28 spots per band with a stability-checked reference, not a "
        "re-run of M167's nine")
    assert api_config.FLOP_CERTIFICATION_FAILURES > 0
    assert api_config.FLOP_CERTIFICATION_SPOTS >= 28


def test_the_flop_note_says_measured_and_refuses_to_name_a_direction(client):
    """M180. The flop cannot use the unmeasured-street note — that note
    says accuracy "has not been measured against a larger solve the way
    the flop has", which contradicts itself when shown on the flop.

    And the flop note must NOT claim which hands are unreliable: at 56
    spots, strong-vs-weak is 1.32 sigma and opening-vs-facing is 1.83
    sigma. Neither is separable. M166 asserted exactly this kind of split
    from a smaller sample and M167 withdrew it.
    """
    body = _advise_body(
        preflop_action_path=["raise", "call_or_check"],
        hero_cards="KcTs", board="ThTd6d", players=2, stack_bb=100.0,
    )
    payload = client.post("/advise", json=body).json()
    assert payload["street"] == "flop"
    reason = payload["aggression_confidence_reason"]

    assert reason.startswith(api_config.FLOP_MEASURED_NOTE), reason[:160]
    assert "has not been measured" not in reason, (
        "the flop has been measured more than any other street; saying "
        "otherwise is false, and self-contradictory in this note's own wording")
    lowered = reason.lower()
    # It must say the error is NOT predictable, not invent a rule.
    assert "neither" in lowered and "predicts" in lowered, (
        "the note must say hand strength does not predict the error — "
        "claiming a direction is what M166 did and M167 had to withdraw")


def test_every_street_note_is_distinct_and_matches_its_evidence(client):
    """M180. Three streets now sit in three different evidential
    positions, and collapsing any two into one sentence would misstate at
    least one of them:

      flop  — measured, refused, no usable direction (neither split separable)
      turn  — measured, refused, correlation +0.057 (no signal at all)
      river — measured, refused, strongly one-sided (strong hands worse)
    """
    notes = {api_config.FLOP_MEASURED_NOTE,
             api_config.UNMEASURED_STREET_NOTE,
             api_config.RIVER_MEASURED_NOTE}
    assert len(notes) == 3, "two streets are sharing a reliability statement"
    # The river names a direction because its split IS separable; the flop
    # must not, because its is not.
    assert "worse for strong hands" in api_config.RIVER_MEASURED_NOTE.lower()
    assert "worse for strong hands" not in api_config.FLOP_MEASURED_NOTE.lower()


def test_the_river_says_it_was_measured_and_which_hands_to_distrust(client):
    """M177. The river was measured (56 spots, 4 cells) and certification
    refused, so the blanket "accuracy on this street has not been
    measured" is now FALSE there — and it buries the actionable half.

    Error concentrates in the band a certificate would vouch for: strong
    hands fail 14 of 28, weak hands 3 of 28, and the direction is
    consistent (over-committing). A player holding a strong hand on the
    river is exactly who needs telling.
    """
    body = _advise_body(
        preflop_action_path=["raise", "call_or_check"],
        flop_action_path=["call_or_check", "call_or_check"], turn_card="Ts",
        turn_action_path=["call_or_check", "call_or_check"], river_card="4c",
        hero_cards="5c4d", board="Kd7c2h", players=2, stack_bb=100.0,
    )
    payload = client.post("/advise", json=body).json()
    assert payload["street"] == "river"
    reason = payload["aggression_confidence_reason"]

    assert reason.startswith(api_config.RIVER_MEASURED_NOTE), reason[:160]
    # It must NOT claim the street is unmeasured any more.
    assert "has not been measured" not in reason, (
        "the river has been measured; saying otherwise is a false disclosure")
    # And it must name the direction, or a player cannot act on it.
    lowered = reason.lower()
    assert "strong" in lowered and "worse" in lowered
    assert "commit" in lowered, (
        "the note must say which way the error runs — over-committing — "
        "not merely that error exists")


def test_the_turn_keeps_the_unmeasured_note_and_the_river_does_not(client):
    """M177. Two streets are uncertified for DIFFERENT reasons and must not
    share one sentence: the turn's strength/error relationship carries no
    signal (M175, correlation +0.057), while the river's is strongly
    one-sided. Collapsing them would restore exactly the over-generalising
    M175 had to withdraw.
    """
    common = {"hero_cards": "AhAd", "board": "Kd7c2h", "players": 2, "stack_bb": 100.0,
              "preflop_action_path": ["raise", "call_or_check"],
              "flop_action_path": ["call_or_check", "call_or_check"]}
    turn = client.post("/advise", json={**common, "turn_card": "Ts"}).json()
    river = client.post("/advise", json={
        **common, "turn_card": "Ts",
        "turn_action_path": ["call_or_check", "call_or_check"], "river_card": "4c"}).json()

    assert turn["street"] == "turn" and river["street"] == "river"
    assert turn["aggression_confidence_reason"].startswith(api_config.UNMEASURED_STREET_NOTE)
    assert river["aggression_confidence_reason"].startswith(api_config.RIVER_MEASURED_NOTE)
    assert (turn["aggression_confidence_reason"]
            != river["aggression_confidence_reason"])


def test_the_river_note_quotes_its_own_measurement(client):
    """M177, the same pin M140 and M175 use. The note tells a player strong
    hands were wrong "more than three times as often"; the recorded
    failure counts have to support that, and stay attached if either moves.
    """
    strong = api_config.RIVER_CERTIFICATION_FAILURES
    total = api_config.RIVER_CERTIFICATION_REFUSED_SPOTS
    weak = 3  # weak-band failures over the same 28 spots
    assert strong > 0 and total >= 20
    assert strong / weak >= 3.0, (
        "the note claims strong hands fail more than 3x as often; the "
        "recorded counts no longer support that")
    assert f"{strong} of {total}" in api_config.RIVER_MEASURED_NOTE
    # And the river must actually be refused certification.
    assert "river" not in api_config.CERTIFY_RELIABILITY_ON_STREETS
    assert api_config.RIVER_SPOTS_PER_CELL >= 12, (
        "M175's lesson: a claimed strength/error split needs more than a "
        "handful of spots per cell before it goes in front of a user")


def test_the_unmeasured_street_note_quotes_its_own_measurement(client):
    """M175, mirroring `test_the_aggression_caveat_quotes_its_own_
    measurement`. The note tells a player the turn was off by more than
    0.30; that number has to be one the measurement actually reached, and
    has to stay attached to it if either moves.

    The failure this prevents is the one M140 found in the aggression
    caveat, which understated its own worst case fivefold.
    """
    quoted = api_config.TURN_RELIABILITY_QUOTED_WORST
    measured = api_config.TURN_CERTIFIED_BAND_WORST_ERROR
    assert quoted <= measured, (
        f"the note quotes {quoted}, which the measurement ({measured}) does not reach")
    assert f"{quoted:.2f}" in api_config.UNMEASURED_STREET_NOTE

    # And the refusal has to rest on the certified band specifically —
    # that is the band a certificate would vouch for.
    assert api_config.TURN_CERTIFIED_BAND_SPOTS_OVER_TENTH > 0
    assert "turn" not in api_config.CERTIFY_RELIABILITY_ON_STREETS


def test_certification_is_refused_on_evidence_not_by_omission(client):
    """M175. The turn is uncertified, and it would be easy for a later
    change to certify it by simply not looking — the flop was certified on
    9 spots, and 4 thin spots per band is what M168 had.

    So the refusal is pinned to a spot count large enough to have been a
    real test. If someone re-measures the turn and it passes, this fails
    and they must update the constants deliberately.
    """
    assert api_config.TURN_RELIABILITY_SPOTS_PER_BAND >= 10, (
        "a band this thin cannot support certifying OR refusing with confidence")
    # M180 withdrew the flop's certificate too, so no street is certified.
    assert api_config.CERTIFY_RELIABILITY_ON_STREETS == ()


def test_every_postflop_street_gets_some_reliability_statement(client):
    """No street may fall through with nothing said about reliability."""
    common = {"hero_cards": "AhAd", "board": "As7d2h", "players": 2,
              "stack_bb": 100.0, "preflop_action_path": ["raise", "call_or_check"]}
    cases = [
        ("flop", {}),
        ("turn", {"flop_action_path": ["call_or_check", "call_or_check"],
                  "turn_card": "9c"}),
        ("river", {"flop_action_path": ["call_or_check", "call_or_check"],
                   "turn_card": "9c",
                   "turn_action_path": ["call_or_check", "call_or_check"],
                   "river_card": "4s"}),
    ]
    seen = {}
    for street, extra in cases:
        response = client.post("/advise", json={**common, **extra})
        assert response.status_code == 200, (street, response.json())
        reason = response.json()["aggression_confidence_reason"]
        assert reason, f"{street} carried no reliability statement at all"
        seen[street] = reason
    # M177: all three statements must DIFFER, because all three streets
    # are now in different evidential positions — the flop is certified
    # above a strength threshold, the turn was measured and carries no
    # usable strength signal, and the river was measured and carries a
    # strongly one-sided one. This used to assert the turn and river
    # shared a sentence; collapsing them again would restore exactly the
    # over-generalisation M175 withdrew.
    assert len({seen["flop"], seen["turn"], seen["river"]}) == 3, (
        "two streets are sharing a reliability statement despite resting on "
        "different evidence")
    assert seen["turn"].startswith(api_config.UNMEASURED_STREET_NOTE)
    assert seen["river"].startswith(api_config.RIVER_MEASURED_NOTE)


def test_the_standalone_river_answers_rather_than_422ing(client, monkeypatch):
    """M174. `/advise` reads keys off whatever the street helper returns —
    `raw["river_iterations"]` for the river. A standalone helper that omits
    one does not fail loudly: the KeyError is caught and reported as
    "unsupported street/table-size combination", which points at the table
    size when the real cause is a missing field.

    That is exactly what happened: EVERY standalone river request 422'd
    with a message about table sizes.

    This asserts the BEHAVIOUR (a real request answers) rather than the
    presence of a string in the source. A source check passed while the
    key was dropped, because the same literal appears in the helper's
    terminal branch — the mutation survived and the test did not notice.
    """
    monkeypatch.setattr(api_config, "RIVER_SOLVE_STANDALONE", True)
    monkeypatch.setattr(api_config, "RIVER_STANDALONE_CLASSES_PER_SIDE", 4)
    # A size menu only the STANDALONE path can offer: the chained river
    # runs FLOP_TO_RIVER_RAISE_SIZES, which is empty, so a sized raise in
    # the answer proves which code path produced it. Without this, deleting
    # the dispatch falls through to the chained path, which also returns
    # 200 — a mutation that survived the first version of this test.
    monkeypatch.setattr(api_config, "RIVER_STANDALONE_RAISE_SIZES", (2.5,))
    monkeypatch.setattr(api_config, "RIVER_STANDALONE_MAX_RAISES", 2)
    assert api_config.FLOP_TO_RIVER_RAISE_SIZES == (), (
        "this test identifies the standalone path by a sized raise the "
        "chained path cannot offer; that no longer holds")
    response = client.post("/advise", json={
        "stack_bb": 20.0,
        "preflop_action_path": ["raise", "call_or_check"],
        "players": 2,
        "hero_cards": "6hKh",
        "board": "9d6dKs",
        "flop_action_path": ["call_or_check", "call_or_check"],
        "turn_card": "As",
        "turn_action_path": ["call_or_check", "call_or_check"],
        "river_card": "Ts",
    })
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["street"] == "river"
    assert body["strategy"], "a river decision must come back with a strategy"
    # The key whose absence caused the 422 is what /advise reports here.
    assert body["solve_iterations"] > 0
    hero_row = body["strategy"].get("Kh6h") or body["strategy"].get("6hKh")
    assert hero_row, body["strategy"].keys()
    assert any(action.startswith("raise:") for action in hero_row), (
        f"no sized raise in {sorted(hero_row)} — the chained path answered, "
        "not the standalone one")


def test_the_turn_cap_is_the_one_its_measurement_justifies(client):
    """M179. The turn range cap went 26 -> 140 on separability, not on a
    mean: paired over 56 spots, only 140 clears two standard errors
    against 26 on aggression (2.4 sigma), and on the FOLD axis it is
    2.9 sigma with 17 spots better and 2 worse.

    Cap 100's aggression gain does NOT clear that bar (1.6 sigma), which
    is why it was not adopted despite being the flop's setting and a third
    of the cost. M141 and M166 were both nearly adopted on exactly that
    kind of non-separable mean, so the distinction is worth pinning.

    This asserts the shipped value and that it is NOT silently lowered to
    a setting the measurement does not support.
    """
    assert api_config.TURN_STANDALONE_CLASSES_PER_SIDE == 140, (
        "the turn cap moved; if that is deliberate, re-run the 56-spot "
        "frontier — 26, 60 and 100 were all measured and only 140 was "
        "separably better than the incumbent")
    # The turn is still solved standalone; the cap only means anything there.
    assert api_config.TURN_SOLVE_STANDALONE is True


def test_the_turn_is_still_refused_certification_at_every_measured_cap(client):
    """M179. Coverage does NOT make the turn certifiable — 12 to 14 of 28
    strong-band spots exceed 0.10 at caps 26, 60, 100 AND 140. The turn's
    refusal is structural, not a coverage budget.

    That retires the hypothesis this project's own benchmark report
    proposed ("the turn needs the flop's coverage"), and it must not be
    quietly re-adopted by a later change that widens the cap again and
    assumes certification follows.
    """
    assert "turn" not in api_config.CERTIFY_RELIABILITY_ON_STREETS, (
        "the turn was certified without new evidence; widening the range "
        "was measured at four settings and never got the strong band clean")


def test_the_standalone_turn_solves_at_the_wider_coverage(client):
    """M173. The mutation that nothing caught until this existed.

    The entire point of solving the turn standalone is that it can afford
    range coverage the chained solve could not: the chained cap of 4 kept
    a median 4.6% of the opponent's range mass, against 28.2% at cap 26.
    Reverting the cap silently undoes the milestone while every other
    turn test still passes — the answers change, but nothing asserts they
    should not.

    Asserts the WIRING rather than a frequency, because the suite's
    fixtures shrink budgets and a threshold on hand counts would be
    measuring the fixture. Paired with the flag check so that turning
    standalone off is caught too.
    """
    import inspect

    source = inspect.getsource(api_solving._query_turn_from_path)
    assert "TURN_STANDALONE_CLASSES_PER_SIDE" in source, (
        "the turn is no longer deriving its ranges at the standalone cap — "
        "coverage silently reverts to 4.6% of the opponent's range"
    )
    assert api_config.TURN_STANDALONE_CLASSES_PER_SIDE >         api_config.MAX_TURN_PATH_QUERY_CLASSES_PER_SIDE

    # And the solve itself must be the standalone street solve, not the
    # flop->turn chain.
    standalone = inspect.getsource(api_solving._query_turn_standalone)
    assert "solve_flop(" in standalone
    assert "solve_flop_turn(" not in standalone


def test_the_standalone_turn_still_refuses_an_impossible_board(client):
    """M173. The chained path got this for free — a card already on the
    board simply had no chance branch to look up. Standalone has no branch
    list, so without an explicit check it answered with a confident
    strategy for a board that cannot exist.

    The behavioural guard is
    `test_solve_turn_from_path_rejects_an_illegal_turn_card`, which is what
    caught the regression; this pins the check itself so it cannot be
    removed while that test passes for some other reason (through
    /advise, a duplicate card is refused earlier still, by the
    request-level "a card can only be in one place" validation).
    """
    import inspect

    source = inspect.getsource(api_solving._query_turn_standalone)
    assert "not a legal turn card" in source
    assert "if turn_card in board_cards:" in source
