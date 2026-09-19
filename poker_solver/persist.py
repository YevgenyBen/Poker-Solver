"""A solved tree in a form that survives the process that solved it.

M284. `StrategyResult.node_data` is keyed by `id(node)` - Python object
identity - which no restart can preserve. So a solve cannot be written to
disk as it stands, and the most expensive thing this engine computes (a
multiway preflop solve: 157s at 7-max, 580s at 8-max, measured) is thrown
away every time the server stops.

**Why it matters.** The 2026-09-18 audit (F56) found 5.81% of real hands
waiting on a cold 7- or 8-handed preflop solve, worst 211s, and warming
them in memory does not fit: an 8-max entry is 194 MB against a cache
budget already spent. Read back from disk the same entries cost 0.22s and
1.48s.

**Why a path key.** A node's action path from the root IS stable across
rebuilds of the same config - the same observation `warmstart.py` (M158)
is built on - so tables are keyed by path on the way out and grafted back
onto a freshly built tree on the way in.

**Why only BUILT children are walked.** `LazyChildren` builds a child the
first time it is touched, and the solver touches only what it visits.
Walking every child instead materialises the whole tree, which at 9-max
is tens of millions of nodes and died with MemoryError when M216 tried
it. `_built` is exactly the part the solve reached, and every `node_data`
entry is inside it (measured: 3,590 of 3,590 at 7-max, 12,276 of 12,276
at 8-max).

**Why not pickle.** Arrays and a JSON index, so `numpy.load` runs with
`allow_pickle=False` and reading a file can never execute code.

Deliberately engine-level and I/O-free: this module turns a result into
arrays and back. WHERE it is stored, and when a stored copy may be
trusted, is `api/solve_store.py`'s job.
"""
from __future__ import annotations

import json

import numpy as np

from .cfr import InfoSetTable
from .solver import StrategyResult

__all__ = ["to_portable", "from_portable", "built_walk"]

FORMAT_VERSION = 1


def built_walk(root):
    """(node, action path) for every MATERIALISED decision node.

    Never builds a child: it reads `children._built`, not `children`, so
    the walk costs what the solve already paid and nothing more.
    """
    stack = [(root, ())]
    while stack:
        node, path = stack.pop()
        if not hasattr(node, "legal_actions"):
            continue
        yield node, path
        built = getattr(node.children, "_built", None)
        if built is None:
            # An eagerly built tree (a plain dict) has nothing lazy to
            # protect, so every child is fair to visit.
            built = {a: node.children[a] for a in node.legal_actions}
        for action, child in built.items():
            stack.append((child, path + (str(action),)))


def to_portable(result: StrategyResult) -> dict:
    """{name: numpy array} holding everything needed to rebuild `result`.

    Only `regret_sum` and `strategy_sum` are kept: they are the whole of
    what a strategy is read from. `last_regret`/`last_strategy` are
    per-iteration scratch that a finished solve never reads.
    """
    paths, shapes, regrets, strategies = [], [], [], []
    for node, path in built_walk(result.root):
        table = result.node_data.get(id(node))
        if table is None:
            continue
        paths.append(list(path))
        shapes.append(table.regret_sum.shape)
        regrets.append(np.asarray(table.regret_sum, dtype=np.float64).ravel())
        strategies.append(np.asarray(table.strategy_sum, dtype=np.float64).ravel())
    missing = len(result.node_data) - len(paths)
    if missing:
        # A table the walk cannot reach belongs to a node the tree does
        # not hold - storing the rest would publish a solve with holes in
        # it and no way for a reader to tell.
        raise ValueError(f"{missing} node_data entries are not reachable through "
                         "the built tree, so this solve cannot be stored faithfully")
    meta = {"version": FORMAT_VERSION, "paths": paths,
            "hands": [str(h) for h in result.hands],
            "iterations": result.iterations}
    empty = np.zeros(0, dtype=np.float64)
    return {
        "meta": np.frombuffer(json.dumps(meta).encode("utf-8"), dtype=np.uint8),
        "shapes": np.asarray(shapes, dtype=np.int64).reshape(-1, 2),
        "regret": np.concatenate(regrets) if regrets else empty,
        "strategy": np.concatenate(strategies) if strategies else empty,
    }


def portable_meta(arrays) -> dict:
    return json.loads(bytes(arrays["meta"]).decode("utf-8"))


def from_portable(arrays, config, hands, build_tree) -> StrategyResult:
    """Rebuild a `StrategyResult` for `config` from `to_portable`'s output.

    `hands` must be the pool the caller would have solved with; a stored
    solve over a different pool is REFUSED rather than re-shaped, because
    row i of every table means hand i and a mismatch would silently give
    one hand another's strategy.
    """
    meta = portable_meta(arrays)
    if meta.get("version") != FORMAT_VERSION:
        raise ValueError(f"stored format {meta.get('version')} is not {FORMAT_VERSION}")
    if meta["hands"] != [str(h) for h in hands]:
        raise ValueError("stored solve was made over a different hand pool")
    root = build_tree(config)
    shapes = arrays["shapes"]
    regret, strategy = arrays["regret"], arrays["strategy"]
    node_data, offset = {}, 0
    for path, (rows, cols) in zip(meta["paths"], shapes):
        size = int(rows) * int(cols)
        node = root
        for step in path:
            # A path can walk off the end of a DIFFERENT tree onto a
            # terminal, which has no actions at all - that is the same news
            # as a missing action, and must be refused the same way rather
            # than crash on it.
            actions = getattr(node, "legal_actions", ())
            action = next((a for a in actions if str(a) == step), None)
            if action is None:
                raise ValueError(f"stored path {path} does not exist in this tree")
            node = node.children[action]
        if not hasattr(node, "legal_actions"):
            raise ValueError(f"stored path {path} ends on a terminal in this tree")
        if len(node.legal_actions) != int(cols):
            raise ValueError(f"stored table at {path} has {cols} actions, "
                             f"the tree has {len(node.legal_actions)}")
        node_data[id(node)] = InfoSetTable(
            regret_sum=regret[offset:offset + size].reshape(int(rows), int(cols)).copy(),
            strategy_sum=strategy[offset:offset + size].reshape(int(rows), int(cols)).copy(),
            last_regret=None, last_strategy=None)
        offset += size
    return StrategyResult(config=config, root=root, hands=list(hands),
                          node_data=node_data, iterations=meta["iterations"],
                          elapsed_seconds=0.0)
