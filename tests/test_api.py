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
    _cache.clear()
    _multiway_cache.clear()
    api_main._flop_cache.clear()
    api_main._flop_turn_cache.clear()
    api_main._flop_to_river_cache.clear()
    api_main._flop_query_library.clear()
    yield
    _cache.clear()
    _multiway_cache.clear()
    api_main._flop_cache.clear()
    api_main._flop_turn_cache.clear()
    api_main._flop_to_river_cache.clear()
    api_main._flop_query_library.clear()


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
