"""A disk tier for the multiway preflop solve (M284).

**Why.** The 2026-09-18 audit's F56: 7- and 8-handed tables are warmed at
100bb only, so 5.81% of real hands wait on a cold preflop solve - 157s at
7-max and 580s at 8-max, measured - and warming more of them in memory
does not fit (an 8-max entry is 194 MB against a `_multiway_cache` budget
already spent). The same entries read back from disk in **0.22s and
1.48s**, bit-identically, at 24 and 79 MB a file.

**The one failure this module must not have: serving a solve of a
different configuration.** M245 found range caps living in no cache key,
so the wrong arm came back and read as a speed-up; a disk tier makes that
permanent, surviving restarts. So a stored solve is filed under a
FINGERPRINT of everything that shapes it:

- the solve's own parameters (table size, bucketed stack);
- **every `cfg.X` constant the solving functions read**, found by reading
  their source rather than from a hand-kept list that someone forgets to
  extend. Adding a constant to the solve changes the function's source,
  and changing a constant's value changes its repr - either moves the
  fingerprint. A constant the solve does NOT read (a flop cap, a copy
  string) leaves it alone, so the store is not thrown away by every
  unrelated edit;
- the source of those functions;
- the source of every `poker_solver/` module, because the solver's
  behaviour lives there. Any engine change invalidates the whole store -
  the background warmer refills it - and that is the right price: a
  stored answer from last week's engine is last week's engine.

**Our own solves only.** Everything here was computed by this engine, so
nothing about it touches the stone law - it is a cache that outlives the
process, not an external dataset.

Writes are atomic (temp file, then `os.replace`), a file that cannot be
read is treated as a miss and removed, and the directory is held under a
byte cap with least-recently-READ eviction.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
import pathlib
import re
import threading
from functools import lru_cache

import numpy as np

from poker_solver import persist

logger = logging.getLogger(__name__)

_ENGINE_DIR = pathlib.Path(persist.__file__).resolve().parent
_CFG_NAME = re.compile(r"\bcfg\.([A-Z][A-Z0-9_]*)\b")


@lru_cache(maxsize=1)
def engine_source_hash() -> str:
    """sha256 over every engine module's source, in path order.

    Cached for the life of the process: the engine's source does not
    change underneath a running server, and hashing it is ~1ms anyway.
    """
    h = hashlib.sha256()
    for path in sorted(_ENGINE_DIR.rglob("*.py")):
        h.update(path.relative_to(_ENGINE_DIR).as_posix().encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def cfg_names_read_by(functions) -> list:
    """Every `cfg.NAME` the given functions reference, sorted."""
    names = set()
    for fn in functions:
        names.update(_CFG_NAME.findall(inspect.getsource(fn)))
    return sorted(names)


def fingerprint(params: dict, functions, cfg) -> str:
    """A key that changes whenever anything shaping the solve changes."""
    h = hashlib.sha256()
    h.update(json.dumps(params, sort_keys=True, default=str).encode())
    for name in cfg_names_read_by(functions):
        h.update(name.encode())
        h.update(repr(getattr(cfg, name)).encode())
    for fn in functions:
        h.update(inspect.getsource(fn).encode())
    h.update(engine_source_hash().encode())
    return h.hexdigest()


class SolveStore:
    """A directory of portable solves, keyed by fingerprint.

    `directory=None` disables it entirely - every call is a no-op miss -
    which is what the test suite runs with, so no test can pass by reading
    a solve some earlier run left behind.
    """

    SUFFIX = ".npz"

    def __init__(self, directory, max_bytes: int):
        self.directory = pathlib.Path(directory) if directory else None
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self.stats = {"hits": 0, "misses": 0, "writes": 0, "evicted": 0, "corrupt": 0}

    @property
    def enabled(self) -> bool:
        return self.directory is not None

    def _path(self, key: str) -> pathlib.Path:
        return self.directory / (key + self.SUFFIX)

    def contains(self, key: str) -> bool:
        return self.enabled and self._path(key).exists()

    def get(self, key: str, config, hands, build_tree):
        if not self.enabled:
            return None
        path = self._path(key)
        if not path.exists():
            self.stats["misses"] += 1
            return None
        try:
            with np.load(path, allow_pickle=False) as arrays:
                result = persist.from_portable(arrays, config, hands, build_tree)
        except Exception:                                  # noqa: BLE001
            # A torn write from a killed process, a format bump, a pool
            # that no longer matches: all of them mean "not this solve".
            # Failing to a fresh solve is always safe; crashing the
            # request, or worse serving a partial tree, is not.
            logger.exception("solve store: unreadable entry %s, removing it", path.name)
            self.stats["corrupt"] += 1
            path.unlink(missing_ok=True)
            return None
        try:
            os.utime(path)        # least-recently-READ eviction
        except OSError:
            pass
        self.stats["hits"] += 1
        return result

    def put(self, key: str, result) -> None:
        if not self.enabled:
            return
        arrays = persist.to_portable(result)
        self.directory.mkdir(parents=True, exist_ok=True)
        final = self._path(key)
        tmp = final.with_name(f"{final.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
        with open(tmp, "wb") as fh:
            np.savez(fh, **arrays)
        # Atomic on both POSIX and Windows: a reader sees the old file or
        # the new one, never half of either.
        os.replace(tmp, final)
        self.stats["writes"] += 1
        self._evict()

    def _evict(self) -> None:
        with self._lock:
            files = [p for p in self.directory.glob("*" + self.SUFFIX)]
            sizes = {p: p.stat().st_size for p in files}
            total = sum(sizes.values())
            for p in sorted(files, key=lambda p: p.stat().st_mtime):
                if total <= self.max_bytes:
                    break
                total -= sizes[p]
                p.unlink(missing_ok=True)
                self.stats["evicted"] += 1

    def total_bytes(self) -> int:
        if not self.enabled or not self.directory.exists():
            return 0
        return sum(p.stat().st_size for p in self.directory.glob("*" + self.SUFFIX))
