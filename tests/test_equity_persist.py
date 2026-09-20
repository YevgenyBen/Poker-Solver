"""M294: a multiway equity cache that survives its process."""
import numpy as np
import pytest

from poker_solver.equity import MultiwayEquityCache
from poker_solver.equity_persist import FORMAT_VERSION, from_portable, portable_meta, to_portable
from poker_solver.starting_hands import all_starting_hands

POOL = all_starting_hands()[:12]


def _filled(samples=8, seed=3, pool=POOL, tuples=((0, 1), (2, 3, 4), (5,))):
    cache = MultiwayEquityCache(hands=list(pool), samples=samples, seed=seed)
    for spec in tuples:
        opponents = tuple(pool[i] for i in spec)
        cache.traverser_equity_vector(opponents)
        cache.traverser_validity_mask(opponents)
    return cache


def test_a_cache_round_trips_exactly():
    source = _filled()
    arrays = to_portable(source)
    target = MultiwayEquityCache(hands=list(POOL), samples=source.samples, seed=source.seed)
    assert from_portable(arrays, target) == len(source._cache)
    assert set(target._cache) == set(source._cache)
    for key, vector in source._cache.items():
        assert np.allclose(target._cache[key], vector, atol=1e-6)
        assert np.array_equal(target._validity[key], source._validity[key])


def test_a_restored_entry_is_a_HIT_not_a_recompute(monkeypatch):
    """The whole point: a restored cache must answer without sampling."""
    source = _filled()
    target = MultiwayEquityCache(hands=list(POOL), samples=source.samples, seed=source.seed)
    from_portable(to_portable(source), target)
    key = next(iter(source._cache))
    monkeypatch.setattr(target, "_compute", None, raising=False)
    import poker_solver.equity as equity_module
    monkeypatch.setattr(equity_module, "deal_n_hands",
                        lambda *a, **k: pytest.fail("a restored entry was recomputed"))
    assert np.allclose(target.traverser_equity_vector(key), source._cache[key], atol=1e-6)


def test_the_key_order_does_not_matter_after_a_round_trip():
    """The cache canonicalises its key by sorting; a restored entry must
    land on the same key or every lookup misses."""
    source = _filled(tuples=((2, 3, 4),))
    target = MultiwayEquityCache(hands=list(POOL), samples=source.samples, seed=source.seed)
    from_portable(to_portable(source), target)
    shuffled = (POOL[4], POOL[2], POOL[3])
    assert tuple(sorted(shuffled, key=str)) in target._cache


def test_a_different_hand_pool_is_refused():
    source = _filled()
    other = MultiwayEquityCache(hands=all_starting_hands()[:11], samples=source.samples,
                                seed=source.seed)
    with pytest.raises(ValueError, match="different hand pool"):
        from_portable(to_portable(source), other)


@pytest.mark.parametrize("samples,seed", [(9, 3), (8, 4)])
def test_a_different_sampling_configuration_is_refused(samples, seed):
    """Every value is a Monte Carlo estimate under exactly these, so a
    silent mismatch would serve one configuration's numbers as another's."""
    source = _filled()
    other = MultiwayEquityCache(hands=list(POOL), samples=samples, seed=seed)
    with pytest.raises(ValueError, match="samples="):
        from_portable(to_portable(source), other)


def test_a_future_format_is_refused():
    arrays = dict(to_portable(_filled()))
    import json
    meta = portable_meta(arrays)
    meta["version"] = FORMAT_VERSION + 1
    arrays["meta"] = np.frombuffer(json.dumps(meta).encode("utf-8"), dtype=np.uint8)
    with pytest.raises(ValueError, match="stored format"):
        from_portable(arrays, MultiwayEquityCache(hands=list(POOL), samples=8, seed=3))


def test_existing_entries_are_kept_not_overwritten():
    source = _filled()
    target = _filled(tuples=((0, 1),))
    keep = target._cache[next(iter(target._cache))].copy()
    added = from_portable(to_portable(source), target)
    assert added == len(source._cache) - 1
    assert np.array_equal(target._cache[next(iter(target._cache))], keep)


def test_an_empty_cache_round_trips():
    empty = MultiwayEquityCache(hands=list(POOL), samples=8, seed=3)
    target = MultiwayEquityCache(hands=list(POOL), samples=8, seed=3)
    assert from_portable(to_portable(empty), target) == 0


def test_the_file_is_readable_without_pickle(tmp_path):
    """`numpy.load(allow_pickle=False)` must be enough, so reading a
    stored cache can never execute code."""
    path = tmp_path / "equity.npz"
    np.savez_compressed(path, **to_portable(_filled()))
    with np.load(path, allow_pickle=False) as arrays:
        target = MultiwayEquityCache(hands=list(POOL), samples=8, seed=3)
        assert from_portable(arrays, target) > 0


def test_an_entry_does_not_depend_on_when_it_was_computed():
    """The property the whole disk tier rests on: each key's value is
    drawn from a per-key seeded stream, so a RESTORED entry equals what a
    fresh sample would have produced. If this ever fails, a stored cache
    would change the advice depending on what happened to be cached."""
    specs = [(0, 1), (2, 3), (4, 5), (6, 7)]
    forward = MultiwayEquityCache(hands=list(POOL), samples=16, seed=3)
    backward = MultiwayEquityCache(hands=list(POOL), samples=16, seed=3)
    for spec in specs:
        forward.traverser_equity_vector(tuple(POOL[i] for i in spec))
    for spec in reversed(specs):
        backward.traverser_equity_vector(tuple(POOL[i] for i in spec))
    for key, vector in forward._cache.items():
        assert np.array_equal(backward._cache[key], vector)


def test_a_restored_entry_matches_a_freshly_sampled_one():
    source = _filled(samples=16, tuples=((0, 1), (2, 3)))
    restored = MultiwayEquityCache(hands=list(POOL), samples=16, seed=source.seed)
    from_portable(to_portable(source), restored)
    fresh = MultiwayEquityCache(hands=list(POOL), samples=16, seed=source.seed)
    for key in source._cache:
        assert np.allclose(restored.traverser_equity_vector(key),
                           fresh.traverser_equity_vector(key), atol=1e-6)
