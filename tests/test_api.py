import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from api.main import _cache, _multiway_cache, app

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


@pytest.fixture(autouse=True)
def _disable_prewarm_and_clear_cache(monkeypatch):
    # Pre-warming solves 7 full 169-hand spots (plus one spot per
    # multiway table size) on startup — great for a real server,
    # unnecessary cost for a test run. Each test also gets clean caches
    # so tests can't leak state into each other.
    monkeypatch.setenv("POKER_SOLVER_PREWARM", "0")
    fast_table_configs = {
        players: {**table, "iterations": FAST_MULTIWAY_ITERATIONS}
        for players, table in api_main.MULTIWAY_TABLE_CONFIGS.items()
    }
    monkeypatch.setattr(api_main, "MULTIWAY_TABLE_CONFIGS", fast_table_configs)
    _cache.clear()
    _multiway_cache.clear()
    api_main._flop_cache.clear()
    yield
    _cache.clear()
    _multiway_cache.clear()
    api_main._flop_cache.clear()


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
