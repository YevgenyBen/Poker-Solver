import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from api.main import _cache, _multiway_cache, app
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
        for players, table in api_main.MULTIWAY_TABLE_CONFIGS.items()
    }
    monkeypatch.setattr(api_main, "MULTIWAY_TABLE_CONFIGS", fast_table_configs)
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
    monkeypatch.setattr(api_main, "DEMO_CHAINED_FLOP_HERO_CLASSES", {StartingHand("9", "8", suited=True): 1.0})
    monkeypatch.setattr(api_main, "DEMO_CHAINED_FLOP_VILLAIN_CLASSES", {StartingHand("6", "4", suited=True): 1.0})
    monkeypatch.setattr(api_main, "FLOP_TURN_MAX_RAISES", 1)
    monkeypatch.setattr(api_main, "FLOP_TURN_RAISE_SIZES", ())
    # /solve_flop_cached (M22) has no `iterations` query param (see its
    # own module-docstring paragraph for why — nothing not part of the
    # canonical key is request-controllable), so unlike every other
    # /solve_flop* endpoint's tests, there's no per-request lever to
    # keep a real solve fast — the fixed pool/iteration constants
    # themselves have to be monkeypatched down instead.
    monkeypatch.setattr(api_main, "FLOP_QUERY_HERO_CLASSES", {StartingHand("A", "A"): 1.0})
    monkeypatch.setattr(api_main, "FLOP_QUERY_VILLAIN_CLASSES", {StartingHand("K", "K"): 1.0})
    monkeypatch.setattr(api_main, "FLOP_QUERY_ITERATIONS", FAST_FLOP_QUERY_ITERATIONS)
    # /solve_flop_from_path (M24) solves a REAL preflop spot over the
    # full 169-class pool internally (that's the whole point — a real
    # derived situation, not a fixed demo range), so there's no fixed
    # class-pool constant to shrink the way FLOP_QUERY_HERO_CLASSES
    # shrinks /solve_flop_cached's. Instead this fixture shrinks the
    # *cap* applied at request time, and the flop-stage iteration count.
    monkeypatch.setattr(api_main, "PATH_QUERY_ITERATIONS", FAST_PATH_QUERY_ITERATIONS)
    monkeypatch.setattr(api_main, "MAX_PATH_QUERY_CLASSES_PER_SIDE", FAST_MAX_PATH_QUERY_CLASSES_PER_SIDE)
    monkeypatch.setattr(api_main, "MAX_TURN_PATH_QUERY_CLASSES_PER_SIDE", FAST_MAX_TURN_PATH_QUERY_CLASSES_PER_SIDE)
    _cache.clear()
    _multiway_cache.clear()
    api_main._flop_cache.clear()
    api_main._flop_turn_cache.clear()
    api_main._flop_to_river_cache.clear()
    api_main._flop_query_library.clear()
    api_main._preflop_raw_cache.clear()
    api_main._path_query_libraries.clear()
    api_main._turn_path_cache.clear()
    yield
    _cache.clear()
    _multiway_cache.clear()
    api_main._flop_cache.clear()
    api_main._flop_turn_cache.clear()
    api_main._flop_to_river_cache.clear()
    api_main._flop_query_library.clear()
    api_main._preflop_raw_cache.clear()
    api_main._path_query_libraries.clear()
    api_main._turn_path_cache.clear()


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


def test_solve_rejects_stack_at_or_below_small_blind(client):
    # Default small_blind is 0.5bb; GameConfig itself rejects stack_bb
    # that small, surfaced here as a 422 rather than a 500.
    response = client.get(f"/solve/0.3?iterations={FAST_ITERATIONS}")
    assert response.status_code == 422


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
    assert len(body["opening_range"]) == len(api_main.DEMO_MULTIWAY_HANDS)


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
        f"&iterations={api_main.MAX_FLOP_TURN_ITERATIONS + 1}"
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
        f"&iterations={api_main.MAX_FLOP_TO_RIVER_ITERATIONS + 1}"
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
    turn_result = next(iter(api_main._flop_turn_cache.values()))
    river_result = next(iter(api_main._flop_to_river_cache.values()))

    def _any_branch_has_a_populated_chance_fn(result):
        return any(
            branch.chance_fn is not None
            for chance_node in result.chance_data.values()
            for branch in chance_node.branches.values()
        )

    assert not _any_branch_has_a_populated_chance_fn(turn_result)
    assert _any_branch_has_a_populated_chance_fn(river_result)


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
    assert body["pot"] == api_main.FLOP_QUERY_POT
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
    too_long = ["call_or_check"] * (api_main.MAX_PATH_LENGTH + 1)
    response = client.post("/preflop_walk", json=_walk_body(too_long))
    assert response.status_code == 422


def test_preflop_walk_rejects_stack_at_or_below_small_blind(client):
    # Mirrors test_solve_rejects_stack_at_or_below_small_blind — default
    # small_blind is 0.5bb, GameConfig itself rejects a stack that small.
    response = client.post("/preflop_walk", json=_walk_body([], stack_bb=0.3))
    assert response.status_code == 422


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

    different_turn_card = client.post("/solve_turn_from_path", json={**base, "turn_card": "4c"}).json()
    assert len(api_main._turn_path_cache) == 1
    assert different_turn_card["turn_card"] != first["turn_card"]

    already_all_in = client.post(
        "/solve_turn_from_path", json={**base, "flop_action_path": ["all_in", "call_or_check"]}
    ).json()
    assert len(api_main._turn_path_cache) == 1
    assert already_all_in["is_terminal"] is True
    assert already_all_in["strategy"] == {}
    assert already_all_in["trained"] == {}
    assert already_all_in["effective_stack_bb"] == pytest.approx(0.0)

    fold_out = client.post("/solve_turn_from_path", json={**base, "flop_action_path": ["all_in", "fold"]}).json()
    assert len(api_main._turn_path_cache) == 1
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
    too_long = ["call_or_check"] * (api_main.MAX_PATH_LENGTH + 1)
    response = client.post("/solve_turn_from_path", json=_turn_body(["raise", "call_or_check"], too_long))
    assert response.status_code == 422


def test_solve_turn_from_path_rejects_a_too_long_preflop_action_path(client):
    too_long = ["call_or_check"] * (api_main.MAX_PATH_LENGTH + 1)
    response = client.post(
        "/solve_turn_from_path", json=_turn_body(too_long, ["call_or_check", "call_or_check"])
    )
    assert response.status_code == 422


def test_solve_turn_from_path_rejects_out_of_range_turn_iterations(client):
    response = client.post(
        "/solve_turn_from_path",
        json=_turn_body(
            ["raise", "call_or_check"], ["call_or_check", "call_or_check"],
            turn_iterations=api_main.MAX_FLOP_TURN_ITERATIONS + 1,
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
