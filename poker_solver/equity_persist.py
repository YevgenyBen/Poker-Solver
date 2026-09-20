"""A multiway equity cache that survives the process that filled it.

M294. `MultiwayEquityCache` memoises the expensive half of every
multiway solve: the win-share of each of 169 hand classes against one
fixed tuple of opponents, Monte Carlo sampled. It is shared across every
stack depth and table size, and it dies with the process.

**Why that costs a player.** Measured on a fresh process: the first
six-handed solve computes 3,651 tuples and takes 112s; the fifth,
against a cache holding 24,308, takes 48s for the same work. A seed
ensemble (M290) multiplies the tuples, not the duplication - its four
seeds sample DIFFERENT opponents, which is why the audit's R1 premise
("the seeds re-sample what each other paid for") was wrong. What is
duplicated is across RESTARTS: every one re-pays the whole bill.

**Shape.** Keys are tuples of `StartingHand` of varying length (one per
opponent), so they are stored as indices into the hand pool plus a
length, and the values as float32 - 13.1 MB for 19,382 entries against
28.2 MB in memory. Arrays and a JSON index, so `numpy.load` runs with
`allow_pickle=False` and reading a file cannot execute code.

Deliberately engine-level and I/O-free, like `persist.py`: this turns a
cache into arrays and back. WHERE it is stored, and when a stored copy
may be trusted, is `api/equity_store.py`'s job.
"""
from __future__ import annotations

import json

import numpy as np

from .equity import MultiwayEquityCache

__all__ = ["to_portable", "from_portable", "portable_meta"]

FORMAT_VERSION = 1


def to_portable(cache: MultiwayEquityCache) -> dict:
    """{name: numpy array} holding everything needed to rebuild `cache`.

    Entries whose validity mask was never computed are stored anyway -
    `traverser_validity_mask` fills it on demand from the same call that
    fills the vector, so a missing mask is a cache miss, not a wrong
    answer.
    """
    index = {str(hand): i for i, hand in enumerate(cache.hands)}
    keys, lengths, vectors, masks, has_mask = [], [], [], [], []
    for key, vector in cache._cache.items():
        seats = [index.get(str(hand)) for hand in key]
        if any(seat is None for seat in seats):
            # An opponent outside this pool cannot be addressed by index,
            # and storing it would make the file unreadable against the
            # pool it claims. Skipping one entry only costs a re-sample.
            continue
        keys.extend(seats)
        lengths.append(len(seats))
        vectors.append(np.asarray(vector, dtype=np.float32).ravel())
        mask = cache._validity.get(key)
        has_mask.append(mask is not None)
        masks.append(np.asarray(mask if mask is not None else np.zeros(len(cache.hands)),
                                dtype=bool).ravel())
    meta = {"version": FORMAT_VERSION, "hands": [str(h) for h in cache.hands],
            "samples": cache.samples, "seed": cache.seed, "entries": len(lengths)}
    empty32 = np.zeros(0, dtype=np.float32)
    return {
        "meta": np.frombuffer(json.dumps(meta).encode("utf-8"), dtype=np.uint8),
        "keys": np.asarray(keys, dtype=np.int16),
        "lengths": np.asarray(lengths, dtype=np.int16),
        "vectors": np.concatenate(vectors) if vectors else empty32,
        "masks": np.concatenate(masks) if masks else np.zeros(0, dtype=bool),
        "has_mask": np.asarray(has_mask, dtype=bool),
    }


def portable_meta(arrays) -> dict:
    return json.loads(bytes(arrays["meta"]).decode("utf-8"))


def from_portable(arrays, cache: MultiwayEquityCache) -> int:
    """Fill `cache` from `to_portable`'s output. Returns entries added.

    REFUSES a file made for a different hand pool, sample count or seed:
    every value in it is a Monte Carlo estimate under exactly those, and
    a silent mismatch would hand one configuration's numbers to another.
    """
    meta = portable_meta(arrays)
    if meta.get("version") != FORMAT_VERSION:
        raise ValueError(f"stored format {meta.get('version')} is not {FORMAT_VERSION}")
    if meta["hands"] != [str(h) for h in cache.hands]:
        raise ValueError("stored equity cache was built over a different hand pool")
    if meta["samples"] != cache.samples or meta["seed"] != cache.seed:
        raise ValueError(
            f"stored equity cache used samples={meta['samples']} seed={meta['seed']}, "
            f"this one uses samples={cache.samples} seed={cache.seed}")
    width = len(cache.hands)
    keys, lengths = arrays["keys"], arrays["lengths"]
    vectors, masks, has_mask = arrays["vectors"], arrays["masks"], arrays["has_mask"]
    added, key_at, value_at = 0, 0, 0
    with cache._lock:
        for entry, length in enumerate(lengths):
            length = int(length)
            seats = keys[key_at:key_at + length]
            key_at += length
            vector = vectors[value_at:value_at + width]
            mask = masks[value_at:value_at + width]
            value_at += width
            cache_key = tuple(sorted((cache.hands[int(s)] for s in seats), key=str))
            if cache_key in cache._cache:
                continue
            cache._cache[cache_key] = np.asarray(vector, dtype=np.float64).copy()
            if bool(has_mask[entry]):
                cache._validity[cache_key] = np.asarray(mask, dtype=bool).copy()
            added += 1
    return added
