"""Suite-wide fixtures.

M284: the multiway preflop DISK TIER is off for every test. With it on, a
test would read whatever solve an earlier run left in `data/solve_store`,
and the ones that count SOLVES (M101) would pass by reading a file - the
suite would stop testing what it says it tests. Tests of the store itself
turn it back on against a `tmp_path`.
"""
import pytest


@pytest.fixture(autouse=True)
def _no_solve_store(monkeypatch):
    from api import caches
    monkeypatch.setattr(caches._multiway_store, "directory", None)
