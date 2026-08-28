"""Reuse a solved tree's accumulators when solving a related tree.

M158. A flop request costs ~17s at the median and 64.5s at p90 (M155),
against the 15-30 seconds a player at a real table has to act — so one
decision in ten cannot be answered before it has to be made. The CFR
solve is 86% of that cost, and 71 of 73 flop requests in a 120-hand
session pay it COLD.

They pay it cold because hero's combo is force-included into the range
before the cap, which makes the solve depend on hero, which is why hero's
hand class is part of the cache key (M76 — without that, the first
request for a spot fixed the pool and every later hand got no advice).

But M155 measured how much hero's inclusion actually changes the solve,
against the right yardstick: adding hero moves other hands' strategies by
p90 0.002-0.107, while re-running the IDENTICAL hero-free solve under a
different equity seed moves them 0.024-0.112 — as much or more. Hero's
influence sits inside the solver's own run-to-run noise. So a cached
hero-free solve of the same canonical spot is a legitimate starting
point, and the only question is how much refinement it needs.

Measured: 50 refinement iterations from a grafted start land within
0.0011-0.0155 of a full 500-iteration cold solve on hero's own row, at
9.7-11.4x less solve time. That error is inside the seed-to-seed noise
above, so the warm answer is not distinguishable from the cold one by
anything this solver can resolve.

**Why a path key.** `node_data` is keyed by `id(node)` and every request
rebuilds its tree, so cached tables cannot be looked up directly. A
node's action path from the root IS stable across rebuilds of the same
config, so tables are re-keyed by path on the way in and grafted back on
the way out.
"""
from __future__ import annotations

import numpy as np

from .cfr import InfoSetTable

__all__ = ["index_by_path", "graft_node_data"]


def _walk(root):
    """(node, action-path) for every decision node, root first.

    Iterative rather than recursive: a deep street tree at max_raises=4
    is well within Python's limit, but this runs per request and the
    explicit stack costs nothing.
    """
    stack, seen = [(root, ())], set()
    while stack:
        node, path = stack.pop()
        if id(node) in seen or not hasattr(node, "legal_actions"):
            continue
        seen.add(id(node))
        yield node, path
        for action in node.legal_actions:
            stack.append((node.children[action], path + (str(action),)))


def index_by_path(root, node_data: dict) -> dict:
    """{action path: InfoSetTable} for a solved tree.

    The stable form of `node_data`, safe to cache across requests that
    rebuild the tree. Nodes the solve never visited are simply absent —
    same meaning they have in `node_data` itself.
    """
    return {path: node_data[id(node)]
            for node, path in _walk(root)
            if id(node) in node_data}


def graft_node_data(root, by_path: dict, cached_hands: list, hands: list) -> dict:
    """Map cached tables onto a rebuilt tree, re-shaped for a new pool.

    Returns a `{id(node): InfoSetTable}` suitable for `cfr.solve`'s
    `initial_node_data`. A hand the cached solve did not have — hero,
    typically — starts at zero regret, exactly as a cold solve would
    start it; every hand the cached solve did have keeps its accumulated
    regret and strategy.

    Tables whose action count no longer matches are DROPPED rather than
    reshaped: a differing action count means the tree shape changed
    (different stack depth, different raise menu), and a cached table
    from a different game is not a starting point for this one. Dropping
    is safe — `_solve_recurse` creates a zero table for anything absent.
    """
    position = {hand: i for i, hand in enumerate(cached_hands)}
    take = [position.get(hand) for hand in hands]
    grafted = {}
    for node, path in _walk(root):
        cached = by_path.get(path)
        if cached is None:
            continue
        if cached.regret_sum.shape[1] != len(node.legal_actions):
            continue
        actions = cached.regret_sum.shape[1]
        regret = np.zeros((len(hands), actions), dtype=float)
        strategy = np.zeros((len(hands), actions), dtype=float)
        for new_index, old_index in enumerate(take):
            if old_index is not None:
                regret[new_index] = cached.regret_sum[old_index]
                strategy[new_index] = cached.strategy_sum[old_index]
        grafted[id(node)] = InfoSetTable(
            regret_sum=regret, strategy_sum=strategy,
            last_regret=None, last_strategy=None,
        )
    return grafted
