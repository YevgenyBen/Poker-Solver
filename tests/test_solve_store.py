"""M284: the disk tier behind the multiway preflop solve.

The failure this store must never have is serving a solve made under a
DIFFERENT configuration - M245's cap-in-no-cache-key trap, made permanent
by surviving restarts. So the fingerprint tests come first and test both
directions: it must move when the solve's configuration moves, and must
NOT move for a constant the solve never reads (or every copy edit would
throw the store away).
"""
import pathlib
import types

import numpy as np
import pytest

from api import solve_store
from api.solve_store import SolveStore
from poker_solver import persist
from poker_solver.equity import MultiwayEquityCache
from poker_solver.game_tree import GameConfig, build_game_tree
from poker_solver.solver import solve_preflop
from poker_solver.starting_hands import StartingHand

HANDS = [StartingHand("A", "A"), StartingHand("K", "K"), StartingHand("7", "2", suited=False)]
CONFIG = GameConfig(positions=("BTN", "SB", "BB"), stack_bb=30.0)


@pytest.fixture(scope="module")
def solved():
    cache = MultiwayEquityCache(hands=HANDS, samples=40, seed=1)
    result = solve_preflop(config=CONFIG, hands=HANDS, equity_cache=cache,
                           iterations=200, seed=1)
    result.prune_empty_nodes()
    return result


# -- the fingerprint -------------------------------------------------------

def _reads_two(cfg):
    return cfg.SOLVE_BUDGET, cfg.SOLVE_POOL


def _reads_one(cfg):
    return cfg.SOLVE_BUDGET


def _fake_cfg(**kw):
    base = dict(SOLVE_BUDGET=3000, SOLVE_POOL=("AA", "KK"), UNRELATED="copy text")
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_the_fingerprint_finds_every_constant_a_function_reads():
    assert solve_store.cfg_names_read_by((_reads_two,)) == ["SOLVE_BUDGET", "SOLVE_POOL"]


def test_a_constant_the_solve_reads_moves_the_fingerprint():
    params = {"players": 8, "stack": 145.0}
    a = solve_store.fingerprint(params, (_reads_two,), _fake_cfg())
    b = solve_store.fingerprint(params, (_reads_two,), _fake_cfg(SOLVE_BUDGET=12000))
    c = solve_store.fingerprint(params, (_reads_two,), _fake_cfg(SOLVE_POOL=("AA",)))
    assert len({a, b, c}) == 3


def test_a_constant_the_solve_does_not_read_leaves_it_alone():
    """Otherwise every copy edit in api/config.py throws away hours of
    stored solves - the store would be correct and useless."""
    params = {"players": 8, "stack": 145.0}
    a = solve_store.fingerprint(params, (_reads_two,), _fake_cfg())
    b = solve_store.fingerprint(params, (_reads_two,), _fake_cfg(UNRELATED="new copy"))
    assert a == b


def test_the_solve_parameters_move_the_fingerprint():
    cfg = _fake_cfg()
    keys = {solve_store.fingerprint({"players": p, "stack": s}, (_reads_one,), cfg)
            for p in (7, 8) for s in (100.0, 145.0)}
    assert len(keys) == 4


def _seed_one(cfg):
    return cfg.SOLVE_BUDGET, 1


def _seed_two(cfg):
    return cfg.SOLVE_BUDGET, 2


def test_a_different_solving_function_moves_the_fingerprint():
    """Editing HOW the solve is called - a new argument, a new seed - must
    invalidate, even when no constant changed.

    The two functions read the SAME constants, so only their source can
    tell them apart. A first version compared functions that also read
    different constants, and dropping the source from the fingerprint
    passed it anyway - M214's dead guard.
    """
    assert (solve_store.cfg_names_read_by((_seed_one,))
            == solve_store.cfg_names_read_by((_seed_two,)))
    cfg = _fake_cfg()
    assert (solve_store.fingerprint({}, (_seed_one,), cfg)
            != solve_store.fingerprint({}, (_seed_two,), cfg))


def test_an_engine_change_moves_the_fingerprint(monkeypatch):
    """The solver's behaviour lives in poker_solver/, so a stored solve
    from a different engine must never be served. Nothing else in the
    fingerprint changes here - only the engine hash."""
    cfg = _fake_cfg()
    a = solve_store.fingerprint({}, (_reads_one,), cfg)
    monkeypatch.setattr(solve_store, "engine_source_hash", lambda: "a different engine")
    assert solve_store.fingerprint({}, (_reads_one,), cfg) != a


def test_the_engine_hash_covers_every_engine_module():
    """The solver's behaviour lives in poker_solver/, so any change there
    must invalidate the store. Pin that the hash reads all of it."""
    engine = pathlib.Path(persist.__file__).resolve().parent
    modules = sorted(engine.rglob("*.py"))
    assert len(modules) > 10
    solve_store.engine_source_hash.cache_clear()
    reads = []
    real = pathlib.Path.read_bytes

    def spy(self):
        reads.append(self.resolve())
        return real(self)

    try:
        pathlib.Path.read_bytes = spy
        solve_store.engine_source_hash()
    finally:
        pathlib.Path.read_bytes = real
        solve_store.engine_source_hash.cache_clear()
    assert set(reads) == {m.resolve() for m in modules}


# -- the store -------------------------------------------------------------

def test_a_disabled_store_is_a_silent_miss(solved, tmp_path):
    store = SolveStore(None, max_bytes=10**9)
    store.put("k", solved)
    assert store.get("k", CONFIG, HANDS, build_game_tree) is None
    assert not store.contains("k")
    assert store.total_bytes() == 0


def test_a_stored_solve_comes_back_identical(solved, tmp_path):
    store = SolveStore(tmp_path, max_bytes=10**9)
    store.put("k", solved)
    back = store.get("k", CONFIG, HANDS, build_game_tree)
    assert back is not None and len(back.node_data) == len(solved.node_data)
    old = {p: n for n, p in persist.built_walk(solved.root)}
    new = {p: n for n, p in persist.built_walk(back.root)}
    for path, node in old.items():
        if id(node) in solved.node_data:
            assert np.array_equal(solved.node_data[id(node)].average_strategy(),
                                  back.node_data[id(new[path])].average_strategy())
    assert store.stats["hits"] == 1 and store.stats["writes"] == 1


def test_a_write_leaves_no_temporary_file_behind(solved, tmp_path):
    SolveStore(tmp_path, max_bytes=10**9).put("k", solved)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["k.npz"]


def test_an_unreadable_file_is_a_miss_and_is_removed(solved, tmp_path):
    """A torn write from a killed process must fall back to solving,
    never crash the request and never serve part of a tree."""
    store = SolveStore(tmp_path, max_bytes=10**9)
    (tmp_path / "k.npz").write_bytes(b"not a solve")
    assert store.get("k", CONFIG, HANDS, build_game_tree) is None
    assert not (tmp_path / "k.npz").exists()
    assert store.stats["corrupt"] == 1


def test_a_solve_over_another_pool_is_a_miss(solved, tmp_path):
    store = SolveStore(tmp_path, max_bytes=10**9)
    store.put("k", solved)
    assert store.get("k", CONFIG, list(reversed(HANDS)), build_game_tree) is None


def test_the_byte_cap_evicts_the_least_recently_read(solved, tmp_path):
    import os
    import time
    store = SolveStore(tmp_path, max_bytes=10**9)
    for key in ("a", "b", "c"):
        store.put(key, solved)
    size = (tmp_path / "a.npz").stat().st_size
    # Age them explicitly, then READ "a" so it is the most recent.
    for i, key in enumerate(("a", "b", "c")):
        os.utime(tmp_path / f"{key}.npz", (time.time() - 100 + i, time.time() - 100 + i))
    store.get("a", CONFIG, HANDS, build_game_tree)
    store.max_bytes = 2 * size
    store._evict()
    assert sorted(p.stem for p in tmp_path.glob("*.npz")) == ["a", "c"]
    assert store.stats["evicted"] == 1
