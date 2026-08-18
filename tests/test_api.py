import pytest
from fastapi.testclient import TestClient

from api.main import _cache, app


@pytest.fixture(autouse=True)
def _disable_prewarm_and_clear_cache(monkeypatch):
    # Pre-warming solves 7 full 169-hand spots on startup — great for a
    # real server, unnecessary cost for a test run. Each test also gets
    # a clean cache so tests can't leak state into each other.
    monkeypatch.setenv("POKER_SOLVER_PREWARM", "0")
    _cache.clear()
    yield
    _cache.clear()


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
