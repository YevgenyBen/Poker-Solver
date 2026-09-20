"""M294: the disk tier for the shared multiway equity cache."""
import numpy as np
import pytest

from api.equity_store import EquityStore, fingerprint
from poker_solver.equity import MultiwayEquityCache
from poker_solver.starting_hands import all_starting_hands

POOL = all_starting_hands()[:10]


def _filled(samples=8, seed=3, pool=POOL, tuples=((0, 1), (2, 3))):
    cache = MultiwayEquityCache(hands=list(pool), samples=samples, seed=seed)
    for spec in tuples:
        cache.traverser_equity_vector(tuple(pool[i] for i in spec))
    return cache


def _store(tmp_path, max_bytes=10 ** 9):
    return EquityStore(tmp_path, max_bytes)


def test_a_saved_cache_loads_back(tmp_path):
    store = _store(tmp_path)
    source = _filled()
    assert store.save(source)
    target = MultiwayEquityCache(hands=list(POOL), samples=8, seed=3)
    assert store.load(target) == len(source._cache)
    assert set(target._cache) == set(source._cache)


def test_a_disabled_store_does_nothing(tmp_path):
    store = EquityStore(None, 10 ** 9)
    assert store.save(_filled()) is False
    assert store.load(MultiwayEquityCache(hands=list(POOL), samples=8, seed=3)) == 0


def test_a_missing_file_is_a_miss_not_an_error(tmp_path):
    assert _store(tmp_path).load(_filled()) == 0


def test_a_file_from_another_sampling_configuration_is_REFUSED(tmp_path):
    """The one failure this must not have: serving one configuration's
    Monte Carlo draws as another's. The fingerprint separates them, and
    a hand-edited file is refused by the loader underneath."""
    store = _store(tmp_path)
    store.save(_filled(samples=8, seed=3))
    other = MultiwayEquityCache(hands=list(POOL), samples=9, seed=3)
    assert fingerprint(other) != fingerprint(_filled(samples=8, seed=3))
    assert store.load(other) == 0          # different fingerprint: no file


def test_a_corrupt_file_is_discarded_not_served(tmp_path):
    store = _store(tmp_path)
    source = _filled()
    store.save(source)
    path = next(tmp_path.glob("equity-*.npz"))
    path.write_bytes(b"not an npz")
    target = MultiwayEquityCache(hands=list(POOL), samples=8, seed=3)
    assert store.load(target) == 0
    assert not path.exists(), "a file that cannot be read must be removed"
    assert store.stats["refused"] == 1


def test_a_file_over_the_cap_is_not_written(tmp_path):
    store = _store(tmp_path, max_bytes=10)
    assert store.save(_filled()) is False
    assert list(tmp_path.glob("*.npz")) == [], "no partial file may survive"


def test_the_fingerprint_moves_with_the_engine_source(monkeypatch):
    cache = _filled()
    before = fingerprint(cache)
    import api.equity_store as module
    monkeypatch.setattr(module, "engine_source_hash", lambda: "different")
    assert fingerprint(cache) != before


def test_the_fingerprint_moves_with_the_hand_pool():
    assert fingerprint(_filled()) != fingerprint(_filled(pool=all_starting_hands()[:9]))


def test_the_suite_runs_with_the_store_disabled():
    """A test that counts equity work would otherwise pass by reading a
    file an earlier run left behind (M284's rule, for this tier)."""
    from api import caches
    assert caches._equity_store.enabled is False


def test_saving_twice_replaces_rather_than_appends(tmp_path):
    store = _store(tmp_path)
    cache = _filled()
    store.save(cache)
    cache.traverser_equity_vector((POOL[4], POOL[5]))
    store.save(cache)
    assert len(list(tmp_path.glob("equity-*.npz"))) == 1
    target = MultiwayEquityCache(hands=list(POOL), samples=8, seed=3)
    assert store.load(target) == len(cache._cache)
