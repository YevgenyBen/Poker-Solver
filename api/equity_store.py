"""A disk tier for the shared multiway equity cache (M294).

**Why.** `MultiwayEquityCache` memoises the expensive half of every
multiway solve and is shared across every stack depth and table size -
but it dies with the process, so every restart re-pays the whole bill.
Measured on a fresh process at six-handed: the first solve computes
3,651 tuples in 112s and the fifth, against a cache holding 24,308,
takes 48s for the same work.

**This is the audit's R1, re-aimed.** R1 assumed M290's four-seed
ensemble re-samples equity its own seeds already paid for. Measured, the
seeds sample DIFFERENT opponent tuples (9,175 new against a single
solve's 3,651), so there is no duplication to remove inside a run. What
IS duplicated is across restarts, and that is what this removes.

**Filed under a fingerprint**, the same discipline as
`api/solve_store.py`: the hand pool, the sample count and seed the
values were drawn under, and the source of the engine modules that
compute them. A file from another configuration is refused rather than
silently mixed in - every value here is a Monte Carlo estimate under
exactly those settings.

One file per fingerprint, written atomically, capped by bytes; a file
that cannot be read is treated as absent and removed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import pathlib

import numpy as np

from poker_solver import equity_persist

from .solve_store import engine_source_hash

logger = logging.getLogger(__name__)


def fingerprint(cache) -> str:
    """Everything that decides what this cache's values mean."""
    h = hashlib.sha256()
    h.update(json.dumps({
        "kind": "multiway_equity",
        "hands": [str(hand) for hand in cache.hands],
        "samples": cache.samples,
        "seed": cache.seed,
        "format": equity_persist.FORMAT_VERSION,
    }, sort_keys=True).encode())
    h.update(engine_source_hash().encode())
    return h.hexdigest()[:32]


class EquityStore:
    """Load a shared equity cache at startup, save it once it has grown."""

    def __init__(self, directory, max_bytes: int):
        self.directory = pathlib.Path(directory) if directory else None
        self.max_bytes = max_bytes
        self.enabled = self.directory is not None
        self.stats = {"loaded": 0, "saved": 0, "entries_loaded": 0, "refused": 0}

    def _path(self, key: str) -> pathlib.Path:
        return self.directory / f"equity-{key}.npz"

    def load(self, cache) -> int:
        """Fill `cache` from disk. Returns entries added; 0 if there is
        nothing to load, the file is unreadable, or the store is off."""
        if not self.enabled:
            return 0
        path = self._path(fingerprint(cache))
        if not path.exists():
            return 0
        try:
            with np.load(path, allow_pickle=False) as arrays:
                added = equity_persist.from_portable(arrays, cache)
        except (OSError, ValueError, KeyError) as exc:
            # A file written by another configuration, or a truncated one:
            # treat as absent rather than serving its numbers.
            logger.warning("equity store: discarding %s (%s)", path.name, exc)
            self.stats["refused"] += 1
            path.unlink(missing_ok=True)
            return 0
        self.stats["loaded"] += 1
        self.stats["entries_loaded"] += added
        logger.info("equity store: loaded %d entries from %s", added, path.name)
        return added

    def save(self, cache) -> bool:
        """Write `cache` to disk, atomically. Returns whether it wrote."""
        if not self.enabled or not cache._cache:
            return False
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(fingerprint(cache))
        arrays = equity_persist.to_portable(cache)
        tmp = path.with_suffix(".tmp.npz")
        np.savez_compressed(tmp, **arrays)
        if tmp.stat().st_size > self.max_bytes:
            # Never leave a file bigger than the cap: a cache that grew
            # past it is kept in memory and simply not persisted.
            logger.warning("equity store: %.1f MB exceeds the %.1f MB cap, not stored",
                           tmp.stat().st_size / 1e6, self.max_bytes / 1e6)
            tmp.unlink(missing_ok=True)
            return False
        os.replace(tmp, path)
        self.stats["saved"] += 1
        logger.info("equity store: saved %d entries to %s", len(cache._cache), path.name)
        return True

    def total_bytes(self) -> int:
        if not self.enabled or not self.directory.exists():
            return 0
        return sum(p.stat().st_size for p in self.directory.glob("equity-*.npz"))
